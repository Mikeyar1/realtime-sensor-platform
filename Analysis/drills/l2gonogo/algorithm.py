"""
drills/l2gonogo/algorithm.py — Level 2 Go / No-Go trial detection.

Scenario overview
─────────────────
VirTra runs two sub-scenarios in sequence within the same session DB:
  • "LVL 2 Go No Go"           — primary go/no-go task
  • "LVL 2 Inhibition Assessment" — inhibition variant (same logic)

Target types present
─────────────────────
  Ivan         → GO stimulus (hostile actor, trainee SHOULD fire)
  neutral      → NO-GO foil  (non-hostile actor, trainee should NOT fire)
                 NOTE: neutral is immediately deactivated/removed after being
                 created; it is NOT presented as a shooting opportunity.
                 It establishes the go/no-go context but does not itself
                 generate a trial.
  fixation     → fixation cross / peripheral point (no response required)
  fixation (2)
  fixation (3)

Trial lifecycle
───────────────
  Target Activated "Ivan"  →  GO stimulus onset = t0
  [Shot Fired + Shot Hit]  →  correct GO response
  [Shot Fired + Shot Miss] →  incorrect motor response (errant shot)
  [no shot]                →  OMISSION error (trainee failed to respond)
  Target Removed   "Ivan"  →  trial ends (window closes)

Outcome classification
──────────────────────
  HIT               — Shot Fired + (Shot Hit or Unscored Hit) within Ivan window
  MISS              — Shot Fired + Shot Miss within Ivan window
  OMISSION          — no shot fired during Ivan window (failure to respond to GO)
  COMMISSION_ERROR  — shot fired at neutral (neutral windows only, if ever shown)

Note: In the current data neutral is never shown as an active shooting target.
The commission error branch is implemented for completeness and future scenarios.

Reaction time
─────────────
  RT = t(first Shot Fired after t0) − t0    (None for OMISSION)

Sub-scenario tagging
────────────────────
Each trial is tagged with the sub-scenario name it occurred under, extracted
from the "Scenario Started" event just before the trial.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from drills.virtra_common import get_all_virtra_events


# ── Identity ──────────────────────────────────────────────────────────────────

DRILL_KEY = "l2gonogo"

TERMINOLOGY = {
    "drill_name":     "L2 Go / No-Go",
    "drill_short":    "L2GoNoGo",
    "anchor_label":   "Ivan Appeared (GO)",
    "hit_label":      "Correct GO (Hit)",
    "miss_label":     "Errant Shot (Miss)",
    "lapse_label":    "Omission (No Response)",
    "fa_label":       "Commission Error (shot neutral)",
    "withhold_label": "Correct Withhold",
    "rt_label":       "Response Time",
    "trial_label":    "Trial",
    "event_desc":     "Ivan (GO target) appeared on screen",
}

# ── Config ────────────────────────────────────────────────────────────────────

GO_TARGET    = "ivan"       # case-insensitive match on target name
NOGO_TARGET  = "neutral"
FIXATION_PFX = "fixation"

HIT              = "HIT"
MISS             = "MISS"
OMISSION         = "OMISSION"
COMMISSION_ERROR = "COMMISSION_ERROR"
CORRECT_WITHHOLD = "CORRECT_WITHHOLD"

LOOKAHEAD_S = 10.0   # max window for orphan activations

_NEEDED = {
    "Scenario Started",
    "Scenario Stopped",
    "Target Activated",
    "Target Deactivated",
    "Target Removed",
    "Shot Fired",
    "Shot Hit",
    "Unscored Hit",
    "Shot Miss",
}


# ── Data class ────────────────────────────────────────────────────────────────

@dataclass
class Trial:
    index:       int
    t0:          float
    t_end:       Optional[float] = None
    target_type: str             = "GO"           # GO | NOGO
    outcome:     str             = OMISSION
    rt_s:        Optional[float] = None
    n_shots:     int             = 0
    distance_m:  Optional[float] = None
    direction:   Optional[float] = None
    sub_scenario: str            = ""             # "LVL 2 Go No Go" etc.


# ── Extraction ────────────────────────────────────────────────────────────────

def extract_trials(db_path: str) -> list[dict]:
    """
    Detect all L2GoNoGo trials from a LabReplay .db recording.

    A trial is defined as one appearance of the Ivan (GO) target.
    Returns trial dicts sorted by t0.
    """
    events = get_all_virtra_events(db_path, event_filter=_NEEDED)
    if not events:
        return []

    # ── Track current sub-scenario name ──────────────────────────────────────
    sub_scenario_name = ""

    # ── Build Ivan appearance windows ─────────────────────────────────────────
    #  Ivan is activated, then later either Deactivated or Removed.
    #  We track the open activation and close it on either event.
    open_ivan_ts: Optional[float] = None
    open_ivan_meta: dict          = {}

    # For neutral target commission errors:
    open_neutral_ts: Optional[float] = None

    # All shot events
    shot_events = [
        (ts, etype, meta)
        for ts, etype, _, meta in events
        if etype in ("Shot Fired", "Shot Hit", "Unscored Hit", "Shot Miss")
    ]

    trials: list[Trial] = []
    current_sub = ""

    for ts, etype, desc, meta in events:

        # ── Sub-scenario tracking ─────────────────────────────────────────────
        if etype == "Scenario Started":
            # Extract scenario name from meta: {"Scenario": "LVL 2 Go No Go", ...}
            current_sub = meta.get("Scenario", "") or _parse_scenario_from_desc(desc)
            continue

        if etype == "Scenario Stopped":
            # Flush any open Ivan window
            if open_ivan_ts is not None:
                trial = _classify_window(
                    t0=open_ivan_ts,
                    t_end=ts,
                    act_meta=open_ivan_meta,
                    sub_scenario=current_sub,
                    shot_events=shot_events,
                    target_type="GO",
                )
                trials.append(trial)
                open_ivan_ts = None
            continue

        # ── Ivan (GO) target ──────────────────────────────────────────────────
        if etype == "Target Activated" and GO_TARGET in desc.lower() and FIXATION_PFX not in desc.lower():
            open_ivan_ts   = ts
            open_ivan_meta = meta
            sub_scenario_name = current_sub
            continue

        if etype in ("Target Deactivated", "Target Removed") and GO_TARGET in desc.lower() and FIXATION_PFX not in desc.lower():
            if open_ivan_ts is not None:
                trial = _classify_window(
                    t0=open_ivan_ts,
                    t_end=ts,
                    act_meta=open_ivan_meta,
                    sub_scenario=sub_scenario_name,
                    shot_events=shot_events,
                    target_type="GO",
                )
                trials.append(trial)
                open_ivan_ts = None
            continue

        # ── Neutral (NO-GO) target ────────────────────────────────────────────
        # In current data neutral is hidden immediately after creation, so it
        # rarely generates a meaningful window. We include the logic for
        # completeness.
        if etype == "Target Activated" and NOGO_TARGET in desc.lower():
            open_neutral_ts = ts
            continue

        if etype in ("Target Deactivated", "Target Removed") and NOGO_TARGET in desc.lower():
            if open_neutral_ts is not None:
                t_window_s = ts - open_neutral_ts
                # Only register as a trial if the neutral was visible long enough
                # to be a genuine NO-GO stimulus (> 0.5 s)
                if t_window_s > 0.5:
                    trial = _classify_window(
                        t0=open_neutral_ts,
                        t_end=ts,
                        act_meta={},
                        sub_scenario=current_sub,
                        shot_events=shot_events,
                        target_type="NOGO",
                    )
                    trials.append(trial)
                open_neutral_ts = None
            continue

    # ── Sort and assign 1-based index ─────────────────────────────────────────
    trials.sort(key=lambda t: t.t0)
    for i, t in enumerate(trials):
        t.index = i + 1

    return [_to_dict(t) for t in trials]


# ── Window classifier ─────────────────────────────────────────────────────────

def _classify_window(
    t0: float,
    t_end: Optional[float],
    act_meta: dict,
    sub_scenario: str,
    shot_events: list[tuple[float, str, dict]],
    target_type: str,
) -> Trial:
    effective_end = t_end if t_end is not None else t0 + LOOKAHEAD_S

    trial = Trial(
        index        = 0,
        t0           = t0,
        t_end        = t_end,
        target_type  = target_type,
        distance_m   = _to_float(act_meta.get("Distance (m)") or act_meta.get("Distance")),
        direction    = _to_float(act_meta.get("Direction")),
        sub_scenario = sub_scenario,
    )

    # Shots within this window (with small buffer)
    shots_in = [
        (ts, etype, meta)
        for ts, etype, meta in shot_events
        if t0 - 0.1 <= ts <= effective_end + 0.3
    ]

    fired  = [(ts, meta) for ts, et, meta in shots_in if et == "Shot Fired"]
    hits   = [ts for ts, et, _ in shots_in if et in ("Shot Hit", "Unscored Hit")]
    misses = [ts for ts, et, _ in shots_in if et == "Shot Miss"]

    if target_type == "GO":
        if not fired:
            trial.outcome = OMISSION
            trial.n_shots = 0
        else:
            first_t = fired[0][0]
            trial.n_shots = len(fired)
            trial.rt_s    = round(first_t - t0, 3)
            if first_t < t0:
                trial.outcome = OMISSION      # anticipation before stimulus
                trial.rt_s    = None
            elif hits:
                trial.outcome = HIT
            else:
                trial.outcome = MISS

    else:  # NOGO
        if not fired:
            trial.outcome = CORRECT_WITHHOLD
            trial.n_shots = 0
        else:
            trial.outcome = COMMISSION_ERROR
            trial.n_shots = len(fired)
            trial.rt_s    = round(fired[0][0] - t0, 3)

    return trial


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_scenario_from_desc(desc: str) -> str:
    """Fallback: extract scenario name from description string."""
    for marker in ("Playing Scenario ", "V-AIMS Playing Scenario: ", "Stopped Scenario "):
        if marker in desc:
            return desc.split(marker, 1)[1].strip()
    return ""


def _to_float(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def _to_dict(t: Trial) -> dict:
    return {
        "index":        t.index,
        "t0":           t.t0,
        "outcome":      t.outcome,
        "rt_s":         t.rt_s,
        "n_shots":      t.n_shots,
        "t_end":        t.t_end,
        "target_type":  t.target_type,
        "distance_m":   t.distance_m,
        "direction":    t.direction,
        "sub_scenario": t.sub_scenario,
        "terminology":  TERMINOLOGY,
    }
