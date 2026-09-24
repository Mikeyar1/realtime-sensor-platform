"""
cognitive_attributes/pvt/metrics.py — PVT summary metric computation.

Consumes the trial list from PvtTracker and produces the AAR metric set for
the Frontend. All metrics use "Response Time" (RT) — the firearm motor chain
(detect → aim → fire) is more complex than a simple button press.

TRIAL RESULT TYPES (from tracker.py)
    hit      — fired and the round struck the target (RT recorded)
    miss     — fired but the round did not strike the target (RT recorded)
    omission — target deactivated with no shot fired (no response)

RT POOL
    RT statistics use genuine responses = hits + misses. Omissions are excluded.

DURATION
    duration_s spans first valid trial activation through final trial closure.
    The tracker supplies it in the session record and completed trial timestamps
    provide the same running value during live updates.
"""

from __future__ import annotations
import statistics


DEFAULT_LAPSE_MULTIPLIER = 2.0   # lapse threshold = session median RT × this


def _mean_extreme_fraction(sorted_rts: list[float], fraction: float, slowest: bool):
    """Mean RT of the fastest or slowest `fraction` of responses."""
    if not sorted_rts:
        return None
    n = max(1, round(len(sorted_rts) * fraction))
    subset = sorted_rts[-n:] if slowest else sorted_rts[:n]
    return round(statistics.mean(subset), 1)


def _rt_slope(trial_numbers: list[float], rts: list[float]):
    """Least-squares slope of RT over trial number (ms per trial)."""
    n = len(rts)
    if n < 2:
        return None
    mean_x = sum(trial_numbers) / n
    mean_y = sum(rts) / n
    denom = sum((x - mean_x) ** 2 for x in trial_numbers)
    if denom == 0:
        return None
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(trial_numbers, rts))
    return num / denom


def compute_metrics(trials: list[dict], lapse_multiplier: float = DEFAULT_LAPSE_MULTIPLIER,
                    duration_s: float | None = None) -> dict:
    """Core PVT metric computation shared by live updates and the final summary."""
    if duration_s is None and trials and all(t.get("completed_at") is not None for t in trials):
        first = min(t.get("first_activated_at") or t["activated_at"] for t in trials)
        last = max(t["completed_at"] for t in trials)
        duration_s = round(last - first, 2)
    total_trials   = len(trials)
    hit_trials     = [t for t in trials if t.get("result") == "hit"]
    miss_trials    = [t for t in trials if t.get("result") == "miss"]
    omission_list  = [t for t in trials if t.get("result") == "omission"]

    n_hits = len(hit_trials)
    n_miss = len(miss_trials)
    n_omit = len(omission_list)
    n_resp = n_hits + n_miss

    # RT pool = genuine responses (hits + misses), ordered by trial number.
    responded = sorted(
        (t for t in (hit_trials + miss_trials)
         if t.get("rt_ms") is not None and t["rt_ms"] > 0),
        key=lambda t: t.get("trial_number", 0),
    )
    rts = [t["rt_ms"] for t in responded]
    rts_sorted = sorted(rts)

    rt_mean   = round(statistics.mean(rts), 1) if rts else None
    rt_median = round(statistics.median(rts), 1) if rts else None
    rt_stdev  = round(statistics.stdev(rts), 1) if len(rts) >= 2 else None
    rt_min    = round(min(rts), 1) if rts else None
    rt_max    = round(max(rts), 1) if rts else None
    rt_cv     = round(rt_stdev / rt_mean * 100, 1) if (rt_stdev is not None and rt_mean) else None

    rt_fast_10 = _mean_extreme_fraction(rts_sorted, 0.10, slowest=False)
    rt_slow_10 = _mean_extreme_fraction(rts_sorted, 0.10, slowest=True)

    # Lapse threshold scales with the session median RT (aiming baseline varies).
    lapse_threshold = round(rt_median * lapse_multiplier, 1) if rt_median else None
    lapse_count = sum(1 for rt in rts if lapse_threshold is not None and rt > lapse_threshold)

    response_rate = round(n_resp / total_trials * 100, 1) if total_trials else None
    omission_rate = round(n_omit / total_trials * 100, 1) if total_trials else None
    accuracy      = round(n_hits / n_resp * 100, 1) if n_resp else None
    hits_pct      = round(n_hits / total_trials * 100, 1) if total_trials else None

    # Mean Radial Error (marksmanship precision) — averaged over hits with a recorded impact point.
    radial_errors = [t["radial_error_cm"] for t in hit_trials if t.get("radial_error_cm") is not None]
    mre_cm = round(statistics.mean(radial_errors), 2) if radial_errors else None

    # Time-on-task: split responses by trial-number midpoint.
    mid = total_trials / 2 if total_trials else 0
    first_half  = [t["rt_ms"] for t in responded if t.get("trial_number", 0) <= mid]
    second_half = [t["rt_ms"] for t in responded if t.get("trial_number", 0) >  mid]
    rt_first  = round(statistics.median(first_half), 1) if first_half else None
    rt_second = round(statistics.median(second_half), 1) if second_half else None
    slope = _rt_slope([t.get("trial_number", 0) for t in responded], rts)
    rt_slope = round(slope, 2) if slope is not None else None

    timeline = [
        {
            "trial":   t.get("trial_number"),
            "outcome": t.get("result", "").upper(),
            "rt_ms":   round(t["rt_ms"], 1) if t.get("rt_ms") is not None else None,
            "t0":      t.get("activated_at"),
        }
        for t in sorted(trials, key=lambda x: x.get("trial_number", 0))
    ]

    return {
        # Session context
        "total_trials":              total_trials,
        "duration_s":                duration_s,
        "duration_basis":           "first_to_last_trial",

        # Counts
        "hits":                      n_hits,
        "hits_pct":                  hits_pct,
        "misses":                    n_miss,
        "omissions":                 n_omit,

        # Response Time — core (RT pool = hits + misses)
        "rt_median_ms":              rt_median,
        "rt_mean_ms":                rt_mean,
        "rt_stdev_ms":               rt_stdev,
        "rt_n":                      len(rts),

        # Response Time — advanced
        "rt_fast_10_ms":             rt_fast_10,
        "rt_slow_10_ms":             rt_slow_10,
        "rt_min_ms":                 rt_min,
        "rt_max_ms":                 rt_max,
        "rt_cv":                     rt_cv,
        "lapse_count":               lapse_count,
        "lapse_ms":                  lapse_threshold,
        "lapse_multiplier":          lapse_multiplier,

        # Vigilance & engagement
        "response_rate_pct":         response_rate,
        "omission_rate_pct":         omission_rate,

        # Accuracy (marksmanship)
        "accuracy_pct":              accuracy,
        "mre_cm":                    mre_cm,
        "mre_n":                     len(radial_errors),

        # Time-on-task (vigilance decrement)
        "rt_median_first_half_ms":   rt_first,
        "rt_median_second_half_ms":  rt_second,
        "rt_slope_ms_per_trial":     rt_slope,

        # Per-trial detail for the temporal trend chart
        "timeline":                  timeline,
    }


def compute_live_metrics(trials: list[dict], session_record: dict | None = None) -> dict:
    """
    Entry point registered in ATTRIBUTE_REGISTRY.

    Pulls the first-to-last-trial duration and lapse threshold from the session
    record assembled by PvtTracker.get_session_record.
    """
    record = session_record or {}
    lapse_multiplier = record.get("lapse_multiplier", DEFAULT_LAPSE_MULTIPLIER)
    duration_s       = record.get("duration_s")
    return compute_metrics(trials, lapse_multiplier=lapse_multiplier, duration_s=duration_s)
