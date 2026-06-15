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

    # ── Actor-type partitioning (BehDisc; ignored by other drills) ────────────
    hostile_trials    = [t for t in trials if t.get("actor_type") == "HOSTILE"]
    nonhostile_trials = [t for t in trials if t.get("actor_type") == "NON_HOSTILE"]

    hits      = [t for t in trials if t["outcome"] == "HIT"]
    misses    = [t for t in trials if t["outcome"] == "MISS"]
    fa        = [t for t in trials if t["outcome"] == "COMMISSION_ERROR"]
    withholds = [t for t in trials if t["outcome"] == "CORRECT_WITHHOLD"]

    # RT and shots — hostile engagements
    rts    = [t["rt_s"]   for t in hostile_trials if t["rt_s"]   is not None]
    nshots = [t["n_shots"] for t in hostile_trials if t.get("n_shots") is not None]

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
        "n_hostile":             len(hostile_trials),
        "n_nonhostile":          len(nonhostile_trials),
        "n_hits":                len(hits),
        "n_misses":              len(misses),
        "n_commission_errors":   len(fa),
        "n_correct_withholds":   len(withholds),
        "accuracy_pct":          accuracy_pct,
        "commission_error_pct":  commission_pct,
        "rt_mean_s":             rt_mean,
        "rt_std_s":              rt_std,
        "rt_min_s":              rt_min,
        "rt_max_s":              rt_max,
        "mean_shots_per_engagement": mean_shots,
        # Commission-error RT
        "ce_rt_mean_s":          ce_rt_mean,
        "ce_rt_std_s":           ce_rt_std,
        "ce_rt_min_s":           ce_rt_min,
        "ce_rt_max_s":           ce_rt_max,
        "ce_mean_shots":         ce_mean_shots,
        # Derived decision metrics
        "decision_accuracy_pct": round(
            (len(hits) + len(withholds)) / len(trials) * 100, 1
        ) if trials else None,
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
    }

    return {
        "trials":      trials,
        "summary":     summary,
        "terminology": module.TERMINOLOGY,
    }
