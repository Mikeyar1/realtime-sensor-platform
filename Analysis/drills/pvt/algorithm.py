"""
drills/pvt/algorithm.py — Psychomotor Vigilance Task trial detection.

Algorithm (VirTra native events only, no PVT controller stream required):
─────────────────────────────────────────────────────────────────────────
Each trial corresponds to one appearance of the "8'' Circle Target":

  Target Activated   → stimulus onset = t0
  [Shot Fired]       → trainee response (may occur 0, 1, or many times)
  [Shot Hit]         → scored hit (result = "Hit")
  [Unscored Hit]     → hit after window closed (score = 0)
  [Shot Miss]        → fired but missed
  Target Deactivated → stimulus offset

Outcome classification
──────────────────────
  HIT               — shot fired within window AND result is Hit or Unscored Hit
  MISS              — shot fired within window but result is Shot Miss
  LAPSE             — no shot fired before Target Deactivated (no-response)
  ANTICIPATION      — shot fired before t0 (before target appeared)

Reaction time
─────────────
  RT = t(first Shot Fired after t0) − t0
       None for LAPSE and ANTICIPATION
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from drills.virtra_common import get_all_virtra_events


DRILL_KEY = "pvt"

TERMINOLOGY = {
    "drill_name":    "Psychomotor Vigilance Task",
    "drill_short":   "PVT",
    "anchor_label":  "Target Appeared",
    "hit_label":     "Hit",
    "miss_label":    "Miss",
    "lapse_label":   "Lapse (No Response)",
    "fa_label":      "Anticipation Error",
    "rt_label":      "Reaction Time",
    "trial_label":   "Trial",
    "event_desc":    "Target circle appeared on screen",
}

# ── Config ────────────────────────────────────────────────────────────────────

#: Expected target name in VirTra events (substring match, case-insensitive)
TARGET_NAME = "8'' circle target"

#: Outcome string constants
HIT          = "HIT"
MISS         = "MISS"
LAPSE        = "LAPSE"
ANTICIPATION = "ANTICIPATION_ERROR"

#: Maximum seconds to look ahead for Shot/Deactivated after Target Activated
LOOKAHEAD_S  = 8.0

#: Relevant event types we need from the DB
_NEEDED = {
    "Scenario Started",
    "Target Activated",
    "Target Deactivated",
    "Shot Fired",
    "Shot Hit",
    "Unscored Hit",
    "Shot Miss",
}


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class Trial:
    index:      int
    t0:         float                     # Target Activated timestamp
    t_end:      Optional[float] = None    # Target Deactivated timestamp
    outcome:    str             = LAPSE
    rt_s:       Optional[float] = None
    n_shots:    int             = 0
    screen:     Optional[int]   = None    # VirTra screen (1/2/3) of first shot
    distance_m: Optional[float] = None   # target distance from meta
    direction:  Optional[float] = None   # target direction (deg) from meta


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_trials(db_path: str) -> list[dict]:
    """
    Parse a LabReplay .db file and return all PVT trials detected via
    Target Activated / Target Deactivated pairs for the circle target.

    Returns a list of trial dicts sorted by t0.
    """
    events = get_all_virtra_events(db_path, event_filter=_NEEDED)
    if not events:
        return []

    # ── Detect scenario start ─────────────────────────────────────────────────
    t_scenario_start: Optional[float] = None
    for ts, etype, desc, _ in events:
        if etype == "Scenario Started" and "PVT" in desc:
            t_scenario_start = ts
            break

    # ── Build (t_activate, t_deactivate, meta) windows ───────────────────────
    open_activations: list[float] = []   # stack of Target Activated timestamps
    windows: list[tuple[float, Optional[float], dict]] = []

    for ts, etype, desc, meta in events:
        if etype == "Target Activated" and TARGET_NAME in desc.lower():
            open_activations.append(ts)
        elif etype == "Target Deactivated" and TARGET_NAME in desc.lower():
            if open_activations:
                t_act = open_activations.pop(0)
                windows.append((t_act, ts, meta))

    # Flush any still-open windows (no deactivation seen)
    for t_act in open_activations:
        windows.append((t_act, None, {}))

    # ── Build shot/result event list for fast lookups ─────────────────────────
    shot_events = [
        (ts, etype, meta)
        for ts, etype, _, meta in events
        if etype in ("Shot Fired", "Shot Hit", "Unscored Hit", "Shot Miss")
    ]

    # ── Classify each window ──────────────────────────────────────────────────
    trials: list[Trial] = []

    for t0, t_end, act_meta in windows:
        # Only count windows that begin after scenario start
        if t_scenario_start is not None and t0 < t_scenario_start - 1.0:
            continue

        # Effective end of window
        effective_end = t_end if t_end is not None else (t0 + LOOKAHEAD_S)

        trial = Trial(
            index       = 0,
            t0          = t0,
            t_end       = t_end,
            distance_m  = _to_float(act_meta.get("Distance (m)") or act_meta.get("Distance")),
            direction   = _to_float(act_meta.get("Direction")),
        )

        # Collect shot events within (t0 − 0.2, effective_end + 0.3)
        shots_in_window = [
            (ts, etype, meta)
            for ts, etype, meta in shot_events
            if t0 - 0.2 <= ts <= effective_end + 0.3
        ]

        fired = [(ts, meta) for ts, et, meta in shots_in_window if et == "Shot Fired"]
        hits  = [ts for ts, et, _ in shots_in_window if et in ("Shot Hit", "Unscored Hit")]
        misses= [ts for ts, et, _ in shots_in_window if et == "Shot Miss"]

        if not fired:
            # No shot at all → LAPSE
            trial.outcome = LAPSE
            trial.n_shots = 0
        else:
            first_shot_ts   = fired[0][0]
            first_shot_meta = fired[0][1]

            # Capture screen number (1-indexed integer)
            try:
                trial.screen = int(first_shot_meta.get("Screen", 0)) or None
            except (TypeError, ValueError):
                trial.screen = None

            if first_shot_ts < t0:
                # Shot before target appeared → anticipation
                trial.outcome = ANTICIPATION
                trial.rt_s    = round(first_shot_ts - t0, 3)  # negative RT
                trial.n_shots = len(fired)
            elif hits:
                # At least one hit result
                trial.outcome = HIT
                trial.rt_s    = round(first_shot_ts - t0, 3)
                trial.n_shots = len(fired)
            elif misses:
                # Shot(s) fired but all missed
                trial.outcome = MISS
                trial.rt_s    = round(first_shot_ts - t0, 3)
                trial.n_shots = len(fired)
            else:
                # Shot Fired events seen but no outcome event yet resolved
                # (rare: deactivated before result event arrived)
                trial.outcome = MISS
                trial.rt_s    = round(first_shot_ts - t0, 3)
                trial.n_shots = len(fired)

        trials.append(trial)

    # ── Sort and assign 1-based index ─────────────────────────────────────────
    trials.sort(key=lambda t: t.t0)
    for i, t in enumerate(trials):
        t.index = i + 1

    return [_to_dict(t) for t in trials]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _to_dict(t: Trial) -> dict:
    return {
        "index":         t.index,
        "t0":            t.t0,
        "outcome":       t.outcome,
        "rt_s":          t.rt_s,
        "n_shots":       t.n_shots,
        "t_end":         t.t_end,
        "screen":        t.screen,
        "distance_m":    t.distance_m,
        "direction_deg": t.direction,   # expose as direction_deg for consistency
        "terminology":   TERMINOLOGY,
    }
