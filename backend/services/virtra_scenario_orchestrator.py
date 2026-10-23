"""
virtra_scenario_orchestrator.py — Real-time VirTra simulation event processor and scenario orchestrator.

Handles live.activate / live.deactivate, parses incoming VirTra LSL events
(Scenario Started, Scenario Stopped, Shot, Target Activated, etc.), maintains
active scenario context via DrillDefinitionRegistry, delegates trials
to the scenario-selected adapter,
delegates physiological telemetry to BiosignalRollingMonitor, and triggers
post-scenario metric computation and session storage workflows.

Scenario configs select trial adapters and metric IDs. Cognitive-skill links
are interpretation metadata, separate from event handling.
"""

import asyncio
import os
import re
import sys
import uuid
import threading
from typing import Callable, Optional

from pylsl import local_clock

from .drill_definition_registry import DrillDefinitionRegistry
from physiology.service import PhysiologyService
from physiology.catalog import projection as physiology_projection
from .analysis_service_client import AnalysisServiceClient
from trial_models.registry import tracker_for
from event_decoders.custom_lsl import CustomLslEvent, decode_custom_lsl_sample
from event_decoders.native_virtra import decode_native_lsl_sample, parse_native_metadata
from virtra_custom_lsl.session import CustomLslSessionBase
from assessments.catalog import CATEGORIES, DRILLS
from assessments.events import EventKind, normalize_custom_event
from .round_assessment_service import RoundAssessmentService
from . import round_repository

# Native VirTra machine stream: name "V_300", type "VirTraEvents".
# Kept as a narrow name match — do NOT broaden to a generic "VirTra" substring,
# that would also match the custom stream's name below ("virtra_lsl").

# Custom, script-driven VirTra stream: name "virtra_lsl", type "VirTra Events"
# (note the space — distinct from the native type above). See
# validate_data/VirTra_Custom_LSL_Stream_Spec.txt.

# Neon speech transcription stream pattern
_SPEECH_TYPE_PATTERN = re.compile(r"speech.transcription", re.IGNORECASE)
_SPEECH_IDX_TEXT = 0
_SPEECH_IDX_USTART = 1
_SPEECH_IDX_UEND = 2


class _PassthroughCustomLslSession(CustomLslSessionBase):
    """
    Placeholder used until a drill_type -> tracker mapping exists (later phase).
    Runs the shared scenario/round lifecycle bookkeeping only — no trial logic.
    """
    def on_drill_event(self, event):
        pass


def _parse_metadata_field(metadata_str: str, field_key: str) -> Optional[str]:
    """
    Extract a value from VirTra metadata string.
    Format: [["Key","Value"],["Key2","Value2"],...]
    """
    if not metadata_str:
        return None
    metadata, _ = parse_native_metadata(metadata_str)
    if field_key in metadata:
        return str(metadata[field_key])

    # Fallback regex extraction for double-quoted CSV variant
    pattern = rf'\[?"?"?{re.escape(field_key)}"?"?\s*,\s*"?"?([^"\]]+)"?"?\]?'
    match = re.search(pattern, metadata_str)
    if match:
        return match.group(1).strip()

    return None


