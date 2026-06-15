"""
services/replay_service.py — Replay mode handlers.

Owns:
  - Session list / load / unload
  - Playback play / pause / stop / finish detection
  - Analysis data reads (_read_analysis_data, _read_full_analysis, _read_session_info)
  - 1-second broadcast timer during replay

Does NOT own:
  - Live mode or Intel capture (see live_service.py)
  - WebSocket transport (see bridge/websocket_bridge.py)
  - Payload builders (shared in session_service.py)
"""

import asyncio
import datetime
import os
import time
from typing import Callable, Optional

import sys as _sys
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)  # Backend/
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)

from replay_engine import ReplayEngine

import sys as _sys
_API_DIR = os.path.join(_BACKEND, "api")
if _API_DIR not in _sys.path:
    _sys.path.insert(0, _API_DIR)

from message_types import (
    MSG_SESSION_STATE, MSG_SESSION_LIST_RESULT, MSG_SESSION_INFO_RESULT,
    MSG_API_ERROR,
    STATE_IDLE, STATE_LOADING, STATE_PLAYING, STATE_PAUSED,
    STATE_STOPPED, STATE_FINISHED,
    MODE_REPLAY, MODE_LIVE,
    ERR_SESSION_NOT_FOUND, ERR_SESSION_INVALID, ERR_SESSION_LOAD_FAILED,
    ERR_SEEK_NOT_IMPLEMENTED, ERR_WRONG_MODE, ERR_NO_SESSION,
)


