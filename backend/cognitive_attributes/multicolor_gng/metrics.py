"""
cognitive_attributes/multicolor_gng/metrics.py — Go/No-Go summary metric computation.

Consumes the trial list from MulticolorGngTracker.get_session_record() and
produces the AAR metric set for the Frontend. Field names intentionally match
the original GNG metrics shape (frontend/js/features/aar/task-types/gng.js) —
no frontend changes needed to consume this tracker's output.

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
FALSE_ALARM       = "false_alarm"
CORRECT_REJECTION = "correct_rejection"


def compute_metrics(trials: list[dict], wild_shots: int = 0) -> dict:
    """Core computation shared by live updates and the final summary."""
    go_trials   = [t for t in trials if t.get("target_type") == "go"]
    nogo_trials = [t for t in trials if t.get("target_type") == "no_go"]

    hits   = [t for t in go_trials   if t.get("result") == HIT]
    misses = [t for t in go_trials   if t.get("result") == MISS]
    fas    = [t for t in nogo_trials if t.get("result") == FALSE_ALARM]
    crs    = [t for t in nogo_trials if t.get("result") == CORRECT_REJECTION]

    n_go, n_nogo   = len(go_trials), len(nogo_trials)
    n_hits, n_miss = len(hits), len(misses)
    n_fa, n_cr     = len(fas), len(crs)

    hit_rate  = round(n_hits / n_go   * 100, 1) if n_go   > 0 else None
    miss_rate = round(n_miss / n_go   * 100, 1) if n_go   > 0 else None
    fa_rate   = round(n_fa   / n_nogo * 100, 1) if n_nogo > 0 else None
    cr_rate   = round(n_cr   / n_nogo * 100, 1) if n_nogo > 0 else None
    go_prop   = round(n_go / (n_go + n_nogo) * 100, 1) if (n_go + n_nogo) > 0 else None

    rt_samples_s = [
        t["rt_ms"] / 1000.0
        for t in hits
        if t.get("rt_ms") is not None and t["rt_ms"] > 0
    ]
    rt_mean   = round(statistics.mean(rt_samples_s),   3) if rt_samples_s else None
    rt_median = round(statistics.median(rt_samples_s), 3) if rt_samples_s else None
    rt_stdev  = round(statistics.stdev(rt_samples_s),  3) if len(rt_samples_s) >= 2 else None

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
        "go_proportion":      go_prop,

        # Core counts
        "hits":               n_hits,
        "misses":             n_miss,
        "false_alarms":       n_fa,
        "correct_rejections": n_cr,
        "wild_shots":         wild_shots,

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


def compute_live_metrics(trials: list[dict], session_record: dict | None = None) -> dict:
    """Entry point registered in ATTRIBUTE_REGISTRY."""
    record = session_record or {}
    return compute_metrics(trials, wild_shots=record.get("wild_shots", 0))
