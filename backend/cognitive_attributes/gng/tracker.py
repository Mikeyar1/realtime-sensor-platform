"""
cognitive_attributes/gng/tracker.py — Real-time Go/No-Go trial tracker.

Processes VirTra events into completed GNG trials and broadcasts
running metrics (hit rate, FA rate, RT) after each trial.

CONFIGURABLE PARAMETERS (set via reset(params=...) from drill_registry.toml)
    late_shot_window_ms  (default 500) — ms after Target Removed/Deactivated to
                                         still accept a Shot Fired as belonging to that trial.

TARGET CLASSIFICATION
    Targets are classified as go, no_go, or ignore based on their Name field.
    The go/no_go/ignore name lists come from [cognitive_attributes.*.targets] in the TOML
    and are passed to reset() via the targets dict.

TRIAL OUTCOMES
    Go target:
        Shot while active (or late shot within window)  → HIT
        No shot                                         → MISS
    No-Go target:
        Shot while active (or late shot within window)  → FALSE_ALARM
        No shot                                         → CORRECT_REJECTION
    Ignore targets: silently skipped — no trial opened.
"""

import asyncio
import statistics
from dataclasses import dataclass
from typing import Optional


@dataclass
class _GngTrialState:
    """Mutable state for one in-progress GNG trial."""
    trial_number:       int
    target_id:          str
    target_name:        str
    target_type:        str             # "go" | "no_go"
    first_activated_at: float
    last_activated_at:  float           # RT origin
    shot_fired_at:      Optional[float] = None
    result:             str             = "pending"


@dataclass
class CompletedGngTrial:
    """Immutable record of a finished GNG trial."""
    trial_number: int
    target_id:    str
    target_name:  str
    target_type:  str               # "go" | "no_go"
    result:       str               # hit | miss | false_alarm | correct_rejection
    rt_ms:        Optional[float]   # None for misses / correct_rejections
    activated_at: float
    shot_at:      Optional[float]


