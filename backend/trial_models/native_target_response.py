"""
trial_models/native_target_response.py — Real-time PVT trial tracker.

Processes VirTra events into completed PVT trials and broadcasts
running metrics after each trial.

DESIGN NOTES
  Target filtering
    Only targets whose Name matches 'valid_targets' (from TOML) are tracked
    as PVT stimuli. Targets in 'ignore_targets' are silently skipped.
    For LVL1_PVT_FITTS_ASSESSMENT: valid_targets = ["neutral"],
    ignore_targets = ["Ivan", "fixation", "fixation (2)", "fixation (3)"].

  ShotID deduplication
    Each bullet (ShotID) may appear in both "Shot Hit" and "Unscored Hit".
    We only handle "Shot Hit". ShotIDs are marked used immediately so the
    same bullet cannot close more than one trial.

  Grace window after Target Deactivated
    VirTra sends "Target Deactivated" ~18 ms before the corresponding
    "Shot Hit" for the same physical trigger pull. We must not finalize a
    trial as an omission immediately on Deactivated; instead we hold the
    trial in a 'grace' state for grace_window_ms (default 50 ms). If
    "Shot Hit" arrives before the window expires the trial is closed as a
    hit; otherwise it is closed as an omission.

    The grace window is implemented with asyncio.sleep() — no real-time
    threading, just a coroutine scheduled on the event loop.

TRIAL STATE MACHINE
  1. Target Activated           → open trial
  2. Target Activated (again)   → repositioning; update RT origin
  3. Shot Hit (before Deact.)   → close as hit immediately
  4. Target Deactivated         → enter grace window
     a. Shot Hit within window  → close as hit
     b. Window expires          → close as omission

CONFIGURABLE PARAMETERS (scenarios/<id>/native.toml [drills.<id>.native_params])
  valid_targets   list[str]  Names that count as stimuli (case-insensitive).
                             Empty → accept any target not in ignore_targets.
  ignore_targets  list[str]  Names always skipped (Ivan, fixation…).
  grace_window_ms int        ms to wait after Target Deactivated for a late
                             Shot Hit. Default 50 ms.
"""

import asyncio
import math
import re
import statistics
import threading
from dataclasses import dataclass
from typing import Optional

from metrics.pvt import compute_metrics


@dataclass
class _PvtTrialState:
    """Mutable state for one in-progress PVT trial."""
    trial_number:     int
    target_id:        str
    target_name:      str
    activated_at:     float          # RT origin — updated only on a genuine reposition
    first_activated_at: float = 0.0 # RT origin fallback — never overwritten, guards against negative RT
    in_grace:         bool  = False  # True after Target Deactivated, awaiting Shot Hit
    pending_shot_id:  str   = ""     # ShotID if Shot Fired but no Unscored Hit yet
    pending_shot_ts:  float = 0.0    # Shot Fired timestamp (0.0 = not yet fired)
    deactivated_at:   Optional[float] = None  # set when Target Deactivated starts the grace window
    last_distance:    Optional[str] = None    # "Distance" at last Activated — detects a real move
    last_direction:   Optional[str] = None    # "Direction" at last Activated — detects a real move


@dataclass
class CompletedPvtTrial:
    """Immutable record of a finished PVT trial."""
    trial_number: int
    target_id:    str
    target_name:  str
    rt_ms:        Optional[float]   # None for omissions
    result:       str               # "hit" | "miss" | "omission"
    shot_id:      Optional[str]
    activated_at: float
    shot_at:      Optional[float]
    radial_error_cm: Optional[float] = None  # None for miss/omission (no impact point)
    first_activated_at: float = 0.0
    completed_at: float = 0.0


def _rt_from(trial: "_PvtTrialState", shot_ts: float) -> float:
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


def _parse_local_hit_radial_cm(meta: dict) -> Optional[float]:
    """
    Parse the "Local Hit (m)" VirTra field "(x, y, z)" into a radial error in cm.
    x/y are the horizontal/vertical offset from the target's local origin; z is a
    small flat-surface depth residual and is not part of the radial error.
    """
    raw = meta.get("Local Hit (m)")
    if not raw:
        return None
    match = re.match(r"\(\s*([-\d.eE]+)\s*,\s*([-\d.eE]+)\s*,\s*([-\d.eE]+)\s*\)", raw)
    if not match:
        return None
    x, y = float(match.group(1)), float(match.group(2))
    return round(math.hypot(x, y) * 100, 2)


