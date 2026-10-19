"""
cognitive_attributes/multicolor_gng/tracker.py — Custom-LSL-driven Go/No-Go tracker.

Registered under task_type "GNG" — replaces the old native-VirTra-driven GNG
tracker, which had no reliable signal to classify a target as go/no-go (native
Target Activated events carry no such field). This tracker is driven entirely
by the custom "virtra_lsl" stream instead (see
validate_data/VirTra_Custom_LSL_Stream_Spec.txt), which already tags each
engagement with ground-truth classification computed by the scenario script
itself.

TRIAL LIFECYCLE
    Target Appeared   -> open trial (target_id, expected_action -> go/no_go)
    Shot Hit           -> closes the trial the shot's target belongs to
                          (hit = go engaged, false_alarm = no_go engaged)
    Target Timed Out   -> closes an unengaged trial
                          (omission = go withheld, correct_rejection = no_go withheld)
    Shot Missed        -> closes an identified target trial; otherwise a wild shot

Target Appeared supplies the required response for each trial. No-Go shots
are commission errors even when they miss, if the stream identifies the target.

Scenario/round lifecycle bookkeeping (Scenario Started/Stopped, Round
Started/Completed/Reset) is inherited from CustomLslSessionBase — this class
only implements on_drill_event() for trial-level logic.
"""

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from virtra_custom_lsl.session import CustomLslSessionBase
from virtra_custom_lsl.event_decoder import CustomLslEvent
from .metrics import compute_metrics

HIT               = "hit"
MISS              = "miss"
OMISSION          = "omission"
FALSE_ALARM       = "false_alarm"
CORRECT_REJECTION = "correct_rejection"


@dataclass
class _GngTrialState:
    """Mutable state for one in-progress trial."""
    trial_number: int
    target_id:    str
    target_name:  str
    target_type:  str          # "go" | "no_go"
    round_number: int
    round_attempt: int
    activated_at: float        # scenario-time seconds at Target Appeared
    pending_shot_id: str = ""
    pending_shot_at: Optional[float] = None
    ambiguous_shot_ids: set[str] = field(default_factory=set)


@dataclass
class CompletedMulticolorGngTrial:
    """Immutable record of a finished trial."""
    trial_number: int
    target_id:    str
    target_name:  str
    target_type:  str
    round_number: int
    round_attempt: int
    result:       str
    rt_ms:        Optional[float]
    activated_at: float
    shot_at:      Optional[float]
    completed_at: float


