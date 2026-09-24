"""
cognitive_attributes/discrimination/tracker.py — Two-phase discrimination tracker.

Handles Drill 3A (Single-Target Discrimination) and Drill 3B (dual-lane).
Both drills run a PVT phase first, then a threat/non-threat discrimination phase.

GROUNDED IN REAL DATA
  Verified against validate_data/V_300_VirTraEvents_drill3A09152026.csv and the
  "Drill 3B" segment of data/Exercise9_007b_27May2026/.../V_300_VirTraEvents_*.csv.

  Engagement signal
    Discrimination targets are hit with "Unscored Hit" (Score=0) — VirTra never
    emits "Shot Hit" for them. "Shot Hit" only appears for Ivan/neutral/fixation
    in the PVT phase. Both event types are therefore accepted as engagements;
    the trial lookup decides whether the impact is relevant.

  Phase routing (no explicit phase boundary needed)
    Phase is implied by the target name, which is more robust than time-slicing:
      *_threat / *_nonthreat  → discrimination trial
      neutral (pvt_targets)   → PVT trial
      Ivan, fixation*         → ignored (start trigger / fixation crosses)
    Setup noise (the mass "Target Deactivated" burst VirTra emits right after
    Scenario Started) is filtered naturally: those targets have no open trial.

  Concurrency (Drill 3B)
    Up to 2 discrimination targets are active simultaneously, in a staggered
    overlap. Trials are keyed by TargetID and every hit event names its own
    Target/TargetID, so attribution is unambiguous even with both lanes live.

TRIAL OUTCOMES
  PVT phase            hit | miss | omission          (same semantics as PvtTracker)
  Discrimination phase
    threat    engaged  → hit                 (correct engagement)
    threat    fired, missed → miss
    threat    withheld → omission            (no shooting response)
    nonthreat engaged  → false_alarm         (shot a civilian — critical error)
    nonthreat withheld → correct_rejection   (correct restraint)

CONFIGURABLE PARAMETERS ([cognitive_attributes.discrimination.params])
  grace_window_ms  int    ms to wait after Target Deactivated for a late impact.
  lapse_multiplier float  PVT-phase lapse threshold = median RT × this.
"""

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Optional

from .metrics import compute_metrics

# Trial result constants
HIT               = "hit"
MISS              = "miss"
OMISSION          = "omission"
FALSE_ALARM       = "false_alarm"
CORRECT_REJECTION = "correct_rejection"

PHASE_PVT  = "pvt"
PHASE_DISC = "discrimination"


def classify_target(name: str) -> Optional[str]:
    """Return "threat" / "nonthreat" for discrimination targets, else None."""
    n = (name or "").lower().strip()
    if "_nonthreat" in n:
        return "nonthreat"
    if "_threat" in n:
        return "threat"
    return None


@dataclass
class _TrialState:
    """Mutable state for one in-progress trial."""
    trial_number:    int
    target_id:       str
    target_name:     str
    phase:           str
    target_class:    str            # "threat" | "nonthreat" | "neutral"
    activated_at:    float          # RT origin — updated on repositioning
    pending_shot_id: str   = ""
    pending_shot_ts: float = 0.0
    ambiguous_shot_ids: set[str] = field(default_factory=set)
    generation:      int   = 0      # bumped on reposition to void a pending grace callback
    deactivated_at:  Optional[float] = None  # set when Target Deactivated starts the grace window
    first_activated_at: Optional[float] = None  # RT origin fallback — never overwritten, guards against negative RT
    last_distance:   Optional[str] = None    # "Distance" at last Activated — detects a real move
    last_direction:  Optional[str] = None    # "Direction" at last Activated — detects a real move



@dataclass
class CompletedDiscriminationTrial:
    """Immutable record of a finished trial."""
    trial_number:    int
    target_id:       str
    target_name:     str
    phase:           str
    target_class:    str
    rt_ms:           Optional[float]
    result:          str
    shot_id:         Optional[str]
    activated_at:    float
    shot_at:         Optional[float]
    first_activated_at: float
    completed_at:    float


