"""Round and component capture for custom drills whose trial contract is pending.

The tracker preserves script metrics by component. It never manufactures PVT or
Fitts scores from an event whose semantics have not been confirmed in the lab.
"""

from virtra_custom_lsl.session import CustomLslSessionBase


class CompositeCustomTracker(CustomLslSessionBase):
    def __init__(self, broadcast_fn, loop):
        super().__init__()

    def reset(self, params=None, targets=None):
        self.current_round = 0
        self.round_history = []
        self._round_open = None

    def on_drill_event(self, event):
        pass

    def get_completed_trials(self):
        return []

    def get_session_record(self, scenario_name, session_id, started_at, stopped_at,
                           biosignals=None, drill_key=""):
        return {
            "schema_version": 3,
            "task_type": self.drill_type or "PVT_FITTS",
            "drill_key": drill_key,
            "scenario_name": scenario_name,
            "session_id": session_id,
            "started_at": started_at,
            "stopped_at": stopped_at,
            "duration_s": max(0, stopped_at - started_at),
            "rounds": self.rounds_snapshot(),
            "trials": [],
            "biosignals": biosignals or {},
        }
