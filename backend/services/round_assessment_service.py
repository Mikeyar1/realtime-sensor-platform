"""Single application boundary for custom-LSL run and round assessments.

The orchestrator routes source events; this service owns event identity,
durable checkpoints, and the versioned round projection. It deliberately
does not depend on the presentation layer or legacy session persistence.
"""

from __future__ import annotations

from collections import deque

from assessments.catalog import CATEGORIES, DRILLS
from metrics.catalog import assessment_snapshot
from . import round_repository


class RoundAssessmentService:
    def __init__(self, recent_limit: int = 4096):
        self._recent = deque(maxlen=recent_limit)
        self._recent_set: set[tuple] = set()
        self._recent_raw_times: dict[tuple[str, ...], float] = {}
        self._last_terminal_id: str | None = None
        self._selection_by_run: dict[str, dict] = {}

    def accepts(self, timestamp: float, raw: list, *, scenario_id: str,
                is_explicit_start: bool, drill_key: str = "") -> bool:
        raw_key = tuple(str(item) for item in raw)
        identity = (timestamp, raw_key)
        previous = self._recent_raw_times.get(raw_key)
        if identity in self._recent_set or (previous is not None and
                                            abs(timestamp - previous) <= 0.01):
            return False
        # A script can reuse scenario_id for the next attempt (and on replay).
        # Only an explicit start may reopen that terminal identity.
        if drill_key in DRILLS and self._last_terminal_id is not None and not is_explicit_start and (
                (scenario_id and scenario_id == self._last_terminal_id) or
                not scenario_id):
            return False
        if len(self._recent) == self._recent.maxlen:
            evicted = self._recent.popleft()
            self._recent_set.discard(evicted)
            if self._recent_raw_times.get(evicted[1]) == evicted[0]:
                del self._recent_raw_times[evicted[1]]
        self._recent.append(identity)
        self._recent_set.add(identity)
        self._recent_raw_times[raw_key] = timestamp
        if is_explicit_start:
            self._last_terminal_id = None
        return True

    def begin(self, run_id: str, drill_key: str, scenario_name: str,
              external_scenario_id: str, timestamp: float,
              assessment_selection: dict | None = None) -> None:
        binding = DRILLS.get(drill_key)
        if not round_repository.available():
            return
        if assessment_selection:
            self._selection_by_run[run_id] = dict(assessment_selection)
        profile = CATEGORIES[binding.category_id].profile if binding else None
        round_repository.capture_run(
            run_id, drill_key=drill_key,
            category_id=binding.category_id if binding else drill_key,
            profile_id=profile.id if profile else drill_key,
            profile_version=profile.version if profile else 1,
            scenario_name=scenario_name, external_scenario_id=external_scenario_id,
            source="custom", started_at=timestamp)

    def checkpoint(self, run_id: str, drill_key: str, tracker) -> list[dict]:
        if drill_key not in DRILLS or not round_repository.available():
            return []
        trials = tracker.get_completed_trials()
        shots = tracker.get_shots()
        results = []
        for row in tracker.rounds_snapshot():
            selection = self._selection_by_run.get(run_id)
            if selection and selection.get("selected_metric_ids"):
                row["metrics"] = dict(row.get("metrics") or {})
                row["metrics"]["_assessment"] = assessment_snapshot(row["metrics"], selection)
            key = (row["round_number"], row.get("attempt", 1))
            selected_trials = [trial for trial in trials if
                               (trial.get("round_number"), trial.get("round_attempt", 1)) == key]
            selected_shots = [shot for shot in shots if
                              (shot.get("round_number"), shot.get("round_attempt", 1)) == key]
            results.append(round_repository.capture_round(
                run_id, row, selected_trials, selected_shots))
        return results

    def end(self, run_id: str, drill_key: str, tracker, timestamp: float,
            reason: str, external_scenario_id: str,
            source: str = "custom") -> list[dict]:
        results = self.checkpoint(run_id, drill_key, tracker)
        if round_repository.available():
            round_repository.close_run(run_id, timestamp, reason, source)
        self._selection_by_run.pop(run_id, None)
        self._last_terminal_id = external_scenario_id
        return results

    def snapshot(self, run_id: str, drill_key: str) -> dict | None:
        if drill_key not in DRILLS or not round_repository.available():
            return None
        return round_repository.get_run(run_id)
