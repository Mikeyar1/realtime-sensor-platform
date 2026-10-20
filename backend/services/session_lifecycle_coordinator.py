"""
session_lifecycle_coordinator.py — Session coordinator and high-level state machine.

Routes incoming WebSocket commands to VirTraScenarioOrchestrator.
Owns the high-level live session state: IDLE ↔ LISTENING ↔ LIVE.

Legacy session persistence flow (round assessments use round.save or
run.save_remaining, with an end-of-run compatibility bridge):
  1. VirTra stop event or session.stop → analysis_service_client auto-saves to DB.
  2. Frontend receives session.metrics_ready with session_uuid + participant_name.
  3. Frontend shows Confirm / Discard panel with a participant name dropdown.
  4. Operator confirms (optionally correcting the name) → db.confirm_session
     → updates participant name in DB if changed, broadcasts db.session_saved.
  5. Operator discards → db.discard_session
     → deletes the DB row, broadcasts db.session_discarded.
  6. Operator clicks New Session → resets to IDLE.
"""

import asyncio
import time
import uuid
from typing import Callable, Optional

from api.message_types import (
    MSG_SESSION_STATE, MSG_STREAMS_CATALOG, MSG_API_ERROR,
    MSG_SESSION_RECORD_SAVED, MSG_SESSION_SAVE_RECORD,
    STATE_IDLE, STATE_LISTENING,
    MODE_LIVE,
    LSL_TYPES_EXCLUDED,
    MSG_DB_GET_PARTICIPANTS, MSG_DB_CONFIRM_SESSION,
    MSG_DB_DISCARD_SESSION, MSG_DB_UPDATE_PARTICIPANT,
    MSG_DB_PARTICIPANTS_LIST, MSG_DB_SESSION_SAVED,
    MSG_DB_SESSION_DISCARDED, MSG_DB_PARTICIPANT_UPDATED,
    MSG_PERF_QUERY, MSG_PERF_RESULT, MSG_PERF_DELETE_SESSION, MSG_PERF_DELETE_ROUND,
    MSG_PERF_DELETE_PARTICIPANT, MSG_PERF_DELETE_RESULT,
)
from .virtra_scenario_orchestrator import VirTraScenarioOrchestrator
from .session_data_writer import write_session_record
from . import round_repository

try:
    import config as backend_config
    _SESSION_DATA_DIR = backend_config.SESSION_DATA_DIR
except (ImportError, AttributeError):
    import os
    _SESSION_DATA_DIR = os.path.abspath("../data/SessionData")


def _db_get_participants() -> list[str]:
    """Thread-safe DB call — run via executor."""
    try:
        from services.database import get_all_participant_names  # type: ignore
        return get_all_participant_names()
    except Exception as e:
        print(f"[SessionLifecycleCoordinator] DB get_participants error: {e}")
        return []


def _db_confirm(session_uuid: str, new_name: str) -> tuple[bool, str]:
    """Thread-safe DB call — update participant name on a session."""
    try:
        from services.database import update_participant_name  # type: ignore
        return update_participant_name(session_uuid, new_name)
    except Exception as e:
        print(f"[SessionLifecycleCoordinator] DB confirm error: {e}")
        return False, str(e)


def _db_confirm_fitts(session_uuid: str, session_name: str,
                      assignments: list[dict]) -> tuple[bool, str]:
    try:
        run = round_repository.get_run(session_uuid) if round_repository.available() else None
        if run:
            by_number = {(r["round_number"], r["attempt"]): r["id"] for r in run["rounds"]}
            explicit = {by_number[(int(item["round_number"]), int(item.get("attempt") or 1))]:
                        str(item["participant_name"]).strip() for item in assignments}
            round_repository.save_remaining(session_uuid, session_name,
                                            str(uuid.uuid4()), explicit)
            return True, "Rounds saved"
        from services.database import confirm_fitts_round_participants
        return confirm_fitts_round_participants(session_uuid, session_name, assignments)
    except Exception as e:
        print(f"[SessionLifecycleCoordinator] Fitts round confirm error: {e}")
        return False, str(e)


def _db_discard(session_uuid: str) -> bool:
    """Thread-safe DB call — delete session + JSON from disk."""
    try:
        if round_repository.available() and round_repository.get_run(session_uuid):
            return round_repository.discard_unsaved_run(session_uuid)
        from services.database import delete_session  # type: ignore
        return delete_session(session_uuid)
    except Exception as e:
        print(f"[SessionLifecycleCoordinator] DB discard error: {e}")
        return False


