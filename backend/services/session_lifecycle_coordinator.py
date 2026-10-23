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
import sqlite3
import threading
import time
import uuid
from collections import deque
from typing import Callable, Optional

from api.message_types import (
    MSG_SESSION_STATE, MSG_STREAMS_CATALOG, MSG_API_ERROR,
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
from . import round_repository
from audit.attribution import EventAttributor, _custom_round
from event_decoders.source_event import decode_source_event
from scenarios.registry import ScenarioRegistry


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
        event_store = None,
    ):
        self._inlets    = inlet_manager
        self._broadcast = broadcast_fn
        self._send      = send_fn
        self._loop      = loop
        self._event_store = event_store
        self._ingest_lock = threading.RLock()
        self._recent_log_sessions: deque[tuple[str, float, float]] = deque(maxlen=8)
        self._log_start_by_run: dict[str, float] = {}

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
        self._event_attributor = (EventAttributor(
            event_store, ScenarioRegistry(self._orchestrator._stream_config))
            if event_store is not None else None)

        print("[SessionLifecycleCoordinator] Initialized — live streaming session mode")

    # ── Message routing ───────────────────────────────────────────────────────

    async def handle_message(self, websocket, msg: dict):
        msg_type = msg.get("type", "")

        if msg_type == "live.activate":
            # Clear stale scenario metadata before activating.
            self._orchestrator.clear_last_session_metadata()
            await self._orchestrator.handle_live_activate(websocket)

        elif msg_type == "live.deactivate":
            await self._orchestrator.handle_live_deactivate(websocket)

        elif msg_type == "session.stop":
            with self._ingest_lock:
                router = self._orchestrator
                run_id = router._custom_session_id or router._session_id
                source = "custom" if router._custom_session_id else "native"
                started_at = (router._custom_session_started_at if source == "custom"
                              else router._session_started_at)
                stopped = router.stop_active_drill()
                if stopped and run_id and self._event_store:
                    ended_at = (router.get_last_session_metadata() or {}).get("stopped_at")
                    if ended_at is not None:
                        self._finish_log(run_id, source, started_at or ended_at, ended_at)
            if stopped:
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

        elif msg_type == "streams.get_catalog":
            await self._send(websocket, self._catalog_payload())

        elif msg_type in {"audit.capture.get", "audit.capture.runs", "audit.run.get"}:
            if self._event_store is None:
                await self._send(websocket, self._error("audit_unavailable", "Audit store is unavailable"))
            else:
                try:
                    limit = max(1, min(int(msg.get("limit", 100)), 1000))
                    if msg_type == "audit.capture.get":
                        capture_id = str(msg.get("capture_id") or self._event_store.capture_id)
                        rows = await self._loop.run_in_executor(
                            None, lambda: self._event_store.get_capture_events(
                                capture_id, limit=limit,
                                after_sequence=int(msg.get("after_sequence", 0))))
                    elif msg_type == "audit.capture.runs":
                        capture_id = str(msg.get("capture_id") or self._event_store.capture_id)
                        rows = await self._loop.run_in_executor(
                            None, lambda: self._event_store.get_capture_runs(capture_id))
                    else:
                        rows = await self._loop.run_in_executor(
                            None, lambda: self._event_store.get_run_events(
                                str(msg.get("run_id") or ""), limit=limit))
                    await self._send(websocket, {"type": "audit.events", "events": rows})
                except (ValueError, TypeError, sqlite3.Error) as exc:
                    await self._send(websocket, self._error("audit_query_invalid", str(exc)))

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
        Updates participant name in DB if changed.
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
            self._orchestrator.clear_last_session_metadata()
            print(f"[SessionLifecycleCoordinator] Session confirmed: "
                  f"uuid={session_uuid!r} participant={participant_name!r}")

    async def _handle_db_discard_session(self, websocket, msg: dict):
        """
        Operator discarded the session.
        Deletes the saved session from the database.
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

    # ── Connection hook ───────────────────────────────────────────────────────

    async def on_client_connect(self, websocket):
        """Send current state + stream catalog immediately upon client connection."""
        await self._send(websocket, self._state_payload())
        await self._send(websocket, self._catalog_payload())
        from metrics.cards import catalog_payload
        await self._send(websocket, catalog_payload())

        # Replay active drill state for late-joining clients
        drill_state = self._orchestrator.get_active_drill_state()
        if drill_state:
            await self._send(websocket, drill_state)

    # ── Ingestion callbacks ───────────────────────────────────────────────────

    def on_sample(self, stream_name: str, stream_type: str, timestamp: float, data: list,
                  *, outlet_uid: str = "", source_id: str = ""):
        source = self._orchestrator._stream_config.stream_source(stream_name, stream_type)
        if self._event_store is None:
            return self._orchestrator.on_sample(stream_name, stream_type, timestamp, data,
                                                outlet_uid=outlet_uid, source_id=source_id)
        # All inlet workers share one order for capture and lifecycle routing.
        with self._ingest_lock:
            if source in {"custom", "native"}:
                return self._audit_and_route(stream_name, stream_type, timestamp, data,
                                             source, outlet_uid, source_id)
            spec = self._orchestrator._physiology.registry.match(
                stream_name, stream_type, source_id)
            if spec:
                router = self._orchestrator
                session_id = router._custom_session_id or router._session_id
                active_start = (router._custom_session_started_at if router._custom_session_id
                                else router._session_started_at)
                if not session_id or (active_start is not None and timestamp < active_start):
                    session_id = next((run_id for run_id, start, end
                                       in reversed(self._recent_log_sessions)
                                       if start <= timestamp <= end), "")
                recorded = self._event_store.append_signal(
                    outlet_uid=outlet_uid, stream_name=stream_name,
                    stream_type=stream_type, source_id=source_id,
                    signal_config_id=spec["id"], lsl_timestamp=timestamp,
                    channels=data, session_id=session_id)
                if not recorded:
                    return "dropped"
            return self._orchestrator.on_sample(
                stream_name, stream_type, timestamp, data,
                outlet_uid=outlet_uid, source_id=source_id)

    def _audit_and_route(self, stream_name, stream_type, timestamp, data,
                         source, outlet_uid, source_id):
        event = decode_source_event(source, data)
        event_id = self._event_store.append(
            outlet_uid=outlet_uid, stream_name=stream_name, stream_type=stream_type,
            source_id=source_id, lsl_timestamp=timestamp, channels=data,
            source_event=event)
        if event_id is None:
            return "dropped"  # never score an event without an audit identity
        router = self._orchestrator
        active_id = (router._custom_session_id if source == "custom" else
                     router._session_id if router._active_drill is not None else "")
        if event.status != "decoded":
            if active_id:
                self._event_store.assign_event_session(event_id, active_id)
            return event.status
        before_custom_run = router._custom_session_id
        before_native_run = router._session_id if router._active_drill is not None else ""
        before_custom_start = router._custom_session_started_at
        before_native_start = router._session_started_at
        before_custom_scenario_id = (router._active_custom_drill or {}).get(
            "stream_policy", {}).get("scenario_id", "")
        before_native_scenario = router._active_scenario or ""
        before_context_matched = router._custom_context.get("context_match_status") in {
            "matched_name", "matched_alias"}
        before_round_id = (_custom_round(router, event, timestamp)
                           if source == "custom" else None)
        try:
            outcome = router.on_sample(stream_name, stream_type, timestamp, data,
                                       source_event=event, outlet_uid=outlet_uid,
                                       source_id=source_id)
            self._event_attributor.route(
                event_id, event, outcome, router, timestamp,
                before_custom_run=before_custom_run,
                before_custom_scenario_id=before_custom_scenario_id,
                before_native_run_id=before_native_run,
                before_native_scenario=before_native_scenario,
                before_context_matched=before_context_matched,
                before_round_id=before_round_id)
            after_custom_run = router._custom_session_id
            after_native_run = router._session_id if router._active_drill is not None else ""
            matched_context = router._custom_context.get("context_match_status") in {
                "matched_name", "matched_alias"}
            run_id = ((after_custom_run or before_custom_run) if source == "custom"
                      else (after_native_run or before_native_run or
                            ((after_custom_run or before_custom_run)
                             if matched_context or before_context_matched else "")))
            if run_id:
                self._event_store.assign_event_session(event_id, run_id)
            if after_custom_run and after_custom_run != before_custom_run:
                context = router._custom_context
                native_start = context.get("started_at") if matched_context else None
                start = min(timestamp, native_start) if native_start is not None else timestamp
                self._log_start_by_run[after_custom_run] = start
                round_repository.attach_session_log(
                    after_custom_run, self._event_store.capture_id, start,
                    context.get("native_session_id", "") if matched_context else "")
                self._event_store.assign_signal_window(after_custom_run, start, timestamp)
                self._event_store.assign_event_window(
                    after_custom_run, "custom", start, timestamp)
                if native_start is not None:
                    self._event_store.assign_event_window(
                        after_custom_run, "native", native_start, timestamp)
            if after_native_run and after_native_run != before_native_run:
                self._log_start_by_run[after_native_run] = timestamp
                round_repository.attach_session_log(
                    after_native_run, self._event_store.capture_id, timestamp,
                    router._native_external_session_id)
                self._event_store.assign_signal_window(after_native_run, timestamp, timestamp)
            if before_custom_run and not after_custom_run:
                self._finish_log(before_custom_run, "custom",
                                 before_custom_start or timestamp, timestamp,
                                 native_context=before_context_matched)
            if before_native_run and not after_native_run:
                self._finish_log(before_native_run, "native",
                                 before_native_start or timestamp, timestamp)
        except Exception as exc:
            self._event_store.mark_processing_error(exc)
            raise
        return outcome

    def _finish_log(self, run_id: str, source: str, started_at: float,
                    ended_at: float, *, native_context: bool = False) -> None:
        started_at = self._log_start_by_run.pop(run_id, started_at)
        self._event_store.assign_signal_window(run_id, started_at, ended_at)
        self._event_store.assign_event_window(run_id, source, started_at, ended_at)
        if native_context:
            self._event_store.assign_event_window(run_id, "native", started_at, ended_at)
        self._recent_log_sessions.append((run_id, started_at, ended_at))
        complete = self._event_store.flush()
        round_repository.close_session_log(run_id, ended_at, complete=complete)

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
            "audit": self._event_store.health() if self._event_store else None,
        }

    def _catalog_payload(self, catalog: list = None) -> dict:
        if catalog is None:
            catalog = self._inlets.get_catalog()
            catalog = [s for s in catalog
                       if s.get("stream_type", s.get("type", "")) not in LSL_TYPES_EXCLUDED]
        return {
            "type":    MSG_STREAMS_CATALOG,
            "streams": catalog,
            "health": self._inlets.get_health() if hasattr(self._inlets, "get_health") else {},
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
