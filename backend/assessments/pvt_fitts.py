"""Pure, versioned PVT/Fitts presentation and shot metrics."""

from __future__ import annotations

import math
import statistics


def _rate(numerator: int, denominator: int) -> float | None:
    return 100 * numerator / denominator if denominator else None


def score(trials: list[dict], shots: list[dict] | None = None,
          *, round_duration_s: float | None = None,
          completed_rounds: int | None = None,
          session_duration_s: float | None = None) -> dict:
    """One observation per presentation and one per unique fired shot.

    ``shots=None`` means the caller has no shot ledger; shot-derived metrics
    remain unavailable instead of inferring fired shots from trial outcomes.
    """
    valid = [t for t in trials if t.get("valid", True)]
    hits = sum(t.get("result") == "hit" for t in valid)
    misses = sum(t.get("result") == "miss" for t in valid)
    omissions = sum(t.get("result") == "omission" for t in valid)
    unresolved = len(valid) - hits - misses - omissions
    rts = [float(t["rt_ms"]) for t in valid
           if t.get("result") in {"hit", "miss"} and
           isinstance(t.get("rt_ms"), (int, float)) and
           not isinstance(t.get("rt_ms"), bool) and
           math.isfinite(t["rt_ms"]) and t["rt_ms"] >= 0]
    shot_count = len(shots) if shots is not None else None
    shot_misses = (sum(s.get("result") == "miss" for s in shots)
                   if shots is not None and all(s.get("result") in {"hit", "miss"}
                                                for s in shots) else None)
    total = len(valid)
    full = unresolved == 0
    result = {
        "assessment_profile": "pvt_fitts_core", "assessment_version": 1,
        "total_trials": total,
        "round_duration_s": round_duration_s,
        "session_duration_s": session_duration_s,
        "completed_rounds": completed_rounds,
        "rt_count": len(rts), "rt_n": len(rts),
        "rt_mean_ms": statistics.mean(rts) if rts else None,
        "rt_median_ms": statistics.median(rts) if rts else None,
        "rt_stdev_ms": statistics.stdev(rts) if len(rts) > 1 else None,
        "rt_min_ms": min(rts) if rts else None,
        "rt_max_ms": max(rts) if rts else None,
        "hits": hits, "misses": misses, "omissions": omissions,
        "hits_pct": _rate(hits, total),
        "response_rate_pct": _rate(hits + misses, total) if full else None,
        "omission_rate_pct": _rate(omissions, total) if full else None,
        "total_shots_fired": shot_count,
        "shot_misses": shot_misses,
        "hit_accuracy_pct": _rate(hits, shot_count) if shot_count is not None else None,
        "unresolved_trials": unresolved,
        "scoring_status": "partial_attribution" if unresolved else "scored",
    }
    # Compatibility aliases for the existing card renderer until Stage 6.
    result["duration_s"] = session_duration_s if completed_rounds is not None else round_duration_s
    result["accuracy_pct"] = result["hit_accuracy_pct"]
    return result


def score_live(trials: list[dict], shots: list[dict] | None = None) -> dict:
    """Live counts use all presentations; provisional rates use closed ones.

    The finalized v1 rates always use every valid presentation. During an
    open target, response and omission rates describe only resolved targets
    and are explicitly marked provisional so the UI does not blank them.
    """
    result = score(trials, shots)
    resolved = [trial for trial in trials if trial.get("valid", True) and
                trial.get("result") in {"hit", "miss", "omission"}]
    closed = score(resolved, shots)
    result["response_rate_pct"] = closed["response_rate_pct"]
    result["omission_rate_pct"] = closed["omission_rate_pct"]
    result["scored_presentations"] = len(resolved)
    result["pending_presentations"] = result["total_trials"] - len(resolved)
    result["scoring_status"] = "in_progress" if result["pending_presentations"] else result["scoring_status"]
    result["rate_basis"] = "resolved_presentations" if result["pending_presentations"] else "all_presentations"
    if shots is not None:
        result["shot_misses"] = sum(shot.get("result") == "miss" for shot in shots)
        result["pending_shots"] = sum(shot.get("result") not in {"hit", "miss"} for shot in shots)
    return result


def score_completed_run(rounds: list[dict], trials: list[dict],
                        shots: list[dict] | None = None) -> dict:
    completed = [r for r in rounds if r.get("ended_reason") == "completed"]
    keys = {(r["round_number"], r.get("attempt", 1)) for r in completed}
    included_trials = [t for t in trials if
                       (t.get("round_number"), t.get("round_attempt", 1)) in keys]
    included_shots = ([s for s in shots if
                       (s.get("round_number"), s.get("round_attempt", 1)) in keys]
                      if shots is not None else None)
    durations = [r["ended_at"] - r["started_at"] for r in completed
                 if isinstance(r.get("started_at"), (int, float)) and
                 isinstance(r.get("ended_at"), (int, float)) and
                 r["ended_at"] >= r["started_at"]]
    duration = sum(durations) if len(durations) == len(completed) else None
    result = score(included_trials, included_shots,
                   completed_rounds=len(completed), session_duration_s=duration)
    result["excluded_rounds"] = len(rounds) - len(completed)
    result["aggregate_scope"] = "completed_rounds"
    return result
