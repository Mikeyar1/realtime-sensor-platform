"""
virtra_scenario_orchestrator.py — Real-time VirTra simulation event processor and scenario orchestrator.

Handles live.activate / live.deactivate, parses incoming VirTra LSL events
(Scenario Started, Scenario Stopped, Shot, Target Activated, etc.), maintains
active cognitive attribute context via DrillDefinitionRegistry, delegates trials
to the appropriate tracker (from cognitive_attributes.ATTRIBUTE_REGISTRY),
delegates physiological telemetry to BiosignalRollingMonitor, and triggers
post-scenario metric computation and session storage workflows.

Adding a new cognitive attribute: create its tracker + metrics in
cognitive_attributes/<name>/, register in ATTRIBUTE_REGISTRY, add TOML section.
No changes needed here.
"""

import asyncio
import json
import os
import re
import sys
from typing import Callable, Optional

from .drill_definition_registry import DrillDefinitionRegistry
from .biosignal_rolling_monitor import BiosignalRollingMonitor
from .analysis_service_client import AnalysisServiceClient
from cognitive_attributes import ATTRIBUTE_REGISTRY
from virtra_custom_lsl.event_decoder import decode_custom_lsl_sample
from virtra_custom_lsl.session import CustomLslSessionBase

# Native VirTra machine stream: name "V_300", type "VirTraEvents".
# Kept as a narrow name match — do NOT broaden to a generic "VirTra" substring,
# that would also match the custom stream's name below ("virtra_lsl").
_VIRTRA_NAME_PATTERN = re.compile(r"^V_300$", re.IGNORECASE)
_VIRTRA_TYPE = "VirTraEvents"

