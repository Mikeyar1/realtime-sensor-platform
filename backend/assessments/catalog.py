"""Static assessment metadata; event adapters and persistence use stable IDs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    label: str
    group: str
    unit: str
    description: str
    scope: str = "round"


@dataclass(frozen=True)
class AssessmentProfile:
    id: str
    version: int
    objectives: tuple[str, ...]
    metrics: tuple[MetricDefinition, ...]


@dataclass(frozen=True)
class CategoryDefinition:
    id: str
    label: str
    profile: AssessmentProfile


@dataclass(frozen=True)
class DrillDefinition:
    id: str
    category_id: str


def _metric(id: str, label: str, group: str, unit: str, description: str,
            scope: str = "round") -> MetricDefinition:
    return MetricDefinition(id, label, group, unit, description, scope)


PVT_FITTS_V1 = AssessmentProfile(
    id="pvt_fitts_core", version=1,
    objectives=(
        "Psychomotor Vigilance (PVT) / Reaction Latency",
        "Motor Acquisition Speed & Pointing Precision (Fitts' Law)",
        "Shooting Performance",
    ),
    metrics=(
        _metric("total_trials", "Total Trials", "Session", "", "Valid stimulus presentations."),
        _metric("session_duration_s", "Session Duration", "Session", "s", "Sum of completed round durations, excluding time between rounds.", "run"),
        _metric("completed_rounds", "Completed Rounds", "Session", "", "Number of completed rounds.", "run"),
        _metric("round_duration_s", "Round Duration", "Session", "s", "Round completion minus round start."),
        _metric("rt_ms", "Response Time", "Response Time", "ms", "First attributable shot time minus target appearance time, multiplied by 1,000.", "presentation"),
        _metric("rt_count", "Response Time Count", "Response Time", "", "Number of included first-shot response times."),
        _metric("rt_mean_ms", "Mean Response Time", "Response Time", "ms", "Arithmetic mean of included response times."),
        _metric("rt_median_ms", "Median Response Time", "Response Time", "ms", "Median of included response times."),
        _metric("rt_stdev_ms", "Response Time StDev", "Response Time", "ms", "Sample standard deviation; unavailable below two observations."),
        _metric("rt_min_ms", "Minimum Response Time", "Response Time", "ms", "Smallest included response time."),
        _metric("rt_max_ms", "Maximum Response Time", "Response Time", "ms", "Largest included response time."),
        _metric("hits", "Targets Hit", "Target Engagement and Marksmanship", "", "Valid presentations struck at least once."),
        _metric("hits_pct", "Hits", "Target Engagement and Marksmanship", "%", "100 × targets hit / valid presentations."),
        _metric("omissions", "Omissions", "Target Engagement and Marksmanship", "", "Presentations expiring without an attributable shot."),
        _metric("misses", "Misses", "Target Engagement and Marksmanship", "", "Presentations with a shot but no hit before closing."),
        _metric("response_rate_pct", "Response Rate", "Target Engagement and Marksmanship", "%", "100 × (targets hit + misses) / valid presentations."),
        _metric("omission_rate_pct", "Omission Rate", "Target Engagement and Marksmanship", "%", "100 × omissions / valid presentations."),
        _metric("total_shots_fired", "Total Shots Fired", "Target Engagement and Marksmanship", "", "Unique shots fired while a round is active."),
        _metric("shot_misses", "Shot Misses", "Target Engagement and Marksmanship", "", "Shots confirmed to have hit no target."),
        _metric("hit_accuracy_pct", "Hit Accuracy", "Target Engagement and Marksmanship", "%", "100 × targets hit / total shots fired."),
    ),
)

CATEGORIES = {
    "pvt_fitts": CategoryDefinition("pvt_fitts", "PVT / Fitts", PVT_FITTS_V1),
}

DRILLS = {
    "fitts_pvt_clay": DrillDefinition("fitts_pvt_clay", "pvt_fitts"),
}
