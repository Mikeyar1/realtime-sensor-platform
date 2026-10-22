"""Trial construction is selected by scenario/source, not cognitive attribute."""

from scenarios.behavioral_discrimination.native_trials import DiscriminationTracker
from scenarios.fitts_gng_clay_colors.custom_trials import FittsGngClayColorsTracker
from scenarios.fitts_pvt_clay.custom_trials import FittsPvtClayTracker
from .native_target_response import PvtTracker


TRIAL_ADAPTERS = {
    "native_target_response": PvtTracker,
    "native_threat_discrimination": DiscriminationTracker,
    "custom_go_no_go": FittsGngClayColorsTracker,
    "custom_fitts_pvt": FittsPvtClayTracker,
}

TASK_DEFAULT_ADAPTERS = {
    "PVT": "native_target_response",
    "DISCRIMINATION": "native_threat_discrimination",
    "GNG": "custom_go_no_go",
    "PVT_FITTS": "custom_fitts_pvt",
}


def tracker_for(drill: dict):
    key = drill.get("trial_adapter") or TASK_DEFAULT_ADAPTERS.get(drill.get("task_type"))
    return TRIAL_ADAPTERS.get(key)
