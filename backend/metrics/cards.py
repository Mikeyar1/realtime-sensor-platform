"""Data-only card contract used by live and historical clients."""

from __future__ import annotations

from cognitive_skills.catalog import METRIC_SKILL_LINKS, SKILLS
from physiology.catalog import METRICS as PHYSIOLOGY_METRICS

from .catalog import METRICS as BEHAVIORAL_METRICS


def catalog_payload() -> dict:
    cards = []
    for metric in BEHAVIORAL_METRICS.values():
        cards.append({
            "metric_id": metric.id, "label": metric.id.split(".")[-1].replace("_", " ").title(),
            "unit": metric.unit, "size": "small", "group": "behavioral",
            "formula_version": metric.formula_version,
            "skills": list(METRIC_SKILL_LINKS.get(metric.id, ())),
            "live_event": "assessment.round_updated",
            "live_path": ["round", "metrics", "_assessment", "metrics", metric.id, "value"],
            "round_history_path": ["metrics", "_assessment", "metrics", metric.id, "value"],
            "session_history_path": ["metrics", "_assessment", "metrics", metric.id, "value"],
        })
    for metric in PHYSIOLOGY_METRICS.values():
        cards.append({
            "metric_id": metric.id, "label": metric.label, "unit": metric.unit,
            "size": metric.size, "group": "physiology",
            "formula_version": metric.formula_version, "skills": [],
            "live_event": "session.biosignal_update",
            "live_path": list(metric.live_path),
            "round_live_event": "assessment.round_physiology",
            "round_live_path": ["physiology", metric.summary_key],
            "round_history_path": ["physiology", metric.summary_key],
            "session_history_path": ["biosignals", metric.summary_key],
        })
    return {
        "type": "assessment.catalog", "catalog_version": 1,
        "skills": [{"id": skill.id, "label": skill.label} for skill in SKILLS.values()],
        "cards": cards,
    }