class MulticolorGngTracker(CustomLslSessionBase):
    """
    One instance per session — created fresh whenever the custom LSL stream's
    "Scenario Started" resolves to a registered custom_lsl_drills entry with
    task_type "GNG". Fed via handle_event(), not on_virtra_event() — this
    tracker has no native-VirTra-stream code path at all.
    """

    def __init__(self, broadcast_fn, loop: asyncio.AbstractEventLoop):
        super().__init__()
        self._broadcast_fn = broadcast_fn
        self._loop          = loop

        self._active:    dict[str, _GngTrialState]          = {}
        self._completed: list[CompletedMulticolorGngTrial]  = []
        self._trial_counter = 0
        self._wild_shots     = 0   # Shot Missed — background misses
        self._wild_shots_by_round: dict[tuple[int, int], int] = {}

    # Standard tracker interface (same shape as every other cognitive attribute)

    def reset(self, params: dict | None = None, targets: dict | None = None):
        self._active.clear()
        self._completed.clear()
        self._trial_counter = 0
        self._wild_shots = 0
        self._wild_shots_by_round.clear()

    def on_virtra_event(self, event_type: str, timestamp: float, metadata_dict: dict):
        return

    def get_completed_trials(self) -> list[dict]:
        return [self._trial_to_dict(t) for t in self._completed]

    def get_session_record(
        self,
        scenario_name: str,
        session_id:    str,
        started_at:    float,
        stopped_at:    float,
        biosignals:    Optional[dict] = None,
        drill_key:     str = "",
    ) -> dict:
        trials = self.get_completed_trials()
        duration_s = round(max(t["completed_at"] for t in trials) -
                           min(t["activated_at"] for t in trials), 2) if trials else None
        return {
            "schema_version": 2,
            "task_type":      "GNG",
            "drill_key":      drill_key,
            "scenario_name":  scenario_name,
            "session_id":     session_id,
            "started_at":     started_at,
            "stopped_at":     stopped_at,
            "duration_s":     duration_s,
            "wild_shots":     self._wild_shots,
            "wild_shots_by_round": [
                {"round_number": number, "round_attempt": attempt, "count": count}
                for (number, attempt), count in self._wild_shots_by_round.items()
            ],
            "rounds":         self.rounds_snapshot(),
            "trials":         trials,
            "biosignals":     biosignals or {},
        }

    # CustomLslSessionBase hook — called for every event after the base class
    # has already handled scenario/round lifecycle bookkeeping.

    def on_drill_event(self, event: CustomLslEvent) -> None:
        if event.event_name == "Target Appeared":
            self._on_target_appeared(event)
        elif event.event_name == "Shot Fired":
            self._on_shot_fired(event)
        elif event.event_name == "Shot Hit":
            self._on_shot_hit(event)
        elif event.event_name == "Target Timed Out":
            self._on_target_timed_out(event)
        elif event.event_name == "Shot Missed":
            self._on_shot_missed(event)

    def on_scenario_stopped(self, event: CustomLslEvent) -> None:
        for trial in list(self._active.values()):
            self._close(trial, self._timeout_result(trial), None,
                        trial.pending_shot_at, event.scenario_time_s)
        self._active.clear()

    def on_round_completed(self, round_number: int, metrics: dict) -> None:
        if not self.round_history:
            return
        record = self.round_history[-1]
        for trial in list(self._active.values()):
            if trial.round_number != round_number or trial.round_attempt != record.attempt:
                continue
            self._active.pop(trial.target_id, None)
            self._close(trial, self._timeout_result(trial), None,
                        trial.pending_shot_at, trial.activated_at, broadcast=False)
        trials = [self._trial_to_dict(trial) for trial in self._completed
                  if trial.round_number == round_number and trial.round_attempt == record.attempt]
        record.metrics = compute_metrics(
            trials, self._wild_shots_by_round.get((round_number, record.attempt), 0))
        if "gng" in record.components:
            record.components["gng"]["metrics"] = record.metrics

    @staticmethod
    def _timeout_result(trial: _GngTrialState) -> str:
        if trial.pending_shot_at is not None:
            return MISS if trial.target_type == "go" else FALSE_ALARM
        if trial.ambiguous_shot_ids:
            return "unresolved"
        return OMISSION if trial.target_type == "go" else CORRECT_REJECTION

    def _on_shot_fired(self, event: CustomLslEvent):
        shot = event.data.get("shot", event.data)
        shot_id = str(shot.get("shot_id") or f"event:{event.scenario_time_s}")
        fired_at = shot.get("fired_time_since_start_seconds", event.scenario_time_s)
        target_id = shot.get("entity_id") or shot.get("target_id")
        trial = self._active.get(target_id) if target_id else None
        if trial is None and len(self._active) == 1:
            trial = next(iter(self._active.values()))
        if trial is not None and not trial.pending_shot_id:
            trial.pending_shot_id = shot_id
            trial.pending_shot_at = fired_at
        elif trial is None:
            for active in self._active.values():
                active.ambiguous_shot_ids.add(shot_id)

    def _on_shot_missed(self, event: CustomLslEvent):
        shot = event.data.get("shot", event.data)
        shot_id = str(shot.get("shot_id") or "")
        target = shot.get("target", {})
        target_id = shot.get("entity_id") or target.get("target_id") or shot.get("target_id")
        trial = self._active.get(target_id) if target_id else None
        if trial is None and shot_id:
            trial = next((t for t in self._active.values() if t.pending_shot_id == shot_id), None)
        if trial is None and len(self._active) == 1:
            only = next(iter(self._active.values()))
            if only.pending_shot_at is not None:
                trial = only
        if trial is None:
            self._wild_shots += 1
            round_key = (self.current_round, self._round_open.attempt if self._round_open else 1)
            self._wild_shots_by_round[round_key] = self._wild_shots_by_round.get(round_key, 0) + 1
            return
        self._active.pop(trial.target_id, None)
        fired_at = trial.pending_shot_at or shot.get("fired_time_since_start_seconds") or event.scenario_time_s
        result = MISS if trial.target_type == "go" else FALSE_ALARM
        self._close(trial, result, None, fired_at, event.scenario_time_s)

    # Event handlers

    def _on_target_appeared(self, event: CustomLslEvent):
        d = event.data
        target_id = d.get("target_id", "")
        if not target_id:
            return

        target_type = "go" if d.get("expected_action") == "shoot" else "no_go"
        self._trial_counter += 1
        self._active[target_id] = _GngTrialState(
            trial_number = self._trial_counter,
            target_id    = target_id,
            target_name  = d.get("target_name", ""),
            target_type  = target_type,
            round_number = self.current_round,
            round_attempt = self._round_open.attempt if self._round_open else 1,
            activated_at = d.get("spawn_timestamp_seconds", event.scenario_time_s),
        )

    def _on_shot_hit(self, event: CustomLslEvent):
        shot   = event.data.get("shot", event.data)
        shot_id = str(shot.get("shot_id") or "")
        target = shot.get("target", {})
        target_id = shot.get("entity_id") or target.get("target_id") or shot.get("target_id", "")

        trial = self._active.pop(target_id, None)
        if trial is None:
            return  # fixation/background hit — not a tracked target
        if shot_id:
            for active in self._active.values():
                active.ambiguous_shot_ids.discard(shot_id)

        result = HIT if trial.target_type == "go" else FALSE_ALARM
        rt_s   = shot.get("reaction_time_seconds")
        rt_ms  = rt_s * 1000.0 if rt_s is not None else None

        self._close(trial, result, rt_ms, event.scenario_time_s, event.scenario_time_s)

    def _on_target_timed_out(self, event: CustomLslEvent):
        target    = event.data.get("target", event.data)
        target_id = target.get("target_id", "")

        trial = self._active.pop(target_id, None)
        if trial is None:
            return

        result = self._timeout_result(trial)
        self._close(trial, result, None, trial.pending_shot_at, event.scenario_time_s)

    def _close(self, trial: _GngTrialState, result: str, rt_ms: Optional[float], shot_at: Optional[float], completed_at: float, broadcast: bool = True):
        completed = CompletedMulticolorGngTrial(
            trial_number = trial.trial_number,
            target_id    = trial.target_id,
            target_name  = trial.target_name,
            target_type  = trial.target_type,
            round_number = trial.round_number,
            round_attempt = trial.round_attempt,
            result       = result,
            rt_ms        = rt_ms,
            activated_at = trial.activated_at,
            shot_at      = shot_at,
            completed_at = completed_at,
        )
        self._completed.append(completed)
        if broadcast:
            self._broadcast_update(completed)

    @staticmethod
    def _trial_to_dict(t: CompletedMulticolorGngTrial) -> dict:
        return {
            "trial_number": t.trial_number,
            "target_id":    t.target_id,
            "target_name":  t.target_name,
            "target_type":  t.target_type,
            "round_number": t.round_number,
            "round_attempt": t.round_attempt,
            "component":    "gng",
            "result":       t.result,
            "rt_ms":        round(t.rt_ms, 2) if t.rt_ms is not None else None,
            "activated_at": t.activated_at,
            "shot_at":      t.shot_at,
            "completed_at": t.completed_at,
        }

    # Running metrics (ms-based — consumed live by the frontend trial_update handler)

    def _compute_running_metrics(self) -> dict:
        current = [t for t in self.get_completed_trials()
                   if t["round_number"] == self.current_round
                   and t["round_attempt"] == (self._round_open.attempt if self._round_open else 1)]
        attempt = self._round_open.attempt if self._round_open else 1
        return {**compute_metrics(current,
                                  self._wild_shots_by_round.get((self.current_round, attempt), 0)),
                "round": self.current_round}

    # Broadcast

    def _broadcast_update(self, trial: CompletedMulticolorGngTrial):
        metrics = self._compute_running_metrics()
        msg = {
            "type":      "session.trial_update",
            "task_type": "GNG",
            "trial": {
                "trial_number": trial.trial_number,
                "target_name":  trial.target_name,
                "target_type":  trial.target_type,
                "result":       trial.result,
                "rt_ms":        round(trial.rt_ms, 1) if trial.rt_ms is not None else None,
            },
            "running_metrics": metrics,
        }
        asyncio.run_coroutine_threadsafe(self._broadcast_fn(msg), self._loop)

        rt_label = f"{trial.rt_ms:.0f}ms" if trial.rt_ms is not None else "—"
        print(
            f"[MulticolorGngTracker] Trial {trial.trial_number:3d} | round={trial.round_number} | "
            f"{trial.target_name:<12s} [{trial.target_type:<6s}] | {trial.result:<18s} | RT={rt_label:>8s} | "
            f"hits={metrics['hits']} miss={metrics['misses']} FA={metrics['false_alarms']} CR={metrics['correct_rejections']}"
        )
