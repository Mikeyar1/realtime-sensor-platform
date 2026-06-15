"""
drills/pvt/algorithm.py — Psychomotor Vigilance Task trial detection.

Event structure (from lsl_unmapped_samples):
  Stream A (PVT controller, ~134 events, ~45 trials):
    SCENARIO_READY   ← inter-trial interval
    TARGET_APPEAR    ← stimulus on screen            ← t = 0
    DRILL_END        ← trial window closes (~4s)

  Stream B (V-AIMS native):
    Target Activated   ← confirms TARGET_APPEAR
    Shot Fired         ← trainee responds
    Shot Hit           ← confirmed hit
    Unscored Hit       ← hit, score = 0
    Shot Miss          ← missed
    Target Deactivated ← stimulus off (lapse if shot not yet fired)

Trial anchor (t = 0):  TARGET_APPEAR
Reaction time:         t(Shot Fired) − t(TARGET_APPEAR)
Outcome:
  HIT   → shot fired in (TARGET_APPEAR, DRILL_END], result = Hit or Unscored Hit
  MISS  → shot fired after Target Deactivated OR no shot before DRILL_END
  ERROR → shot fired before TARGET_APPEAR (anticipation)
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


DRILL_KEY = "pvt"

TERMINOLOGY = {
    "drill_name":    "Psychomotor Vigilance Task",
    "drill_short":   "PVT",
    "anchor_label":  "Target Appeared",
    "hit_label":     "Hit",
    "miss_label":    "Miss",
    "fa_label":      "Anticipation Error",
    "rt_label":      "Reaction Time",
    "trial_label":   "Trial",
    "event_desc":    "Target appeared on screen",
}

TRIAL_WINDOW_S = 5.0   # Maximum expected RT; beyond this = miss


@dataclass
class Trial:
    index:        int
    t0:           float             # TARGET_APPEAR absolute timestamp
    t_drill_end:  Optional[float] = None
    t_shot:       Optional[float] = None
    outcome:      str = "MISS"      # HIT | MISS | ANTICIPATION_ERROR
    rt_s:         Optional[float] = None
    shot_number:  Optional[int]  = None


def _parse_sample(raw_json: str) -> tuple[str, str, dict]:
    try:
        parsed = json.loads(raw_json)
    except Exception:
        return "", "", {}

    if isinstance(parsed, list) and len(parsed) >= 1:
        etype = parsed[0].get("value", "") if isinstance(parsed[0], dict) else str(parsed[0])
        desc  = parsed[1].get("value", "") if len(parsed) > 1 and isinstance(parsed[1], dict) else ""
        meta_raw = parsed[3].get("value", "") if len(parsed) > 3 and isinstance(parsed[3], dict) else ""
        meta = {}
        try:
            pairs = json.loads(meta_raw)
            if isinstance(pairs, list):
                meta = {str(p[0]): str(p[1]) for p in pairs if len(p) >= 2}
        except Exception:
            pass
        return etype, desc, meta
    return "", "", {}


def _all_events(db_path: str) -> list[tuple[float, str, str, dict]]:
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cols = [c[1] for c in cur.execute("PRAGMA table_info(lsl_unmapped_samples)").fetchall()]
    json_col = "sample_json" if "sample_json" in cols else (cols[6] if len(cols) > 6 else cols[-1])
    rows = cur.execute(
        f"SELECT unix_timestamp_seconds, {json_col} FROM lsl_unmapped_samples "
        f"ORDER BY unix_timestamp_seconds"
    ).fetchall()
    con.close()

    events = []
    for ts, raw in rows:
        etype, desc, meta = _parse_sample(raw)
        if etype:
            events.append((float(ts), etype, desc, meta))
    return events


def extract_trials(db_path: str) -> list[dict]:
    events = _all_events(db_path)
    if not events:
        return []

    # Controller events (PVT stream)
    ctrl = [(ts, et, desc, meta)
            for ts, et, desc, meta in events
            if et in ("SCENARIO_READY", "TARGET_APPEAR", "DRILL_END")]

    # V-AIMS shot/outcome events
    shots = [(ts, et, desc, meta)
             for ts, et, desc, meta in events
             if et in ("Shot Fired", "Shot Hit", "Unscored Hit", "Shot Miss",
                       "Target Activated", "Target Deactivated")]

    trials: list[Trial] = []
    idx = 0
    i   = 0

    while i < len(ctrl):
        ts, et, desc, meta = ctrl[i]

        if et == "TARGET_APPEAR":
            t_appear = ts
            t_end    = None

            # Find next DRILL_END
            j = i + 1
            while j < len(ctrl):
                nts, net, _, _ = ctrl[j]
                if nts - t_appear > TRIAL_WINDOW_S + 2.0:
                    break
                if net == "DRILL_END":
                    t_end = nts
                    j += 1
                    break
                j += 1

            t_window_end = t_end if t_end else (t_appear + TRIAL_WINDOW_S)
            trial = Trial(index=idx + 1, t0=t_appear, t_drill_end=t_window_end)

            # Find first Shot Fired in [t_appear - 0.05, t_window_end]
            for sts, set_, _, smeta in shots:
                if sts < t_appear - 0.05:
                    continue
                if sts > t_window_end + 0.5:
                    break
                if set_ == "Shot Fired":
                    trial.t_shot     = sts
                    trial.rt_s       = round(sts - t_appear, 3)
                    trial.shot_number = int(smeta.get("ShotNumber", 0)) or None
                    # Shot before stimulus = anticipation error
                    if sts < t_appear:
                        trial.outcome = "ANTICIPATION_ERROR"
                    else:
                        # Look for outcome event within 0.2s
                        trial.outcome = "MISS"
                        for rts, ret, _, _ in shots:
                            if abs(rts - sts) < 0.3 and ret in ("Shot Hit", "Unscored Hit", "Shot Miss"):
                                trial.outcome = "HIT" if ret != "Shot Miss" else "MISS"
                                break
                    break

            trials.append(trial)
            idx += 1
            i = j
            continue

        i += 1

    return [_to_dict(t) for t in trials]


def _to_dict(t: Trial) -> dict:
    return {
        "index":       t.index,
        "t0":          t.t0,
        "outcome":     t.outcome,
        "rt_s":        t.rt_s,
        "shot_number": t.shot_number,
        "t_drill_end": t.t_drill_end,
        "terminology": TERMINOLOGY,
    }
