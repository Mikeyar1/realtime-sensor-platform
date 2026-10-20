"""
virtra_custom_lsl/session.py — Shared scenario/round lifecycle for trackers
driven by the custom VirTra LSL stream.

Every drill fed by this stream shares the same lifecycle shape:
    Scenario Started -> N x (Round Started ... Round Completed | Round Reset)
    -> Scenario Stopped | Scenario Destroyed

This base class owns that bookkeeping so drill-specific trackers
(cognitive_attributes/<name>/tracker.py) only have to implement trial logic
by subclassing CustomLslSessionBase and overriding on_drill_event().

NOTE ON SCENARIO STOPPED
    The scenario script does emit "Scenario Stopped"/"Scenario Destroyed" in
    normal completion (see validate_data/VirTra_Custom_LSL_Stream_Spec.txt).
    A capture that ends without one (e.g. stopped mid-round) simply means the
    operator ended the session early — handle_live_deactivate() is the
    backstop that closes the session in that case.
"""

from dataclasses import asdict, dataclass, field
from typing import Optional
import re
import uuid

from event_decoders.custom_lsl import CustomLslEvent

_LIFECYCLE_START = "Scenario Started"
_LIFECYCLE_STOP  = ("Scenario Stopped", "Scenario Destroyed")
_ROUND_START     = "Round Started"
_ROUND_END       = ("Round Completed", "Round Reset")


