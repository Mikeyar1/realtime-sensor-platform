"""Existing pure metric functions keyed by trial/metric set."""

from .pvt import compute_live_metrics as score_pvt
from .go_no_go import compute_live_metrics as score_go_no_go
from .discrimination import compute_live_metrics as score_discrimination
from .fitts_pvt import compute_metrics as score_fitts_pvt


METRIC_FUNCTIONS = {
    "PVT": score_pvt,
    "GNG": score_go_no_go,
    "DISCRIMINATION": score_discrimination,
    "PVT_FITTS": score_fitts_pvt,
}


def score_session(record: dict):
    metric_set = record.get("metric_set") or record.get("task_type")
    formula = METRIC_FUNCTIONS.get(metric_set)
    if formula is None:
        return None
    return formula(record.get("trials", []), record)
