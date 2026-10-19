"""Source-neutral lifecycle envelope for round assessment adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EventKind(str, Enum):
    SCENARIO_START = "scenario_start"
    SCENARIO_STOP = "scenario_stop"
    ROUND_START = "round_start"
    ROUND_COMPLETE = "round_complete"
    ROUND_RESET = "round_reset"
    EVIDENCE = "evidence"


@dataclass(frozen=True)
class AssessmentEvent:
    kind: EventKind
    source: str
    timestamp: float
    external_scenario_id: str
    scenario_name: str
    round_number: int
    raw_name: str


def normalize_custom_event(event, timestamp: float, lifecycle) -> AssessmentEvent:
    kind = EventKind.EVIDENCE
    for candidate in (EventKind.SCENARIO_START, EventKind.SCENARIO_STOP,
                      EventKind.ROUND_START, EventKind.ROUND_COMPLETE,
                      EventKind.ROUND_RESET):
        if event.event_name in lifecycle.event_names(candidate.value):
            kind = candidate
            break
    return AssessmentEvent(
        kind=kind, source="custom", timestamp=timestamp,
        external_scenario_id=event.scenario_id,
        scenario_name=event.scenario_name, round_number=event.round,
        raw_name=event.event_name)
