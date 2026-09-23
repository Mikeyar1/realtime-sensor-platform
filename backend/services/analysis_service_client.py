"""
analysis_service_client.py — Post-scenario metric computation and session record management.

Replaces the former Analysis HTTP microservice with direct calls to the
cognitive_attributes metric functions. No external process needed.

Responsibilities:
  1. Compute summary metrics from the trial list via the attribute's metrics function.
  2. Auto-save session to disk (session_record.json) AND to the SQLite database.
  3. Broadcast session.metrics_ready to all connected Frontend clients.
  4. Cache session_record + metrics for operator-driven confirm/discard flow.
     - db.confirm_session → updates participant name in DB
     - db.discard_session → deletes session from DB + JSON on disk
"""

import asyncio
import datetime
import json
import os
import traceback
from typing import Callable, Optional

from cognitive_attributes import ATTRIBUTE_REGISTRY

# Absolute path to the LiveSessions folder at the project root.
# This is 2 levels up from this file (services/ → backend/ → project root).
_PROJECT_ROOT      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LIVE_SESSIONS_DIR = os.path.join(_PROJECT_ROOT, "liveSessions")


def _compute_metrics_for_session(session_record: dict) -> Optional[dict]:
    """
    Compute summary metrics directly from the session record trial list.
    Uses the metrics function registered in ATTRIBUTE_REGISTRY for this task_type.
    Returns None if the task_type has no registered metrics function.
    """
    task_type = (session_record.get("task_type") or "").upper()
    drill_key = session_record.get("drill_key", "")

    # Look up by task_type first; fall back to drill_key (uppercase)
    entry = ATTRIBUTE_REGISTRY.get(task_type) or ATTRIBUTE_REGISTRY.get(drill_key.upper())
    if entry is None:
        print(f"[AnalysisServiceClient] No metrics function for task_type={task_type!r} — skipping")
        return None

    _, metrics_fn = entry
    try:
        trials  = session_record.get("trials", [])
        metrics = metrics_fn(trials, session_record)
        return metrics
    except Exception as e:
        print(f"[AnalysisServiceClient] ✗ Metrics computation error: {e}")
        traceback.print_exc()
        return None


def _save_session_json(session_record: dict, live_sessions_dir: str) -> Optional[str]:
    """
    Persist the session record to disk as a JSON file.

    File is written to:
        <live_sessions_dir>/<YYYY-MM-DD>/<session_id>_<scenario_name>.json

    Returns the full path to the saved file, or None on failure.
    """
    try:
        session_id    = session_record.get("session_id", "unknown")
        scenario_name = session_record.get("scenario_name", "session").replace(" ", "_")
        started_at    = session_record.get("started_at")

        # Build date-based subdirectory
        if started_at:
            date_str = datetime.datetime.fromtimestamp(started_at).strftime("%Y-%m-%d")
        else:
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")

        output_dir = os.path.join(live_sessions_dir, date_str)
        os.makedirs(output_dir, exist_ok=True)

        filename = f"{session_id}_{scenario_name}.json"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session_record, f, indent=2, default=str)

        print(f"[AnalysisServiceClient] Session saved -> {filepath}")
        return filepath

    except Exception as e:
        print(f"[AnalysisServiceClient] ✗ Failed to save session JSON: {e}")
        traceback.print_exc()
        return None


