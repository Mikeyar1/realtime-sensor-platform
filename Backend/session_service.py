"""
session_service.py — Session coordinator.

Routes incoming WebSocket messages to ReplayService or LiveService.
Builds shared state/catalog payloads and wires the two sub-services together.

State machine:
  idle → loading → playing → paused → stopped → idle
                           → finished           → idle
"""

import asyncio
import os
import sys
import time
from typing import Callable, Optional

HERE    = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.join(HERE, "api")
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from replay_engine import ReplayEngine
from inlet_manager import InletManager

sys.path.insert(0, HERE)
from services.replay_service import ReplayService
from services.live_service   import LiveService

from message_types import (
    MSG_SESSION_STATE, MSG_STREAMS_CATALOG, MSG_API_ERROR,
    STATE_IDLE, STATE_LISTENING,
    MODE_REPLAY, MODE_LIVE,
    ERR_SEEK_NOT_IMPLEMENTED,
    LSL_TYPES_EXCLUDED,
)


class SessionService:
    """Thin coordinator — dispatches WS messages to ReplayService or LiveService."""

    def __init__(
        self,
        replay_engine: ReplayEngine,
        inlet_manager: InletManager,
        broadcast_fn:  Callable,
        send_fn:       Callable,
        loop:          asyncio.AbstractEventLoop,
        db_scan_dir:   str = "",
    ):
        self._inlets    = inlet_manager
        self._broadcast = broadcast_fn
        self._send      = send_fn
        self._loop      = loop

        # ReplayService
        self._replay = ReplayService(
            engine               = replay_engine,
            broadcast_fn         = broadcast_fn,
            send_fn              = send_fn,
            get_state_payload_fn = self._state_payload,
            error_fn             = self._error,
            on_finish_fn         = self._on_replay_finish,
            loop                 = loop,
        )

        # LiveService
        self._live = LiveService(
            inlets                = inlet_manager,
            broadcast_fn          = broadcast_fn,
            send_fn               = send_fn,
            get_state_payload_fn  = self._state_payload,
            error_fn              = self._error,
            on_live_activate_fn   = self._on_live_activate,
            on_live_deactivate_fn = self._on_live_deactivate,
            loop                  = loop,
        )

        print("[SessionService] Initialised")

    # Message routing

    async def handle_message(self, websocket, msg: dict):
        t = msg.get("type", "")

        if   t == "session.list":
            await self._replay.handle_session_list(websocket)
        elif t == "session.load":
            await self._replay.handle_session_load(websocket, msg.get("path", "").strip())
        elif t == "session.unload":
            await self._replay.handle_session_unload(websocket)
        elif t == "session.get_csv":
            await self._replay.handle_get_csv(websocket)
        elif t == "analysis.load":
            await self._replay.handle_analysis_load(websocket, msg.get("db_path", "").strip())
        elif t == "session.get_info":
            await self._replay.handle_session_get_info(websocket, msg.get("db_path", "").strip())
        elif t == "playback.play":
            await self._replay.handle_play(websocket)
        elif t == "playback.pause":
            await self._replay.handle_pause(websocket)
        elif t == "playback.stop":
            await self._replay.handle_stop(websocket)
        elif t == "playback.seek":
            await self._send(websocket, self._error(
                ERR_SEEK_NOT_IMPLEMENTED,
                "Seek is not yet supported. Total Recall does not expose a seek API.",
                context="playback.seek",
            ))
        elif t == "live.activate":
            await self._live.handle_live_activate(websocket)
        elif t == "live.deactivate":
            await self._live.handle_live_deactivate(websocket)
        elif t == "streams.get_catalog":
            await self._send(websocket, self._catalog_payload())
        elif t == "system.get_state":
            await self._send(websocket, self._state_payload())
            await self._send(websocket, self._catalog_payload())
        elif t == "system.ping":
            await self._send(websocket, {
                "type":      "system.pong",
                "client_ts": msg.get("ts", 0),
                "server_ts": time.time(),
            })
        else:
            print(f"[SessionService] Unknown message type: {t!r}")

    # Connection hook

    async def on_client_connect(self, websocket):
        await self._send(websocket, self._state_payload())
        await self._send(websocket, self._catalog_payload())
        if self._replay.mode == MODE_REPLAY:
            await self._replay.handle_session_list(websocket)

    # InletManager callbacks

    def on_sample(self, stream_name: str, timestamp: float, data: list):
        self._live.on_sample(stream_name, timestamp, data)

    def on_catalog_changed(self, catalog: list):
        self._live.on_catalog_changed(catalog)
        filtered = [s for s in catalog
                    if s.get("stream_type", s.get("type", "")) not in LSL_TYPES_EXCLUDED]
        self._loop.call_soon_threadsafe(
            self._loop.create_task,
            self._broadcast(self._catalog_payload(filtered))
        )

    # WsBridge helpers

    def is_paused(self) -> bool:
        return self._replay.state == "paused"

    def get_elapsed_s(self, lsl_timestamp: float) -> float | None:
        if not self._replay.play_start_unix:
            return None
        raw = lsl_timestamp - self._replay.play_start_unix - self._replay.total_pause_s
        return round(max(0.0, raw), 3)

    # Payload builders

    def _state_payload(self) -> dict:
        r = self._replay
        return {
            "type":             MSG_SESSION_STATE,
            "mode":             r.mode,
            "state":            r.state,
            "elapsed_seconds":  round(r.current_elapsed(), 2),
            "duration_seconds": r.duration_seconds,
            "session_id":       r.session_id,
            "session_name":     r.session_name,
            "db_path":          r.db_path,
            "stream_count":     r.stream_count,
            "start_unix":       r.start_unix,
        }

    def _catalog_payload(self, catalog: list = None) -> dict:
        if catalog is None:
            catalog = self._inlets.get_catalog()
            catalog = [s for s in catalog
                       if s.get("stream_type", s.get("type", "")) not in LSL_TYPES_EXCLUDED]
        return {
            "type":    MSG_STREAMS_CATALOG,
            "streams": catalog,
        }

    @staticmethod
    def _error(code: str, message: str, context: str = "", request_id: str = "") -> dict:
        payload = {"type": MSG_API_ERROR, "code": code, "message": message}
        if context:    payload["context"]    = context
        if request_id: payload["request_id"] = request_id
        return payload

    # Mode callbacks

    async def _on_live_activate(self):
        self._replay.stop_timer()
        self._replay.reset_session()
        self._replay.mode             = MODE_LIVE
        self._replay.state            = STATE_LISTENING
        self._replay.play_start_wall  = time.monotonic()
        self._replay.play_start_unix  = time.time()
        self._replay.elapsed_at_pause = 0.0
        self._replay.start_timer()

    async def _on_live_deactivate(self):
        self._replay.stop_timer()
        self._replay.mode  = MODE_REPLAY
        self._replay.state = STATE_IDLE
        self._replay.reset_session()

    def _on_replay_finish(self):
        pass