class ReplayService:
    """
    Handles all replay-mode session and playback lifecycle.

    Injected dependencies (via __init__):
      engine         — ReplayEngine instance
      broadcast_fn   — async fn(payload) → all clients
      send_fn        — async fn(ws, payload) → one client
      get_state_payload_fn  — callable returning current state dict
      error_fn       — callable(code, msg, ctx) → error dict
      on_finish_fn   — called when replay finishes naturally
    """

    def __init__(
        self,
        engine: ReplayEngine,
        broadcast_fn: Callable,
        send_fn: Callable,
        get_state_payload_fn: Callable,
        error_fn: Callable,
        on_finish_fn: Callable,
        loop: asyncio.AbstractEventLoop,
    ):
        self._engine              = engine
        self._broadcast           = broadcast_fn
        self._send                = send_fn
        self._state_payload       = get_state_payload_fn
        self._error               = error_fn
        self._on_finish           = on_finish_fn
        self._loop                = loop

        # ── Mutable state owned by this service ──
        self.mode: str            = MODE_REPLAY
        self.state: str           = STATE_IDLE
        self.session_id: str      = ""
        self.session_name: str    = ""
        self.db_path: str         = ""
        self.duration_seconds: float = 0.0
        self.stream_count: int    = 0
        self.start_unix: float    = 0.0

        # Elapsed tracking
        self.play_start_wall: float   = 0.0
        self.play_start_unix: float   = 0.0
        self.elapsed_at_pause: float  = 0.0
        self.total_pause_s: float     = 0.0
        self.pause_started_unix: float = 0.0
        self.paused: bool             = False
        self._elapsed_at_finish: float = 0.0

        # 1-second timer task
        self._timer_task: Optional[asyncio.Task] = None

    # ── Public message handlers ────────────────────────────────────────────────

    async def handle_session_list(self, websocket):
        loop = asyncio.get_running_loop()
        files = await loop.run_in_executor(None, self._engine.scan)
        await self._send(websocket, {
            "type":     MSG_SESSION_LIST_RESULT,
            "sessions": files,
        })

    async def handle_session_load(self, websocket, db_path: str):
        if not db_path:
            await self._send(websocket, self._error(
                ERR_SESSION_NOT_FOUND, "No path provided.", "session.load"))
            return

        if self.mode == MODE_LIVE:
            await self._send(websocket, self._error(
                ERR_WRONG_MODE, "Switch to Replay mode before loading a session.", "session.load"))
            return

        self.state = STATE_LOADING
        await self._broadcast(self._state_payload())

        loop = asyncio.get_running_loop()
        try:
            session = await loop.run_in_executor(None, self._engine.preload, db_path)
        except FileNotFoundError as e:
            self.state = STATE_IDLE
            await self._broadcast(self._state_payload())
            await self._send(websocket, self._error(ERR_SESSION_NOT_FOUND, str(e), "session.load"))
            return
        except RuntimeError as e:
            msg_lower = str(e).lower()
            code = ERR_SESSION_INVALID if "metadata" in msg_lower else ERR_SESSION_LOAD_FAILED
            self.state = STATE_IDLE
            await self._broadcast(self._state_payload())
            await self._send(websocket, self._error(code, str(e), "session.load"))
            return

        # Successful load (metadata only — publishers NOT yet started)
        self.db_path          = db_path
        self.session_id       = os.path.splitext(os.path.basename(db_path))[0]
        self.session_name     = self.session_id.replace("_", " ")
        self.duration_seconds = session.get("duration_seconds", 0.0)
        self.stream_count     = session.get("stream_count", 0)

        # Parse start_unix from ISO string
        try:
            start_iso = session.get("start", "")
            if start_iso:
                dt = datetime.datetime.fromisoformat(str(start_iso))
                self.start_unix = dt.replace(tzinfo=datetime.timezone.utc).timestamp()
            else:
                self.start_unix = 0.0
        except Exception:
            self.start_unix = 0.0

        self.state            = STATE_STOPPED
        self.play_start_wall  = 0.0
        self.elapsed_at_pause = 0.0
        self.paused           = False

        print(f"[ReplayService] Loaded: {self.session_id} "
              f"({self.stream_count} streams, {self.duration_seconds:.1f}s)")

        await self._broadcast(self._state_payload())

    async def handle_session_unload(self, websocket):
        self.stop_timer()
        self._engine.stop()
        self.reset_session()
        self.state = STATE_IDLE
        await self._broadcast(self._state_payload())

    async def handle_get_csv(self, websocket):
        """Load full analysis data and send over WebSocket."""
        if not self.db_path:
            await self._send(websocket, self._error(
                ERR_NO_SESSION, "No session loaded.", "session.get_csv"))
            return

        session_dir = os.path.dirname(self.db_path)
        dnlc_path   = os.path.join(session_dir, "dnlc.db")
        if not os.path.exists(dnlc_path):
            dnlc_path = self.db_path

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: _read_analysis_data(dnlc_path))

        await self._send(websocket, {
            "type":         "session.csv_data",
            "virtra_rows":  result["virtra"],
            "speech_rows":  result["speech"],
            "hr_rows":      result["hr"],
            "gaze_rows":    result["gaze"],
            "ecg_rows":     result["ecg"],
            "start_unix":   self.start_unix,
        })
        print(f"[ReplayService] Sent CSV data: "
              f"{len(result['virtra'])} VirTra, "
              f"{len(result['hr'])} HR, "
              f"{len(result['gaze'])} gaze")

    async def handle_analysis_load(self, websocket, db_path: str):
        """Independent analysis loader — reads any session .db by path."""
        if not db_path:
            await self._send(websocket, self._error(
                ERR_NO_SESSION, "No db_path provided.", "analysis.load"))
            return

        session_dir = os.path.dirname(db_path)
        dnlc_path   = os.path.join(session_dir, "dnlc.db")
        if not os.path.exists(dnlc_path):
            dnlc_path = db_path

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: _read_full_analysis(dnlc_path))

        await self._send(websocket, {
            "type":          "analysis.data",
            "valid":         result["valid"],
            "reason":        result.get("reason", ""),
            "events":        result.get("events", []),
            "event_types":   result.get("event_types", []),
            "hr":            result.get("hr", []),
            "gaze":          result.get("gaze", []),
            "ecg":           result.get("ecg", []),
            "session_start": result.get("session_start", 0),
            "session_end":   result.get("session_end", 0),
        })
        print(f"[ReplayService] analysis.data sent: valid={result['valid']}, "
              f"{len(result.get('events', []))} events")

    async def handle_session_get_info(self, websocket, db_path: str):
        """Read session_info table from a .db file and reply."""
        if not db_path:
            await self._send(websocket, self._error(
                ERR_NO_SESSION, "No db_path provided.", "session.get_info"))
            return

        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(None, _read_session_info, db_path)
        await self._send(websocket, {
            "type":           MSG_SESSION_INFO_RESULT,
            "db_path":        db_path,
            "participant_id": info.get("participant_id", ""),
            "session_name":   info.get("session_name",   ""),
            "drill":          info.get("drill",           ""),
            "recorded_at":    info.get("recorded_at",     ""),
        })

    # ── Playback handlers ─────────────────────────────────────────────────────

    async def handle_play(self, websocket):
        if self.mode == MODE_LIVE:
            await self._send(websocket, self._error(
                ERR_WRONG_MODE, "Not available in live mode.", "playback.play"))
            return
        if self.state not in (STATE_PAUSED, STATE_STOPPED, STATE_IDLE):
            return

        if self.state == STATE_IDLE or not self.db_path:
            await self._send(websocket, self._error(
                ERR_NO_SESSION, "No session loaded.", "playback.play"))
            return

        if self.state == STATE_STOPPED:
            if self._engine.is_prepared():
                loop = asyncio.get_running_loop()
                try:
                    await loop.run_in_executor(None, self._engine.start_publishers)
                except Exception as e:
                    self.state = STATE_STOPPED
                    await self._broadcast(self._state_payload())
                    await self._send(websocket, self._error(ERR_SESSION_LOAD_FAILED, str(e), "playback.play"))
                    return
                self.elapsed_at_pause = 0.0
                self.total_pause_s    = 0.0
            else:
                # Slow path: re-preload
                self.state = STATE_LOADING
                await self._broadcast(self._state_payload())
                loop = asyncio.get_running_loop()
                try:
                    session = await loop.run_in_executor(None, self._engine.load, self.db_path)
                except Exception as e:
                    self.state = STATE_STOPPED
                    await self._broadcast(self._state_payload())
                    await self._send(websocket, self._error(ERR_SESSION_LOAD_FAILED, str(e), "playback.play"))
                    return
                self.duration_seconds = session.get("duration_seconds", self.duration_seconds)
                self.stream_count     = session.get("stream_count", self.stream_count)
                self.elapsed_at_pause = 0.0
                self.total_pause_s    = 0.0

        # Resume from paused position
        if self.state == STATE_PAUSED and self.mode == MODE_REPLAY:
            self._engine.resume_publishers()

        if self.pause_started_unix > 0:
            self.total_pause_s      += time.time() - self.pause_started_unix
            self.pause_started_unix  = 0.0

        self.play_start_wall = time.monotonic()
        self.play_start_unix = time.time()
        self.paused          = False
        self.state           = STATE_PLAYING
        await self._broadcast(self._state_payload())
        self.start_timer()

    async def handle_pause(self, websocket):
        if self.mode == MODE_LIVE:
            await self._send(websocket, self._error(
                ERR_WRONG_MODE, "Not available in live mode.", "playback.pause"))
            return
        if self.state != STATE_PLAYING:
            return

        self.elapsed_at_pause    = self.current_elapsed()
        self.paused              = True
        self.pause_started_unix  = time.time()
        self.state               = STATE_PAUSED
        self.stop_timer()
        if self.mode == MODE_REPLAY:
            self._engine.pause_publishers()
        await self._broadcast(self._state_payload())

    async def handle_stop(self, websocket):
        self.stop_timer()
        if self.mode == MODE_REPLAY:
            self._engine.stop()
        self.elapsed_at_pause    = 0.0
        self.total_pause_s       = 0.0
        self.pause_started_unix  = 0.0
        self.paused              = False
        self.state               = STATE_STOPPED
        await self._broadcast(self._state_payload())

    # ── Timer ─────────────────────────────────────────────────────────────────

    def start_timer(self):
        self.stop_timer()
        self._timer_task = self._loop.create_task(self._timer_loop())

    def stop_timer(self):
        if self._timer_task and not self._timer_task.done():
            self._timer_task.cancel()
        self._timer_task = None

    async def _timer_loop(self):
        try:
            while True:
                await self._tick()
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            pass

    async def _tick(self):
        if self.state == STATE_PLAYING and self.mode == MODE_REPLAY:
            if not self._engine.is_running():
                self._elapsed_at_finish = self.current_elapsed()
                self.state = STATE_FINISHED
                self.stop_timer()
                await self._broadcast(self._state_payload())
                self._on_finish()
                return
        await self._broadcast(self._state_payload())

    # ── Helpers ───────────────────────────────────────────────────────────────

    def current_elapsed(self) -> float:
        """Returns elapsed seconds. Frozen during pause."""
        if self.state in (STATE_IDLE, STATE_LOADING, STATE_STOPPED):
            return 0.0
        if self.state == STATE_PAUSED:
            return self.elapsed_at_pause
        if self.state == STATE_FINISHED:
            return getattr(self, "_elapsed_at_finish", self.elapsed_at_pause)
        return self.elapsed_at_pause + (time.monotonic() - self.play_start_wall)

    def reset_session(self):
        self.db_path          = ""
        self.session_id       = ""
        self.session_name     = ""
        self.duration_seconds = 0.0
        self.stream_count     = 0
        self.elapsed_at_pause = 0.0
        self.play_start_wall  = 0.0
        self.start_unix       = 0.0