class VirTraScenarioOrchestrator:
    """
    Coordinates live simulation events from VirTra, tracks active drills,
    and manages trial and biosignal sub-monitors.
    """

    def __init__(
        self,
        inlets,
        broadcast_fn: Callable,
        send_fn: Callable,
        get_state_payload_fn: Callable,
        error_fn: Callable,
        on_live_activate_fn: Callable,
        on_live_deactivate_fn: Callable,
        loop: asyncio.AbstractEventLoop,
        registry: Optional[DrillDefinitionRegistry] = None,
    ):
        self._inlets = inlets
        self._broadcast = broadcast_fn
        self._send = send_fn
        self._state_payload = get_state_payload_fn
        self._error = error_fn
        self._on_live_activate = on_live_activate_fn
        self._on_live_deactivate = on_live_deactivate_fn
        self._loop = loop
        self._registry = registry or DrillDefinitionRegistry()
        self._stream_config = self._registry.stream_config
        self._run_source: str | None = None
        self._native_context: dict = {}
        self._custom_context: dict = {}
        self._waiting_for_custom: str = ""

        # Active drill state
        self._active_drill: Optional[dict] = None
        self._active_scenario: Optional[str] = None
        self._session_started_at: Optional[float] = None
        self._session_id: str = ""
        self._native_external_session_id: str = ""

        # Simulator metadata captured at scenario start
        self._simulator: str = ""
        self._simulator_system: str = ""

        # Weapon info — captured from first Shot Fired event; reset each scenario
        self._weapon_type_seen: bool = False
        self._weapon_type: str = ""

        # Participant info — populated from a "Participant" LSL stream if present.
        # The Frontend save form pre-fills from these; operator can always override.
        self._participant_id:   str = ""
        self._participant_name: str = ""

        # Snapshot of active stream names captured at scenario start.
        # Included in session_record.json so it's clear what sensors were connected.
        self._active_streams_at_start: list[str] = []

        # Metadata from the last completed scenario — held until operator saves/discards.
        self._last_session_metadata: dict | None = None

        # Active cognitive attribute tracker — instantiated from ATTRIBUTE_REGISTRY
        # at Scenario Started; None between sessions.
        self._active_tracker = None

        # Custom VirTra LSL session — independent lifecycle from the native stream.
        self._active_custom_session: Optional[CustomLslSessionBase] = None
        self._active_custom_drill: Optional[dict] = None
        self._active_custom_scenario: str = ""
        self._custom_session_started_at: Optional[float] = None
        self._custom_observed_at: Optional[float] = None
        self._custom_session_id: str = ""
        self._custom_partial_start = False
        self._custom_simulator: str = ""
        self._custom_simulator_system: str = ""
        self._event_lock = threading.RLock()
        self._round_assessments = RoundAssessmentService()
        self._physiology = PhysiologyService(broadcast_fn, loop)
        self._round_physiology = self._physiology.rounds
        self._last_physio_preview_at = float("-inf")

        # Biosignal rolling monitor
        self._biosignals = self._physiology.session

        # Analysis / metrics client (now calls metrics directly, no HTTP)
        self._analysis_client = AnalysisServiceClient(broadcast_fn=broadcast_fn, loop=loop)

    # ── Sample routing ────────────────────────────────────────────────────────

    def on_sample(self, stream_name: str, stream_type: str, timestamp: float, data: list,
                  *, source_event=None, source_id: str = "", outlet_uid: str = ""):
        """Called for every LSL sample from any stream."""
        if source_event is not None and source_event.status != "decoded":
            return source_event.status
        # LSL producers are not consistent about type casing or separators.
        # Normalize here so "HR", "hr", "Heart Rate", and similar variants
        # reach the same session accumulator.
        signal_type = re.sub(r"[^a-z0-9]+", "", (stream_type or "").lower())
        signal_name = re.sub(r"[^a-z0-9]+", "", (stream_name or "").lower())

        # 0. Participant info stream — future: LSL stream of type "Participant"
        if signal_type == "participant":
            self._handle_participant_info_sample(data)
            return

        # 1. Physiology sources share a registry and one normalized observation.
        recognized, preview = self._physiology.ingest(
            stream_name, stream_type, timestamp, data,
            source_id=source_id, outlet_uid=outlet_uid)
        if recognized:
            self._capture_round_physiology(preview, timestamp)
            return
        if _SPEECH_TYPE_PATTERN.search(stream_type) or _SPEECH_TYPE_PATTERN.search(stream_name):
            self._handle_speech_sample(timestamp, data)
            return

        # Classify format first. The drill's metrics_source decides scoring.
        source = self._stream_config.stream_source(stream_name, stream_type)
        if source == "custom":
            return self._on_custom_lsl_sample(timestamp, data, source_event=source_event)

        # 3. VirTra stream check
        if source != "native":
            return "unrecognized"
        if len(data) < 1:
            return "malformed"

        decoded = source_event.decoded if source_event is not None else decode_native_lsl_sample(data)
        if decoded is None:
            return "malformed"
        event_type = decoded.event_name
        description = decoded.description
        category = decoded.category
        metadata_raw = decoded.metadata_raw
        metadata_dict = decoded.metadata
        decode_outcome = ("unrecognized" if decoded.metadata_format == "unrecognized"
                          else None)

        print(f"[VirTraScenarioOrchestrator] VirTra > {event_type!r}  |  {description[:80]}")

        # Scenario lifecycle
        if event_type == "Scenario Started":
            with self._event_lock:
                self._remember_native_start(timestamp, description, metadata_raw)
                self._handle_scenario_started(timestamp, event_type, description, category, metadata_raw)
        elif event_type in ("Scenario Stopped", "Scenario Ended", "Scenario Destroyed"):
            with self._event_lock:
                self._handle_scenario_stopped(timestamp, metadata_raw)

        if event_type == "Shot Fired":
            weapon_type = str(metadata_dict.get("WeaponType") or "")
            if weapon_type:
                self._remember_native_weapon(timestamp, weapon_type)

        if event_type in {"Shot Hit", "Shot Miss"}:
            with self._event_lock:
                tracker = self._active_custom_session
                if (self._active_custom_drill and
                        self._active_custom_drill.get("drill_key") in DRILLS and
                        self._custom_context.get("context_match_status") in
                        {"matched_name", "matched_alias"} and
                        callable(getattr(tracker, "on_native_shot_result", None))):
                    changed = tracker.on_native_shot_result(
                        event_type, str(metadata_dict.get("ShotID") or "").strip())
                    if changed:
                        asyncio.run_coroutine_threadsafe(self._broadcast({
                            "type": "assessment.round_projection",
                            "run_id": self._custom_session_id,
                            "timestamp": timestamp,
                            "rounds": tracker.rounds_snapshot(),
                        }), self._loop)

        # If no registered session is active, drop all further processing
        if self._active_drill is None:
            return decode_outcome

        # Forward events to the active cognitive attribute tracker.
        # The tracker decides internally which event types it cares about.
        _TRIAL_EVENTS = (
            "Target Activated", "Target Deactivated", "Target Removed",
            "Shot Fired", "Shot Hit", "Unscored Hit", "Shot Miss",
        )
        policy = self._active_drill.get("stream_policy", {})
        accepted_formats = policy.get("native_metadata_formats")
        valid_format = (not accepted_formats or decoded.metadata_format in accepted_formats)
        if self._active_tracker is not None and event_type in _TRIAL_EVENTS and valid_format:
            self._active_tracker.on_virtra_event(event_type, timestamp, metadata_dict)

        # Broadcast every raw VirTra event to clients unconditionally
        msg = {
            "type":        "session.virtra_event",
            "event_type":  event_type,
            "description": description,
            "category":    category,
            "metadata":    metadata_dict,
            "timestamp":   timestamp,
            "drill_key":   self._active_drill["drill_key"] if self._active_drill else "",
        }
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
        return decode_outcome

    def _remember_native_start(self, timestamp: float, description: str, metadata_raw: str):
        scenario_name = _parse_metadata_field(metadata_raw, "Scenario") or description.strip()
        self._native_context = {
            "native_scenario_name": scenario_name,
            "native_session_id": _parse_metadata_field(metadata_raw, "SessionID") or "",
            "simulator": _parse_metadata_field(metadata_raw, "Simulator") or "",
            "simulator_system": _parse_metadata_field(metadata_raw, "SimulatorSystem") or "",
            "weapon_type": "",
            "started_at": timestamp,
            "observed_at": local_clock(),
            "stopped_at": None,
        }
        if self._active_custom_session is not None:
            self._match_native_context(timestamp)

    def _match_native_context(self, timestamp: float):
        if self._active_custom_session is None or not self._native_context:
            return
        context = self._native_context
        native_seen = context.get("observed_at", local_clock())
        custom_seen = self._custom_observed_at or local_clock()
        if abs(custom_seen - native_seen) > 30:
            return
        if (context.get("observed_stopped_at") is not None
                and context["observed_stopped_at"] < custom_seen):
            return
        native_name = context.get("native_scenario_name", "").casefold().strip()
        custom_name = self._active_custom_scenario.casefold().strip()
        aliases = {name.casefold().strip() for name in
                   self._active_custom_drill.get("stream_policy", {}).get("native_scenarios", [])}
        status = ("matched_alias" if native_name in aliases else
                  "matched_name" if native_name and native_name == custom_name else
                  "temporal_only")
        self._custom_context = {**context, "context_match_status": status}
        self._custom_simulator = context.get("simulator", "") or self._custom_simulator
        self._custom_simulator_system = context.get("simulator_system", "") or self._custom_simulator_system
        asyncio.run_coroutine_threadsafe(self._broadcast({
            "type": "session.context_updated",
            "session_id": self._custom_session_id,
            "scenario_name": self._display_custom_scenario(),
            "simulator": self._custom_simulator,
            "simulator_system": self._custom_simulator_system,
            "weapon_type": self._custom_context.get("weapon_type", ""),
            "context_match_status": status,
        }), self._loop)

    def _display_custom_scenario(self) -> str:
        if self._custom_context.get("context_match_status") in {"matched_alias", "matched_name"}:
            return self._custom_context.get("native_scenario_name") or self._active_custom_scenario
        return self._active_custom_scenario

    def _remember_native_weapon(self, timestamp: float, weapon_type: str):
        if self._active_custom_session is not None and self._run_source == "custom":
            if (self._custom_context.get("context_match_status") == "unmatched"
                    and self._native_context):
                self._match_native_context(timestamp)
            self._custom_context["weapon_type"] = weapon_type
            if self._custom_context.get("context_match_status") == "unmatched":
                self._custom_context["context_match_status"] = "temporal_only"
        elif self._active_drill is not None and not self._weapon_type_seen:
            self._weapon_type = weapon_type
            self._weapon_type_seen = True
        else:
            return
        asyncio.run_coroutine_threadsafe(self._broadcast({
            "type": "session.weapon_info", "weapon_type": weapon_type,
        }), self._loop)

    def _on_custom_lsl_sample(self, timestamp: float, data: list, *, source_event=None):
        with self._event_lock:
            return self._process_custom_lsl_sample(timestamp, data, source_event=source_event)

    def _process_custom_lsl_sample(self, timestamp: float, data: list, *, source_event=None):
        """
        Route one sample from the custom, script-driven VirTra LSL stream.

        Fully independent lifecycle from the native VirTra stream — the custom
        stream emits its own "Scenario Started"/"Scenario Stopped" and resolves
        trackers by payload drill_type via [custom_lsl_drills] in the registry.
        """
        event = source_event.decoded if source_event is not None else decode_custom_lsl_sample(data)
        if event is None:
            return "malformed"
        normalized = normalize_custom_event(event, timestamp, self._stream_config)
        if self._active_custom_session is not None:
            active_external_id = self._active_custom_session.scenario_id
            if active_external_id and event.scenario_id and event.scenario_id != active_external_id:
                return "dropped"
            active_name = self._active_custom_scenario.casefold().strip()
            if (not active_external_id and active_name and event.scenario_name and
                    event.scenario_name.casefold().strip() != active_name):
                return "dropped"
        stream_drill = self._stream_config.drill_for_custom_type(event.drill_type)
        can_start_from_round = self._stream_config.custom_lifecycle.get(
            "allow_round_start_without_scenario_start", False)
        starts_run = (normalized.kind == EventKind.SCENARIO_START or
                      (can_start_from_round and normalized.kind == EventKind.ROUND_START
                       and self._active_custom_session is None))
        if not self._round_assessments.accepts(
                timestamp, data, scenario_id=event.scenario_id,
                is_explicit_start=starts_run,
                drill_key=stream_drill.get("canonical_key", "") if stream_drill else ""):
            return "dropped"
        if (self._active_custom_drill is not None and event.drill_type
                and event.drill_type.casefold() !=
                self._active_custom_drill.get("custom_lsl_type", "").casefold()):
            return "dropped"

        print(f"[VirTraScenarioOrchestrator] CustomLSL > {event.event_name!r} | {event.event_description[:80]}")

        if (normalized.kind == EventKind.SCENARIO_START or
                (can_start_from_round and normalized.kind == EventKind.ROUND_START)) and self._active_custom_session is None:
            self._handle_custom_lsl_scenario_started(timestamp, event)

        if self._active_custom_session is not None:
            if normalized.kind == EventKind.ROUND_START and not self._active_custom_session.is_active:
                self._active_custom_session._start_session(event)
            if normalized.kind != EventKind.SCENARIO_START or not self._active_custom_session.is_active:
                self._active_custom_session.handle_event(event, timestamp)
            if normalized.kind in {EventKind.ROUND_START, EventKind.ROUND_COMPLETE,
                                   EventKind.ROUND_RESET}:
                self._persist_custom_rounds()

        msg = {
            "type":        "session.custom_lsl_event",
            "event_name":  event.event_name,
            "event_code":  event.event_code,
            "description": event.event_description,
            "drill_type":  event.drill_type,
            "phase":       event.phase,
            "round":       event.round,
            "state":       event.state,
            "data":        event.data,
            "timestamp":   timestamp,
            "session_id": self._custom_session_id if self._active_custom_session else "",
            "rounds": self._active_custom_session.rounds_snapshot()
                      if self._active_custom_session else [],
        }
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

        if normalized.kind == EventKind.SCENARIO_STOP:
            self._handle_custom_lsl_scenario_stopped(timestamp, event)
        return "unrecognized" if stream_drill is None else None

    def _persist_custom_rounds(self):
        tracker = self._active_custom_session
        if tracker is None or self._active_custom_drill is None:
            return
        rows = self._round_assessments.checkpoint(
            self._custom_session_id, self._active_custom_drill["drill_key"], tracker)
        self._sync_physiology_windows(tracker.rounds_snapshot())
        for persisted in rows:
            asyncio.run_coroutine_threadsafe(self._broadcast({
                "type": "assessment.round_updated", "projection_version": 1,
                "run_id": self._custom_session_id,
                "round": persisted,
            }), self._loop)

    def _capture_round_physiology(self, preview, timestamp):
        if (preview and self._active_custom_session is not None and
                timestamp - self._last_physio_preview_at >= 0.25):
            self._last_physio_preview_at = timestamp
            asyncio.run_coroutine_threadsafe(self._broadcast({
                "type": "assessment.round_physiology", "projection_version": 1,
                "run_id": self._custom_session_id, "physiology": preview,
            }), self._loop)

    def _sync_physiology_windows(self, rounds: list[dict]):
        if not round_repository.available():
            return
        for row in rounds:
            round_id = row["round_id"]
            self._round_physiology.begin(round_id, row["started_at"])
            if (row.get("ended_at") is not None and
                    self._round_physiology.close(round_id, row["ended_at"])):
                asyncio.run_coroutine_threadsafe(
                    self._finish_round_physiology_after_grace(round_id, self._custom_session_id),
                    self._loop)

    async def _finish_round_physiology_after_grace(self, round_id: str, run_id: str):
        await asyncio.sleep(0.35)
        try:
            result = await self._loop.run_in_executor(
                None, self._round_physiology.finalize, round_id)
            if result:
                await self._broadcast({
                    "type": "assessment.round_updated", "projection_version": 1,
                    "run_id": run_id, "round": result,
                })
        except Exception as exc:
            print(f"[VirTraScenarioOrchestrator] Round physiology finalization failed: {exc}")

    def _handle_custom_lsl_scenario_started(self, timestamp: float, event):
        self._registry.reload()
        drill_info = self._registry.resolve_custom_lsl_drill(event.drill_type)
        if drill_info is None:
            return
        if self._active_drill is not None:
            return

        task_type = drill_info.get("task_type", "")
        TrackerClass = tracker_for(drill_info)
        if TrackerClass is None or not issubclass(TrackerClass, CustomLslSessionBase):
            print(f"[VirTraScenarioOrchestrator] No custom LSL tracker for task_type={task_type!r}")
            return

        self._run_source = "custom"
        self._waiting_for_custom = ""

        self._active_custom_drill = drill_info
        self._active_custom_scenario = event.scenario_name
        self._custom_session_started_at = timestamp
        self._custom_observed_at = local_clock()
        self._custom_session_id = str(uuid.uuid4())
        self._custom_partial_start = event.event_name not in self._stream_config.event_names("scenario_start")
        # The custom stream's scenario_display_name names the drill, not the
        # simulator system. Only show simulator fields if the event supplies them.
        context = event.data if isinstance(event.data, dict) else {}
        self._custom_simulator = str(context.get("simulator") or "")
        self._custom_simulator_system = str(context.get("simulator_system") or "")
        self._custom_context = {"context_match_status": "unmatched"}
        self._active_custom_session = TrackerClass(broadcast_fn=self._broadcast, loop=self._loop)
        self._active_custom_session.reset(
            params=drill_info.get("params", {}),
            targets=drill_info.get("targets", {}),
        )
        stream_drill = self._stream_config.drill_for_custom_type(event.drill_type)
        if stream_drill:
            self._active_custom_session.configure_components(stream_drill, {
                key: self._stream_config.event_names(key)
                for key in ("scenario_start", "scenario_stop", "round_start",
                            "round_complete", "round_reset")
            })
        self._round_assessments.begin(
            self._custom_session_id, drill_info["drill_key"], event.scenario_name,
            event.scenario_id, timestamp, {
                key: drill_info.get(key) for key in
                ("scenario_id", "scenario_revision", "selected_metric_ids", "cognitive_skills")
            })
        self._biosignals.reset()
        if self._native_context:
            self._match_native_context(timestamp)

        msg = {
            "type":          "session.scenario_started",
            "scenario_name": self._display_custom_scenario() or event.scenario_name,
            "session_id":    self._custom_session_id,
            "source":        "custom",
            "metrics_source": "custom",
            "capture_status": "partial" if self._custom_partial_start else "complete",
            "timestamp":     timestamp,
            "drill_key":     drill_info["drill_key"],
            "drill_label":   drill_info["label"],
            "task_type":     task_type,
            "selected_metric_ids": drill_info.get("selected_metric_ids", []),
            "cognitive_skills": drill_info.get("cognitive_skills", []),
            "category_id": (DRILLS[drill_info["drill_key"]].category_id
                            if drill_info["drill_key"] in DRILLS else ""),
            "assessment_profile": (
                CATEGORIES[DRILLS[drill_info["drill_key"]].category_id].profile.id
                if drill_info["drill_key"] in DRILLS else ""),
            "targets":       drill_info.get("targets", {}),
            "tracked_events": [],
            "simulator":     self._custom_simulator,
            "simulator_system": self._custom_simulator_system,
            "weapon_type": self._custom_context.get("weapon_type", ""),
        }
        print(
            f"[VirTraScenarioOrchestrator] > Custom LSL Scenario Started: {event.scenario_name!r} "
            f"-> drill_key={drill_info['drill_key']!r} task_type={task_type!r}"
        )
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    def _handle_custom_lsl_scenario_stopped(self, timestamp: float, event, manual: bool = False):
        if self._active_custom_session is None or self._active_custom_drill is None:
            return

        drill_info = self._active_custom_drill
        scenario_name = self._display_custom_scenario() or event.scenario_name
        session_id = self._custom_session_id
        started_at = self._custom_session_started_at or timestamp
        final_rows = self._round_assessments.end(
            session_id, drill_info["drill_key"], self._active_custom_session,
            timestamp, "manual" if manual else event.event_name,
            event.scenario_id or self._active_custom_session.scenario_id,
            "manual" if manual else event.data.get("terminal_source", "custom"))
        self._sync_physiology_windows(self._active_custom_session.rounds_snapshot())
        for persisted in final_rows:
            asyncio.run_coroutine_threadsafe(self._broadcast({
                "type": "assessment.round_updated", "projection_version": 1,
                "run_id": session_id, "round": persisted,
            }), self._loop)

        msg = {
            "type":          "session.scenario_stopped",
            "scenario_name": scenario_name,
            "session_id":    session_id,
            "timestamp":     timestamp,
            "drill_key":     drill_info["drill_key"],
            "manual":        manual,
        }
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

        biosignals = self._biosignals.snapshot_biosignals()
        if biosignals:
            biosignals["_assessment"] = physiology_projection(
                biosignals, self._biosignals.snapshot_quality())
        final_physiology = self._biosignals.final_payload()
        if final_physiology:
            asyncio.run_coroutine_threadsafe(self._broadcast(final_physiology), self._loop)
        session_record = self._active_custom_session.get_session_record(
            scenario_name=scenario_name,
            session_id=session_id,
            started_at=started_at,
            stopped_at=timestamp,
            biosignals=biosignals,
            drill_key=drill_info["drill_key"],
        )

        session_record["rounds"] = self._active_custom_session.rounds_snapshot()
        session_record["source"] = "custom"
        session_record["metrics_source"] = "custom"
        session_record["native_context"] = dict(self._custom_context)
        session_record["custom_scenario_name"] = self._active_custom_scenario
        session_record["capture_status"] = (
            "partial" if self._custom_partial_start else "complete")
        session_record["task_type"] = drill_info["task_type"]
        session_record["assessment_selection"] = {
            key: drill_info.get(key) for key in
            ("scenario_id", "scenario_revision", "selected_metric_ids", "cognitive_skills")
        }
        session_record["external_scenario_id"] = (
            event.scenario_id or self._active_custom_session.scenario_id)

        # Build metadata BEFORE calling run_after_live_scenario so it receives
        # the current session's values (not the previous session's or None).
        self._last_session_metadata = {
            "drill_key":      drill_info["drill_key"],
            "task_type":      drill_info["task_type"],
            "scenario_name":  scenario_name,
            "session_id":     session_id,
            "started_at":     started_at,
            "stopped_at":     timestamp,
            "duration_s":     session_record.get("duration_s"),
            "active_streams": list(self._active_streams_at_start),
            "participant_id":   self._participant_id,
            "participant_name": self._participant_name,
            # Physiological snapshot captured at Scenario Stopped
            "biosignals":       biosignals,
            "native_context": dict(self._custom_context),
        }

        asyncio.run_coroutine_threadsafe(
            self._analysis_client.run_after_live_scenario(
                drill_key=drill_info["drill_key"],
                session_record=session_record,
                scenario_name=scenario_name,
                session_metadata=self._last_session_metadata,
            ),
            self._loop,
        )

        print(f"[VirTraScenarioOrchestrator] Custom LSL Scenario Stopped: {scenario_name!r}")
        self._active_custom_session = None
        self._active_custom_drill = None
        self._active_custom_scenario = ""
        self._custom_session_started_at = None
        self._custom_observed_at = None
        self._custom_session_id = ""
        self._custom_partial_start = False
        self._custom_simulator = ""
        self._custom_simulator_system = ""
        self._custom_context = {}
        self._native_context = {}
        self._run_source = None

    def on_catalog_changed(self, catalog: list):
        """Track active stream names for session metadata capture."""
        self._active_streams_at_start = [
            s.get("stream_name", s.get("name", ""))
            for s in catalog
            if s.get("stream_name") or s.get("name")
        ]

    def _handle_participant_info_sample(self, data: list):
        """
        Passive handler for a "Participant" LSL stream.
        Expected format: [participant_id, participant_name] (both strings).
        Populated into self._participant_id / _participant_name so the Frontend
        save form can pre-fill them without operator typing.
        """
        if len(data) >= 1 and str(data[0]).strip():
            self._participant_id = str(data[0]).strip()
        if len(data) >= 2 and str(data[1]).strip():
            self._participant_name = str(data[1]).strip()
        print(
            f"[VirTraScenarioOrchestrator] Participant info from LSL: "
            f"id={self._participant_id!r} name={self._participant_name!r}"
        )

    def _handle_speech_sample(self, timestamp: float, data: list):
        try:
            text = str(data[_SPEECH_IDX_TEXT]).strip() if len(data) > _SPEECH_IDX_TEXT else ""
            if not text:
                return
            ustart_ms = float(data[_SPEECH_IDX_USTART]) if len(data) > _SPEECH_IDX_USTART else 0
            uend_ms = float(data[_SPEECH_IDX_UEND]) if len(data) > _SPEECH_IDX_UEND else 0
            msg = {
                "type":       "session.speech_update",
                "text":       text,
                "timestamp":  timestamp,
                "start_ms":   ustart_ms,
                "end_ms":     uend_ms,
            }
            print(f"[VirTraScenarioOrchestrator] Speech utterance: {text!r:.80}")
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)
        except (IndexError, ValueError, TypeError) as e:
            print(f"[VirTraScenarioOrchestrator] Speech sample error: {e}")

    def _handle_scenario_started(self, timestamp: float, event_type: str, description: str,
                                 category: str, metadata_raw: str):
        self._registry.reload()
        scenario_name = _parse_metadata_field(metadata_raw, "Scenario")
        if not scenario_name:
            match = re.search(r"Scenario[: ]+(.+)", description)
            scenario_name = match.group(1).strip() if match else description.strip()

        policy = self._stream_config.drill_for_native_scenario(scenario_name, include_disabled=True)
        if policy and policy["metrics_source"] == "custom":
            if self._active_custom_session is not None:
                # A native start can arrive after the matching custom session.
                # The source is already active, so do not announce a wait.
                return
            self._waiting_for_custom = scenario_name
            asyncio.run_coroutine_threadsafe(self._broadcast({
                "type": "session.scoring_waiting", "scenario_name": scenario_name,
                "metrics_source": "custom",
            }), self._loop)
            return
        if (policy and policy["metrics_source"] != "native") or self._active_custom_session is not None:
            return
        drill_info = self._registry.resolve_drill(scenario_name)

        # Unregistered scenario — drop entirely, no session started
        if drill_info is None:
            return

        if tracker_for(drill_info) is None:
            print(f"[VirTraScenarioOrchestrator] Unsupported trial adapter: {drill_info.get('trial_adapter')!r}")
            return
        self._run_source = "native"

        already_active = (
            self._active_scenario == scenario_name
            and self._active_drill is not None
        )

        self._active_scenario = scenario_name
        self._active_drill = drill_info

        if not already_active:
            # Instantiate the tracker for this cognitive attribute from the registry
            task_type = drill_info.get("task_type", "")
            TrackerClass = tracker_for(drill_info)
            if TrackerClass is not None:
                self._active_tracker = TrackerClass(
                    broadcast_fn=self._broadcast, loop=self._loop
                )
                # All trackers accept params; GNG-type trackers also accept targets.
                # PVT ignores targets (keyword not in its signature) — handled by try/except.
                try:
                    self._active_tracker.reset(
                        params=drill_info.get("params", {}),
                        targets=drill_info.get("targets", {}),
                    )
                except TypeError:
                    # Tracker does not accept 'targets' (e.g. PVT) — call without it
                    self._active_tracker.reset(params=drill_info.get("params", {}))
            else:
                print(f"[VirTraScenarioOrchestrator] No tracker for task_type={task_type!r} — ignoring scenario")
                return
            self._biosignals.reset()
            self._session_started_at = timestamp
            self._session_id = str(uuid.uuid4())
            self._native_external_session_id = (
                _parse_metadata_field(metadata_raw, "SessionID") or "")
            if round_repository.available():
                round_repository.capture_run(
                    self._session_id, drill_key=drill_info["drill_key"],
                    category_id=drill_info.get("category_id") or task_type.casefold(),
                    profile_id=drill_info.get("assessment_profile") or
                               drill_info["trial_adapter"],
                    profile_version=1, scenario_name=scenario_name,
                    external_scenario_id=(
                        _parse_metadata_field(metadata_raw, "ScenarioID") or ""),
                    source="native", started_at=timestamp)
            self._weapon_type_seen = False
            self._weapon_type = ""

        # Parse simulator context from this event's metadata
        self._simulator = (
            _parse_metadata_field(metadata_raw, "Simulator")
            or (self._simulator if already_active else "")
        )
        self._simulator_system = (
            _parse_metadata_field(metadata_raw, "SimulatorSystem")
            or (self._simulator_system if already_active else "")
        )

        msg = {
            "type":             "session.scenario_started",
            "scenario_name":    scenario_name,
            "session_id":       self._session_id,
            "source":           "native",
            "metrics_source":   "native",
            "timestamp":        timestamp,
            "drill_key":        drill_info["drill_key"],
            "drill_label":      drill_info["label"],
            "task_type":        drill_info["task_type"],
            "selected_metric_ids": drill_info.get("selected_metric_ids", []),
            "cognitive_skills": drill_info.get("cognitive_skills", []),
            "targets":          drill_info.get("targets", {}),
            "tracked_events":   self._registry.all_tracked_events_for(drill_info["drill_key"]),
            "simulator":        self._simulator,
            "simulator_system": self._simulator_system,
            "weapon_type":     self._weapon_type,
        }

        print(f"[VirTraScenarioOrchestrator] > Scenario Started: {scenario_name!r}"
              f"  -> drill_key={drill_info['drill_key']!r}"
              f"  task_type={drill_info['task_type']!r}")
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    def _handle_scenario_stopped(self, timestamp: float, metadata_raw: str, manual: bool = False):
        if self._native_context:
            self._native_context["stopped_at"] = timestamp
            self._native_context["observed_stopped_at"] = local_clock()
        if self._active_custom_session is not None:
            with self._event_lock:
                if self._active_custom_drill.get("drill_key") not in DRILLS:
                    return
                matched = self._custom_context.get("context_match_status") in {
                    "matched_alias", "matched_name"}
                native_id = _parse_metadata_field(metadata_raw, "SessionID") or ""
                contextual_id = self._custom_context.get("native_session_id") or ""
                stop_name = (_parse_metadata_field(metadata_raw, "Scenario") or "").casefold().strip()
                context_name = self._custom_context.get("native_scenario_name", "").casefold().strip()
                same_id = not native_id or not contextual_id or native_id == contextual_id
                same_name = not stop_name or stop_name == context_name
                after_start = timestamp >= (self._custom_session_started_at or timestamp)
                if matched and same_id and same_name and after_start:
                    event = CustomLslEvent(
                        event_name=self._stream_config.event_names("scenario_stop")[0],
                        scenario_name=self._active_custom_scenario,
                        scenario_id=self._active_custom_session.scenario_id,
                        drill_type=self._active_custom_session.drill_type,
                        data={"terminal_source": "native_correlated"},
                    )
                    self._active_custom_session.handle_event(event, timestamp)
                    self._handle_custom_lsl_scenario_stopped(timestamp, event)
            return
        if self._waiting_for_custom:
            self._waiting_for_custom = ""
            return
        # If no registered session was ever started, ignore the stop event
        if self._active_drill is None:
            print("[VirTraScenarioOrchestrator] Scenario Stopped received but no active registered session — ignoring")
            return

        scenario_name = _parse_metadata_field(metadata_raw, "Scenario") or self._active_scenario or ""
        session_id = self._session_id

        drill_key_for_analysis = self._active_drill["drill_key"]
        task_type_for_analysis = self._active_drill["task_type"]
        assessment_selection = {
            key: self._active_drill.get(key) for key in
            ("scenario_id", "scenario_revision", "selected_metric_ids", "cognitive_skills")
        }

        msg = {
            "type":          "session.scenario_stopped",
            "scenario_name": scenario_name,
            "session_id":    session_id,
            "timestamp":     timestamp,
            "drill_key":     drill_key_for_analysis,
            "manual":        manual,
        }

        print(f"[VirTraScenarioOrchestrator] [] Scenario Stopped: {scenario_name!r}"
              f"  -> drill_key={drill_key_for_analysis!r}")
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

        self._active_drill = None
        self._active_scenario = None
        if round_repository.available():
            round_repository.close_run(session_id, timestamp,
                                       "manual" if manual else "scenario_stopped", "native")

        # Take physiological snapshot (full-session averages, not rolling window)
        biosignals = self._biosignals.snapshot_biosignals()
        if biosignals:
            biosignals["_assessment"] = physiology_projection(
                biosignals, self._biosignals.snapshot_quality())

        # Final session values use the same projection for both stream types.
        final_physiology = self._biosignals.final_payload()
        if final_physiology:
            asyncio.run_coroutine_threadsafe(self._broadcast(final_physiology), self._loop)

        # Build session record using the active tracker (if any)
        session_record = None
        if self._active_tracker is not None:
            session_record = self._active_tracker.get_session_record(
                scenario_name=scenario_name,
                session_id=session_id,
                started_at=self._session_started_at or timestamp,
                stopped_at=timestamp,
                biosignals=biosignals,
                **(dict(drill_key=drill_key_for_analysis)
                   if task_type_for_analysis == "GNG" else {})
            )
            session_record["source"] = "native"
            session_record["metrics_source"] = "native"
            session_record["assessment_selection"] = assessment_selection
            session_record["native_context"] = {
                **self._native_context, "weapon_type": self._weapon_type,
            }

        # Build metadata BEFORE calling run_after_live_scenario so it receives
        # the current session's values (not the previous session's or None).
        self._last_session_metadata = {
            "drill_key":      drill_key_for_analysis,
            "task_type":      task_type_for_analysis,
            "scenario_name":  scenario_name,
            "session_id":     session_id,
            "started_at":     self._session_started_at or timestamp,
            "stopped_at":     timestamp,
            "duration_s":     session_record.get("duration_s") if session_record else None,
            "active_streams": list(self._active_streams_at_start),
            "native_context": {**self._native_context, "weapon_type": self._weapon_type},
            # Participant info from LSL (may be empty strings if no Participant stream present)
            "participant_id":   self._participant_id,
            "participant_name": self._participant_name,
            # Physiological snapshot captured at Scenario Stopped
            "biosignals":       biosignals,
        }

        if session_record is not None:
            asyncio.run_coroutine_threadsafe(
                self._analysis_client.run_after_live_scenario(
                    drill_key=drill_key_for_analysis,
                    session_record=session_record,
                    scenario_name=scenario_name,
                    session_metadata=self._last_session_metadata,
                ),
                self._loop,
            )

        # Release tracker for next scenario (new one instantiated at Scenario Started)
        self._active_tracker = None
        self._session_started_at = None
        self._session_id = ""
        self._native_external_session_id = ""
        self._native_context = {}
        self._run_source = None

    def stop_active_drill(self) -> bool:
        """Finalize the active drill through the same path as a VirTra stop event."""
        with self._event_lock:
            timestamp = local_clock()
            if self._active_custom_session is not None and self._active_custom_drill is not None:
                event = CustomLslEvent(
                    event_name=self._stream_config.event_names("scenario_stop")[0],
                    scenario_name=self._active_custom_scenario,
                    scenario_id=self._active_custom_session.scenario_id,
                )
                self._active_custom_session.handle_event(event, timestamp)
                self._handle_custom_lsl_scenario_stopped(timestamp, event, manual=True)
                return True
            if self._active_drill is not None:
                self._handle_scenario_stopped(timestamp, "", manual=True)
                return True
            return False

    def get_active_drill_state(self) -> dict | None:
        """Return active drill metadata for reconnecting clients."""
        if self._active_drill is None and self._active_custom_drill is not None:
            return {
                "type":           "session.scenario_started",
                "scenario_name":  self._display_custom_scenario(),
                "session_id":     self._custom_session_id or "",
                "timestamp":      self._custom_session_started_at or 0.0,
                "drill_key":      self._active_custom_drill["drill_key"],
                "drill_label":    self._active_custom_drill["label"],
                "task_type":      self._active_custom_drill["task_type"],
                "category_id": (DRILLS[self._active_custom_drill["drill_key"]].category_id
                                if self._active_custom_drill["drill_key"] in DRILLS else ""),
                "assessment_profile": (
                    CATEGORIES[DRILLS[self._active_custom_drill["drill_key"]].category_id].profile.id
                    if self._active_custom_drill["drill_key"] in DRILLS else ""),
                "targets":        self._active_custom_drill.get("targets", {}),
                "tracked_events": [],
                "simulator":      self._custom_simulator,
                "simulator_system": self._custom_simulator_system,
                "weapon_type":    self._custom_context.get("weapon_type", ""),
                "source":        "custom",
                "metrics_source": "custom",
                "capture_status": "partial" if self._custom_partial_start else "complete",
                "rounds":        self._active_custom_session.rounds_snapshot()
                                 if self._active_custom_session else [],
                "assessment_run": self._round_assessments.snapshot(
                    self._custom_session_id, self._active_custom_drill["drill_key"]),
                "replayed":       True,
            }
        if self._active_drill is None:
            return None
        return {
            "type":           "session.scenario_started",
            "scenario_name":  self._active_scenario or "",
            "session_id":     self._session_id or "",
            "timestamp":      self._session_started_at or 0.0,
            "drill_key":      self._active_drill["drill_key"],
            "drill_label":    self._active_drill["label"],
            "task_type":      self._active_drill["task_type"],
            "targets":        self._active_drill.get("targets", {}),
            "tracked_events": self._registry.all_tracked_events_for(self._active_drill["drill_key"]),
            "simulator":     self._simulator,
            "simulator_system": self._simulator_system,
            "weapon_type":   self._weapon_type,
            "inferred":       self._active_drill.get("inferred", False),
            "source":         "native",
            "metrics_source": "native",
            "replayed":       True,
        }

    def get_last_session_metadata(self) -> dict | None:
        """
        Return metadata captured at the last scenario stop.
        Used by SessionLifecycleCoordinator to assemble the session_record.json.
        Remains populated until clear_last_session_metadata() is called.
        """
        return self._last_session_metadata

    def clear_last_session_metadata(self) -> None:
        """Clear the last session metadata after operator saves or starts a new session."""
        self._last_session_metadata = None
        self._participant_id   = ""
        self._participant_name = ""

    async def handle_live_activate(self, websocket):
        await self._on_live_activate()
        await self._broadcast(self._state_payload())

    async def handle_live_deactivate(self, websocket):
        await self._on_live_deactivate()
        self._active_drill = None
        self._active_scenario = None
        self._waiting_for_custom = ""
        self._native_context = {}
        await self._broadcast(self._state_payload())

