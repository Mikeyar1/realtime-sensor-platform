"""
signals/extractor.py — Epoch windowing, binning, z-scoring, and grand averaging.

All epoch math lives here. It is signal-agnostic — it calls any extractor
function via the SIGNALS registry and produces a uniform output dict.

Public API:
  extract_epochs(db_path, anchor_times, signal_name, ...) -> dict
  grand_average(epochs) -> (mean, upper, lower)
  _clean(lst)           -> JSON-safe list
  _scalar(v)            -> JSON-safe scalar
"""

import math
import numpy as np
from .registry import SIGNALS


# ── Binning ───────────────────────────────────────────────────────────────────

def bin_signal(
    times: np.ndarray,
    values: np.ndarray,
    t0: float,
    t_start_rel: float,
    t_end_rel: float,
    bin_s: float = 0.1,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Bin a signal onto a fixed relative-time axis.
    t_start_rel, t_end_rel are relative to t0 (e.g. -2.0, +2.0).
    Returns (relative_times, binned_values). NaN where no data.
    """
    rel_times = times - t0
    n_bins = int(round((t_end_rel - t_start_rel) / bin_s))
    bin_centers = np.linspace(t_start_rel, t_end_rel, n_bins)
    half = bin_s / 2
    binned = np.full(len(bin_centers), np.nan)

    for i, tc in enumerate(bin_centers):
        mask = (rel_times >= tc - half) & (rel_times < tc + half)
        if mask.sum() > 0:
            binned[i] = float(np.nanmean(values[mask]))

    return bin_centers, binned


# ── Z-scoring ────────────────────────────────────────────────────────────────

def zscore_epoch(
    bin_centers: np.ndarray,
    binned: np.ndarray,
    baseline_s: float,
) -> np.ndarray:
    """
    Z-score relative to baseline window [t_start_rel, 0].
    Returns z-scored array (NaN preserved).
    """
    bl_mask = (bin_centers < 0) & (bin_centers >= -baseline_s)
    bl_vals = binned[bl_mask]
    bl_vals = bl_vals[~np.isnan(bl_vals)]

    if len(bl_vals) < 2:
        return binned - np.nanmean(binned) if len(bl_vals) > 0 else binned.copy()

    bl_mean = float(np.mean(bl_vals))
    bl_std  = float(np.std(bl_vals))

    if bl_std < 1e-10:
        return binned - bl_mean

    return (binned - bl_mean) / bl_std


# ── Grand average + CI ────────────────────────────────────────────────────────

def grand_average(
    epochs: list[np.ndarray],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Stack epochs (same length) and compute mean ± 1 SD.
    Returns (mean, mean+SD, mean-SD). NaN where all trials are NaN.
    """
    if not epochs:
        return np.array([]), np.array([]), np.array([])

    stack = np.vstack(epochs)   # (n_trials, n_bins)
    avg = np.nanmean(stack, axis=0)
    sd  = np.nanstd(stack, axis=0)
    return avg, avg + sd, avg - sd


# ── High-level epoch extractor ────────────────────────────────────────────────

def extract_epochs(
    db_path: str,
    anchor_times: list[float],   # absolute Unix timestamps of t=0 events
    signal_name: str,
    baseline_s: float,
    analysis_s: float,
    bin_s: float = 0.1,
    do_zscore: bool = True,
) -> dict:
    """
    For each anchor, extract [t0 - baseline_s, t0 + analysis_s] from the signal,
    bin to a fixed axis, optionally z-score.

    Returns dict with:
        times         : relative time axis (n_bins,)
        epochs        : list of (n_bins,) arrays — one per anchor
        grand_avg     : grand average (n_bins,)
        ci_upper      : grand_avg + 1SD
        ci_lower      : grand_avg - 1SD
        baseline_mean : float (native units, pre z-score)
        analysis_mean : float (native units)
        delta         : float
        delta_pct     : float
        label / unit / color : from signal registry
    """
    sig_info = SIGNALS.get(signal_name)
    if not sig_info:
        raise ValueError(f"Unknown signal: {signal_name}. Valid: {list(SIGNALS)}")

    fn     = sig_info["fn"]
    margin = 0.5   # extra buffer for edge bins

    all_epochs = []
    bl_means   = []
    an_means   = []

    # Shared time axis (computed once)
    bin_centers, _ = bin_signal(
        np.array([0.0]),
        np.array([0.0]),
        0.0,
        -baseline_s,
        analysis_s,
        bin_s,
    )

    for t0 in anchor_times:
        t_start = t0 - baseline_s - margin
        t_end   = t0 + analysis_s + margin

        times, values = fn(db_path, t_start, t_end)
        if len(times) < 2:
            all_epochs.append(np.full(len(bin_centers), np.nan))
            continue

        _, binned = bin_signal(times, values, t0, -baseline_s, analysis_s, bin_s)

        # Native-unit stats before z-scoring
        bl_mask = bin_centers < 0
        an_mask = bin_centers >= 0
        bl_vals = binned[bl_mask]; bl_vals = bl_vals[~np.isnan(bl_vals)]
        an_vals = binned[an_mask]; an_vals = an_vals[~np.isnan(an_vals)]
        if len(bl_vals) > 0: bl_means.append(float(np.mean(bl_vals)))
        if len(an_vals) > 0: an_means.append(float(np.mean(an_vals)))

        if do_zscore:
            binned = zscore_epoch(bin_centers, binned, baseline_s)

        all_epochs.append(binned)

    avg, ci_upper, ci_lower = grand_average(all_epochs)

    bl_mean   = float(np.mean(bl_means)) if bl_means else float("nan")
    an_mean   = float(np.mean(an_means)) if an_means else float("nan")
    delta     = an_mean - bl_mean if (bl_means and an_means) else float("nan")
    delta_pct = (delta / bl_mean * 100) if (bl_means and bl_mean != 0) else float("nan")

    return {
        "times":         _clean(bin_centers.tolist()),
        "epochs":        [_clean(e.tolist()) for e in all_epochs],
        "grand_avg":     _clean(avg.tolist())       if len(avg)      else [],
        "ci_upper":      _clean(ci_upper.tolist())  if len(ci_upper) else [],
        "ci_lower":      _clean(ci_lower.tolist())  if len(ci_lower) else [],
        "baseline_mean": _scalar(bl_mean),
        "analysis_mean": _scalar(an_mean),
        "delta":         _scalar(delta),
        "delta_pct":     _scalar(delta_pct),
        "n_epochs":      len(anchor_times),
        "signal":        signal_name,
        "label":         sig_info["label"],
        "unit":          sig_info["unit"],
        "color":         sig_info["color"],
    }


# ── JSON helpers ──────────────────────────────────────────────────────────────

def _clean(lst: list) -> list:
    """Replace nan/inf with None for JSON compliance. Handles numpy dtypes."""
    out = []
    for v in lst:
        try:
            fv = float(v)
            out.append(None if not math.isfinite(fv) else fv)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _scalar(v) -> float | None:
    """Return a finite rounded float or None for JSON compliance."""
    try:
        fv = float(v)
        return None if not math.isfinite(fv) else round(fv, 4)
    except (TypeError, ValueError):
        return None