class AnalysisServiceClient:

    """
    Manages post-scenario metric computation, auto-save to DB and disk,
    and the operator confirm/discard flow.
    """

    def __init__(
        self,
        broadcast_fn: Callable,
        loop: asyncio.AbstractEventLoop,
        live_sessions_dir: str = _LIVE_SESSIONS_DIR,
    ):
        self._broadcast = broadcast_fn
        self._loop = loop
        self._live_sessions_dir = live_sessions_dir

        # Pending state — populated after auto-save, cleared after confirm or discard.
        # These allow the operator to correct the participant name or discard the session.
        self._pending_metrics:        dict | None = None
        self._pending_session_record: dict | None = None   # full tracker session_record
        self._pending_session_metadata: dict | None = None

    async def run_after_live_scenario(
        self,
        drill_key: str,
        session_record: dict,
        scenario_name: str = "",
        session_metadata: dict | None = None,
    ) -> None:
        """
        Post-scenario workflow (runs after scenario stops):
          1. Save session.json to disk (LiveSessions/).
          2. Compute summary metrics from the trial list.
          3. Auto-save to SQLite DB with detected participant name (or unknown_participant_N).
          4. Cache everything for the operator confirm/discard panel.
          5. Broadcast session.metrics_ready to all connected clients.

        Args:
            drill_key:        The cognitive attribute key ("pvt", "gng", …).
            session_record:   Full record built by the tracker (has "trials", "biosignals").
            scenario_name:    Display name of the completed scenario.
            session_metadata: Metadata from VirTraScenarioOrchestrator._last_session_metadata.
                              Contains participant_name, biosignals, active_streams, etc.
        """
        meta  = session_metadata or {}
        n_trials = len(session_record.get("trials", []))
        print(f"[AnalysisServiceClient] Post-scenario: drill={drill_key!r}"
              f"  scenario={scenario_name!r}  trials={n_trials}")

        # 1. Save session JSON to disk (LiveSessions/ — raw trial log)
        saved_path = await self._loop.run_in_executor(
            None, _save_session_json, session_record, self._live_sessions_dir
        )

        if n_trials == 0:
            print("[AnalysisServiceClient] No trials recorded — skipping metric computation")
            await self._broadcast({
                "type":      "session.metrics_ready",
                "drill_key": drill_key,
                "metrics":   None,
                "error":     "no_trials",
            })
            return

        # 2. Compute metrics
        metrics = await self._loop.run_in_executor(
            None, _compute_metrics_for_session, session_record
        )

        if metrics is None:
            print("[AnalysisServiceClient] No metrics produced for this cognitive attribute")
            await self._broadcast({
                "type":       "session.metrics_ready",
                "drill_key":  drill_key,
                "metrics":    None,
                "error":      "metrics_unavailable",
                "saved_path": saved_path,
            })
            return

        # 3. Auto-save to DB
        # Build the merged metadata dict that database.insert_session() expects.
        # Merge session_record fields + orchestrator metadata for a complete picture.
        db_metadata = {
            "session_id":    session_record.get("session_id", meta.get("session_id", "")),
            "drill_key":     drill_key,
            "task_type":     session_record.get("task_type", meta.get("task_type", "")),
            "scenario_name": scenario_name or session_record.get("scenario_name", ""),
            "started_at":    session_record.get("started_at", meta.get("started_at")),
            "stopped_at":    session_record.get("stopped_at", meta.get("stopped_at")),
            "duration_s":    session_record.get("duration_s", meta.get("duration_s")),
            "active_streams": meta.get("active_streams", []),
            "notes":          "",
            # Biosignals from orchestrator metadata (threaded in via _last_session_metadata)
            "biosignals":    meta.get("biosignals", session_record.get("biosignals", {})),
            # Trials for per-trial DB rows
            "trials":        session_record.get("trials", []),
        }

        participant_name = (
            meta.get("participant_name", "")
            or session_record.get("participant_name", "")
        ).strip()

        db_session_id = await self._loop.run_in_executor(
            None,
            _db_insert_session,
            participant_name,
            db_metadata,
            metrics,
            saved_path or "",
        )

        session_uuid = db_metadata["session_id"]

        # 4. Cache for operator confirm/discard
        self._pending_metrics          = metrics
        self._pending_session_record   = session_record
        self._pending_session_metadata = {
            "drill_key":        drill_key,
            "scenario_name":    scenario_name,
            "session_uuid":     session_uuid,
            "participant_name": participant_name or _get_auto_name(session_uuid),
        }

        # 5. Broadcast metrics_ready — frontend shows Confirm/Discard panel
        await self._broadcast({
            "type":             "session.metrics_ready",
            "drill_key":        drill_key,
            "scenario_name":    scenario_name,
            "metrics":          metrics,
            "saved_path":       saved_path,
            "session_uuid":     session_uuid,
            "participant_name": self._pending_session_metadata["participant_name"],
            "auto_saved":       db_session_id is not None,
        })
        print(f"[AnalysisServiceClient] ✓ Metrics ready + auto-saved: "
              f"drill={drill_key!r}  participant={participant_name!r}  db_id={db_session_id}")

    # ── Pending result accessors ──────────────────────────────────────────────

    def get_pending_metrics(self) -> dict | None:
        """Return cached metrics from the last Analysis call, or None if not available."""
        return self._pending_metrics

    def get_pending_session_metadata(self) -> dict | None:
        """Return cached session metadata from the last completed scenario, or None."""
        return self._pending_session_metadata

    def clear_pending(self) -> None:
        """Clear cached metrics and metadata. Called after operator confirms or discards."""
        self._pending_metrics          = None
        self._pending_session_record   = None
        self._pending_session_metadata = None
        print("[AnalysisServiceClient] Pending metrics cleared")


# ── DB helper (called via run_in_executor so it's off the event loop) ─────────

def _db_insert_session(
    participant_name: str,
    db_metadata: dict,
    metrics: dict,
    record_path: str,
) -> Optional[int]:
    """Thin wrapper — import database lazily to avoid circular import at module load."""
    try:
        from services.database import insert_session  # type: ignore
        return insert_session(
            participant_name  = participant_name,
            session_metadata  = db_metadata,
            metrics           = metrics,
            record_path       = record_path,
        )
    except Exception as e:
        print(f"[AnalysisServiceClient] ✗ DB insert failed: {e}")
        traceback.print_exc()
        return None


def _get_auto_name(session_uuid: str) -> str:
    """Return the auto-assigned participant name for a session_uuid from the DB."""
    try:
        from services.database import _connect  # type: ignore
        with _connect() as conn:
            row = conn.execute(
                """SELECT p.participant_name FROM participants p
                   JOIN sessions s ON s.participant_id = p.id
                   WHERE s.session_uuid = ?""",
                (session_uuid,),
            ).fetchone()
            return row["participant_name"] if row else ""
    except Exception:
        return ""
