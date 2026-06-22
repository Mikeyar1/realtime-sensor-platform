"""
routers/trials.py — GET /api/trials

Extracts and returns all detected trials for a session + drill combination.
Trial parsing is delegated to the drill's extract_trials() function.
Summary statistics are computed here (drill-agnostic numeric aggregation)
with BehDisc-specific fields included when actor_type data is present.
"""

import math
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from config import SESSIONS_DIR
from drills import DRILL_MAP
from signals.gaze_metrics import enrich_trials

router = APIRouter()


def _db_path(filename: str) -> Path:
    p = SESSIONS_DIR / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Session not found: {filename}")
    return p


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _mean_opt(values: list) -> float | None:
    """Mean of a list that may contain None — ignores Nones, returns None if no data."""
    clean = [v for v in values if v is not None]
    return round(sum(clean) / len(clean), 1) if clean else None


@router.get("/api/trials")
def get_trials(
    session: str = Query(..., description="DB filename"),
    drill:   str = Query(..., description="behdisc | pvt | l2gonogo"),
):
    """
    Extract and return all trials for the given session + drill.
    Each trial includes: index, t0, outcome, rt_s, terminology.
    """
    if drill not in DRILL_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown drill: {drill}. Valid: {list(DRILL_MAP)}",
        )

    db     = _db_path(session)
    module = DRILL_MAP[drill]

    try:
        trials = module.extract_trials(str(db))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Trial extraction failed: {e}")

    if not trials:
        return {"trials": [], "summary": {}, "terminology": module.TERMINOLOGY}

    # Enrich all trials with per-trial gaze metrics (ttff_ms, gaze_dispersion_px)
    enrich_trials(str(db), trials)

    # ── Actor-type partitioning (BehDisc only) ───────────────────────────────
    hostile_trials    = [t for t in trials if t.get("actor_type") == "HOSTILE"]
    nonhostile_trials = [t for t in trials if t.get("actor_type") == "NON_HOSTILE"]
    has_actor_types   = bool(hostile_trials or nonhostile_trials)

    hits      = [t for t in trials if t["outcome"] == "HIT"]
    misses    = [t for t in trials if t["outcome"] == "MISS"]
    lapses    = [t for t in trials if t["outcome"] in ("LAPSE", "OMISSION")]
    fa        = [t for t in trials if t["outcome"] == "COMMISSION_ERROR"]
    withholds = [t for t in trials if t["outcome"] == "CORRECT_WITHHOLD"]
    ant_errors= [t for t in trials if t["outcome"] == "ANTICIPATION_ERROR"]

    # RT and shots:
    #   BehDisc (has actor_type) → use hostile_trials only
    #   All other drills         → use ALL trials that have a valid RT
    rt_source   = hostile_trials if hostile_trials else trials
    shot_source = hostile_trials if hostile_trials else trials

    rts    = [t["rt_s"]   for t in rt_source   if t.get("rt_s")    is not None]
    nshots = [t["n_shots"] for t in shot_source if t.get("n_shots") is not None and t["n_shots"] > 0]

    rt_mean    = round(sum(rts) / len(rts), 3)         if rts else None
    rt_std     = round(_std(rts), 3)                   if len(rts) > 1 else None
    rt_min     = round(min(rts), 3)                    if rts else None
    rt_max     = round(max(rts), 3)                    if rts else None
    mean_shots = round(sum(nshots) / len(nshots), 1)   if nshots else None

    # RT and shots — commission errors (non-hostile where participant fired)
    ce_rts    = [t["rt_s"]   for t in fa if t.get("rt_s")   is not None]
    ce_nshots = [t["n_shots"] for t in fa if t.get("n_shots") is not None]

    ce_rt_mean    = round(sum(ce_rts) / len(ce_rts), 3)         if ce_rts else None
    ce_rt_std     = round(_std(ce_rts), 3)                      if len(ce_rts) > 1 else None
    ce_rt_min     = round(min(ce_rts), 3)                       if ce_rts else None
    ce_rt_max     = round(max(ce_rts), 3)                       if ce_rts else None
    ce_mean_shots = round(sum(ce_nshots) / len(ce_nshots), 1)   if ce_nshots else None

    # Accuracy — drill-aware
    if hostile_trials:
        accuracy_pct    = round(len(hits) / len(hostile_trials) * 100, 1)
        commission_pct  = round(len(fa) / len(nonhostile_trials) * 100, 1) if nonhostile_trials else None
    else:
        accuracy_pct    = round(len(hits) / len(trials) * 100, 1) if trials else None
        commission_pct  = None

    summary = {
        "n_trials":              len(trials),
        # Only populated for drills with actor_type (BehDisc); None for PVT/others
        "n_hostile":             len(hostile_trials) if has_actor_types else None,
        "n_nonhostile":          len(nonhostile_trials) if has_actor_types else None,
        "n_hits":                len(hits),
        "n_misses":              len(misses),
        "n_lapses":              len(lapses),
        "n_anticipation_errors": len(ant_errors),
        "n_commission_errors":   len(fa),
        "n_correct_withholds":   len(withholds),
        "accuracy_pct":          accuracy_pct,
        "commission_error_pct":  commission_pct,
        "rt_mean_s":             rt_mean,
        "rt_std_s":              rt_std,
        "rt_min_s":              rt_min,
        "rt_max_s":              rt_max,
        "mean_shots_per_engagement": mean_shots,
        # Commission-error RT (BehDisc only)
        "ce_rt_mean_s":          ce_rt_mean,
        "ce_rt_std_s":           ce_rt_std,
        "ce_rt_min_s":           ce_rt_min,
        "ce_rt_max_s":           ce_rt_max,
        "ce_mean_shots":         ce_mean_shots,
        # Derived decision metrics (BehDisc only — None for generic drills)
        "decision_accuracy_pct": round(
            (len(hits) + len(withholds)) / len(trials) * 100, 1
        ) if has_actor_types and trials else None,
        "correct_engagement_rate_pct": round(
            len(hits) / len(hostile_trials) * 100, 1
        ) if hostile_trials else None,
        "correct_restraint_rate_pct": round(
            len(withholds) / len(nonhostile_trials) * 100, 1
        ) if nonhostile_trials else None,
        "false_positive_rate_pct": round(
            len(fa) / len(nonhostile_trials) * 100, 1
        ) if nonhostile_trials else None,
        "false_negative_rate_pct": round(
            len(misses) / len(hostile_trials) * 100, 1
        ) if hostile_trials else None,
        # Gaze metrics aggregate (all drills)
        "mean_ttff_ms": _mean_opt([t.get("ttff_ms") for t in trials]),
        "mean_gaze_dispersion_px": _mean_opt([t.get("gaze_dispersion_px") for t in trials]),
    }

    # ── L2GoNoGo-specific enrichment ────────────────────────────────────────
    # target_type field exists only on L2GoNoGo trials ("GO" | "NOGO")
    go_trials   = [t for t in trials if t.get("target_type") == "GO"]
    nogo_trials = [t for t in trials if t.get("target_type") == "NOGO"]
    if go_trials or nogo_trials:
        go_hits      = [t for t in go_trials if t["outcome"] == "HIT"]
        go_omissions = [t for t in go_trials if t["outcome"] == "OMISSION"]
        go_misses    = [t for t in go_trials if t["outcome"] == "MISS"]
        nogo_withholds = [t for t in nogo_trials if t["outcome"] == "CORRECT_WITHHOLD"]
        nogo_errors    = [t for t in nogo_trials if t["outcome"] == "COMMISSION_ERROR"]

        pct_of = lambda n, d: round(n / d * 100, 1) if d > 0 else None

        distances = [t["distance_m"] for t in go_hits if t.get("distance_m") is not None]
        sub_scens = sorted({t["sub_scenario"] for t in trials if t.get("sub_scenario")})

        summary["n_go_trials"]               = len(go_trials)
        summary["n_nogo_trials"]              = len(nogo_trials)
        summary["go_hit_pct"]                = pct_of(len(go_hits),      len(go_trials))
        summary["go_omission_pct"]            = pct_of(len(go_omissions), len(go_trials))
        summary["go_miss_pct"]               = pct_of(len(go_misses),    len(go_trials))
        summary["nogo_correct_withhold_pct"]  = pct_of(len(nogo_withholds), len(nogo_trials))
        summary["nogo_commission_error_pct"]  = pct_of(len(nogo_errors),    len(nogo_trials))
        summary["mean_distance_m"]            = round(sum(distances) / len(distances), 1) if distances else None
        summary["sub_scenarios"]              = sub_scens
    else:
        summary["n_go_trials"]               = None
        summary["n_nogo_trials"]              = None
        summary["go_hit_pct"]                = None
        summary["go_omission_pct"]            = None
        summary["go_miss_pct"]               = None
        summary["nogo_correct_withhold_pct"]  = None
        summary["nogo_commission_error_pct"]  = None
        summary["mean_distance_m"]            = None
        summary["sub_scenarios"]              = []

    return {
        "trials":      trials,
        "summary":     summary,
        "terminology": module.TERMINOLOGY,
    }
