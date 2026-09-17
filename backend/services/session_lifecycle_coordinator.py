"""
session_lifecycle_coordinator.py — Session coordinator and high-level state machine.

Routes incoming WebSocket commands to VirTraScenarioOrchestrator.
Owns the high-level live session state: IDLE ↔ LISTENING ↔ LIVE.
"""

import asyncio
import time
from typing import Callable, Optional

from api.message_types import (
    MSG_SESSION_STATE, MSG_STREAMS_CATALOG, MSG_API_ERROR,
    MSG_SESSION_SAVE_RECORD, MSG_SESSION_RECORD_SAVED,
    STATE_IDLE, STATE_LISTENING,
    MODE_LIVE,
    LSL_TYPES_EXCLUDED,
)
from .virtra_scenario_orchestrator import VirTraScenarioOrchestrator
from .session_data_writer import write_session_record

try:
    import config as backend_config
    _SESSION_DATA_DIR = backend_config.SESSION_DATA_DIR
except (ImportError, AttributeError):
    import os
    _SESSION_DATA_DIR = os.path.abspath("../data/SessionData")


class SessionLifecycleCoordinator:
    """
    Coordinates session state lifecycle (IDLE, LISTENING), processes client
    system requests (system.get_state, system.ping, live.activate), and coordinates
    sample dispatch to the scenario orchestrator.
    """

    def __init__(
        self,
        inlet_manager,
        broadcast_fn:  Callable,
        send_fn:       Callable,
        loop:          asyncio.AbstractEventLoop,
    ):
        self._inlets    = inlet_manager
        self._broadcast = broadcast_fn
        self._send      = send_fn
        self._loop      = loop

        # Live session state
        self._state:           str   = STATE_IDLE
        self._live_start_wall: float = 0.0

        self._orchestrator = VirTraScenarioOrchestrator(
            inlets                = inlet_manager,
            broadcast_fn          = broadcast_fn,
            send_fn               = send_fn,
            get_state_payload_fn  = self._state_payload,
            error_fn              = self._error,
            on_live_activate_fn   = self._on_live_activate,
            on_live_deactivate_fn = self._on_live_deactivate,
            loop                  = loop,
        )

        print("[SessionLifecycleCoordinator] Initialized — live streaming session mode")

    # ── Message routing ───────────────────────────────────────────────────────

    async def handle_message(self, websocket, msg: dict):
        msg_type = msg.get("type", "")

        if msg_type == "live.activate":
            # Clear any stale metadata from a previous session before activating
            self._orchestrator.clear_last_session_metadata()
            self._orchestrator._analysis_client.clear_pending()
            await self._orchestrator.handle_live_activate(websocket)
        elif msg_type == "live.deactivate":
            await self._orchestrator.handle_live_deactivate(websocket)
        elif msg_type == MSG_SESSION_SAVE_RECORD:
            await self._handle_save_record(websocket, msg)
        elif msg_type == "streams.get_catalog":
            await self._send(websocket, self._catalog_payload())
        elif msg_type == "system.get_state":
            await self._send(websocket, self._state_payload())
            await self._send(websocket, self._catalog_payload())
        elif msg_type == "system.ping":
            await self._send(websocket, {
                "type":      "system.pong",
                "client_ts": msg.get("ts", 0),
                "server_ts": time.time(),
            })
        else:
            print(f"[SessionLifecycleCoordinator] Unknown message type: {msg_type!r} — ignoring")

    async def _handle_save_record(self, websocket, msg: dict):
        """
        Operator has confirmed participant metadata and clicked Save.
        Assembles session_record.json from cached scenario metadata + Analysis metrics
        + operator-supplied fields, writes to disk, broadcasts session.record_saved.
        """
        analysis_client = self._orchestrator._analysis_client
        pending_metrics  = analysis_client.get_pending_metrics()
        session_metadata = self._orchestrator.get_last_session_metadata() or {}

        # Merge operator-supplied participant fields into session_metadata
        operator_input = {
            "participant_id":   str(msg.get("participant_id",   "")).strip(),
            "participant_name": str(msg.get("participant_name", "")).strip(),
            "notes":            str(msg.get("notes",            "")).strip(),
        }

        # Run the file write in a thread so it never blocks the asyncio event loop
        saved_path = await self._loop.run_in_executor(
            None,
            write_session_record,
            session_metadata,
            pending_metrics,
            operator_input,
            _SESSION_DATA_DIR,
        )

        # Confirm to the requesting client and broadcast to all
        confirmation = {
            "type":       MSG_SESSION_RECORD_SAVED,
            "drill_key":  session_metadata.get("drill_key", ""),
            "saved_path": saved_path or "",
            "success":    saved_path is not None,
        }
        await self._send(websocket, confirmation)
        await self._broadcast(confirmation)

        # Clean up: clear pending state so the next session starts fresh
        analysis_client.clear_pending()
        self._orchestrator.clear_last_session_metadata()


    # ── Connection hook ───────────────────────────────────────────────────────

    async def on_client_connect(self, websocket):
        """Send current state + stream catalog immediately upon client connection."""
        await self._send(websocket, self._state_payload())
        await self._send(websocket, self._catalog_payload())

        # Replay active drill state for late-joining clients
        drill_state = self._orchestrator.get_active_drill_state()
        if drill_state:
            await self._send(websocket, drill_state)

    # ── Ingestion callbacks ───────────────────────────────────────────────────

    def on_sample(self, stream_name: str, stream_type: str, timestamp: float, data: list):
        self._orchestrator.on_sample(stream_name, stream_type, timestamp, data)

    def on_catalog_changed(self, catalog: list):
        self._orchestrator.on_catalog_changed(catalog)
        filtered = [s for s in catalog
                    if s.get("stream_type", s.get("type", "")) not in LSL_TYPES_EXCLUDED]
        self._loop.call_soon_threadsafe(
            self._loop.create_task,
            self._broadcast(self._catalog_payload(filtered))
        )

    # ── Timing helpers ────────────────────────────────────────────────────────

    def is_paused(self) -> bool:
        return False

    def get_elapsed_s(self, lsl_timestamp: float) -> Optional[float]:
        """Elapsed seconds since live mode was activated."""
        if self._state != STATE_LISTENING or not self._live_start_wall:
            return None
        return round(time.monotonic() - self._live_start_wall, 3)

    # ── Payload builders ──────────────────────────────────────────────────────

    def _state_payload(self) -> dict:
        elapsed = 0.0
        if self._state == STATE_LISTENING and self._live_start_wall:
            elapsed = round(time.monotonic() - self._live_start_wall, 2)
        return {
            "type":             MSG_SESSION_STATE,
            "mode":             MODE_LIVE,
            "state":            self._state,
            "elapsed_seconds":  elapsed,
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

    # ── Mode callbacks ────────────────────────────────────────────────────────

    async def _on_live_activate(self):
        self._state = STATE_LISTENING
        self._live_start_wall = time.monotonic()
        print(f"[SessionLifecycleCoordinator] Live mode ACTIVATED — listening for LSL streams")

    async def _on_live_deactivate(self):
        elapsed = round(time.monotonic() - self._live_start_wall, 1) if self._live_start_wall else 0.0
        self._state = STATE_IDLE
        self._live_start_wall = 0.0
        print(f"[SessionLifecycleCoordinator] Live mode DEACTIVATED — session ran for {elapsed}s")

