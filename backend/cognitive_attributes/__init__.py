"""
cognitive_attributes/__init__.py — Registry of all cognitive attributes.

This is the single place that maps a task_type (from drill_registry.toml)
to the TrackerClass and metrics function for that cognitive attribute.

HOW TO ADD A NEW COGNITIVE ATTRIBUTE
1. Create backend/cognitive_attributes/<name>/tracker.py
       → class <Name>Tracker with reset(params), on_virtra_event(), get_completed_trials(), get_session_record()
2. Create backend/cognitive_attributes/<name>/metrics.py
       → def compute_live_metrics(trials: list[dict]) -> dict
3. Import both below and add one entry to ATTRIBUTE_REGISTRY.
4. Add [cognitive_attributes.<name>] section to config/drill_registry.toml.
Done — no other code changes needed.
"""

from .pvt.tracker import PvtTracker
from .pvt.metrics import compute_live_metrics as _pvt_metrics

from .gng.tracker import GngTracker
from .gng.metrics import compute_live_metrics as _gng_metrics

from .discrimination.tracker import DiscriminationTracker
from .discrimination.metrics import compute_live_metrics as _discrimination_metrics

# task_type (uppercase, matches task_type field in drill_registry.toml)
#     → (TrackerClass, metrics_function)
#
# TrackerClass must implement:
#   reset(params: dict | None)  — called at scenario start with TOML params
#   on_virtra_event(event_type, timestamp, metadata_dict)
#   get_completed_trials() -> list[dict]
#   get_session_record(scenario_name, session_id, started_at, stopped_at, biosignals) -> dict
#
# metrics_function must implement:
#   compute_live_metrics(trials: list[dict]) -> dict

ATTRIBUTE_REGISTRY: dict[str, tuple] = {
    "PVT": (PvtTracker, _pvt_metrics),
    "GNG": (GngTracker, _gng_metrics),
    "DISCRIMINATION": (DiscriminationTracker, _discrimination_metrics),
    # "MARKSMANSHIP": (MarksmanshipTracker, _marksmanship_metrics),  ← add future attributes here
}

__all__ = ["ATTRIBUTE_REGISTRY"]