def _rt_from(trial: "_TrialState", shot_ts: float) -> float:
    """
    RT can never be negative — a shot cannot precede the stimulus it responded to.
    A negative/zero result here means a same-ID reposition event was processed
    out of true chronological order relative to its own Shot Fired (VirTra
    occasionally delivers events slightly out of order), corrupting the RT
    origin. Fall back to the trial's original activation in that case.
    """
    rt_ms = (shot_ts - trial.activated_at) * 1000.0
    if rt_ms <= 0 and trial.first_activated_at:
        fallback_rt_ms = (shot_ts - trial.first_activated_at) * 1000.0
        if fallback_rt_ms > 0:
            return fallback_rt_ms
    return rt_ms


class DiscriminationTracker:
    """
    Real-time tracker for two-phase PVT → threat/non-threat discrimination drills.

    One instance per session. Call reset(params, targets) at Scenario Started,
    then feed every VirTra event to on_virtra_event().
    """

    _DEFAULT_IGNORE_TARGETS:   list  = ["ivan", "fixation", "fixation (2)", "fixation (3)"]
    _DEFAULT_PVT_TARGETS:      list  = ["neutral"]
    _DEFAULT_GRACE_WINDOW_MS:  int   = 50
    _DEFAULT_LAPSE_MULTIPLIER: float = 2.0

    def __init__(self, broadcast_fn, loop: asyncio.AbstractEventLoop):
        self._broadcast_fn = broadcast_fn
        self._loop         = loop

        self._active:    dict[str, _TrialState]                 = {}
        self._completed: list[CompletedDiscriminationTrial]      = []
        self._trial_counter = 0

        self._used_shot_ids:   set[str]         = set()
        self._shot_timestamps: dict[str, float] = {}
        self._wild_shots: int = 0   # Shot Miss — trigger pulls that struck nothing

        self._ignore_targets:   list  = list(self._DEFAULT_IGNORE_TARGETS)
        self._pvt_targets:      list  = list(self._DEFAULT_PVT_TARGETS)
        self._grace_window_s:   float = self._DEFAULT_GRACE_WINDOW_MS / 1000.0
        self._lapse_multiplier: float = self._DEFAULT_LAPSE_MULTIPLIER

        # LSL inlet thread writes; asyncio grace callbacks read
        self._lock = threading.Lock()

    # Public interface

    def reset(self, params: dict | None = None, targets: dict | None = None):
        """Call at the start of each scenario."""
        t = targets or {}
        p = params  or {}

        self._ignore_targets = [n.lower() for n in p.get(
            "ignore_targets", t.get("ignore_targets", self._DEFAULT_IGNORE_TARGETS))]
        self._pvt_targets = [n.lower() for n in p.get(
            "pvt_targets", t.get("pvt_targets", self._DEFAULT_PVT_TARGETS))]
        self._grace_window_s   = p.get("grace_window_ms", self._DEFAULT_GRACE_WINDOW_MS) / 1000.0
        self._lapse_multiplier = p.get("lapse_multiplier", self._DEFAULT_LAPSE_MULTIPLIER)

        with self._lock:
            self._active.clear()
            self._completed.clear()
            self._used_shot_ids.clear()
            self._shot_timestamps.clear()
            self._trial_counter = 0
            self._wild_shots    = 0

        print(
            f"[DiscriminationTracker] Reset — pvt_targets={self._pvt_targets} "
            f"ignore={self._ignore_targets} grace={self._grace_window_s*1000:.0f}ms"
        )

    def on_virtra_event(self, event_type: str, timestamp: float, metadata_dict: dict):
        """Route one VirTra event to the appropriate handler."""
        with self._lock:
            if event_type == "Target Activated":
                self._on_target_activated(timestamp, metadata_dict)
            elif event_type == "Shot Fired":
                self._on_shot_fired(timestamp, metadata_dict)
            elif event_type in ("Unscored Hit", "Shot Hit"):
                # Discrimination targets only ever produce "Unscored Hit" (Score=0);
                # PVT-phase targets produce both. Trial lookup filters the rest.
                self._on_impact(timestamp, metadata_dict)
            elif event_type == "Shot Miss":
                self._wild_shots += 1
                shot_id = str(metadata_dict.get("ShotID", ""))
                target_id = metadata_dict.get("TargetID")
                trial = self._active.get(target_id) if target_id else None
                if trial and shot_id and not trial.pending_shot_id:
                    trial.pending_shot_id = shot_id
                    trial.pending_shot_ts = self._shot_timestamps.get(shot_id, timestamp)
            elif event_type == "Target Deactivated":
                self._on_target_deactivated(timestamp, metadata_dict)

    def get_completed_trials(self) -> list[dict]:
        """Return all completed trials as plain dicts (for serialization)."""
        with self._lock:
            return [self._trial_to_dict(t) for t in self._completed]

    def get_session_record(
        self,
        scenario_name: str,
        session_id:    str,
        started_at:    float,
        stopped_at:    float,
        biosignals:    Optional[dict] = None,
    ) -> dict:
        """Assemble the session record for metric computation and disk storage."""
        self.finalize(stopped_at)
        trials = self.get_completed_trials()
        duration_s = round(
            max(t["completed_at"] for t in trials) - min(t["first_activated_at"] for t in trials), 2
        ) if trials else None
        return {
            "schema_version":   2,
            "task_type":        "DISCRIMINATION",
            "scenario_name":    scenario_name,
            "session_id":       session_id,
            "started_at":       started_at,
            "stopped_at":       stopped_at,
            "duration_s":       duration_s,
            "lapse_multiplier": self._lapse_multiplier,
            "wild_shots":       self._wild_shots,
            "trials":           trials,
            "biosignals":       biosignals or {},
        }

    # Target routing

    def _resolve_phase(self, target_name: str):
        """Return (phase, target_class) or None if the target is not tracked."""
        name = (target_name or "").lower().strip()
        if not name or name in self._ignore_targets:
            return None

        target_class = classify_target(name)
        if target_class:
            return PHASE_DISC, target_class

        if any(name.startswith(v) for v in self._pvt_targets):
            return PHASE_PVT, "neutral"

        return None

    # Event handlers (called with self._lock held)

    def _on_target_activated(self, ts: float, meta: dict):
        target_id   = meta.get("ID", "")
        target_name = meta.get("Name", meta.get("Target", ""))

        resolved = self._resolve_phase(target_name)
        if resolved is None:
            return
        phase, target_class = resolved

        if target_id in self._active:
            existing = self._active[target_id]
            stale = (
                existing.deactivated_at is not None
                and (ts - existing.deactivated_at) > self._grace_window_s
            )
            if not stale:
                # VirTra re-announces the same placement (Distance/Direction unchanged)
                # far more often than a target truly repositions — only move the RT
                # origin when the position actually changed.
                moved = (
                    meta.get("Distance") != existing.last_distance
                    or meta.get("Direction") != existing.last_direction
                )
                if moved:
                    existing.activated_at   = ts
                    existing.last_distance  = meta.get("Distance")
                    existing.last_direction = meta.get("Direction")
                existing.deactivated_at = None
                existing.generation     += 1
                return
            # The previous presentation's grace window has already elapsed (by event
            # time, not wall-clock time) — this activation is a genuinely new trial,
            # not a reposition. Resolve the stale trial now instead of silently
            # merging it away (that merge used to erase misses/false-alarms and
            # misattribute their outcome to whatever this new presentation becomes).
            del self._active[target_id]
            self._resolve_now(existing)

        self._trial_counter += 1
        self._active[target_id] = _TrialState(
            trial_number = self._trial_counter,
            target_id    = target_id,
            target_name  = target_name,
            phase        = phase,
            target_class = target_class,
            activated_at = ts,
            first_activated_at = ts,
            last_distance  = meta.get("Distance"),
            last_direction = meta.get("Direction"),
        )
        print(
            f"[DiscriminationTracker] Trial {self._trial_counter:3d} OPENED | "
            f"{phase}/{target_class} | {target_name!r}"
        )

    def _on_shot_fired(self, ts: float, meta: dict):
        """Record the trigger-pull timestamp so RT can use it instead of the impact time."""
        shot_id = str(meta.get("ShotID") or f"event:{ts}")
        self._shot_timestamps[shot_id] = ts

        # Attribute only when the event identifies a tracked target or one
        # trial is active. Overlapping targets make an unlabelled shot ambiguous.
        target = self._active.get(meta.get("TargetID")) if meta.get("TargetID") else None
        if target is None and len(self._active) == 1:
            target = next(iter(self._active.values()))
        if target is not None and not target.pending_shot_id:
            target.pending_shot_id = shot_id
            target.pending_shot_ts = ts
        elif target is None:
            for active in self._active.values():
                active.ambiguous_shot_ids.add(shot_id)

    def _on_impact(self, ts: float, meta: dict):
        """Close the trial belonging to the target that was struck."""
        shot_id   = str(meta.get("ShotID", ""))
        target_id = meta.get("TargetID", meta.get("ID", ""))

        if shot_id and shot_id in self._used_shot_ids:
            return

        trial = self._active.get(target_id)
        if trial is None:
            return  # fixation cross, Ivan, or an already-closed trial
        if shot_id:
            for active in self._active.values():
                active.ambiguous_shot_ids.discard(shot_id)

        shot_ts = self._shot_timestamps.get(shot_id, ts)
        rt_ms   = _rt_from(trial, shot_ts)

        if trial.phase == PHASE_DISC:
            result = HIT if trial.target_class == "threat" else FALSE_ALARM
        else:
            result = HIT

        if shot_id:
            self._used_shot_ids.add(shot_id)
        del self._active[target_id]
        self._close(trial, rt_ms, result, shot_id or None, shot_ts, ts)

    def _on_target_deactivated(self, ts: float, meta: dict):
        """Start the grace window — a late impact may still arrive for this trigger pull."""
        target_id = meta.get("ID", "")
        trial = self._active.get(target_id)
        if trial is None:
            return
        trial.deactivated_at = ts

        self._loop.call_soon_threadsafe(
            lambda tid=target_id, tnum=trial.trial_number, gen=trial.generation:
                asyncio.ensure_future(
                    self._resolve_after_grace(tid, tnum, gen), loop=self._loop,
                )
        )

    # Grace window

    def _resolve_now(self, trial: "_TrialState", stopped_at: float | None = None):
        """
        Synchronously resolve a trial as a non-engagement (no impact arrived).
        Shared by the wall-clock grace callback and the event-time staleness
        check in _on_target_activated (the latter fires when a same-ID target
        reactivates after the grace window has already elapsed by event time,
        which the wall-clock timer may not have caught up to yet).
        """
        if trial.ambiguous_shot_ids and not trial.pending_shot_ts:
            result = "unresolved"
            rt_ms = None
            shot_ts = None
            shot_id = None
        elif trial.phase == PHASE_DISC:
            if trial.pending_shot_ts:
                result = MISS if trial.target_class == "threat" else FALSE_ALARM
                shot_ts = trial.pending_shot_ts
                shot_id = trial.pending_shot_id or None
                rt_ms = _rt_from(trial, shot_ts)
            else:
                result = OMISSION if trial.target_class == "threat" else CORRECT_REJECTION
                rt_ms = None
                shot_ts = None
                shot_id = None
        elif trial.pending_shot_ts:
            result  = MISS          # fired, round did not connect
            rt_ms   = _rt_from(trial, trial.pending_shot_ts)
            shot_ts = trial.pending_shot_ts
            shot_id = trial.pending_shot_id or None
        else:
            result  = OMISSION      # no trigger pull at all
            rt_ms   = None
            shot_ts = None
            shot_id = None

        self._close(trial, rt_ms, result, shot_id, shot_ts,
                    trial.deactivated_at or stopped_at or shot_ts or trial.activated_at)

    def finalize(self, stopped_at: float):
        """Score remaining valid trials at session stop."""
        with self._lock:
            pending = list(self._active.values())
            self._active.clear()
            for trial in pending:
                self._resolve_now(trial, stopped_at)

    async def _resolve_after_grace(self, target_id: str, trial_number: int, generation: int):
        """Close the trial as a non-engagement if no impact landed during the window."""
        await asyncio.sleep(self._grace_window_s)

        with self._lock:
            trial = self._active.get(target_id)
            if trial is None or trial.trial_number != trial_number:
                return  # already closed by an impact (or resolved by the staleness check)
            if trial.generation != generation:
                return  # target reappeared during the window — a later grace will resolve it

            del self._active[target_id]
            self._resolve_now(trial)

    # Trial completion

    def _close(self, trial: _TrialState, rt_ms, result, shot_id, shot_at, completed_at):
        completed = CompletedDiscriminationTrial(
            trial_number    = trial.trial_number,
            target_id       = trial.target_id,
            target_name     = trial.target_name,
            phase           = trial.phase,
            target_class    = trial.target_class,
            rt_ms           = rt_ms,
            result          = result,
            shot_id         = shot_id,
            activated_at    = trial.activated_at,
            shot_at         = shot_at,
            first_activated_at = trial.first_activated_at or trial.activated_at,
            completed_at    = completed_at,
        )
        self._completed.append(completed)
        self._broadcast_update(completed)

    @staticmethod
    def _trial_to_dict(t: CompletedDiscriminationTrial) -> dict:
        return {
            "trial_number":    t.trial_number,
            "target_id":       t.target_id,
            "target_name":     t.target_name,
            "phase":           t.phase,
            "target_class":    t.target_class,
            "rt_ms":           round(t.rt_ms, 2) if t.rt_ms is not None else None,
            "result":          t.result,
            "shot_id":         t.shot_id,
            "activated_at":    t.activated_at,
            "shot_at":         t.shot_at,
            "first_activated_at": t.first_activated_at,
            "completed_at":    t.completed_at,
        }

    # Running metrics

    def _compute_running_metrics(self) -> dict:
        trials = [self._trial_to_dict(t) for t in self._completed]
        return compute_metrics(
            trials,
            lapse_multiplier = self._lapse_multiplier,
            wild_shots       = self._wild_shots,
        )

    # Broadcast

    def _broadcast_update(self, trial: CompletedDiscriminationTrial):
        metrics = self._compute_running_metrics()
        msg = {
            "type":      "session.trial_update",
            "task_type": "DISCRIMINATION",
            "trial": {
                "trial_number": trial.trial_number,
                "target_name":  trial.target_name,
                "phase":        trial.phase,
                "target_class": trial.target_class,
                "rt_ms":        round(trial.rt_ms, 1) if trial.rt_ms is not None else None,
                "result":       trial.result,
                "shot_id":      trial.shot_id,
            },
            "running_metrics": metrics,
        }
        asyncio.run_coroutine_threadsafe(self._broadcast_fn(msg), self._loop)

        rt_str = f"{trial.rt_ms:.0f}ms" if trial.rt_ms is not None else "—"
        d = metrics["discrimination"]
        print(
            f"[DiscriminationTracker] Trial {trial.trial_number:3d} | "
            f"{trial.phase:14s} | {trial.result:18s} | RT={rt_str:>8s} | "
            f"hits={d['hits']} miss={d['misses']} FA={d['false_alarms']} CR={d['correct_rejections']}"
        )
