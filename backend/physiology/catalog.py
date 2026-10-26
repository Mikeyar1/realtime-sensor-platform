"""Stable physiology metric IDs and view paths for existing sensor summaries."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhysiologyMetric:
    id: str
    label: str
    unit: str
    summary_key: str
    live_path: tuple[str, ...]
    size: str = "small"
    formula_version: int = 1


METRICS = {
    item.id: item for item in (
        PhysiologyMetric("physio.hr_mean_bpm", "Heart rate", "bpm", "hr_mean_bpm", ("hr", "mean")),
        PhysiologyMetric("physio.hrv_rmssd_ms", "Heart rate variability", "ms", "hrv_rmssd_ms", ("hrv", "rmssd_ms")),
        PhysiologyMetric("physio.pupil_mean_mm", "Pupil diameter", "mm", "pupil_mean_mm", ("pupil", "mean_mm")),
    )
}


def projection(summary: dict, quality: dict) -> dict:
    """Snapshot exact values and availability for later historical rendering."""
    return {
        "catalog_version": 1,
        "metrics": {
            metric.id: {
                "value": summary.get(metric.summary_key), "unit": metric.unit,
                "formula_version": metric.formula_version,
                "unavailable_reason": None if summary.get(metric.summary_key) is not None else
                quality.get("hrv_status" if metric.id.endswith("hrv_rmssd_ms") else
                            "pupil_status" if metric.id.endswith("pupil_mean_mm") else "hr_status"),
            } for metric in METRICS.values()
        },
        "quality": quality,
    }
