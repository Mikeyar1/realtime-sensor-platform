"""Score Fitts-PVT Clay custom LSL rounds with the existing PVT metric contract.

An unattributed shot during concurrent presentations makes those trial
outcomes uncertain; it is never silently counted as an omission.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite

from virtra_custom_lsl.session import CustomLslSessionBase


from metrics.fitts_pvt import compute_metrics
from assessments.pvt_fitts import score as score_pvt_fitts, score_live


def _number(value):
    try:
        result = float(value)
        return result if isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


class FittsPvtClayTracker(CustomLslSessionBase):
    def __init__(self, broadcast_fn, loop):
        super().__init__()
        self.reset()

    def reset(self, params=None, targets=None):
        self.current_round = 0
        self.round_history = []
        self._round_open = None
        self.component = ""
        self._trials: list[dict] = []
        self._open: list[dict] = []
        self._unassigned_shots: list[dict] = []
        self._seen_shot_ids: set[str] = set()
        self._shots: dict[tuple[int, int, str], dict] = {}
        self._shot_identity_incomplete: set[tuple[int, int]] = set()
        self._native_outcomes: dict[str, str] = {}
        self._native_reconciled: dict[tuple[int, int], set[str]] = {}
        self._target_index_reconciled: dict[tuple[int, int], int] = {}

    def _on_round_start(self):
        self._open = []
        self._seen_shot_ids = set()
        self._native_outcomes = {}

    def on_round_started(self, round_number, settings, metrics):
        self._on_round_start()
        if self._round_open is not None:
            # The script has no categorical difficulty level. Preserve the
            # actual active parameters for every round instead.
            self._round_open.settings = dict(settings) if isinstance(settings, dict) else {}

    def on_round_completed(self, round_number, metrics):
        if not self.round_history:
            return
        row = self.round_history[-1]
        for trial in self._open:
            trial["result"] = "unresolved"
        self._open = []
        trials = [trial for trial in self._trials
                  if trial["round_number"] == row.round_number and
                  trial["round_attempt"] == row.attempt]
        shots = self._scorable_shots(row.round_number, row.attempt)
        computed = score_pvt_fitts(trials, shots,
                                   round_duration_s=max(0, row.ended_at - row.started_at))
        computed["duration_basis"] = "round_boundaries"
        row.metrics = computed
        row.script_metrics = dict(metrics) if isinstance(metrics, dict) else {}
        if "pvt_fitts" in row.components:
            row.components["pvt_fitts"]["metrics"] = computed
            row.components["pvt_fitts"]["script_metrics"] = row.script_metrics

    def on_drill_event(self, event):
        if self._round_open is None:
            return
        data = event.data
        name = event.event_name
        target = data.get("target")
        if self.event_matches("trial_start", name, "Target Appeared"):
            self._open_trial(data, event)
        elif self.event_matches("shot_fired", name, "Shot Fired"):
            shot = data.get("shot") if isinstance(data.get("shot"), dict) else data
            self._upsert_shot(shot, event)
            self._record_shot(shot, event)
        elif self.event_matches("shot_hit", name, "Shot Hit") and event.reason == "shot_hit":
            shot = data.get("shot")
            self._upsert_shot(shot, event, "hit")
            if isinstance(shot, dict) and shot.get("counted_as_first_target_hit") is True:
                self._close_target(shot.get("target"), "hit", event,
                                   _number(self.field_value(event, "reaction_time_seconds", (
                                       "data.shot.reaction_time_seconds",))))
        elif self.event_matches("trial_timeout", name, "Target Timed Out") and isinstance(target, dict):
            self._close_target(target, "timeout", event, None)
        elif self.event_matches("shot_miss", name, "Shot Missed"):
            shot = data.get("shot") if isinstance(data.get("shot"), dict) else data
            self._upsert_shot(shot, event, "miss")
            self._record_shot(shot, event)
        self._refresh_live_metrics()
        if "pvt_fitts" in self._round_open.components:
            script_metrics = data.get("metrics")
            if not isinstance(script_metrics, dict) and self.event_matches(
                    "script_metrics", name, "Metrics Updated"):
                script_metrics = data
            if isinstance(script_metrics, dict):
                self._round_open.components["pvt_fitts"]["script_metrics"] = dict(script_metrics)

    def _refresh_live_metrics(self):
        if self._round_open is None:
            return
        row = self._round_open
        trials = [trial for trial in self._trials
                  if trial["round_number"] == row.round_number and
                  trial["round_attempt"] == row.attempt]
        computed = score_live(trials, self._scorable_shots(row.round_number, row.attempt))
        row.metrics = computed
        if "pvt_fitts" in row.components:
            row.components["pvt_fitts"]["metrics"] = computed

    def on_native_shot_result(self, event_type: str, shot_id: str) -> bool:
        """Reconcile only a shot already emitted by this custom drill.

        Native results can precede their custom Shot Fired copy by milliseconds.
        Keep the result within the current round until that copy arrives. A
        native hit classifies the shot, but only a custom counted target hit
        can close a valid presentation.
        """
        row = self._round_open
        if row is None or event_type not in {"Shot Hit", "Shot Miss"} or not shot_id:
            return False
        outcome = "hit" if event_type == "Shot Hit" else "miss"
        self._native_outcomes[shot_id] = outcome
        key = (row.round_number, row.attempt, shot_id)
        shot = self._shots.get(key)
        if shot is None or shot["result"] != "unknown":
            return False
        shot["result"] = outcome
        shot["outcome_source"] = "native_shot_id"
        self._native_reconciled.setdefault(key[:2], set()).add(shot_id)
        self._refresh_live_metrics()
        return True

    def _upsert_shot(self, shot, event, result="unknown"):
        if not isinstance(shot, dict) or self._round_open is None:
            return
        shot_id = str(self.field_value(event, "shot_id", (
            "data.shot.shot_id", "data.shot_id")) or shot.get("shot_id") or "").strip()
        if not shot_id:
            self._shot_identity_incomplete.add((self._round_open.round_number,
                                                self._round_open.attempt))
            return  # No reliable identity: do not invent a unique shot.
        row = self._round_open
        key = (row.round_number, row.attempt, shot_id)
        saved = self._shots.setdefault(key, {
            "round_number": row.round_number, "round_attempt": row.attempt,
            "shot_id": shot_id, "fired_at": _number(
                shot.get("fired_time_since_start_seconds")) or event.scenario_time_s,
            "result": "unknown",
        })
        if result != "unknown":
            saved["result"] = result
            saved["outcome_source"] = "custom"
            self._native_reconciled.setdefault(key[:2], set()).discard(shot_id)
        elif saved["result"] == "unknown" and shot_id in self._native_outcomes:
            saved["result"] = self._native_outcomes[shot_id]
            saved["outcome_source"] = "native_shot_id"
            self._native_reconciled.setdefault(key[:2], set()).add(shot_id)

    def _round_shots(self, round_number, attempt):
        return [deepcopy(shot) for (number, run_attempt, _), shot in self._shots.items()
                if number == round_number and run_attempt == attempt]

    def _scorable_shots(self, round_number, attempt):
        return (None if (round_number, attempt) in self._shot_identity_incomplete
                else self._round_shots(round_number, attempt))

    def _open_trial(self, target, event):
        target_id = str(self.field_value(event, "target_id", (
            "data.target_id", "data.target.target_id")) or target.get("target_id") or "")
        index = self.field_value(event, "trial_index", (
            "data.presentation_index", "data.target.presentation_index"))
        if index is None:
            index = target.get("presentation_index")
        if not target_id or index is None:
            return
        if any(t["target_id"] == target_id and t["presentation_index"] == index
               and t["round_number"] == self._round_open.round_number
               and t["round_attempt"] == self._round_open.attempt
               for t in self._trials):
            return
        trial = {
            "round_number": self._round_open.round_number,
            "round_attempt": self._round_open.attempt,
            "component_key": "pvt_fitts",
            "component": "pvt_fitts",
            "target_id": target_id,
            "presentation_index": index,
            "trial_number": index,
            "activated_at": event.scenario_time_s,
            "first_activated_at": event.scenario_time_s,
            "result": "open",
            "rt_ms": None,
            "spawn_timestamp_seconds": _number(target.get("spawn_timestamp_seconds")),
            "pending_shot_id": None,
            "pending_shot_at": None,
            "ambiguous_shot": False,
            "target_position": target.get("position"),
        }
        self._trials.append(trial)
        self._open.append(trial)

    def _close_target(self, target, result, event, rt_s):
        if not isinstance(target, dict):
            return
        target_id = str(self.field_value(event, "target_id", (
            "data.target.target_id", "data.shot.target.target_id")) or target.get("target_id") or "")
        index = self.field_value(event, "trial_index", (
            "data.target.presentation_index", "data.shot.target.presentation_index"))
        if index is None:
            index = target.get("presentation_index")
        candidates = [trial for trial in self._open if trial["target_id"] == target_id]
        spawn = _number(target.get("spawn_timestamp_seconds"))
        if spawn is not None and any(trial.get("spawn_timestamp_seconds") is not None
                                     for trial in candidates):
            candidates = [trial for trial in candidates
                          if trial.get("spawn_timestamp_seconds") is not None and
                          abs(trial["spawn_timestamp_seconds"] - spawn) <= .001]
        matches = [trial for trial in candidates
                   if index is None or trial["presentation_index"] == index]
        if not matches and index is not None:
            # VirTra can report the next presentation index on a timeout.
            # Target identity and spawn time resolve it if exactly one matching
            # target is open; never guess when that identity is ambiguous.
            if len(candidates) == 1:
                matches = candidates
                key = (self._round_open.round_number, self._round_open.attempt)
                self._target_index_reconciled[key] = self._target_index_reconciled.get(key, 0) + 1
        if index is None and len(matches) != 1:
            return
        if not matches:
            return
        trial = matches[-1]
        if result == "timeout":
            if trial["pending_shot_at"] is not None:
                result = "miss"
                trial["shot_id"] = trial["pending_shot_id"]
                trial["shot_at"] = trial["pending_shot_at"]
                trial["rt_ms"] = trial.get("pending_rt_ms")
            else:
                result = "unresolved" if trial["ambiguous_shot"] else "omission"
        trial["result"] = result
        trial["completed_at"] = event.scenario_time_s
        trial["ended_at"] = event.scenario_time_s
        if result == "hit":
            # The first attributable shot defines response latency, even when
            # a later shot is the first one to hit the target.
            trial["rt_ms"] = (trial.get("pending_rt_ms") if trial.get("pending_rt_ms") is not None
                              else round(rt_s * 1000, 1) if rt_s is not None and rt_s >= 0
                              else None)
        self._open.remove(trial)

    def _record_shot(self, shot, event):
        if not isinstance(shot, dict):
            return
        shot_id = str(self.field_value(event, "shot_id", (
            "data.shot.shot_id", "data.shot_id")) or shot.get("shot_id") or "")
        if shot_id and shot_id in self._seen_shot_ids:
            return
        if shot_id:
            self._seen_shot_ids.add(shot_id)
        if len(self._open) == 1 and shot.get("visible_target_count") == 1:
            trial = self._open[0]
            if trial["pending_shot_at"] is None:
                fired_at = _number(shot.get("fired_time_since_start_seconds"))
                spawned_at = trial.get("spawn_timestamp_seconds")
                rt_s = (fired_at - spawned_at if fired_at is not None and spawned_at is not None
                        else event.scenario_time_s - trial["activated_at"])
                if rt_s >= 0:
                    trial["pending_shot_id"] = shot_id or None
                    trial["pending_shot_at"] = event.scenario_time_s
                    trial["pending_rt_ms"] = round(rt_s * 1000, 1)
            return
        if self._open:
            for trial in self._open:
                trial["ambiguous_shot"] = True
        self._unassigned_shots.append({
            "round_number": self._round_open.round_number,
            "round_attempt": self._round_open.attempt,
            "shot_id": shot_id or None,
            "scenario_time_s": event.scenario_time_s,
        })

    def get_completed_trials(self):
        return deepcopy(self._trials)

    def rounds_snapshot(self):
        rows = super().rounds_snapshot()
        for row in rows:
            complete = (row["round_number"], row.get("attempt", 1)) not in self._shot_identity_incomplete
            key = (row["round_number"], row.get("attempt", 1))
            row["quality"] = {
                "shot_identity_complete": complete,
                "native_shot_outcomes_reconciled": len(self._native_reconciled.get(key, ())),
                "target_index_mismatches_reconciled": self._target_index_reconciled.get(key, 0),
            }
        return rows

    def get_shots(self):
        return [deepcopy(shot) for shot in self._shots.values()]

    def get_session_record(self, scenario_name, session_id, started_at, stopped_at,
                           biosignals=None, drill_key=""):
        return {
            "schema_version": 3,
            "task_type": "PVT_FITTS",
            "drill_key": drill_key,
            "scenario_name": scenario_name,
            "session_id": session_id,
            "started_at": started_at,
            "stopped_at": stopped_at,
            "duration_s": max(0, stopped_at - started_at),
            "rounds": self.rounds_snapshot(),
            "trials": self.get_completed_trials(),
            "shots": self.get_shots(),
            "shot_identity_complete": not self._shot_identity_incomplete,
            "unassigned_shots": deepcopy(self._unassigned_shots),
            "biosignals": biosignals or {},
        }
