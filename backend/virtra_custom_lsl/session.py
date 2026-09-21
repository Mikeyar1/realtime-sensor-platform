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

from dataclasses import dataclass, field
from typing import Optional

from .event_decoder import CustomLslEvent

_LIFECYCLE_START = "Scenario Started"
_LIFECYCLE_STOP  = ("Scenario Stopped", "Scenario Destroyed")
_ROUND_START     = "Round Started"
_ROUND_END       = ("Round Completed", "Round Reset")


@dataclass
class RoundRecord:
    """Summary of one completed (or reset) round."""
    round_number: int
    started_at:   float
    ended_at:     Optional[float] = None
    ended_reason: str  = ""              # "completed" | "reset" | "scenario_stopped"
    settings:     dict = field(default_factory=dict)
    metrics:      dict = field(default_factory=dict)


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

    # Public entry point — called for every decoded custom-LSL sample

    def handle_event(self, event: CustomLslEvent, timestamp: float) -> None:
        if event.event_name == _LIFECYCLE_START:
            self._start_session(event)
        elif event.event_name in _LIFECYCLE_STOP:
            self._stop_session(event, timestamp)
        elif event.event_name == _ROUND_START:
            self._start_round(event, timestamp)
        elif event.event_name in _ROUND_END:
            self._end_round(event, timestamp, reset=(event.event_name == "Round Reset"))

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
        self.on_scenario_started(event)

    def _stop_session(self, event: CustomLslEvent, timestamp: float):
        if self._round_open is not None:
            self._end_round(event, timestamp, reset=False, reason="scenario_stopped")
        self.is_active = False
        self.on_scenario_stopped(event)

    # Rounds

    def _start_round(self, event: CustomLslEvent, timestamp: float):
        self.current_round = event.round or (self.current_round + 1)
        self._round_open = RoundRecord(
            round_number = self.current_round,
            started_at   = timestamp,
            settings     = event.data.get("settings", {}),
        )
        self.on_round_started(self.current_round, self._round_open.settings, event.data.get("metrics", {}))

    def _end_round(self, event: CustomLslEvent, timestamp: float, reset: bool, reason: str = ""):
        if self._round_open is None:
            return
        self._round_open.ended_at     = timestamp
        self._round_open.ended_reason = reason or ("reset" if reset else "completed")
        self._round_open.metrics      = event.data
        self.round_history.append(self._round_open)
        self.on_round_completed(self._round_open.round_number, self._round_open.metrics)
        self._round_open = None

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