class GngTracker:
    """
    Real-time tracker for Go/No-Go trials.

    One instance is created per session. Call reset(params, targets) at each
    Scenario Started, then feed every VirTra event to on_virtra_event().
    """

    _DEFAULT_LATE_SHOT_WINDOW_MS = 500

    def __init__(self, broadcast_fn, loop: asyncio.AbstractEventLoop):
        self._broadcast_fn = broadcast_fn
        self._loop         = loop

        self._go_names:    set[str] = set()
        self._no_go_names: set[str] = set()

        self._active:    dict[str, _GngTrialState]  = {}
        self._completed: list[CompletedGngTrial]    = []
        self._trial_counter = 0
        self._last_shot_ts: Optional[float] = None

        # Buffer for the last closed miss/CR — for retroactive late-shot correction
        self._last_omission: Optional[tuple[CompletedGngTrial, float]] = None

        self._late_shot_window_s = self._DEFAULT_LATE_SHOT_WINDOW_MS / 1000.0

    # Public interface

    def reset(self, params: dict | None = None, targets: dict | None = None):
        """
        Call at the start of each scenario.

        params:  from [cognitive_attributes.*.params] in drill_registry.toml
        targets: from [cognitive_attributes.*.targets] — go/no_go/ignore lists
        """
        p = params or {}
        self._late_shot_window_s = p.get("late_shot_window_ms", self._DEFAULT_LATE_SHOT_WINDOW_MS) / 1000.0

        t = targets or {}
        self._go_names    = {n.lower() for n in t.get("go",    [])}
        self._no_go_names = {n.lower() for n in t.get("no_go", [])}
        # ignore list is implicit — anything not in go or no_go is ignored

        self._active.clear()
        self._completed.clear()
        self._trial_counter = 0
        self._last_shot_ts  = None
        self._last_omission = None

    def update_targets(self, go_names: list[str], no_go_names: list[str]):
        """Update go/no-go name sets mid-session (called from orchestrator on scenario start)."""
        self._go_names    = {n.lower() for n in go_names}
        self._no_go_names = {n.lower() for n in no_go_names}

    def on_virtra_event(self, event_type: str, timestamp: float, metadata_dict: dict):
        """Route one VirTra event to the appropriate handler."""
        if event_type == "Target Activated":
            self._on_target_activated(timestamp, metadata_dict)
        elif event_type == "Shot Fired":
            self._on_shot_fired(timestamp, metadata_dict)
        elif event_type == "Shot Hit":
            self._on_shot_result(timestamp, metadata_dict, scored=True)
        elif event_type == "Unscored Hit":
            self._on_shot_result(timestamp, metadata_dict, scored=False)
        elif event_type in ("Target Deactivated", "Target Removed"):
            self._on_target_closed(timestamp, metadata_dict)

    def get_completed_trials(self) -> list[dict]:
        """Return all completed trials as plain dicts (for serialization)."""
        return [
            {
                "trial_number": t.trial_number,
                "target_id":    t.target_id,
                "target_name":  t.target_name,
                "target_type":  t.target_type,
                "result":       t.result,
                "rt_ms":        round(t.rt_ms, 2) if t.rt_ms is not None else None,
                "activated_at": t.activated_at,
                "shot_at":      t.shot_at,
            }
            for t in self._completed
        ]

    def get_session_record(
        self,
        scenario_name: str,
        session_id:    str,
        started_at:    float,
        stopped_at:    float,
        biosignals:    Optional[dict] = None,
        drill_key:     str = "",
    ) -> dict:
        """Assemble the session record for metric computation and disk storage."""
        duration_s = round(stopped_at - started_at, 2) if started_at and stopped_at else None
        return {
            "schema_version": 1,
            "task_type":      "GNG",
            "drill_key":      drill_key,
            "scenario_name":  scenario_name,
            "session_id":     session_id,
            "started_at":     started_at,
            "stopped_at":     stopped_at,
            "duration_s":     duration_s,
            "trials":         self.get_completed_trials(),
            "biosignals":     biosignals or {},
        }

    # Target classification

    def _classify_target(self, name: str) -> Optional[str]:
        """Return 'go', 'no_go', or None (ignore)."""
        nl = name.lower().strip()
        if nl in self._go_names:
            return "go"
        if nl in self._no_go_names:
            return "no_go"
        return None

    # ── Event handlers ────────────────────────────────────────────────────────

    def _on_target_activated(self, ts: float, meta: dict):
        target_id   = meta.get("ID", "")
        target_name = meta.get("Name", meta.get("Target", ""))
        ttype       = self._classify_target(target_name)

        if ttype is None:
            return  # fixation/ignore target

        if target_id in self._active:
            self._active[target_id].last_activated_at = ts
        else:
            self._trial_counter += 1
            self._active[target_id] = _GngTrialState(
                trial_number       = self._trial_counter,
                target_id          = target_id,
                target_name        = target_name,
                target_type        = ttype,
                first_activated_at = ts,
                last_activated_at  = ts,
            )

    def _on_shot_fired(self, ts: float, meta: dict):
        self._last_shot_ts = ts

        # Retroactive correction: Shot Fired arrived just after target closed
        if self._last_omission is not None:
            prev_trial, deactivated_at = self._last_omission
            if (ts - deactivated_at) <= self._late_shot_window_s:
                self._retroactive_correct(prev_trial, ts, deactivated_at)
            self._last_omission = None

    def _on_shot_result(self, ts: float, meta: dict, scored: bool):
        target_id = (
            meta.get("TargetID")
            or meta.get("Target")
            or (next(iter(self._active), None))
        )
        trial = self._active.get(target_id)
        if trial is None:
            return

        shot_ts = self._last_shot_ts if self._last_shot_ts is not None else ts
        rt_ms   = (shot_ts - trial.last_activated_at) * 1000.0 if trial.target_type == "go" else None
        result  = "hit" if trial.target_type == "go" else "false_alarm"

        completed = CompletedGngTrial(
            trial_number = trial.trial_number,
            target_id    = trial.target_id,
            target_name  = trial.target_name,
            target_type  = trial.target_type,
            result       = result,
            rt_ms        = rt_ms,
            activated_at = trial.last_activated_at,
            shot_at      = shot_ts,
        )
        del self._active[target_id]
        self._completed.append(completed)
        self._broadcast_update(completed)

    def _on_target_closed(self, ts: float, meta: dict):
        target_id = meta.get("ID", "") or meta.get("Name", "")
        if not target_id:
            name = meta.get("Name", "")
            for tid, t in self._active.items():
                if t.target_name.lower() == name.lower():
                    target_id = tid
                    break

        trial = self._active.get(target_id)
        if trial is None:
            return

        shot_ts = trial.shot_fired_at
        if shot_ts is None and self._last_shot_ts is not None:
            if self._last_shot_ts >= trial.last_activated_at:
                shot_ts = self._last_shot_ts

        if shot_ts is not None:
            rt_ms  = (shot_ts - trial.last_activated_at) * 1000.0 if trial.target_type == "go" else None
            result = "hit" if trial.target_type == "go" else "false_alarm"
            completed = CompletedGngTrial(
                trial_number = trial.trial_number,
                target_id    = trial.target_id,
                target_name  = trial.target_name,
                target_type  = trial.target_type,
                result       = result,
                rt_ms        = rt_ms,
                activated_at = trial.last_activated_at,
                shot_at      = shot_ts,
            )
            self._last_shot_ts = None
        else:
            result = "miss" if trial.target_type == "go" else "correct_rejection"
            completed = CompletedGngTrial(
                trial_number = trial.trial_number,
                target_id    = trial.target_id,
                target_name  = trial.target_name,
                target_type  = trial.target_type,
                result       = result,
                rt_ms        = None,
                activated_at = trial.last_activated_at,
                shot_at      = None,
            )
            self._last_omission = (completed, ts)

        del self._active[target_id]
        self._completed.append(completed)
        self._broadcast_update(completed)

    def _retroactive_correct(self, old: CompletedGngTrial, shot_ts: float, deactivated_at: float):
        try:
            idx = next(i for i, t in enumerate(self._completed) if t.trial_number == old.trial_number)
        except StopIteration:
            return

        result = "hit" if old.target_type == "go" else "false_alarm"
        rt_ms  = (shot_ts - old.activated_at) * 1000.0 if old.target_type == "go" else None

        corrected = CompletedGngTrial(
            trial_number = old.trial_number,
            target_id    = old.target_id,
            target_name  = old.target_name,
            target_type  = old.target_type,
            result       = result,
            rt_ms        = rt_ms,
            activated_at = old.activated_at,
            shot_at      = shot_ts,
        )
        self._completed[idx] = corrected

        lag_ms = (shot_ts - deactivated_at) * 1000.0
        print(
            f"[GngTracker] Late-shot correction: trial #{old.trial_number} "
            f"{old.result} → {result}  (shot {lag_ms:.0f}ms after deactivation"
            + (f", RT={rt_ms:.0f}ms)" if rt_ms is not None else ")")
        )
        self._broadcast_update(corrected)

    # Running metrics

    def _compute_running_metrics(self) -> dict:
        total_go   = sum(1 for t in self._completed if t.target_type == "go")
        total_nogo = sum(1 for t in self._completed if t.target_type == "no_go")
        hits  = sum(1 for t in self._completed if t.result == "hit")
        misses= sum(1 for t in self._completed if t.result == "miss")
        fas   = sum(1 for t in self._completed if t.result == "false_alarm")
        crs   = sum(1 for t in self._completed if t.result == "correct_rejection")
        rts   = [t.rt_ms for t in self._completed if t.rt_ms is not None]

        return {
            "total_go":           total_go,
            "total_no_go":        total_nogo,
            "hits":               hits,
            "misses":             misses,
            "false_alarms":       fas,
            "correct_rejections": crs,
            "hit_rate_pct":       round(hits  / total_go   * 100, 1) if total_go   else None,
            "miss_rate_pct":      round(misses / total_go   * 100, 1) if total_go   else None,
            "fa_rate_pct":        round(fas   / total_nogo * 100, 1) if total_nogo else None,
            "cr_rate_pct":        round(crs   / total_nogo * 100, 1) if total_nogo else None,
            "mean_rt_ms":         round(statistics.mean(rts),   1) if rts else None,
            "median_rt_ms":       round(statistics.median(rts), 1) if rts else None,
            "sd_rt_ms":           round(statistics.stdev(rts),  1) if len(rts) >= 2 else None,
        }

    # Broadcast

    def _broadcast_update(self, trial: CompletedGngTrial):
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
        label = {"hit": "HIT ✓", "miss": "MISS ✗", "false_alarm": "FALSE ALARM ✗",
                 "correct_rejection": "CR ✓"}.get(trial.result, trial.result)
        rt_label = f"{trial.rt_ms:.0f}ms" if trial.rt_ms is not None else "—"
        print(
            f"[GngTracker] Trial {trial.trial_number:3d} | "
            f"{trial.target_name:<12s} [{trial.target_type:<6s}] | "
            f"{label:<18s} | RT={rt_label:>7s} | "
            f"Hits={metrics['hits']}  Misses={metrics['misses']}  "
            f"FAs={metrics['false_alarms']}  CRs={metrics['correct_rejections']}"
        )