# ── Module-level DB readers (pure functions, no class state) ──────────────────

def _read_full_analysis(db_path: str) -> dict:
    """Read all data needed for event-locked analysis from a session .db file."""
    import sqlite3 as _sqlite3
    import json as _json

    out = {
        "valid": False, "reason": "", "events": [], "event_types": [],
        "hr": [], "gaze": [], "ecg": [], "session_start": 0, "session_end": 0,
    }
    try:
        conn = _sqlite3.connect(db_path)
        c    = conn.cursor()

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lsl_metadata'")
        if not c.fetchone():
            out["reason"] = "No lsl_metadata table — session format not supported"
            conn.close(); return out

        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lsl_unmapped_samples'")
        if not c.fetchone():
            out["reason"] = "No lsl_unmapped_samples table"
            conn.close(); return out

        out["valid"] = True

        # ── VirTra event streams ──────────────────────────────────────────────
        c.execute("""
            SELECT lsl_metadata_id, name, type
            FROM lsl_metadata
            WHERE channel_format = 'string'
               OR type IN ('VirTraEvents', 'Markers', 'Events')
            ORDER BY lsl_metadata_id ASC
        """)
        meta_ids = [r[0] for r in c.fetchall()]

        if meta_ids:
            ph = ",".join("?" * len(meta_ids))
            c.execute(f"""
                SELECT unix_timestamp_seconds, sample_json, lsl_metadata_id
                FROM lsl_unmapped_samples
                WHERE lsl_metadata_id IN ({ph})
                ORDER BY unix_timestamp_seconds ASC
            """, meta_ids)
            seen: dict = {}
            for ts, sj, mid in c.fetchall():
                try:
                    chans   = _json.loads(sj)
                    ev_type = str(chans[0]["value"]).strip() if chans else ""
                    desc    = str(chans[1]["value"]).strip() if len(chans) > 1 else ""
                    if ev_type:
                        out["events"].append({"ts": ts, "type": ev_type, "desc": desc, "meta_id": mid})
                        seen[ev_type] = seen.get(ev_type, 0) + 1
                except Exception:
                    pass
            out["event_types"] = [{"type": k, "count": v}
                                   for k, v in sorted(seen.items(), key=lambda x: -x[1])]

        # ── Heart Rate ────────────────────────────────────────────────────────
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='polar_h10_heart_rate'")
        if c.fetchone():
            c.execute("SELECT unix_timestamp_seconds, beats_per_minute FROM polar_h10_heart_rate ORDER BY unix_timestamp_seconds")
            out["hr"] = [{"ts": r[0], "bpm": r[1]} for r in c.fetchall()]
        if not out["hr"]:
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='polar_verity_sense_hr'")
            if c.fetchone():
                c.execute("SELECT unix_timestamp_seconds, heart_rate_bpm FROM polar_verity_sense_hr ORDER BY unix_timestamp_seconds")
                out["hr"] = [{"ts": r[0], "bpm": r[1]} for r in c.fetchall()]

        # ── Gaze + Pupil ──────────────────────────────────────────────────────
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='neon_gaze'")
        if c.fetchone():
            c.execute("""
                SELECT unix_timestamp_seconds,
                       pupil_diameter_left_millimeters,
                       pupil_diameter_right_millimeters
                FROM neon_gaze ORDER BY unix_timestamp_seconds
            """)
            for r in c.fetchall():
                pl, pr = r[1], r[2]
                if pl and pr and pl > 0 and pr > 0:
                    pupil = (pl + pr) / 2.0
                else:
                    pupil = pl if (pl and pl > 0) else (pr if (pr and pr > 0) else None)
                if pupil:
                    out["gaze"].append({"ts": r[0], "pupil": pupil})

        # ── ECG (every 5th sample, ~26 Hz) ───────────────────────────────────
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='polar_h10_ecg'")
        if c.fetchone():
            c.execute("SELECT unix_timestamp_seconds, microvolts FROM polar_h10_ecg ORDER BY unix_timestamp_seconds")
            rows = c.fetchall()
            out["ecg"] = [{"ts": r[0], "uv": r[1]} for i, r in enumerate(rows) if i % 5 == 0]

        # ── Session time range ────────────────────────────────────────────────
        all_ts = (
            [r["ts"] for r in out["gaze"]] or
            [r["ts"] for r in out["hr"]]   or
            [e["ts"] for e in out["events"]]
        )
        if all_ts:
            out["session_start"] = min(all_ts)
            out["session_end"]   = max(all_ts)

        conn.close()
    except Exception as e:
        out["valid"]  = False
        out["reason"] = str(e)
        print(f"[ReplayService] _read_full_analysis error: {e}")
    return out


