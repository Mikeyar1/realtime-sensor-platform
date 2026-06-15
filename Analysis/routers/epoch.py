"""
routers/epoch.py — POST /api/epoch

Extracts physio epochs anchored to trial t0 timestamps.
Handles both the grand average and actor-type split (BehDisc hostile vs non-hostile).
Fully signal-agnostic — delegates to signals.extract_epochs() via the SIGNALS registry.
"""

import numpy as np
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import SESSIONS_DIR
from drills import DRILL_MAP
from signals import extract_epochs, SIGNALS, grand_average, _clean, _scalar

router = APIRouter()


class EpochRequest(BaseModel):
    session:    str
    drill:      str
    trial_ids:  Optional[list[int]] = None   # 1-based indices; None = all
    signals:    list[str] = ["hr", "pupil", "motion"]
    baseline_s: float = 2.0
    analysis_s: float = 2.0
    bin_s:      float = 0.1
    do_zscore:  bool  = True


def _db_path(filename: str) -> Path:
    p = SESSIONS_DIR / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Session not found: {filename}")
    return p


def _split_avg(epoch_lists: list, result: dict) -> tuple:
    """
    Compute grand average for a subset of epochs.
    Returns (avg, ci_upper, ci_lower, baseline_mean, analysis_mean, delta).
    """
    if not epoch_lists:
        return [], [], [], None, None, None

    arrays = [
        np.array([v if v is not None else float("nan") for v in e])
        for e in epoch_lists
    ]
    avg, upper, lower = grand_average(arrays)

    times_arr = np.array(result["times"])
    bl_mask   = times_arr < 0
    an_mask   = times_arr >= 0
    bl_vals   = avg[bl_mask]; bl_vals = bl_vals[~np.isnan(bl_vals)]
    an_vals   = avg[an_mask]; an_vals = an_vals[~np.isnan(an_vals)]
    bl_mean   = float(np.mean(bl_vals)) if len(bl_vals) > 0 else None
    an_mean   = float(np.mean(an_vals)) if len(an_vals) > 0 else None
    delta     = round(an_mean - bl_mean, 4) if (bl_mean is not None and an_mean is not None) else None

    return _clean(avg.tolist()), _clean(upper.tolist()), _clean(lower.tolist()), bl_mean, an_mean, delta


@router.post("/api/epoch")
def compute_epoch(req: EpochRequest):
    """
    Extract physio epochs anchored to trial t0 timestamps.
    Returns per-signal epoch data + grand average.
    For drills with actor_type (BehDisc), also returns hostile vs non-hostile averages.
    """
    if req.drill not in DRILL_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown drill: {req.drill}. Valid: {list(DRILL_MAP)}",
        )

    db     = _db_path(req.session)
    module = DRILL_MAP[req.drill]

    # Extract trials
    try:
        trials = module.extract_trials(str(db))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Trial extraction failed: {e}")

    if not trials:
        raise HTTPException(status_code=404, detail="No trials found in session")

    # Filter by trial_ids if provided
    if req.trial_ids is not None:
        trials = [t for t in trials if t["index"] in req.trial_ids]

    if not trials:
        raise HTTPException(status_code=404, detail="No trials match the requested IDs")

    anchor_times = [t["t0"] for t in trials]

    # Validate signals
    invalid = [s for s in req.signals if s not in SIGNALS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown signals: {invalid}. Valid: {list(SIGNALS)}",
        )

    # Extract epochs per signal
    signal_results: dict = {}
    for sig in req.signals:
        try:
            result = extract_epochs(
                db_path      = str(db),
                anchor_times = anchor_times,
                signal_name  = sig,
                baseline_s   = req.baseline_s,
                analysis_s   = req.analysis_s,
                bin_s        = req.bin_s,
                do_zscore    = req.do_zscore,
            )
            # Attach per-trial metadata alongside epoch data
            for i, trial in enumerate(trials):
                if i < len(result["epochs"]):
                    result["epochs"][i] = {
                        "trial_index": trial["index"],
                        "outcome":     trial["outcome"],
                        "rt_s":        trial.get("rt_s"),
                        "actor_type":  trial.get("actor_type"),
                        "values":      result["epochs"][i],
                    }
            signal_results[sig] = result
        except Exception as e:
            signal_results[sig] = {"error": str(e)}

    # ── Actor-type split averages (BehDisc: hostile vs non-hostile) ──────────
    hostile_indices    = {t["index"] for t in trials if t.get("actor_type") == "HOSTILE"}
    nonhostile_indices = {t["index"] for t in trials if t.get("actor_type") == "NON_HOSTILE"}
    has_type_split     = bool(hostile_indices or nonhostile_indices)

    if has_type_split:
        for sig, result in signal_results.items():
            if "error" in result or "epochs" not in result:
                continue

            epochs_all = result["epochs"]
            h_epochs   = [ep["values"] for ep in epochs_all
                          if ep.get("actor_type") == "HOSTILE" and ep.get("values")]
            nh_epochs  = [ep["values"] for ep in epochs_all
                          if ep.get("actor_type") == "NON_HOSTILE" and ep.get("values")]

            h_avg,  h_upper,  h_lower,  h_bl,  h_an,  h_delta   = _split_avg(h_epochs,  result)
            nh_avg, nh_upper, nh_lower, nh_bl, nh_an, nh_delta   = _split_avg(nh_epochs, result)

            result.update({
                "hostile_avg":              h_avg,
                "hostile_ci_upper":         h_upper,
                "hostile_ci_lower":         h_lower,
                "n_hostile":                len(h_epochs),
                "hostile_baseline_mean":    _scalar(h_bl),
                "hostile_analysis_mean":    _scalar(h_an),
                "hostile_delta":            _scalar(h_delta),
                "nonhostile_avg":           nh_avg,
                "nonhostile_ci_upper":      nh_upper,
                "nonhostile_ci_lower":      nh_lower,
                "n_nonhostile":             len(nh_epochs),
                "nonhostile_baseline_mean": _scalar(nh_bl),
                "nonhostile_analysis_mean": _scalar(nh_an),
                "nonhostile_delta":         _scalar(nh_delta),
            })

    return {
        "signals":        signal_results,
        "has_type_split": has_type_split,
        "trials_used": [
            {
                "index":       t["index"],
                "outcome":     t["outcome"],
                "rt_s":        t.get("rt_s"),
                "actor_type":  t.get("actor_type"),
                "actor_name":  t.get("actor_name"),
                "actor_short": t.get("actor_short"),
                "n_shots":     t.get("n_shots"),
            }
            for t in trials
        ],
        "n_trials":    len(trials),
        "baseline_s":  req.baseline_s,
        "analysis_s":  req.analysis_s,
        "bin_s":       req.bin_s,
        "terminology": module.TERMINOLOGY,
    }
