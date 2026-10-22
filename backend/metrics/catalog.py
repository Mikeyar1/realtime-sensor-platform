"""Stable metric IDs and availability rules for implemented behavioral scores."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Metric:
    id: str
    unit: str
    output_path: tuple[str, ...]
    formula_version: int = 1
    min_trials: int = 1
    evidence: str = "completed_trials"


def _metrics(prefix: str, names: dict[str, str], *, nested: bool = False) -> dict[str, Metric]:
    return {f"{prefix}.{name}": Metric(f"{prefix}.{name}", unit,
            (prefix, name) if nested else (name,)) for name, unit in names.items()}


METRICS = {
    **_metrics("pvt", {
        "total_trials": "count", "hits": "count", "misses": "count", "omissions": "count",
        "rt_n": "count", "rt_mean_ms": "ms", "rt_median_ms": "ms", "rt_stdev_ms": "ms",
        "rt_min_ms": "ms", "rt_max_ms": "ms", "rt_fast_10_ms": "ms", "rt_slow_10_ms": "ms",
        "rt_cv": "%", "rt_slope_ms_per_trial": "ms/trial", "lapse_count": "count",
        "response_rate_pct": "%", "omission_rate_pct": "%", "accuracy_pct": "%",
    }),
    **_metrics("gng", {
        "total_trials": "count", "go_trials": "count", "no_go_trials": "count",
        "hits": "count", "misses": "count", "omissions": "count",
        "false_alarms": "count", "correct_rejections": "count", "unresolved_trials": "count",
        "hit_rate_pct": "%", "false_alarm_rate": "%", "correct_rejection_rate_pct": "%",
        "overall_accuracy_pct": "%", "rt_n": "count", "rt_mean_ms": "ms", "rt_median_ms": "ms",
    }),
    **_metrics("discrimination", {
        "total_trials": "count", "threat_trials": "count", "nonthreat_trials": "count",
        "hits": "count", "misses": "count", "omissions": "count",
        "false_alarms": "count", "correct_rejections": "count",
        "threat_hit_rate_pct": "%", "false_alarm_rate_pct": "%",
        "correct_rejection_rate_pct": "%", "discrimination_accuracy_pct": "%",
        "threat_rt_median_ms": "ms", "false_alarm_rt_median_ms": "ms",
    }, nested=True),
    **_metrics("pvt_fitts", {
        "total_trials": "count", "completed_rounds": "count", "hits": "count",
        "omissions": "count", "misses": "count", "rt_count": "count",
        "rt_mean_ms": "ms", "rt_median_ms": "ms", "rt_stdev_ms": "ms",
        "hits_pct": "%", "response_rate_pct": "%", "omission_rate_pct": "%",
        "hit_accuracy_pct": "%", "total_shots_fired": "count",
    }),
}
METRICS["discrimination.unresolved_trials"] = Metric(
    "discrimination.unresolved_trials", "count", ("unresolved_trials",))


def project(metrics: dict[str, Any], metric_ids: list[str] | tuple[str, ...]) -> dict:
    """A versioned selection; null has a reason instead of looking like zero."""
    result = {}
    for metric_id in metric_ids:
        spec = METRICS[metric_id]
        value: Any = metrics.get("pvt", metrics) if metric_id.startswith("pvt.") else metrics
        for part in spec.output_path:
            value = value.get(part) if isinstance(value, dict) else None
        count = (metrics.get("pvt", metrics).get("total_trials") if metric_id.startswith("pvt.")
                 else metrics.get("discrimination", metrics).get("total_trials")
                 if metric_id.startswith("discrimination.") else metrics.get("total_trials"))
        result[metric_id] = {
            "value": value, "unit": spec.unit, "formula_version": spec.formula_version,
            "unavailable_reason": (None if value is not None else
                                   "no_eligible_trials" if count == 0 else
                                   "insufficient_evidence"),
        }
    return result


def assessment_snapshot(metrics: dict, scenario: dict) -> dict:
    """Persist the exact metric selection and interpretation for a scored run."""
    from cognitive_skills.catalog import METRIC_SKILL_LINKS

    selected = list(scenario.get("selected_metric_ids", []))
    skills = list(scenario.get("cognitive_skills", []))
    return {
        "catalog_version": 1,
        "scenario_id": scenario.get("scenario_id", ""),
        "scenario_revision": scenario.get("scenario_revision", 1),
        "cognitive_skills": skills,
        "metrics": project(metrics, selected),
        "skill_links": {metric_id: [skill for skill in METRIC_SKILL_LINKS.get(metric_id, ())
                                     if skill in skills] for metric_id in selected},
        "interpretation_status": "task_association_not_validated_skill_score",
    }
