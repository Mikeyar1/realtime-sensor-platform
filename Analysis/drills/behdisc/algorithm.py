"""
drills/behdisc/algorithm.py — Behavioral Discrimination trial detection.

Algorithm (per-actor-appearance):
  1. Parse 'Actor IsVisible Changed → True/False' events to build actor windows.
  2. Within each window, find:
       - 'First Movement Non-Hostile event' → non_hostile_t
       - 'First Movement Hostile event'     → hostile_t
  3. Actor type:
       HOSTILE     if hostile_t exists (includes actors that start non-hostile then go hostile)
       NON_HOSTILE if only non_hostile_t (or no movement event at all)
  4. Anchor (t0) for physio epoch:
       HOSTILE     → hostile_t
       NON_HOSTILE → non_hostile_t
  5. Shots during window:
       Since actors appear one-at-a-time, all 'Shot Fired' during the window
       were aimed at this actor.
       RT = first Shot Fired at/after t0 − t0   (for HOSTILE only)
       n_shots = count of all Shot Fired at/after t0  (HOSTILE only)
  6. Outcomes:
       HOSTILE:     Shot Hit within window → HIT  | else → MISS
       NON_HOSTILE: any Shot Fired         → COMMISSION_ERROR | else → CORRECT_WITHHOLD
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


DRILL_KEY = "behdisc"

TERMINOLOGY = {
    "drill_name":     "Behavioral Discrimination",
    "drill_short":    "BehDisc",
    "anchor_label":   "Actor Movement",
    "hit_label":      "Hit",
    "miss_label":     "Miss",
    "fa_label":       "Commission Error",
    "withhold_label": "Correct Withhold",
    "rt_label":       "Engagement RT",
    "trial_label":    "Engagement",
    "event_desc":     "First hostile / non-hostile movement event",
}


@dataclass
class Trial:
    index:      int
    t0:         float            # anchor timestamp
    actor_name: str = ""
    actor_type: str = "NON_HOSTILE"   # HOSTILE | NON_HOSTILE
    outcome:    str = "CORRECT_WITHHOLD"
    rt_s:       Optional[float] = None
    n_shots:    Optional[int]   = None


# ── Low-level parsing ─────────────────────────────────────────────────────────

def _parse_sample(raw_json: str) -> tuple[str, str]:
    """Returns (event_type, description). Empty strings on failure."""
    try:
        parsed = json.loads(raw_json)
    except Exception:
        return "", ""

    if isinstance(parsed, list) and len(parsed) >= 2:
        etype = parsed[0].get("value", "") if isinstance(parsed[0], dict) else str(parsed[0])
        desc  = parsed[1].get("value", "") if isinstance(parsed[1], dict) else ""
        return str(etype), str(desc)
    return "", ""


def _detect_virtra_stream(cur: sqlite3.Cursor) -> Optional[int]:
    """
    Find the lsl_metadata_id of the VirTra event stream.
    Identified by presence of 'Actor Event' or 'Shot Fired' event types.
    """
    streams = [r[0] for r in cur.execute(
        "SELECT DISTINCT lsl_metadata_id FROM lsl_unmapped_samples"
    ).fetchall()]

    for sid in streams:
        rows = cur.execute(
            "SELECT sample_json FROM lsl_unmapped_samples "
            "WHERE lsl_metadata_id=? LIMIT 200", (sid,)
        ).fetchall()
        for (raw,) in rows:
            etype, _ = _parse_sample(raw)
            if etype in ("Actor Event", "Shot Fired", "Actor IsVisible Changed"):
                return sid
    return None


def _all_events(db_path: str) -> list[tuple[float, str, str]]:
    """Return (timestamp, event_type, description) for every VirTra event, sorted."""
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    sid = _detect_virtra_stream(cur)
    if sid is None:
        con.close()
        return []

    rows = cur.execute(
        "SELECT unix_timestamp_seconds, sample_json "
        "FROM lsl_unmapped_samples "
        "WHERE lsl_metadata_id=? "
        "ORDER BY unix_timestamp_seconds",
        (sid,)
    ).fetchall()
    con.close()

    events = []
    for ts, raw in rows:
        etype, desc = _parse_sample(raw)
        if etype:
            events.append((float(ts), etype, desc))
    return events


# ── Main extraction ───────────────────────────────────────────────────────────

def extract_trials(db_path: str) -> list[dict]:
    """
    Detect all BehDisc engagements using per-actor visibility windows.
    Returns list of trial dicts with correct actor_type, outcome, RT, n_shots.
    """
    events = _all_events(db_path)
    if not events:
        return []

    # ── Build actor visibility windows ────────────────────────────────────────
    open_windows: dict[str, float] = {}   # actor_name → t_appeared
    windows: list[tuple[str, float, float]] = []  # (actor, t_start, t_end)

    for ts, etype, desc in events:
        if etype != "Actor IsVisible Changed":
            continue
        parts = desc.split(" IsVisible changed to ")
        if len(parts) != 2:
            continue
        actor = parts[0].strip()
        visible = parts[1].strip().lower() == "true"
        if visible:
            open_windows[actor] = ts
        else:
            if actor in open_windows:
                windows.append((actor, open_windows.pop(actor), ts))

    # Flush any actors still visible at session end
    t_last = events[-1][0] + 1.0
    for actor, t_start in open_windows.items():
        windows.append((actor, t_start, t_last))

    # Sort windows by start time
    windows.sort(key=lambda w: w[1])

    # ── Classify each window ──────────────────────────────────────────────────
    raw_trials: list[Trial] = []

    for actor_name, t_start, t_end in windows:

        # Collect actor-specific movement events within window
        non_hostile_t: Optional[float] = None
        hostile_t:     Optional[float] = None

        for ts, etype, desc in events:
            if ts < t_start - 0.5 or ts > t_end + 0.5:
                continue
            if etype != "Actor Event":
                continue
            if not desc.startswith(actor_name):
                continue
            if "First Movement Non-Hostile event" in desc and non_hostile_t is None:
                non_hostile_t = ts
            elif "First Movement Hostile event" in desc and hostile_t is None:
                hostile_t = ts

        # Need at least one movement event to form a trial
        if non_hostile_t is None and hostile_t is None:
            continue

        # Actor type + anchor
        if hostile_t is not None:
            actor_type = "HOSTILE"
            t0 = hostile_t
        else:
            actor_type = "NON_HOSTILE"
            t0 = non_hostile_t  # type: ignore[assignment]

        # Collect shots within the visibility window
        # (actors appear one-at-a-time, so all shots are at this actor)
        shots_in_window: list[float] = []
        first_hit_t:     Optional[float] = None

        for ts, etype, desc in events:
            if ts < t_start - 0.1 or ts > t_end + 0.5:
                continue
            if etype == "Shot Fired":
                shots_in_window.append(ts)
            elif etype == "Shot Hit" and actor_name in desc:
                if first_hit_t is None:
                    first_hit_t = ts

        # Shots that count: at or after t0 (the anchor / hostile moment)
        response_shots = [s for s in shots_in_window if s >= t0 - 0.05]

        # Classify outcome
        if actor_type == "HOSTILE":
            outcome = "HIT" if first_hit_t is not None else "MISS"
            first_response = response_shots[0] if response_shots else None
            rt_s    = round(first_response - t0, 3) if first_response is not None else None
            n_shots = len(response_shots)
        else:  # NON_HOSTILE
            outcome = "COMMISSION_ERROR" if shots_in_window else "CORRECT_WITHHOLD"
            if shots_in_window:
                first_ce_shot = min(s for s in shots_in_window if s >= t0 - 0.05) \
                                if any(s >= t0 - 0.05 for s in shots_in_window) \
                                else shots_in_window[0]
                rt_s    = round(first_ce_shot - t0, 3)
                n_shots = len([s for s in shots_in_window if s >= t0 - 0.05])
            else:
                rt_s    = None
                n_shots = None

        raw_trials.append(Trial(
            index      = 0,          # assigned after sort
            t0         = t0,
            actor_name = actor_name,
            actor_type = actor_type,
            outcome    = outcome,
            rt_s       = rt_s,
            n_shots    = n_shots,
        ))

    # Sort by anchor time and assign 1-based index
    raw_trials.sort(key=lambda t: t.t0)
    for i, trial in enumerate(raw_trials):
        trial.index = i + 1

    return [_to_dict(t) for t in raw_trials]


def _actor_short(name: str) -> str:
    """
    Abbreviate actor name for display.
    'A8_ADM_MEMC_R_3_2_S1' → 'A8 (MEMC·S1)'
    """
    parts = name.split("_")
    if len(parts) >= 3:
        actor_id = parts[0]
        # parts[2] = category e.g. MEMC, BMC, WFC, WMC
        category = parts[2] if len(parts) > 2 else ""
        screen   = parts[-1] if parts[-1].startswith("S") else ""
        if category and screen:
            return f"{actor_id} ({category}·{screen})"
        return f"{actor_id}"
    return name


def _to_dict(t: Trial) -> dict:
    return {
        "index":       t.index,
        "t0":          t.t0,
        "actor_name":  t.actor_name,
        "actor_short": _actor_short(t.actor_name),
        "actor_type":  t.actor_type,
        "outcome":     t.outcome,
        "rt_s":        t.rt_s,
        "n_shots":     t.n_shots,
        "terminology": TERMINOLOGY,
    }