# Custom, script-driven VirTra stream: name "virtra_lsl", type "VirTra Events"
# (note the space — distinct from the native type above). See
# validate_data/VirTra_Custom_LSL_Stream_Spec.txt.
_VIRTRA_CUSTOM_TYPE = "VirTra Events"

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
    try:
        pairs = json.loads(metadata_str)
        for pair in pairs:
            if isinstance(pair, list) and len(pair) == 2:
                if pair[0] == field_key:
                    return str(pair[1])
    except (json.JSONDecodeError, TypeError):
        pass

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

        # Active drill state
        self._active_drill: Optional[dict] = None
        self._active_scenario: Optional[str] = None
        self._session_started_at: Optional[float] = None
        self._session_id: str = ""

        # Simulator metadata captured at scenario start
        self._simulator: str = ""
        self._simulator_system: str = ""

        # Weapon info — captured from first Shot Fired event; reset each scenario
        self._weapon_type_seen: bool = False

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
        self._custom_session_id: str = ""

        # Biosignal rolling monitor
        self._biosignals = BiosignalRollingMonitor(broadcast_fn=broadcast_fn, loop=loop)

        # Analysis / metrics client (now calls metrics directly, no HTTP)
        self._analysis_client = AnalysisServiceClient(broadcast_fn=broadcast_fn, loop=loop)

    # ── Sample routing ────────────────────────────────────────────────────────

    def on_sample(self, stream_name: str, stream_type: str, timestamp: float, data: list):
        """Called for every LSL sample from any stream."""
        # 0. Participant info stream — future: LSL stream of type "Participant"
        if stream_type == "Participant":
            self._handle_participant_info_sample(data)
            return

        # 1. Biosignal streams
        if stream_type == "HR":
            self._biosignals.handle_hr_sample(timestamp, data)
            return
        if stream_type == "PPI":
            self._biosignals.handle_ppi_sample(timestamp, data)
            return
        if stream_type == "Gaze":
            self._biosignals.handle_gaze_sample(timestamp, data)
            return
        if _SPEECH_TYPE_PATTERN.search(stream_type) or _SPEECH_TYPE_PATTERN.search(stream_name):
            self._handle_speech_sample(timestamp, data)
            return

        # 2. Custom, script-driven VirTra stream — fully independent lifecycle
        # from the native stream (3, below). Must be checked first: its name
        # ("virtra_lsl") would otherwise be mistaken for the native stream.
        if stream_type == _VIRTRA_CUSTOM_TYPE:
            self._on_custom_lsl_sample(timestamp, data)
            return

        # 3. VirTra stream check
        is_virtra = (
            _VIRTRA_NAME_PATTERN.search(stream_name)
            or stream_type == _VIRTRA_TYPE
        )
        if not is_virtra or len(data) < 1:
            return

        event_type = str(data[0]).strip() if len(data) > 0 else ""
        description = str(data[1]).strip() if len(data) > 1 else ""
        category = str(data[2]).strip() if len(data) > 2 else ""
        metadata_raw = str(data[3]).strip() if len(data) > 3 else ""

        if not event_type:
            return

        print(f"[VirTraScenarioOrchestrator] VirTra ▸ {event_type!r}  |  {description[:80]}")

        # Parse metadata
        metadata_dict: dict = {}
        if metadata_raw:
            try:
                pairs = json.loads(metadata_raw)
                if isinstance(pairs, list):
                    metadata_dict = {p[0]: p[1] for p in pairs if isinstance(p, list) and len(p) == 2}
            except (json.JSONDecodeError, TypeError):
                pass

        # Scenario lifecycle
        if event_type in ("Scenario Started"):
            self._handle_scenario_started(timestamp, event_type, description, metadata_raw)
        elif event_type == "Scenario Stopped":
            self._handle_scenario_stopped(timestamp, metadata_raw)

        # If no registered session is active, drop all further processing
        if self._active_drill is None:
            return

        # Capture weapon type from first Shot Fired of this session
        if event_type == "Shot Fired" and not self._weapon_type_seen:
            weapon_type = metadata_dict.get("WeaponType", "")
            if weapon_type:
                self._weapon_type_seen = True
                weapon_msg = {
                    "type":        "session.weapon_info",
                    "weapon_type": weapon_type,
                }
                asyncio.run_coroutine_threadsafe(self._broadcast(weapon_msg), self._loop)

        # Forward events to the active cognitive attribute tracker.
        # The tracker decides internally which event types it cares about.
        _TRIAL_EVENTS = (
            "Target Activated", "Target Deactivated", "Target Removed",
            "Shot Fired", "Shot Hit", "Unscored Hit", "Shot Miss",
        )
        if self._active_tracker is not None and event_type in _TRIAL_EVENTS:
            self._active_tracker.on_virtra_event(event_type, timestamp, metadata_dict)

        # Broadcast every raw VirTra event to clients unconditionally
        msg = {
            "type":        "session.virtra_event",
            "event_type":  event_type,
            "description": description,
            "category":    category,
            "metadata":    metadata_dict,
            "timestamp":   timestamp,
            "drill_key":   self._active_drill["drill_key"] if self._active_drill else "general",
        }
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    def _on_custom_lsl_sample(self, timestamp: float, data: list):
        """
        Route one sample from the custom, script-driven VirTra LSL stream.

        Fully independent lifecycle from the native VirTra stream — the custom
        stream emits its own "Scenario Started"/"Scenario Stopped" and resolves
        trackers by payload drill_type via [custom_lsl_drills] in the registry.
        """
        event = decode_custom_lsl_sample(data)
        if event is None:
            return

        print(f"[VirTraScenarioOrchestrator] CustomLSL ▸ {event.event_name!r} | {event.event_description[:80]}")

        if event.event_name == "Scenario Started" and self._active_custom_session is None:
            self._handle_custom_lsl_scenario_started(timestamp, event)

        if self._active_custom_session is not None:
            self._active_custom_session.handle_event(event, timestamp)

        msg = {
            "type":        "session.custom_lsl_event",
            "event_name":  event.event_name,
            "event_code":  event.event_code,
            "description": event.event_description,
            "drill_type":  event.drill_type,
            "round":       event.round,
            "state":       event.state,
            "data":        event.data,
            "timestamp":   timestamp,
        }
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

        if event.event_name in ("Scenario Stopped", "Scenario Destroyed"):
            self._handle_custom_lsl_scenario_stopped(timestamp, event)

    def _handle_custom_lsl_scenario_started(self, timestamp: float, event):
        self._registry.reload()
        drill_info = self._registry.resolve_custom_lsl_drill(event.drill_type)
        if drill_info is None:
            return

        task_type = drill_info.get("task_type", "")
        TrackerClass, _ = ATTRIBUTE_REGISTRY.get(task_type, (None, None))
        if TrackerClass is None:
            print(f"[VirTraScenarioOrchestrator] No custom LSL tracker for task_type={task_type!r}")
            return

        self._active_custom_drill = drill_info
        self._active_custom_scenario = event.scenario_name
        self._custom_session_started_at = timestamp
        self._custom_session_id = event.scenario_id
        self._active_custom_session = TrackerClass(broadcast_fn=self._broadcast, loop=self._loop)
        self._active_custom_session.reset(
            params=drill_info.get("params", {}),
            targets=drill_info.get("targets", {}),
        )
        self._biosignals.reset()

        msg = {
            "type":          "session.scenario_started",
            "scenario_name": event.scenario_name,
            "session_id":    event.scenario_id,
            "timestamp":     timestamp,
            "drill_key":     drill_info["drill_key"],
            "drill_label":   drill_info["label"],
            "task_type":     task_type,
            "targets":       drill_info.get("targets", {}),
            "tracked_events": [],
            "simulator":     "VirTra Custom LSL",
            "simulator_system": event.scenario_display_name,
        }
        print(
            f"[VirTraScenarioOrchestrator] > Custom LSL Scenario Started: {event.scenario_name!r} "
            f"-> drill_key={drill_info['drill_key']!r} task_type={task_type!r}"
        )
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    def _handle_custom_lsl_scenario_stopped(self, timestamp: float, event):
        if self._active_custom_session is None or self._active_custom_drill is None:
            return

        drill_info = self._active_custom_drill
        scenario_name = event.scenario_name or self._active_custom_scenario
        session_id = event.scenario_id or self._custom_session_id
        started_at = self._custom_session_started_at or timestamp

        msg = {
            "type":          "session.scenario_stopped",
            "scenario_name": scenario_name,
            "session_id":    session_id,
            "timestamp":     timestamp,
            "drill_key":     drill_info["drill_key"],
        }
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

        biosignals = self._biosignals.snapshot_biosignals()
        session_record = self._active_custom_session.get_session_record(
            scenario_name=scenario_name,
            session_id=session_id,
            started_at=started_at,
            stopped_at=timestamp,
            biosignals=biosignals,
            drill_key=drill_info["drill_key"],
        )

        # Build metadata BEFORE calling run_after_live_scenario so it receives
        # the current session's values (not the previous session's or None).
        self._last_session_metadata = {
            "drill_key":      drill_info["drill_key"],
            "task_type":      drill_info.get("task_type", "GENERAL"),
            "scenario_name":  scenario_name,
            "session_id":     session_id,
            "started_at":     started_at,
            "stopped_at":     timestamp,
            "duration_s":     round(timestamp - started_at, 2),
            "active_streams": list(self._active_streams_at_start),
            "participant_id":   self._participant_id,
            "participant_name": self._participant_name,
            # Physiological snapshot captured at Scenario Stopped
            "biosignals":       biosignals,
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

        print(f"[VirTraScenarioOrchestrator] ■ Custom LSL Scenario Stopped: {scenario_name!r}")
        self._active_custom_session = None
        self._active_custom_drill = None
        self._active_custom_scenario = ""
        self._custom_session_started_at = None
        self._custom_session_id = ""

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

    def _handle_scenario_started(self, timestamp: float, event_type: str, description: str, metadata_raw: str):
        self._registry.reload()
        scenario_name = _parse_metadata_field(metadata_raw, "Scenario")
        if not scenario_name:
            match = re.search(r"Scenario[: ]+(.+)", description)
            scenario_name = match.group(1).strip() if match else description.strip()

        drill_info = self._registry.resolve_drill(scenario_name)

        # Unregistered scenario — drop entirely, no session started
        if drill_info is None:
            return

        already_active = (
            self._active_scenario == scenario_name
            and self._active_drill is not None
        )

        self._active_scenario = scenario_name
        self._active_drill = drill_info

        if not already_active:
            # Instantiate the tracker for this cognitive attribute from the registry
            task_type = drill_info.get("task_type", "")
            TrackerClass, _ = ATTRIBUTE_REGISTRY.get(task_type, (None, None))
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
                # Unimplemented cognitive attribute (e.g. MARKSMANSHIP) — track events only
                self._active_tracker = None
                print(f"[VirTraScenarioOrchestrator] No tracker for task_type={task_type!r} — events captured only")
            self._biosignals.reset()
            self._session_started_at = timestamp
            self._session_id = _parse_metadata_field(metadata_raw, "SessionID") or ""
            self._weapon_type_seen = False

        # Parse simulator context from this event's metadata
        self._simulator        = _parse_metadata_field(metadata_raw, "Simulator") or ""
        self._simulator_system = _parse_metadata_field(metadata_raw, "SimulatorSystem") or ""

        msg = {
            "type":             "session.scenario_started",
            "scenario_name":    scenario_name,
            "session_id":       self._session_id,
            "timestamp":        timestamp,
            "drill_key":        drill_info["drill_key"],
            "drill_label":      drill_info["label"],
            "task_type":        drill_info.get("task_type", "GENERAL"),
            "targets":          drill_info.get("targets", {}),
            "tracked_events":   self._registry.all_tracked_events_for(drill_info["drill_key"]),
            "simulator":        self._simulator,
            "simulator_system": self._simulator_system,
        }

        print(f"[VirTraScenarioOrchestrator] > Scenario Started: {scenario_name!r}"
              f"  -> drill_key={drill_info['drill_key']!r}"
              f"  task_type={drill_info.get('task_type', 'GENERAL')!r}")
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    def _handle_scenario_stopped(self, timestamp: float, metadata_raw: str):
        # If no registered session was ever started, ignore the stop event
        if self._active_drill is None:
            print("[VirTraScenarioOrchestrator] Scenario Stopped received but no active registered session — ignoring")
            return

        scenario_name = _parse_metadata_field(metadata_raw, "Scenario") or self._active_scenario or ""
        session_id = _parse_metadata_field(metadata_raw, "SessionID") or self._session_id

        drill_key_for_analysis = (
            self._active_drill["drill_key"] if self._active_drill else "general"
        )
        task_type_for_analysis = (
            self._active_drill.get("task_type", "GENERAL") if self._active_drill else "GENERAL"
        )

        msg = {
            "type":          "session.scenario_stopped",
            "scenario_name": scenario_name,
            "session_id":    session_id,
            "timestamp":     timestamp,
            "drill_key":     drill_key_for_analysis,
        }

        print(f"[VirTraScenarioOrchestrator] [] Scenario Stopped: {scenario_name!r}"
              f"  -> drill_key={drill_key_for_analysis!r}")
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

        self._active_drill = None
        self._active_scenario = None

        # Take physiological snapshot (full-session averages, not rolling window)
        biosignals = self._biosignals.snapshot_biosignals()

        # Broadcast final session-average biosignals to the frontend so the
        # physio cards update to true session means at scenario end.
        if biosignals:
            final_biosignal_msg: dict = {"type": "session.biosignal_update", "is_final": True}
            if "hr_mean_bpm" in biosignals:
                final_biosignal_msg["hr"] = {
                    "mean":   biosignals["hr_mean_bpm"],
                    "latest": biosignals["hr_mean_bpm"],
                    "n":      biosignals.get("hr_n_samples", 0),
                }
            if "hrv_rmssd_ms" in biosignals:
                final_biosignal_msg["hrv"] = {
                    "rmssd_ms":    biosignals["hrv_rmssd_ms"],
                    "n_intervals": biosignals.get("hrv_n_intervals", 0),
                }
            if "pupil_l_mean_mm" in biosignals and "pupil_r_mean_mm" in biosignals:
                pl = biosignals["pupil_l_mean_mm"]
                pr = biosignals["pupil_r_mean_mm"]
                final_biosignal_msg["pupil"] = {
                    "left_mean_mm":  pl,
                    "right_mean_mm": pr,
                    "mean_mm":       round((pl + pr) / 2, 3),
                    "n":             biosignals.get("pupil_l_n", 0),
                }
            asyncio.run_coroutine_threadsafe(self._broadcast(final_biosignal_msg), self._loop)
            print(f"[VirTraScenarioOrchestrator] Broadcast final biosignal averages: {biosignals}")

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

        # Build metadata BEFORE calling run_after_live_scenario so it receives
        # the current session's values (not the previous session's or None).
        self._last_session_metadata = {
            "drill_key":      drill_key_for_analysis,
            "task_type":      task_type_for_analysis,
            "scenario_name":  scenario_name,
            "session_id":     session_id,
            "started_at":     self._session_started_at or timestamp,
            "stopped_at":     timestamp,
            "duration_s":     round(timestamp - (self._session_started_at or timestamp), 2),
            "active_streams": list(self._active_streams_at_start),
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

    def get_active_drill_state(self) -> dict | None:
        """Return active drill metadata for reconnecting clients."""
        if self._active_drill is None and self._active_custom_drill is not None:
            return {
                "type":           "session.scenario_started",
                "scenario_name":  self._active_custom_scenario or "",
                "session_id":     self._custom_session_id or "",
                "timestamp":      self._custom_session_started_at or 0.0,
                "drill_key":      self._active_custom_drill["drill_key"],
                "drill_label":    self._active_custom_drill["label"],
                "task_type":      self._active_custom_drill.get("task_type", "GENERAL"),
                "targets":        self._active_custom_drill.get("targets", {}),
                "tracked_events": [],
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
            "task_type":      self._active_drill.get("task_type", "GENERAL"),
            "targets":        self._active_drill.get("targets", {}),
            "tracked_events": self._registry.all_tracked_events_for(self._active_drill["drill_key"]),
            "inferred":       self._active_drill.get("inferred", False),
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
        await self._broadcast(self._state_payload())

