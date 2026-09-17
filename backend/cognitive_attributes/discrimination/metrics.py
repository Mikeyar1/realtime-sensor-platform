"""
cognitive_attributes/discrimination/metrics.py — Two-phase discrimination metrics.

Consumes the trial list from DiscriminationTracker and produces the AAR metric
set for Drill 3A / 3B.

OUTPUT SHAPE
    {
      "total_trials", "duration_s", "wild_shots",
      "pvt":            { ...full PVT metric set for the first phase... },
    "discrimination": { ...threat/non-threat count, rate, and timing metrics... },
      "timeline":       [ per-trial rows spanning both phases ]
    }

NO MARKSMANSHIP METRIC HERE
    Shot placement is deliberately not reported for these drills. VirTra's
    "Local Hit (m)" origin sits at the actor's base, so for human figures
    sqrt(x^2+y^2) is dominated by a fixed ~1.8 m vertical offset and measures
    target height rather than precision.
"""

from __future__ import annotations
import statistics
from ..pvt.metrics import compute_metrics as _compute_pvt_metrics

# Trial result constants (must match tracker.py)
HIT               = "hit"
MISS              = "miss"
OMISSION          = "omission"
FALSE_ALARM       = "false_alarm"
CORRECT_REJECTION = "correct_rejection"

PHASE_PVT  = "pvt"
PHASE_DISC = "discrimination"

DEFAULT_LAPSE_MULTIPLIER = 2.0

def _rt_stats(trials: list[dict]) -> dict:
    """Median / mean / SD / n over the RTs present in `trials`."""
    rts = [t["rt_ms"] for t in trials if t.get("rt_ms") is not None and t["rt_ms"] > 0]
    return {
        "rt_median_ms": round(statistics.median(rts), 1) if rts else None,
        "rt_mean_ms":   round(statistics.mean(rts), 1) if rts else None,
        "rt_stdev_ms":  round(statistics.stdev(rts), 1) if len(rts) >= 2 else None,
        "rt_n":         len(rts),
    }


def _compute_discrimination_metrics(trials: list[dict], wild_shots: int) -> dict:
    """Count, rate, and response-timing metrics for threat/non-threat trials."""
    threat    = [t for t in trials if t.get("target_class") == "threat"]
    nonthreat = [t for t in trials if t.get("target_class") == "nonthreat"]

    hits = [t for t in threat    if t.get("result") == HIT]
    miss = [t for t in threat    if t.get("result") == MISS]
    fas  = [t for t in nonthreat if t.get("result") == FALSE_ALARM]
    crs  = [t for t in nonthreat if t.get("result") == CORRECT_REJECTION]

    n_threat, n_nonthreat = len(threat), len(nonthreat)
    n_hit, n_miss, n_fa, n_cr = len(hits), len(miss), len(fas), len(crs)
    n_total = n_threat + n_nonthreat

    hit_rate = round(n_hit / n_threat * 100, 1) if n_threat else None
    fa_rate  = round(n_fa / n_nonthreat * 100, 1) if n_nonthreat else None
    accuracy = round((n_hit + n_cr) / n_total * 100, 1) if n_total else None

    threat_rt = _rt_stats(hits)
    fa_rt     = _rt_stats(fas)

    # Faster false alarms than correct engagements = reflexive, pre-identification firing.
    rt_cost = None
    if threat_rt["rt_median_ms"] is not None and fa_rt["rt_median_ms"] is not None:
        rt_cost = round(threat_rt["rt_median_ms"] - fa_rt["rt_median_ms"], 1)

    return {
        # Counts
        "threat_trials":            n_threat,
        "nonthreat_trials":         n_nonthreat,
        "total_trials":             n_total,
        "hits":                     n_hit,
        "misses":                   n_miss,
        "false_alarms":             n_fa,
        "correct_rejections":       n_cr,

        # Raw rates
        "hit_rate_pct":             hit_rate,
        "miss_rate_pct":            round(n_miss / n_threat * 100, 1) if n_threat else None,
        "false_alarm_rate_pct":     fa_rate,
        "correct_rejection_rate_pct": round(n_cr / n_nonthreat * 100, 1) if n_nonthreat else None,
        "discrimination_accuracy_pct": accuracy,

        # Response timing
        "threat_rt_median_ms":      threat_rt["rt_median_ms"],
        "threat_rt_mean_ms":        threat_rt["rt_mean_ms"],
        "threat_rt_stdev_ms":       threat_rt["rt_stdev_ms"],
        "threat_rt_n":              threat_rt["rt_n"],
        "false_alarm_rt_median_ms": fa_rt["rt_median_ms"],
        "false_alarm_rt_n":         fa_rt["rt_n"],
        "rt_cost_ms":               rt_cost,

        # Trigger discipline
        "wild_shots":               wild_shots,
    }


def compute_metrics(trials: list[dict],
                    lapse_multiplier: float = DEFAULT_LAPSE_MULTIPLIER,
                    duration_s: float | None = None,
                    wild_shots: int = 0) -> dict:
    """Core computation shared by live updates and the final summary."""
    pvt_trials  = [t for t in trials if t.get("phase") == PHASE_PVT]
    disc_trials = [t for t in trials if t.get("phase") == PHASE_DISC]

    pvt_metrics = _compute_pvt_metrics(pvt_trials, lapse_multiplier=lapse_multiplier)
    disc_metrics = _compute_discrimination_metrics(disc_trials, wild_shots)

    timeline = [
        {
            "trial":        t.get("trial_number"),
            "phase":        t.get("phase"),
            "target_class": t.get("target_class"),
            "target_name":  t.get("target_name"),
            "outcome":      (t.get("result") or "").upper(),
            "rt_ms":        round(t["rt_ms"], 1) if t.get("rt_ms") is not None else None,
            "t0":           t.get("activated_at"),
        }
        for t in sorted(trials, key=lambda x: x.get("trial_number", 0))
    ]

    return {
        "total_trials":   len(trials),
        "duration_s":     duration_s,
        "wild_shots":     wild_shots,
        "pvt":            pvt_metrics,
        "discrimination": disc_metrics,
        "timeline":       timeline,
    }


def compute_live_metrics(trials: list[dict], session_record: dict | None = None) -> dict:
    """Entry point registered in ATTRIBUTE_REGISTRY."""
    record = session_record or {}
    return compute_metrics(
        trials,
        lapse_multiplier = record.get("lapse_multiplier", DEFAULT_LAPSE_MULTIPLIER),
        duration_s       = record.get("duration_s"),
        wild_shots       = record.get("wild_shots", 0),
    )
