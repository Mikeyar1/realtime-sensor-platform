"""Fitts-PVT Clay behavioral metrics (formula version 1)."""

from __future__ import annotations

from assessments.pvt_fitts import score as score_pvt_fitts, score_completed_run
from .pvt import compute_metrics as compute_pvt_metrics

def compute_metrics(trials: list[dict], record: dict | None = None) -> dict:
    """Score the versioned PVT/Fitts contract, retaining old display-only extras."""
    resolved = [trial for trial in trials if trial.get("result") in {"hit", "miss", "omission"}]
    metric_trials = ([{**trial, "trial_number": index}
                      for index, trial in enumerate(sorted(
                          resolved, key=lambda item: item.get("activated_at") or 0), 1)]
                     if record is not None else resolved)
    extras = compute_pvt_metrics(metric_trials)
    if record is not None:
        rounds = record.get("rounds", [])
        if all("round_number" in row for row in rounds):
            completed_shots_known = all(
                row.get("quality", {}).get("shot_identity_complete", True)
                for row in rounds if row.get("ended_reason") == "completed")
            metrics = score_completed_run(rounds, trials, record.get("shots")
                                          if completed_shots_known else None)
            metrics["duration_basis"] = "sum_completed_round_boundaries"
        else:
            # Older saved records have no round boundaries. Keep their duration
            # explicitly marked as a legacy estimate, never a new v1 result.
            metrics = score_pvt_fitts(trials, record.get("shots"),
                            completed_rounds=sum(r.get("ended_reason") == "completed" for r in rounds))
            durations = [r.get("metrics", {}).get("duration_s") for r in rounds
                         if r.get("ended_reason") == "completed"]
            if durations and all(isinstance(value, (int, float)) for value in durations):
                metrics["duration_s"] = sum(durations)
            metrics["duration_basis"] = "legacy_trial_windows"
            metrics["excluded_rounds"] = len(rounds) - metrics["completed_rounds"]
    else:
        metrics = score_pvt_fitts(trials)
        metrics["duration_s"] = extras.get("duration_s")
        metrics["duration_basis"] = "provisional_trial_span"
    for key, value in extras.items():
        metrics.setdefault(key, value)
    return metrics


