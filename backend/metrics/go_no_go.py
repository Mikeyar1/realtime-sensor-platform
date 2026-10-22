"""
metrics/go_no_go.py — Go/No-Go summary metric computation.

Consumes the trial list from MulticolorGngTracker.get_session_record() and
produces the AAR metric set for the Frontend. Live and final metrics use the
same calculation and millisecond RT units.

For now this only computes the same hit/miss/false-alarm/correct-rejection +
RT statistics every other drill computes from its own trial list. The custom
LSL stream also carries richer per-event data (target distance, hit position,
its own running metrics, etc.) that could support additional derived metrics
later — deliberately not used yet, kept for a future iteration.
"""

from __future__ import annotations
import statistics


HIT               = "hit"
MISS              = "miss"
OMISSION          = "omission"
FALSE_ALARM       = "false_alarm"
CORRECT_REJECTION = "correct_rejection"


def compute_metrics(trials: list[dict], wild_shots: int = 0) -> dict:
    """Core computation shared by live updates and the final summary."""
    duration_s = None
    if trials and all(t.get("completed_at") is not None for t in trials):
        duration_s = round(max(t["completed_at"] for t in trials) -
                           min(t["activated_at"] for t in trials), 2)
    unresolved_count = sum(1 for t in trials if t.get("result") == "unresolved")
    trials = [t for t in trials if t.get("result") != "unresolved"]
    go_trials   = [t for t in trials if t.get("target_type") == "go"]
    nogo_trials = [t for t in trials if t.get("target_type") == "no_go"]

    hits   = [t for t in go_trials   if t.get("result") == HIT]
    misses = [t for t in go_trials if t.get("result") == MISS and t.get("shot_at") is not None]
    omissions = [t for t in go_trials if t.get("result") == OMISSION or
                 (t.get("result") == MISS and t.get("shot_at") is None)]
    fas    = [t for t in nogo_trials if t.get("result") == FALSE_ALARM]
    crs    = [t for t in nogo_trials if t.get("result") == CORRECT_REJECTION]

    n_go, n_nogo   = len(go_trials), len(nogo_trials)
    n_hits, n_miss, n_omit = len(hits), len(misses), len(omissions)
    n_fa, n_cr     = len(fas), len(crs)

    hit_rate  = round(n_hits / n_go   * 100, 1) if n_go   > 0 else None
    miss_rate = round(n_miss / n_go   * 100, 1) if n_go   > 0 else None
    fa_rate   = round(n_fa   / n_nogo * 100, 1) if n_nogo > 0 else None
    cr_rate   = round(n_cr   / n_nogo * 100, 1) if n_nogo > 0 else None
    go_prop   = round(n_go / (n_go + n_nogo) * 100, 1) if (n_go + n_nogo) > 0 else None

    rt_samples_ms = [
        t["rt_ms"]
        for t in hits
        if t.get("rt_ms") is not None and t["rt_ms"] > 0
    ]
    rt_mean   = round(statistics.mean(rt_samples_ms), 1) if rt_samples_ms else None
    rt_median = round(statistics.median(rt_samples_ms), 1) if rt_samples_ms else None
    rt_stdev  = round(statistics.stdev(rt_samples_ms), 1) if len(rt_samples_ms) >= 2 else None
    n_total = n_go + n_nogo

    timeline = [
        {
            "trial":       t.get("trial_number"),
            "round":       t.get("round_number"),
            "target_type": t.get("target_type"),
            "outcome":     t.get("result"),
            "rt_s":        round(t["rt_ms"] / 1000.0, 3) if t.get("rt_ms") is not None else None,
            "t0":          t.get("activated_at"),
        }
        for t in sorted(trials, key=lambda x: x.get("trial_number", 0))
    ]

    return {
        # Session context
        "go_trials":          n_go,
        "no_go_trials":       n_nogo,
        "total_trials":       n_total,
        "duration_s":         duration_s,
        "duration_basis":     "first_to_last_trial",
        "go_proportion":      go_prop,

        # Core counts
        "hits":               n_hits,
        "misses":             n_miss,
        "omissions":          n_omit,
        "commission_errors":  n_fa,
        "false_alarms":       n_fa,
        "correct_rejections": n_cr,
        "wild_shots":         wild_shots,
        "unresolved_trials":  unresolved_count,

        # Rates (%)
        "hit_rate":           hit_rate,
        "miss_rate":          miss_rate,
        "false_alarm_rate":   fa_rate,
        "correct_rej_rate":   cr_rate,
        "hit_rate_pct":       hit_rate,
        "correct_rejection_rate_pct": cr_rate,
        "overall_accuracy_pct": round((n_hits + n_cr) / n_total * 100, 1) if n_total else None,

        # RT (seconds — Hit trials only)
        "rt_mean_ms":         rt_mean,
        "rt_median_ms":       rt_median,
        "rt_stdev_ms":        rt_stdev,
        "rt_n":               len(rt_samples_ms),

        # Per-trial detail for the overlay chart
        "timeline":           timeline,
    }


def compute_live_metrics(trials: list[dict], session_record: dict | None = None) -> dict:
    """Entry point registered in ATTRIBUTE_REGISTRY."""
    record = session_record or {}
    return compute_metrics(trials, wild_shots=record.get("wild_shots", 0))