def _preset_label(event: CustomLslEvent) -> str:
    """Read a reported label, including the script's nested preset object."""
    data = event.data if isinstance(event.data, dict) else {}
    settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    for source in (data, settings):
        for key in ("preset_label", "preset_name", "preset"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                name = value.get("name") or value.get("label")
                if isinstance(name, str) and name.strip():
                    return name.strip()
    if event.event_name == "Preset Changed":
        match = re.search(r"preset changed to\s+(.+?)\s*$", event.event_description, re.I)
        if match:
            return match.group(1).strip()
    return ""


@dataclass
class RoundRecord:
    """Summary of one completed (or reset) round."""
    round_number: int
    started_at:   float
    ended_at:     Optional[float] = None
    ended_reason: str  = ""              # "completed" | "reset" | "scenario_stopped"
    settings:     dict = field(default_factory=dict)
    metrics:      dict = field(default_factory=dict)
    script_metrics: dict = field(default_factory=dict)
    attempt:      int = 1
    component:    str = ""
    components:   dict = field(default_factory=dict)
    preset_label: str = ""
    preset_mixed: bool = False
    participant_name: str = ""
    round_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    preset_provenance: str = ""


class CustomLslSessionBase:
    """
    Base class for trackers driven by the custom VirTra LSL stream.
    One instance per scenario session (create fresh on each Scenario Started).
    """

    def __init__(self):
        self.scenario_id:   str  = ""
        self.scenario_name: str  = ""
        self.drill_type:    str  = ""
        self.is_active:     bool = False

        self.current_round: int = 0
        self.round_history: list[RoundRecord] = []
        self._round_open:   Optional[RoundRecord] = None
        self.component: str = ""
        self._component_map: dict[str, str] = {}
        self._default_component: str = ""
        self._component_events: dict[str, str] = {}
        self._script_metric_events: set[str] = set()
        self._drill_events: dict[str, set[str]] = {}
        self._component_drill_events: dict[str, dict[str, set[str]]] = {}
        self._pending_preset = ""
        self._default_preset = ""
        self._default_settings: dict = {}
        self._seen_round_starts: set[tuple[int, float]] = set()
        self._lifecycle = {
            "scenario_start": {_LIFECYCLE_START}, "scenario_stop": set(_LIFECYCLE_STOP),
            "round_start": {_ROUND_START}, "round_complete": {"Round Completed"},
            "round_reset": {"Round Reset"},
        }

    def configure_components(self, drill: dict, lifecycle: dict | None = None) -> None:
        if lifecycle:
            self._lifecycle = {key: set(values) for key, values in lifecycle.items()}
        self._default_component = drill.get("default_component", "")
        self._drill_events = {
            key: {str(name).casefold() for name in names}
            for key, names in drill.get("events", {}).items()
        }
        self._component_drill_events = {
            component: {
                key: {str(name).casefold() for name in names}
                for key, names in spec.get("events", {}).items()
            }
            for component, spec in drill.get("components", {}).items()
        }
        self._default_preset = str(drill.get("default_preset_label") or "")
        self._default_settings = dict(drill.get("default_settings") or {})
        self._component_map = {
            str(value).casefold(): name
            for name, spec in drill.get("components", {}).items()
            for value in spec.get("phase_values", [])
        }
        self._component_events = {
            str(value).casefold(): name
            for name, spec in drill.get("components", {}).items()
            for key, values in spec.get("events", {}).items()
            if key in {"start", "component_start", "component_change"}
            for value in values
        }
        self._script_metric_events = {
            str(value).casefold()
            for value in drill.get("events", {}).get("script_metrics", [])
        }
        self._script_metric_events.update(
            str(value).casefold()
            for spec in drill.get("components", {}).values()
            for value in spec.get("events", {}).get("script_metrics", []))

    def event_matches(self, key: str, event_name: str, default: str) -> bool:
        """Use the scenario's event names, or the legacy default if unconfigured."""
        component_events = self._component_drill_events.get(self.component, {})
        if key in component_events:
            names = component_events[key]
        elif key in self._drill_events:
            names = self._drill_events[key]
        else:
            names = {default.casefold()}
        return event_name.casefold() in names

    def rounds_snapshot(self) -> list[dict]:
        rows = [asdict(record) for record in self.round_history]
        if self._round_open is not None:
            rows.append(asdict(self._round_open))
        return rows

    # Public entry point — called for every decoded custom-LSL sample

    def handle_event(self, event: CustomLslEvent, timestamp: float) -> None:
        if event.event_name in self._lifecycle["scenario_start"] and self.is_active:
            return
        if event.event_name in self._lifecycle["scenario_stop"] and not self.is_active:
            return
        if event.event_name in self._lifecycle["round_start"]:
            identity = (event.round, timestamp)
            if identity in self._seen_round_starts:
                return
            self._seen_round_starts.add(identity)
        if event.event_name == "Preset Changed":
            label = _preset_label(event)
            if label:
                if self._round_open is not None:
                    self._round_open.preset_mixed = True
                self._pending_preset = label
        if event.event_name in self._lifecycle["scenario_start"]:
            self._start_session(event)
        elif event.event_name in self._lifecycle["scenario_stop"]:
            self._stop_session(event, timestamp)
        elif event.event_name in self._lifecycle["round_start"]:
            self._start_round(event, timestamp)
        elif event.event_name in (self._lifecycle["round_complete"] |
                                    self._lifecycle["round_reset"]):
            self._end_round(event, timestamp,
                            reset=(event.event_name in self._lifecycle["round_reset"]))

        phase = (event.phase or event.data.get("phase") or "").casefold()
        next_component = (self._component_map.get(phase) or
                          self._component_events.get(event.event_name.casefold()))
        if not next_component and not phase and event.event_name in self._lifecycle["round_start"]:
            next_component = self._default_component
        if next_component and self._round_open is not None:
            if self.component != next_component:
                if self.component in self._round_open.components:
                    self._round_open.components[self.component]["ended_at"] = timestamp
                self.component = next_component
                self._round_open.component = next_component
                self._round_open.components.setdefault(next_component, {
                    "started_at": timestamp, "ended_at": None, "metrics": {},
                })
        if self._round_open is not None and self.component:
            metrics = event.data.get("metrics")
            if not isinstance(metrics, dict) and event.event_name.casefold() in self._script_metric_events:
                metrics = event.data
            if isinstance(metrics, dict):
                self._round_open.components[self.component]["metrics"].update(metrics)

        self.on_drill_event(event)

    # Lifecycle

    def _start_session(self, event: CustomLslEvent):
        self.scenario_id    = event.scenario_id
        self.scenario_name  = event.scenario_name
        self.drill_type     = event.drill_type
        self.is_active      = True
        self.current_round  = 0
        self.round_history  = []
        self._round_open    = None
        self.component = ""
        self._pending_preset = ""
        self._seen_round_starts.clear()
        self.on_scenario_started(event)

    def _stop_session(self, event: CustomLslEvent, timestamp: float):
        if self._round_open is not None:
            self._end_round(event, timestamp, reset=False, reason="incomplete")
        self.is_active = False
        self.on_scenario_stopped(event)

    # Rounds

    def _start_round(self, event: CustomLslEvent, timestamp: float):
        if self._round_open is not None:
            self._end_round(event, timestamp, reset=False, reason="incomplete")
        self.current_round = event.round or (self.current_round + 1)
        attempt = 1 + sum(record.round_number == self.current_round for record in self.round_history)
        self.component = ""
        reported_label = _preset_label(event)
        defaulted = not reported_label and not self._pending_preset
        reported_settings = event.data.get("settings")
        settings = dict(reported_settings) if isinstance(reported_settings, dict) else {}
        if defaulted:
            settings = {**self._default_settings, **settings}
        self._round_open = RoundRecord(
            round_number = self.current_round,
            started_at   = timestamp,
            settings     = settings,
            attempt      = attempt,
            preset_label = reported_label or self._pending_preset or self._default_preset,
            preset_provenance = ("reported" if reported_label else
                                 "inherited" if self._pending_preset else
                                 "defaulted" if self._default_preset else "unavailable"),
        )
        self.on_round_started(self.current_round, self._round_open.settings, event.data.get("metrics", {}))

    def _end_round(self, event: CustomLslEvent, timestamp: float, reset: bool, reason: str = ""):
        if self._round_open is None:
            return
        if not self._round_open.preset_label:
            self._round_open.preset_label = _preset_label(event)
        self._round_open.ended_at     = timestamp
        self._round_open.ended_reason = reason or ("reset" if reset else "completed")
        self._round_open.metrics      = event.data.get("metrics", {})
        self._round_open.script_metrics = dict(self._round_open.metrics)
        if self.component in self._round_open.components:
            self._round_open.components[self.component]["ended_at"] = timestamp
        self.round_history.append(self._round_open)
        self.on_round_completed(self._round_open.round_number, self._round_open.metrics)
        self._round_open = None
        self.component = ""

    # Hooks — override in subclasses as needed

    def on_scenario_started(self, event: CustomLslEvent) -> None:
        pass

    def on_scenario_stopped(self, event: CustomLslEvent) -> None:
        pass

    def on_round_started(self, round_number: int, settings: dict, metrics: dict) -> None:
        pass

    def on_round_completed(self, round_number: int, metrics: dict) -> None:
        pass

    def on_drill_event(self, event: CustomLslEvent) -> None:
        """Required override — drill-specific trial logic (go/no-go, hits, etc.)."""
        raise NotImplementedError