class PvtTracker:
    """
    Real-time tracker for Psychomotor Vigilance Task (PVT) trials.

    One instance is created per session. Call reset(params, targets) at each
    Scenario Started, then feed every VirTra event to on_virtra_event().
    """

    _DEFAULT_VALID_TARGETS:   list = []
    _DEFAULT_IGNORE_TARGETS:  list = ["fixation", "fixation (2)", "fixation (3)", "Ivan"]
    _DEFAULT_GRACE_WINDOW_MS: int  = 50    # VirTra delay is ~18 ms; 50 ms gives safe margin
    _DEFAULT_LAPSE_MULTIPLIER: float = 2.0 # lapse threshold = session median RT × this

    def __init__(self, broadcast_fn, loop: asyncio.AbstractEventLoop):
        self._broadcast_fn = broadcast_fn
        self._loop         = loop

        self._active:    dict[str, _PvtTrialState] = {}
        self._completed: list[CompletedPvtTrial]   = []
        self._trial_counter = 0

        # ShotID deduplication
        self._used_shot_ids:    set[str]           = set()
        self._shot_timestamps:  dict[str, float]   = {}   # shot_id → fired_at timestamp

        # Applied params (defaults until reset() is called)
        self._valid_targets:   list = list(self._DEFAULT_VALID_TARGETS)
        self._ignore_targets:  list = list(self._DEFAULT_IGNORE_TARGETS)
        self._grace_window_s:  float = self._DEFAULT_GRACE_WINDOW_MS / 1000.0
        self._lapse_multiplier: float = self._DEFAULT_LAPSE_MULTIPLIER

        # Thread safety: LSL inlet thread writes; asyncio grace callbacks read
        self._lock = threading.Lock()

    # Public interface

    def reset(self, params: dict | None = None, targets: dict | None = None):
        """
        Call at the start of each scenario.

        params  — from [drills.<id>.native_params] in native.toml
        targets — from [drills.<id>.native_targets] in native.toml
                  May contain 'valid_targets' and/or 'ignore_targets' lists.
                  Values in params take precedence over values in targets.
        """
        t = targets or {}
        p = params  or {}

        self._valid_targets  = [n.lower() for n in p.get("valid_targets",  t.get("valid_targets",  self._DEFAULT_VALID_TARGETS))]
        self._ignore_targets = [n.lower() for n in p.get("ignore_targets", t.get("ignore_targets", self._DEFAULT_IGNORE_TARGETS))]
        self._grace_window_s = p.get("grace_window_ms", self._DEFAULT_GRACE_WINDOW_MS) / 1000.0
        self._lapse_multiplier = p.get("lapse_multiplier", self._DEFAULT_LAPSE_MULTIPLIER)

        with self._lock:
            self._active.clear()
            self._completed.clear()
            self._used_shot_ids.clear()
            self._shot_timestamps.clear()
            self._trial_counter = 0

        print(
            f"[PvtTracker] Reset — valid_targets={self._valid_targets or 'ALL'} "
            f"ignore={self._ignore_targets}  "
            f"grace={self._grace_window_s*1000:.0f}ms"
        )

    def on_virtra_event(self, event_type: str, timestamp: float, metadata_dict: dict):
        """Route one VirTra event to the appropriate handler."""
        with self._lock:
            if event_type == "Target Activated":
                self._on_target_activated(timestamp, metadata_dict)
            elif event_type == "Shot Fired":
                self._on_shot_fired(timestamp, metadata_dict)
            elif event_type in ("Shot Hit", "Unscored Hit"):
                # In the PVT drill, VirTra classifies neutral hits as "Unscored Hit"
                # We treat both the same: close the open trial if the target is tracked.
                # The trial filter (_active lookup) handles discrimination automatically:
                #   - neutral -> trial exists -> accepted as a hit
                #   - fixation / Ivan -> no trial open -> silently ignored
                self._on_shot_hit(timestamp, metadata_dict)
            elif event_type == "Target Deactivated":
                self._on_target_deactivated(timestamp, metadata_dict)
            # Target Removed is for visual cleanup only, no trial logic.

    def get_completed_trials(self) -> list[dict]:
        """Return all completed trials as plain dicts (for serialization)."""
        with self._lock:
            return [
                {
                    "trial_number": t.trial_number,
                    "target_id":    t.target_id,
                    "target_name":  t.target_name,
                    "rt_ms":        round(t.rt_ms, 2) if t.rt_ms is not None else None,
                    "result":       t.result,
                    "shot_id":      t.shot_id,
                    "activated_at": t.activated_at,
                    "first_activated_at": t.first_activated_at,
                    "completed_at": t.completed_at,
                    "shot_at":      t.shot_at,
                    "radial_error_cm": t.radial_error_cm,
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
    ) -> dict:
        """Assemble the session record for metric computation and disk storage."""
        self.finalize(stopped_at)
        trials = self.get_completed_trials()
        duration_s = round(
            max(t["completed_at"] for t in trials) - min(t["first_activated_at"] for t in trials), 2
        ) if trials else None
        return {
            "schema_version": 2,
            "task_type":      "PVT",
            "scenario_name":  scenario_name,
            "session_id":     session_id,
            "started_at":     started_at,
            "stopped_at":     stopped_at,
            "duration_s":     duration_s,
            "lapse_multiplier": self._lapse_multiplier,
            "trials":         trials,
            "biosignals":     biosignals or {},
        }

    # Target filter

    def _is_valid_target(self, target_name: str) -> bool:
        name = target_name.lower().strip()
        if name in self._ignore_targets:
            return False
        if self._valid_targets and not any(name.startswith(v) for v in self._valid_targets):
            return False
        return True

    # Event handlers (called with self._lock held)

    def _on_target_activated(self, ts: float, meta: dict):
        target_id   = meta.get("ID", "")
        target_name = meta.get("Name", meta.get("Target", ""))

        if not self._is_valid_target(target_name):
            print(f"[PvtTracker] Skip Target Activated: {target_name!r} (not a stimulus)")
            return

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
                    print(f"[PvtTracker] Trial {existing.trial_number:3d} | repositioned → RT origin={ts:.3f}")
                existing.deactivated_at = None
                return
            # Previous presentation's grace window already elapsed by event time —
            # this is a new trial, not a reposition. Resolve the stale one first.
            del self._active[target_id]
            self._resolve_now(existing)

        self._trial_counter += 1
        self._active[target_id] = _PvtTrialState(
            trial_number = self._trial_counter,
            target_id    = target_id,
            target_name  = target_name,
            activated_at = ts,
            first_activated_at = ts,
            last_distance  = meta.get("Distance"),
            last_direction = meta.get("Direction"),
        )
        print(f"[PvtTracker] Trial {self._trial_counter:3d} OPENED | target={target_name!r} id={target_id!r}")

    def _on_shot_fired(self, ts: float, meta: dict):
        """
        Record the shot timestamp and attach it to the currently open trial (if any).
        This lets _resolve_after_grace distinguish a miss (fired, didn't connect)
        from an omission (no response at all).
        """
        shot_id = str(meta.get("ShotID", ""))
        if not shot_id:
            return
        self._shot_timestamps[shot_id] = ts

        # Attach to the open trial — in PVT there is typically only one active trial.
        # If multiple are open (edge case), attach to the most recently activated one.
        open_trials = [t for t in self._active.values() if not t.pending_shot_id]
        if open_trials:
            target = max(open_trials, key=lambda t: t.activated_at)
            target.pending_shot_id = shot_id
            target.pending_shot_ts = ts

    def _on_shot_hit(self, ts: float, meta: dict):
        """
        Close the open trial for the target that was hit.
        Works whether the trial is still active or already in the grace window.
        """
        shot_id   = str(meta.get("ShotID", ""))
        target_id = meta.get("TargetID", meta.get("Target", ""))

        # Deduplicate by ShotID
        if shot_id and shot_id in self._used_shot_ids:
            print(f"[PvtTracker] Duplicate ShotID {shot_id!r} — already consumed, skipping")
            return

        trial = self._active.get(target_id)
        if trial is None:
            # Not a tracked stimulus (e.g. hit on fixation cross) — discard
            print(f"[PvtTracker] Shot Hit on untracked target {target_id!r} — ignored")
            return

        # Use Shot Fired timestamp for RT if available (more precise)
        shot_ts = self._shot_timestamps.get(shot_id, ts)
        rt_ms   = _rt_from(trial, shot_ts)
        result  = "hit"

        completed = CompletedPvtTrial(
            trial_number    = trial.trial_number,
            target_id       = trial.target_id,
            target_name     = trial.target_name,
            rt_ms           = rt_ms,
            result          = result,
            shot_id         = shot_id or None,
            activated_at    = trial.activated_at,
            shot_at         = shot_ts,
            radial_error_cm = _parse_local_hit_radial_cm(meta),
            first_activated_at = trial.first_activated_at,
            completed_at = ts,
        )

        if shot_id:
            self._used_shot_ids.add(shot_id)
        del self._active[target_id]
        self._completed.append(completed)
        self._broadcast_update(completed)

    def _on_target_deactivated(self, ts: float, meta: dict):
        """
        Target disappeared. Start grace window — VirTra sends Deactivated ~18 ms
        before the Shot Hit for the same trigger pull, so we cannot declare an
        omission immediately.
        """
        target_id = meta.get("ID", "")
        trial = self._active.get(target_id)
        if trial is None:
            return  # Not a tracked stimulus or already closed

        trial.in_grace = True  # Mark so we know it's pending resolution
        trial.deactivated_at = ts

        # Schedule grace-window resolution on the asyncio loop
        self._loop.call_soon_threadsafe(
            lambda tid=target_id, tnum=trial.trial_number: asyncio.ensure_future(
                self._resolve_after_grace(tid, tnum),
                loop=self._loop,
            )
        )
        print(
            f"[PvtTracker] Trial {trial.trial_number:3d} | Target Deactivated — "
            f"grace {self._grace_window_s*1000:.0f}ms started"
        )

    # Grace window

    def _resolve_now(self, trial: "_PvtTrialState", stopped_at: float | None = None):
        """
        Synchronously resolve a trial as miss/omission (no hit arrived).
        Shared by the wall-clock grace callback and the event-time staleness
        check in _on_target_activated.
        """
        if trial.pending_shot_ts:
            # They pulled the trigger but the bullet didn't connect with the target
            rt_ms   = _rt_from(trial, trial.pending_shot_ts)
            result  = "miss"
            shot_ts = trial.pending_shot_ts
            shot_id = trial.pending_shot_id or None
            print(f"[PvtTracker] Trial {trial.trial_number:3d} | MISS (fired RT={rt_ms:.0f}ms, no hit)")
        else:
            # No trigger pull at all → omission
            rt_ms   = None
            result  = "omission"
            shot_ts = None
            shot_id = None
            print(f"[PvtTracker] Trial {trial.trial_number:3d} | OMISSION (no response)")

        completed = CompletedPvtTrial(
            trial_number = trial.trial_number,
            target_id    = trial.target_id,
            target_name  = trial.target_name,
            rt_ms        = rt_ms,
            result       = result,
            shot_id      = shot_id,
            activated_at = trial.activated_at,
            shot_at      = shot_ts,
            first_activated_at = trial.first_activated_at,
            completed_at = trial.deactivated_at or stopped_at or shot_ts or trial.activated_at,
        )

        self._completed.append(completed)
        self._broadcast_update(completed)

    def finalize(self, stopped_at: float):
        """Score trials still open when the scenario stops."""
        with self._lock:
            pending = list(self._active.values())
            self._active.clear()
            for trial in pending:
                self._resolve_now(trial, stopped_at)

    async def _resolve_after_grace(self, target_id: str, trial_number: int):
        """
        Wait grace_window_s, then close the trial if Shot Hit hasn't already closed it.

        Outcomes:
          - Unscored Hit arrived during grace → trial already deleted, nothing to do.
          - Shot Fired recorded but no Unscored Hit → MISS (responded, didn't connect).
          - No Shot Fired at all                    → OMISSION (no response).
        """
        await asyncio.sleep(self._grace_window_s)

        with self._lock:
            trial = self._active.get(target_id)
            # Already closed by an Unscored Hit during the grace window → nothing to do
            if trial is None or trial.trial_number != trial_number:
                return

            del self._active[target_id]
            self._resolve_now(trial)

    #  Running metrics

    def _compute_running_metrics(self) -> dict:
        """Live running metrics — identical computation to the final summary."""
        trials = [
            {
                "trial_number": t.trial_number,
                "result":       t.result,
                "rt_ms":        t.rt_ms,
                "activated_at": t.activated_at,
                "first_activated_at": t.first_activated_at,
                "completed_at": t.completed_at,
                "shot_at":      t.shot_at,
                "radial_error_cm": t.radial_error_cm,
            }
            for t in self._completed
        ]
        return compute_metrics(trials, lapse_multiplier=self._lapse_multiplier)

    # Broadcast

    def _broadcast_update(self, trial: CompletedPvtTrial):
        metrics = self._compute_running_metrics()
        msg = {
            "type":      "session.trial_update",
            "task_type": "PVT",
            "trial": {
                "trial_number": trial.trial_number,
                "target_name":  trial.target_name,
                "rt_ms":        round(trial.rt_ms, 1) if trial.rt_ms is not None else None,
                "result":       trial.result,
                "shot_id":      trial.shot_id,
            },
            "running_metrics": metrics,
            "all_rts": [
                round(t.rt_ms, 1)
                for t in self._completed
                if t.result in ("hit", "miss") and t.rt_ms is not None
            ],
        }
        asyncio.run_coroutine_threadsafe(self._broadcast_fn(msg), self._loop)
        rt_str = f"{trial.rt_ms:.0f}ms" if trial.rt_ms is not None else "—"
        print(
            f"[PvtTracker] Trial {trial.trial_number:3d} | {trial.result:18s} | "
            f"RT={rt_str:>8s} | "
            f"n={metrics['total_trials']}  hits={metrics['hits']}  "
            f"median={metrics['rt_median_ms']}ms  omissions={metrics['omissions']}"
        )