def _read_analysis_data(db_path: str) -> dict:
    """Read analysis streams (VirTra, HR, gaze, ECG, speech) from session .db."""
    import sqlite3 as _sqlite3
    import json as _json

    result = {"virtra": [], "speech": [], "hr": [], "gaze": [], "ecg": []}
    try:
        conn = _sqlite3.connect(db_path)
        c    = conn.cursor()

        # ── VirTra events ─────────────────────────────────────────────────────
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lsl_metadata'")
        has_meta = c.fetchone() is not None

        if has_meta:
            c.execute("""
                SELECT u.unix_timestamp_seconds, u.sample_json
                FROM lsl_unmapped_samples u
                JOIN lsl_metadata m ON u.lsl_metadata_id = m.lsl_metadata_id
                WHERE m.type = 'VirTraEvents'
                ORDER BY u.unix_timestamp_seconds ASC
            """)
        else:
            c.execute("SELECT unix_timestamp_seconds, sample_json FROM lsl_unmapped_samples ORDER BY unix_timestamp_seconds ASC")

        for ts, sj in c.fetchall():
            try:
                chans   = _json.loads(sj)
                ev_type = str(chans[0]["value"]).strip() if len(chans) > 0 else ""
                desc    = str(chans[1]["value"]).strip() if len(chans) > 1 else ""
                tag     = str(chans[2]["value"]).strip() if len(chans) > 2 else ""
                if not has_meta:
                    try:
                        float(ev_type); continue
                    except (ValueError, TypeError):
                        pass
                if ev_type:
                    result["virtra"].append({"ts": ts, "type": ev_type, "description": desc, "tag": tag})
            except Exception:
                pass

        # ── Heart Rate ────────────────────────────────────────────────────────
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='polar_h10_heart_rate'")
        if c.fetchone():
            c.execute("SELECT unix_timestamp_seconds, beats_per_minute FROM polar_h10_heart_rate ORDER BY unix_timestamp_seconds ASC")
            result["hr"] = [{"ts": r[0], "bpm": r[1]} for r in c.fetchall()]
        if not result["hr"]:
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='polar_verity_sense_hr'")
            if c.fetchone():
                c.execute("SELECT unix_timestamp_seconds, heart_rate_bpm FROM polar_verity_sense_hr ORDER BY unix_timestamp_seconds ASC")
                result["hr"] = [{"ts": r[0], "bpm": r[1]} for r in c.fetchall()]

        # ── Gaze + Pupil ──────────────────────────────────────────────────────
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='neon_gaze'")
        if c.fetchone():
            c.execute("""
                SELECT unix_timestamp_seconds, scene_x_pixels, scene_y_pixels,
                       pupil_diameter_left_millimeters, pupil_diameter_right_millimeters
                FROM neon_gaze ORDER BY unix_timestamp_seconds ASC
            """)
            for r in c.fetchall():
                pl, pr = r[3], r[4]
                if pl and pr and pl > 0 and pr > 0:
                    pupil = (pl + pr) / 2
                else:
                    pupil = pl if (pl and pl > 0) else pr
                result["gaze"].append({"ts": r[0], "x": r[1], "y": r[2], "pupil": pupil})

        # ── ECG (every 5th sample) ────────────────────────────────────────────
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='polar_h10_ecg'")
        if c.fetchone():
            c.execute("SELECT unix_timestamp_seconds, microvolts FROM polar_h10_ecg ORDER BY unix_timestamp_seconds ASC")
            rows = c.fetchall()
            result["ecg"] = [{"ts": r[0], "uv": r[1]} for i, r in enumerate(rows) if i % 5 == 0]

        # ── Speech ────────────────────────────────────────────────────────────
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='neon_middleware_speech_transcription'")
        if c.fetchone():
            c.execute("""
                SELECT unix_timestamp_seconds, transcribed_text,
                       utterance_start_timestamp_unix_ms,
                       utterance_end_timestamp_unix_ms
                FROM neon_middleware_speech_transcription
                ORDER BY unix_timestamp_seconds ASC
            """)
            for ts, text, start_ms, end_ms in c.fetchall():
                if text and text.strip():
                    result["speech"].append(
                        {"ts": ts, "text": text.strip(), "start_ms": start_ms, "end_ms": end_ms})

        conn.close()
    except Exception as e:
        print(f"[ReplayService] _read_analysis_data error: {e}")
    return result


def _read_session_info(db_path: str) -> dict:
    """Read the session_info table written by LiveSessionWriter."""
    try:
        import sqlite3 as _sqlite3
        conn = _sqlite3.connect(db_path)
        c    = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='session_info'")
        if not c.fetchone():
            conn.close(); return {}
        c.execute(
            "SELECT participant_id, session_name, drill, recorded_at "
            "FROM session_info ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        conn.close()
        if not row:
            return {}
        return {
            "participant_id": row[0] or "",
            "session_name":   row[1] or "",
            "drill":          row[2] or "",
            "recorded_at":    row[3] or "",
        }
    except Exception as e:
        print(f"[ReplayService] _read_session_info error: {e}")
        return {}