def _db_get_sessions(participant_name: str, drill_key: str | None, limit: int | None) -> list:
    """Thread-safe DB call — query session history for a participant."""
    try:
        from services.database import get_sessions_for_participant  # type: ignore
        return get_sessions_for_participant(
            participant_name, drill_key=drill_key or None, limit=limit
        )
    except Exception as e:
        print(f"[SessionLifecycleCoordinator] DB get_sessions error: {e}")
        return []


def _db_get_session_detail(session_uuid: str, participant_name: str,
                           round_number: int | None = None,
                           attempt: int = 1) -> dict | None:
    from services.database import get_session_detail  # type: ignore
    return get_session_detail(session_uuid, participant_name, round_number, attempt)


def _db_delete_history_session(session_uuid: str, participant_name: str) -> bool:
    from services.database import delete_history_session  # type: ignore
    return delete_history_session(session_uuid, participant_name)


def _db_delete_history_round(round_id: int, participant_name: str) -> bool:
    from services.database import delete_history_round  # type: ignore
    return delete_history_round(round_id, participant_name)


def _db_delete_participant_history(participant_name: str) -> int:
    from services.database import delete_participant_history  # type: ignore
    return delete_participant_history(participant_name)


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

        elif msg_type == "session.stop":
            if self._orchestrator.stop_active_drill():
                await self._orchestrator.handle_live_deactivate(websocket)
            else:
                await self._send(websocket, self._error(
                    "no_active_drill", "There is no active drill to stop", "session.stop"
                ))

        elif msg_type in {
            "round.save", "round.reassign", "run.save_remaining", "round.assignment.update",
            "run.snapshot.get", "history.rounds.query", "history.round.get",
            "participants.search", "history.round.delete", "history.participant.delete",
        }:
            await self._handle_assessment_command(websocket, msg)

        # Legacy save_record — kept for backwards compat; forwards to confirm flow
        elif msg_type == MSG_SESSION_SAVE_RECORD:
            await self._handle_legacy_save_record(websocket, msg)

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

        # ── Database message handlers ─────────────────────────────────────────

        elif msg_type == MSG_DB_GET_PARTICIPANTS:
            await self._handle_db_get_participants(websocket)

        elif msg_type == MSG_DB_CONFIRM_SESSION:
            await self._handle_db_confirm_session(websocket, msg)

        elif msg_type == MSG_DB_DISCARD_SESSION:
            await self._handle_db_discard_session(websocket, msg)

        elif msg_type == MSG_DB_UPDATE_PARTICIPANT:
            await self._handle_db_update_participant(websocket, msg)

        elif msg_type == MSG_PERF_QUERY:
            await self._handle_perf_query(websocket, msg)

        elif msg_type == "perf.session_detail":
            session_uuid = str(msg.get("session_uuid", "")).strip()
            participant_name = str(msg.get("participant_name", "")).strip()
            round_number = msg.get("round_number")
            round_number = int(round_number) if round_number is not None else None
            attempt = int(msg.get("round_attempt") or 1)
            detail = await self._loop.run_in_executor(
                None, _db_get_session_detail, session_uuid, participant_name,
                round_number, attempt)
            await self._send(websocket, {
                "type": "perf.session_detail_result",
                "session_uuid": session_uuid,
                "round_number": round_number,
                "round_attempt": attempt if round_number is not None else None,
                "session": detail,
            })

        elif msg_type in (MSG_PERF_DELETE_SESSION, MSG_PERF_DELETE_ROUND,
                          MSG_PERF_DELETE_PARTICIPANT):
            await self._handle_perf_delete(websocket, msg)

        else:
            print(f"[SessionLifecycleCoordinator] Unknown message type: {msg_type!r} — ignoring")

    # ── Database handlers ────────────────────────────────────────────────────

    async def _handle_assessment_command(self, websocket, msg: dict):
        command = msg["type"]
        request_id = str(msg.get("request_id") or uuid.uuid4())
        try:
            if command == "round.save":
                result = await self._loop.run_in_executor(
                    None, round_repository.save_round, str(msg.get("round_id", "")),
                    str(msg.get("participant_name", "")), request_id,
                    msg.get("expected_revision"))
            elif command == "round.reassign":
                result = await self._loop.run_in_executor(
                    None, round_repository.reassign_round, str(msg.get("round_id", "")),
                    str(msg.get("participant_name", "")), request_id,
                    msg.get("expected_revision"))
            elif command == "run.save_remaining":
                result = await self._loop.run_in_executor(
                    None, round_repository.save_remaining, str(msg.get("run_id", "")),
                    str(msg.get("participant_name", "")), request_id,
                    msg.get("explicit_names") or {})
            elif command == "round.assignment.update":
                result = await self._loop.run_in_executor(
                    None, round_repository.set_draft_participant,
                    str(msg.get("round_id", "")), str(msg.get("participant_name", "")),
                    msg.get("expected_revision"))
            elif command == "run.snapshot.get":
                result = await self._loop.run_in_executor(
                    None, round_repository.get_run, str(msg.get("run_id", "")))
            elif command == "history.rounds.query":
                result = await self._loop.run_in_executor(
                    None, lambda: round_repository.list_history(
                        str(msg.get("participant_name", "")),
                        scenario_name=str(msg.get("scenario_name", "")),
                        preset_label=str(msg.get("preset_label", "")),
                        limit=int(msg.get("limit") or 100)))
            elif command == "history.round.get":
                result = await self._loop.run_in_executor(
                    None, round_repository.get_saved_round_for_participant,
                    str(msg.get("round_id", "")), str(msg.get("participant_name", "")))
            elif command == "participants.search":
                result = await self._loop.run_in_executor(None, round_repository.list_participants)
            elif command == "history.round.delete":
                result = await self._loop.run_in_executor(
                    None, round_repository.delete_round, str(msg.get("round_id", "")),
                    str(msg.get("participant_name", "")))
            else:
                result = await self._loop.run_in_executor(
                    None, round_repository.delete_participant_history,
                    str(msg.get("participant_name", "")))
            await self._send(websocket, {
                "type": "assessment.command_result", "command": command,
                "request_id": request_id, "round_id": msg.get("round_id"),
                "run_id": msg.get("run_id"), "success": True, "result": result,
            })
        except Exception as exc:
            print(f"[SessionLifecycleCoordinator] Assessment command {command!r} failed: {exc}")
            await self._send(websocket, {
                "type": "assessment.command_result", "command": command,
                "request_id": request_id, "round_id": msg.get("round_id"),
                "run_id": msg.get("run_id"), "success": False, "error": str(exc),
            })

    async def _handle_db_get_participants(self, websocket):
        """Return all known participant names for the frontend dropdown."""
        names = await self._loop.run_in_executor(None, _db_get_participants)
        await self._send(websocket, {
            "type":  MSG_DB_PARTICIPANTS_LIST,
            "names": names,
        })

    async def _handle_db_confirm_session(self, websocket, msg: dict):
        """
        Operator confirmed the session (optionally correcting the participant name).
        Updates participant name in DB if changed; clears pending state.
        """
        session_uuid     = str(msg.get("session_uuid", "")).strip()
        participant_name = str(msg.get("participant_name", "")).strip()
        notes            = str(msg.get("notes", "")).strip()

        if msg.get("round_participants") is not None:
            success, detail = await self._loop.run_in_executor(
                None, _db_confirm_fitts, session_uuid, participant_name,
                msg["round_participants"])
        else:
            success, detail = await self._loop.run_in_executor(
                None, _db_confirm, session_uuid, participant_name)

        response = {
            "type":             MSG_DB_SESSION_SAVED,
            "session_uuid":     session_uuid,
            "participant_name": participant_name,
            "success":          success,
            "detail":           detail,
        }
        await self._send(websocket, response)
        await self._broadcast(response)

        if success:
            self._orchestrator._analysis_client.clear_pending()
            self._orchestrator.clear_last_session_metadata()
            print(f"[SessionLifecycleCoordinator] Session confirmed: "
                  f"uuid={session_uuid!r} participant={participant_name!r}")

    async def _handle_db_discard_session(self, websocket, msg: dict):
        """
        Operator discarded the session.
        Deletes session from DB + JSON file from disk; clears pending state.
        """
        session_uuid = str(msg.get("session_uuid", "")).strip()

        deleted = await self._loop.run_in_executor(None, _db_discard, session_uuid)

        response = {
            "type":         MSG_DB_SESSION_DISCARDED,
            "session_uuid": session_uuid,
            "success":      deleted,
        }
        await self._send(websocket, response)
        await self._broadcast(response)

        self._orchestrator._analysis_client.clear_pending()
        self._orchestrator.clear_last_session_metadata()
        print(f"[SessionLifecycleCoordinator] Session discarded: uuid={session_uuid!r}")

    async def _handle_db_update_participant(self, websocket, msg: dict):
        """Rename a participant linked to a specific session_uuid."""
        session_uuid = str(msg.get("session_uuid", "")).strip()
        new_name     = str(msg.get("new_name", "")).strip()

        success, detail = await self._loop.run_in_executor(
            None, _db_confirm, session_uuid, new_name
        )

        await self._send(websocket, {
            "type":         MSG_DB_PARTICIPANT_UPDATED,
            "session_uuid": session_uuid,
            "new_name":     new_name,
            "success":      success,
            "detail":       detail,
        })

    async def _handle_perf_query(self, websocket, msg: dict):
        """
        Return session history for a participant.
        Request:  {participant_name, drill_key?, limit?}
        Response: {type: perf.result, participant_name, drill_key, sessions: [...]}
        Each session dict has: id, session_uuid, drill_key, task_type, scenario_name,
        date, started_at, stopped_at, duration_s, metrics, biosignals, notes, saved_at.
        """
        participant_name = str(msg.get("participant_name", "")).strip()
        drill_key        = str(msg.get("drill_key", "")).strip() or None
        limit_raw        = msg.get("limit")
        limit            = int(limit_raw) if limit_raw else None

        sessions = await self._loop.run_in_executor(
            None, _db_get_sessions, participant_name, drill_key, limit
        )
        await self._send(websocket, {
            "type":             MSG_PERF_RESULT,
            "request_id":       msg.get("request_id"),
            "participant_name": participant_name,
            "drill_key":        drill_key or "",
            "sessions":         sessions,
        })

    async def _handle_perf_delete(self, websocket, msg: dict):
        """Delete confirmed history rows and return a scoped result to the caller."""
        operation = msg["type"]
        participant_name = str(msg.get("participant_name", "")).strip()
        session_uuid = str(msg.get("session_uuid", "")).strip()
        round_id = msg.get("round_id")
        deleted_count = 0
        detail = ""
        try:
            if not participant_name:
                detail = "A participant name is required."
            elif operation == MSG_PERF_DELETE_SESSION:
                if not session_uuid:
                    detail = "A session ID is required."
                else:
                    deleted_count = int(await self._loop.run_in_executor(
                        None, _db_delete_history_session, session_uuid, participant_name
                    ))
            elif operation == MSG_PERF_DELETE_ROUND:
                try:
                    parsed_round_id = int(round_id)
                except (TypeError, ValueError):
                    detail = "A round ID is required."
                else:
                    deleted_count = int(await self._loop.run_in_executor(
                        None, _db_delete_history_round, parsed_round_id, participant_name
                    ))
            else:
                deleted_count = await self._loop.run_in_executor(
                    None, _db_delete_participant_history, participant_name
                )
            if not detail and deleted_count == 0:
                detail = "No matching saved results were found."
        except Exception as exc:
            print(f"[SessionLifecycleCoordinator] History deletion error: {exc}")
            detail = str(exc)

        await self._send(websocket, {
            "type": MSG_PERF_DELETE_RESULT,
            "operation": operation,
            "participant_name": participant_name,
            "session_uuid": session_uuid,
            "round_id": round_id,
            "success": deleted_count > 0,
            "deleted_count": deleted_count,
            "detail": detail,
        })

    # ── Legacy save_record (backwards-compat shim) ────────────────────────────

    async def _handle_legacy_save_record(self, websocket, msg: dict):
        """
        Backwards-compatible handler for the old session.save_record message.
        Routes to the confirm flow using the participant_name from the message.
        The JSON write to SessionData/ is still triggered for compatibility.
        """
        analysis_client  = self._orchestrator._analysis_client
        pending_metrics  = analysis_client.get_pending_metrics()
        session_metadata = self._orchestrator.get_last_session_metadata() or {}

        operator_input = {
            "participant_id":   str(msg.get("participant_id", "")).strip(),
            "participant_name": str(msg.get("participant_name", "")).strip(),
            "notes":            str(msg.get("notes", "")).strip(),
        }

        # Write the final SessionData/ record (legacy path)
        saved_path = await self._loop.run_in_executor(
            None,
            write_session_record,
            session_metadata,
            pending_metrics,
            operator_input,
            _SESSION_DATA_DIR,
        )

        confirmation = {
            "type":       MSG_SESSION_RECORD_SAVED,
            "drill_key":  session_metadata.get("drill_key", ""),
            "saved_path": saved_path or "",
            "success":    saved_path is not None,
        }
        await self._send(websocket, confirmation)
        await self._broadcast(confirmation)

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
