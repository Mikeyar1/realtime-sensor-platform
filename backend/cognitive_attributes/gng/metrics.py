"""
cognitive_attributes/gng/metrics.py — Go/No-Go summary metric computation.

Consumes the trial list from GngTracker.get_session_record() and produces
the AAR metric set for the Frontend.

OUTPUT METRICS
    go_trials           — number of Go stimulus presentations
    no_go_trials        — number of No-Go stimulus presentations
    hits                — Go trials with correct response
    misses              — Go trials with no response (errors of omission)
    false_alarms        — No-Go trials with incorrect response (errors of commission)
    correct_rejections  — No-Go trials with correct withhold
    hit_rate            — hits / go_trials × 100 (%)
    miss_rate           — misses / go_trials × 100 (%)
    false_alarm_rate    — false_alarms / no_go_trials × 100 (%)
    correct_rej_rate    — correct_rejections / no_go_trials × 100 (%)
    rt_mean_s           — mean RT across Hit trials (seconds)
    rt_median_s         — median RT (seconds)
    rt_stdev_s          — SD of RT (seconds); None if < 2 hit trials
    rt_n                — number of RT samples used
    timeline            — per-trial list for the speed-accuracy overlay chart
"""

from __future__ import annotations
import statistics


# Outcome constants (must match GngTracker result strings)
HIT               = "hit"
MISS              = "miss"
FALSE_ALARM       = "false_alarm"
CORRECT_REJECTION = "correct_rejection"


def compute_live_metrics(trials: list[dict], session_record: dict | None = None) -> dict:
    """
    Compute GNG summary metrics from the live trial list produced by GngTracker.

    Args:
        trials: list of dicts from GngTracker.get_completed_trials()
                Each trial has: trial_number, target_id, target_name,
                target_type, result, rt_ms, activated_at, shot_at

    Returns:
        Metric dict sent to Frontend as session.metrics_ready payload.
        Same schema the AAR gng.js display module expects.
    """
    go_trials   = [t for t in trials if t.get("target_type") == "go"]
    nogo_trials = [t for t in trials if t.get("target_type") == "no_go"]

    hits  = [t for t in go_trials   if t.get("result") == HIT]
    misses= [t for t in go_trials   if t.get("result") == MISS]
    fas   = [t for t in nogo_trials if t.get("result") == FALSE_ALARM]
    crs   = [t for t in nogo_trials if t.get("result") == CORRECT_REJECTION]

    n_go   = len(go_trials)
    n_nogo = len(nogo_trials)
    n_hits = len(hits)
    n_miss = len(misses)
    n_fa   = len(fas)
    n_cr   = len(crs)

    hit_rate = round(n_hits / n_go   * 100, 1) if n_go   > 0 else None
    fa_rate  = round(n_fa   / n_nogo * 100, 1) if n_nogo > 0 else None
    miss_rate= round(n_miss / n_go   * 100, 1) if n_go   > 0 else None
    cr_rate  = round(n_cr   / n_nogo * 100, 1) if n_nogo > 0 else None
    go_prop  = round(n_go / (n_go + n_nogo) * 100, 1) if (n_go + n_nogo) > 0 else None

    # RT in seconds from the tracker (rt_ms / 1000)
    rt_samples_s = [
        t["rt_ms"] / 1000.0
        for t in hits
        if t.get("rt_ms") is not None and t["rt_ms"] > 0
    ]
    rt_mean   = round(statistics.mean(rt_samples_s),   3) if rt_samples_s else None
    rt_median = round(statistics.median(rt_samples_s), 3) if rt_samples_s else None
    rt_stdev  = round(statistics.stdev(rt_samples_s),  3) if len(rt_samples_s) >= 2 else None

    # Per-trial timeline for the speed-accuracy overlay chart
    timeline = [
        {
            "trial":       t.get("trial_number"),
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
        "go_proportion":      go_prop,

        # Core counts
        "hits":               n_hits,
        "misses":             n_miss,
        "false_alarms":       n_fa,
        "correct_rejections": n_cr,

        # Rates (%)
        "hit_rate":           hit_rate,
        "miss_rate":          miss_rate,
        "false_alarm_rate":   fa_rate,
        "correct_rej_rate":   cr_rate,

        # RT (seconds — Hit trials only)
        "rt_mean_s":          rt_mean,
        "rt_median_s":        rt_median,
        "rt_stdev_s":         rt_stdev,
        "rt_n":               len(rt_samples_s),

        # Per-trial detail for the overlay chart
        "timeline":           timeline,
    }
