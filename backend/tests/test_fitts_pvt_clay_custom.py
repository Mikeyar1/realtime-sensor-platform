"""Contract checks using the event shapes emitted by Fitts-PVT-Clay-Yong.cs."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cognitive_attributes.fitts_pvt_clay import FittsPvtClayTracker, compute_metrics
from services import database
from services.drill_definition_registry import DrillDefinitionRegistry
from services.session_lifecycle_coordinator import _db_confirm_fitts
from services.virtra_stream_config import VirtraStreamConfig
from services.virtra_scenario_orchestrator import VirTraScenarioOrchestrator
from virtra_custom_lsl.event_decoder import decode_custom_lsl_sample


def sample(name, reason, round_number, data, scenario_time,
           drill_type="fitts_pvt_clay", description=""):
    payload = {
        "schema_version": "1.0", "reason": reason,
        "scenario_name": "Fitts-PVT Clay", "scenario_id": "same-external-id",
        "drill_type": drill_type, "round": round_number,
        "scenario_time_seconds": scenario_time, "data": data,
    }
    return [name, description, name[:2].upper(), json.dumps(payload)]


@contextmanager
def custom_source_config():
    """Use the bundled custom source, isolated from operator config."""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "virtra_streams.toml"
        current = VirtraStreamConfig().path.read_text(encoding="utf-8")
        path.write_text(current, encoding="utf-8")
        with patch.dict(os.environ, {"LABREVIEW_VIRTRA_STREAMS_CONFIG": str(path)}):
            yield


@contextmanager
def native_source_config():
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "virtra_streams.toml"
        current = VirtraStreamConfig().path.read_text(encoding="utf-8")
        path.write_text(current.replace(
            '[drills.fitts_pvt_clay]\nmetrics_source = "custom"',
            '[drills.fitts_pvt_clay]\nmetrics_source = "native"'), encoding="utf-8")
        with patch.dict(os.environ, {"LABREVIEW_VIRTRA_STREAMS_CONFIG": str(path)}):
            yield


class FittsPvtClayCustomTest(unittest.TestCase):
    def test_source_is_configurable_and_native_uses_pvt_tracker(self):
        registry = DrillDefinitionRegistry()
        self.assertIsNone(registry.resolve_drill("Fitts-PVT_Clay"))
        for alias in ("fitts_pvt", "fitts_pvt_clay"):
            self.assertEqual(registry.resolve_custom_lsl_drill(alias)["drill_key"],
                             "fitts_pvt_clay")
        with native_source_config():
            native_registry = DrillDefinitionRegistry()
            native = native_registry.resolve_drill("Fitts-PVT_Clay")
            self.assertEqual(native["drill_key"], "pvt")
            self.assertEqual(native["task_type"], "PVT")
            self.assertIsNone(native_registry.resolve_custom_lsl_drill("fitts_pvt"))
        self.assertEqual(registry.resolve_drill("basePVT_Fitts_Clay")["task_type"], "PVT")

    def test_native_ui_mode_ignores_custom_scoring_events(self):
        async def run():
            messages = []
            async def send(message):
                messages.append(message)
            orchestrator = VirTraScenarioOrchestrator(
                inlets={}, broadcast_fn=send, send_fn=send,
                get_state_payload_fn=lambda: {}, error_fn=lambda *args: {},
                on_live_activate_fn=lambda: None, on_live_deactivate_fn=lambda: None,
                loop=asyncio.get_running_loop())
            native = json.dumps([["Scenario", "Fitts-PVT_Clay"]])
            orchestrator.on_sample("V_300", "VirTraEvents", 1,
                                   ["Scenario Started", "", "", native])
            self.assertEqual(orchestrator._run_source, "native")
            self.assertEqual(orchestrator._active_drill["task_type"], "PVT")
            self.assertFalse(orchestrator._active_tracker._is_valid_target("neutral"))
            self.assertTrue(orchestrator._active_tracker._is_valid_target("neutral_0"))
            orchestrator.on_sample("virtra_lsl", "VirTra Events", 2,
                                   sample("Round Started", "round_started", 1, {}, 1))
            await asyncio.sleep(.01)
            self.assertIsNone(orchestrator._active_custom_session)
            self.assertEqual(len([m for m in messages
                                  if m.get("type") == "session.scenario_started"]), 1)
        with native_source_config():
            asyncio.run(run())

    def test_round_scoring_and_reused_target_identity(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {"script_metrics": ["Metrics Updated"]},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        events = [
            sample("Scenario Started", "scenario_started", 0, {}, 0),
            sample("Round Started", "round_started", 1, {"settings": {
                "active_distance_meters": 10, "active_targets_per_round": 2}}, 1),
            sample("Target Appeared", "target_appeared", 1,
                   {"target_id": "entity-1", "presentation_index": 1}, 2),
            sample("Shot Hit", "shot_hit", 1, {"shot": {
                "counted_as_first_target_hit": True, "reaction_time_seconds": .4,
                "target": {"target_id": "entity-1", "presentation_index": 1}}}, 2.4),
            sample("Target Appeared", "target_appeared", 1,
                   {"target_id": "entity-1", "presentation_index": 2}, 4),
            sample("Target Timed Out", "target_timeout", 1,
                   {"target": {"target_id": "entity-1", "presentation_index": 2}}, 6),
            sample("Round Completed", "round_completed", 1,
                   {"metrics": {"targets_hit": 1, "targets_presented": 2}}, 7),
            sample("Round Started", "round_started", 2, {"settings": {
                "active_distance_meters": 20}}, 8),
            sample("Target Appeared", "target_appeared", 2,
                   {"target_id": "entity-1", "presentation_index": 1}, 9),
            sample("Round Reset", "round_reset", 2, {}, 10),
            sample("Round Started", "round_started", 2, {"settings": {
                "active_distance_meters": 30}}, 11),
            sample("Target Appeared", "target_appeared", 2,
                   {"target_id": "entity-1", "presentation_index": 1}, 12),
            sample("Shot Hit", "shot_hit", 2, {"shot": {
                "counted_as_first_target_hit": True, "reaction_time_seconds": .6,
                "target": {"target_id": "entity-1", "presentation_index": 1}}}, 12.6),
            sample("Round Completed", "round_completed", 2,
                   {"metrics": {"targets_hit": 1}}, 13),
            sample("Round Started", "round_started", 3, {"settings": {}}, 14),
            sample("Target Appeared", "target_appeared", 3,
                   {"target_id": "entity-1", "presentation_index": 1}, 15),
            sample("Scenario Stopped", "scenario_stopped", 3, {}, 16),
        ]
        for index, raw in enumerate(events):
            tracker.handle_event(decode_custom_lsl_sample(raw), float(index))
        rounds = tracker.rounds_snapshot()
        self.assertEqual([row["ended_reason"] for row in rounds],
                         ["completed", "reset", "completed", "incomplete"])
        self.assertEqual([row["attempt"] for row in rounds], [1, 1, 2, 1])
        self.assertEqual(rounds[0]["settings"]["active_distance_meters"], 10)
        self.assertEqual(rounds[2]["settings"]["active_distance_meters"], 30)
        self.assertEqual(rounds[0]["script_metrics"]["targets_hit"], 1)
        self.assertEqual(rounds[1]["metrics"]["unresolved_trials"], 1)
        trials = tracker.get_completed_trials()
        eligible = {(r["round_number"], r["attempt"]) for r in rounds
                    if r["ended_reason"] == "completed"}
        scored = [t for t in trials if (t["round_number"], t["round_attempt"]) in eligible]
        aggregate = compute_metrics(scored)
        self.assertEqual((aggregate["total_trials"], aggregate["hits"],
                          aggregate["omissions"], aggregate["rt_mean_ms"]), (3, 2, 1, 500))
        self.assertEqual(aggregate["misses"], 0)
        self.assertAlmostEqual(aggregate["response_rate_pct"], 66.7, places=1)
        self.assertAlmostEqual(aggregate["omission_rate_pct"], 33.3, places=1)
        self.assertEqual(aggregate["rt_median_ms"], 500)

    def test_shot_before_timeout_is_miss_with_pvt_metric_names(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        events = [
            sample("Round Started", "round_started", 1, {}, 1),
            sample("Target Appeared", "target_appeared", 1,
                   {"target_id": "a", "presentation_index": 1,
                    "spawn_timestamp_seconds": 2}, 2),
            sample("Shot Fired", "shot_fired", 1,
                   {"shot_id": 7, "visible_target_count": 1,
                    "fired_time_since_start_seconds": 2.4}, 2.4),
            sample("Shot Missed", "shot_missed", 1,
                   {"shot_id": 7, "visible_target_count": 1}, 2.5),
            sample("Target Timed Out", "target_timeout", 1,
                   {"target": {"target_id": "a", "presentation_index": 1}}, 3),
            sample("Round Completed", "round_completed", 1, {}, 4),
        ]
        for index, raw in enumerate(events):
            tracker.handle_event(decode_custom_lsl_sample(raw), float(index))
        metrics = tracker.rounds_snapshot()[0]["metrics"]
        self.assertEqual((metrics["total_trials"], metrics["hits"], metrics["misses"],
                          metrics["omissions"], metrics["response_rate_pct"]),
                         (1, 0, 1, 0, 100))
        self.assertEqual(metrics["rt_median_ms"], 400)

    def test_first_miss_then_hit_uses_first_shot_latency_and_two_shots(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        events = [
            sample("Round Started", "round_started", 1, {}, 1),
            sample("Target Appeared", "target_appeared", 1,
                   {"target_id": "a", "presentation_index": 1,
                    "spawn_timestamp_seconds": 2}, 2),
            sample("Shot Fired", "shot_fired", 1,
                   {"shot_id": "first", "visible_target_count": 1,
                    "fired_time_since_start_seconds": 2.2}, 2.2),
            sample("Shot Missed", "shot_missed", 1,
                   {"shot_id": "first", "visible_target_count": 1}, 2.25),
            sample("Shot Fired", "shot_fired", 1,
                   {"shot_id": "second", "visible_target_count": 1,
                    "fired_time_since_start_seconds": 2.5}, 2.5),
            sample("Shot Hit", "shot_hit", 1, {"shot": {
                "shot_id": "second", "counted_as_first_target_hit": True,
                "reaction_time_seconds": .5,
                "target": {"target_id": "a", "presentation_index": 1}}}, 2.55),
            sample("Round Completed", "round_completed", 1, {}, 4),
        ]
        for index, raw in enumerate(events):
            tracker.handle_event(decode_custom_lsl_sample(raw), float(index))
        metrics = tracker.rounds_snapshot()[0]["metrics"]
        self.assertEqual((metrics["hits"], metrics["misses"], metrics["rt_mean_ms"],
                          metrics["total_shots_fired"], metrics["shot_misses"],
                          metrics["hit_accuracy_pct"]), (1, 0, 200, 2, 1, 50))

    def test_native_shot_result_reconciles_before_or_after_custom_fired(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        def emit(name, reason, data, when):
            tracker.handle_event(decode_custom_lsl_sample(
                sample(name, reason, 1, data, when)), when)

        emit("Round Started", "round_started", {}, 1)
        emit("Target Appeared", "target_appeared",
             {"target_id": "a", "presentation_index": 1}, 2)
        self.assertFalse(tracker.on_native_shot_result("Shot Miss", "7"))
        emit("Shot Fired", "shot_fired",
             {"shot_id": 7, "visible_target_count": 1}, 2.4)
        self.assertEqual(tracker.rounds_snapshot()[0]["metrics"]["shot_misses"], 1)
        emit("Shot Fired", "shot_fired",
             {"shot_id": 8, "visible_target_count": 1}, 2.6)
        self.assertTrue(tracker.on_native_shot_result("Shot Hit", "8"))
        self.assertEqual(tracker.rounds_snapshot()[0]["metrics"]["pending_shots"], 0)
        emit("Target Timed Out", "target_timeout",
             {"target": {"target_id": "a", "presentation_index": 1}}, 3)
        emit("Round Completed", "round_completed", {}, 4)
        metrics = tracker.rounds_snapshot()[0]["metrics"]
        self.assertEqual((metrics["total_shots_fired"], metrics["shot_misses"]), (2, 1))
        self.assertEqual(tracker.rounds_snapshot()[0]["quality"]["native_shot_outcomes_reconciled"], 2)
        # A native hit classifies the shot, but cannot create a custom target hit.
        self.assertEqual((metrics["hits"], metrics["misses"], metrics["rt_count"]), (0, 1, 1))

    def test_custom_shot_result_overrides_native_classification(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        tracker.handle_event(decode_custom_lsl_sample(
            sample("Round Started", "round_started", 1, {}, 1)), 1)
        tracker.handle_event(decode_custom_lsl_sample(
            sample("Shot Fired", "shot_fired", 1, {"shot_id": 7}, 2)), 2)
        self.assertTrue(tracker.on_native_shot_result("Shot Hit", "7"))
        tracker.handle_event(decode_custom_lsl_sample(
            sample("Shot Missed", "shot_missed", 1, {"shot_id": 7}, 2.1)), 2.1)
        self.assertEqual(tracker.get_shots()[0]["result"], "miss")
        self.assertEqual(tracker.get_shots()[0]["outcome_source"], "custom")
        self.assertEqual(tracker.rounds_snapshot()[0]["quality"]["native_shot_outcomes_reconciled"], 0)

    def test_unreported_preset_uses_configured_default_settings(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components(VirtraStreamConfig().drills["fitts_pvt_clay"])
        tracker.handle_event(decode_custom_lsl_sample(
            sample("Round Started", "round_started", 1, {"settings": {}}, 1)), 1)
        round_record = tracker.rounds_snapshot()[0]
        self.assertEqual(round_record["preset_label"], "Default")
        self.assertEqual(round_record["preset_provenance"], "defaulted")
        self.assertEqual(round_record["settings"]["active_distance_meters"], 10)
        self.assertEqual(round_record["settings"]["active_targets_per_round"], 15)

    def test_final_aggregate_pools_trials_and_excludes_inter_round_wait(self):
        trials = [
            {"result": "hit", "rt_ms": 100, "trial_number": 1,
             "activated_at": 1, "first_activated_at": 1, "completed_at": 1.1},
            {"result": "omission", "rt_ms": None, "trial_number": 2,
             "activated_at": 2, "first_activated_at": 2, "completed_at": 3},
            {"result": "hit", "rt_ms": 300, "trial_number": 1,
             "activated_at": 100, "first_activated_at": 100, "completed_at": 100.3},
        ]
        record = {"rounds": [
            {"ended_reason": "completed", "metrics": {"duration_s": 2.0}},
            {"ended_reason": "reset", "metrics": {"duration_s": 1.0}},
            {"ended_reason": "completed", "metrics": {"duration_s": 3.0}},
        ]}
        metrics = compute_metrics(trials, record)
        self.assertEqual((metrics["total_trials"], metrics["hits"],
                          metrics["omissions"], metrics["completed_rounds"]),
                         (3, 2, 1, 2))
        self.assertAlmostEqual(metrics["hits_pct"], 66.7, places=1)
        self.assertAlmostEqual(metrics["response_rate_pct"], 66.7, places=1)
        self.assertAlmostEqual(metrics["omission_rate_pct"], 33.3, places=1)
        self.assertEqual((metrics["rt_mean_ms"], metrics["rt_median_ms"]), (200, 200))
        self.assertEqual(metrics["rt_slope_ms_per_trial"], 100)
        self.assertEqual(metrics["duration_s"], 5)
        self.assertEqual(metrics["duration_basis"], "legacy_trial_windows")
        self.assertEqual(metrics["excluded_rounds"], 1)

    def test_concurrent_unattributed_shot_does_not_become_omission(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        events = [
            sample("Round Started", "round_started", 1, {}, 1),
            sample("Target Appeared", "target_appeared", 1,
                   {"target_id": "a", "presentation_index": 1}, 2),
            sample("Target Appeared", "target_appeared", 1,
                   {"target_id": "b", "presentation_index": 2}, 2.1),
            sample("Shot Fired", "shot_fired", 1,
                   {"shot_id": 9, "visible_target_count": 2}, 2.4),
            sample("Target Timed Out", "target_timeout", 1,
                   {"target": {"target_id": "a", "presentation_index": 1}}, 3),
            sample("Target Timed Out", "target_timeout", 1,
                   {"target": {"target_id": "b", "presentation_index": 2}}, 3.1),
            sample("Round Completed", "round_completed", 1, {}, 4),
        ]
        for index, raw in enumerate(events):
            tracker.handle_event(decode_custom_lsl_sample(raw), float(index))
        metrics = tracker.rounds_snapshot()[0]["metrics"]
        self.assertEqual(metrics["unresolved_trials"], 2)
        self.assertEqual(metrics["total_trials"], 2)
        self.assertEqual(metrics["omissions"], 0)
        self.assertIsNone(metrics["omission_rate_pct"])

    def test_timeout_wrong_index_uses_unique_open_target_identity(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        def emit(name, reason, data, when):
            tracker.handle_event(decode_custom_lsl_sample(
                sample(name, reason, 1, data, when)), when)
        emit("Round Started", "round_started", {}, 1)
        emit("Target Appeared", "target_appeared",
             {"target_id": "reused", "presentation_index": 1}, 2)
        emit("Target Timed Out", "target_timeout",
             {"target": {"target_id": "reused", "presentation_index": 2}}, 3)
        emit("Target Appeared", "target_appeared",
             {"target_id": "reused", "presentation_index": 2}, 4)
        emit("Target Timed Out", "target_timeout",
             {"target": {"target_id": "reused", "presentation_index": 3}}, 5)
        emit("Round Completed", "round_completed", {}, 6)
        row = tracker.rounds_snapshot()[0]
        self.assertEqual((row["metrics"]["omissions"],
                          row["metrics"]["unresolved_trials"],
                          row["quality"]["target_index_mismatches_reconciled"]),
                         (2, 0, 2))

    def test_wrong_index_does_not_guess_between_duplicate_open_target_ids(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        for when, name, reason, data in [
            (1, "Round Started", "round_started", {}),
            (2, "Target Appeared", "target_appeared",
             {"target_id": "reused", "presentation_index": 1}),
            (2.1, "Target Appeared", "target_appeared",
             {"target_id": "reused", "presentation_index": 2}),
            (3, "Target Timed Out", "target_timeout",
             {"target": {"target_id": "reused", "presentation_index": 3}}),
            (4, "Round Completed", "round_completed", {}),
        ]:
            tracker.handle_event(decode_custom_lsl_sample(
                sample(name, reason, 1, data, when)), when)
        row = tracker.rounds_snapshot()[0]
        self.assertEqual(row["metrics"]["unresolved_trials"], 2)
        self.assertEqual(row["metrics"]["omissions"], 0)
        self.assertEqual(row["quality"]["target_index_mismatches_reconciled"], 0)

    def test_stale_timeout_with_reused_target_id_does_not_close_new_presentation(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        for when, name, reason, data in [
            (1, "Round Started", "round_started", {}),
            (2, "Target Appeared", "target_appeared",
             {"target_id": "reused", "presentation_index": 2,
              "spawn_timestamp_seconds": 2}),
            (3, "Target Timed Out", "target_timeout",
             {"target": {"target_id": "reused", "presentation_index": 3,
                         "spawn_timestamp_seconds": 1}}),
            (4, "Round Completed", "round_completed", {}),
        ]:
            tracker.handle_event(decode_custom_lsl_sample(
                sample(name, reason, 1, data, when)), when)
        row = tracker.rounds_snapshot()[0]
        self.assertEqual(row["metrics"]["unresolved_trials"], 1)
        self.assertEqual(row["metrics"]["omissions"], 0)

    def test_native_start_waits_for_registered_custom_stream(self):
        async def run():
            messages = []
            async def send(message):
                messages.append(message)
            orchestrator = VirTraScenarioOrchestrator(
                inlets={}, broadcast_fn=send, send_fn=send,
                get_state_payload_fn=lambda: {}, error_fn=lambda *args: {},
                on_live_activate_fn=lambda: None, on_live_deactivate_fn=lambda: None,
                loop=asyncio.get_running_loop())
            native = json.dumps([["Scenario", "Fitts-PVT_Clay"]])
            orchestrator.on_sample("V_300", "VirTraEvents", 1,
                                   ["Scenario Started", "", "", native])
            self.assertIsNone(orchestrator._active_drill)
            orchestrator.on_sample("virtra_lsl", "VirTra Events", 2,
                                   sample("Round Started", "round_started", 1,
                                          {"settings": {"active_distance_meters": 10}}, 1,
                                          drill_type="fitts_pvt"))
            await asyncio.sleep(.01)
            self.assertEqual(orchestrator._run_source, "custom")
            self.assertEqual(orchestrator._active_custom_drill["drill_key"], "fitts_pvt_clay")
            self.assertEqual(len([m for m in messages
                                  if m.get("type") == "session.scenario_started"]), 1)
            waiting_before = len([m for m in messages
                                  if m.get("type") == "session.scoring_waiting"])
            orchestrator.on_sample("V_300", "VirTraEvents", 3,
                                   ["Scenario Started", "", "", native])
            await asyncio.sleep(.01)
            self.assertEqual(len([m for m in messages
                                  if m.get("type") == "session.scoring_waiting"]),
                             waiting_before)
            self.assertEqual(orchestrator._waiting_for_custom, "")
        with custom_source_config():
            asyncio.run(run())

    def test_matched_native_outcome_updates_custom_round_projection(self):
        async def run():
            messages = []
            async def send(message):
                messages.append(message)
            orchestrator = VirTraScenarioOrchestrator(
                inlets={}, broadcast_fn=send, send_fn=send,
                get_state_payload_fn=lambda: {}, error_fn=lambda *args: {},
                on_live_activate_fn=lambda: None, on_live_deactivate_fn=lambda: None,
                loop=asyncio.get_running_loop())
            native_scenario = json.dumps([["Scenario", "Fitts-PVT_Clay"]])
            orchestrator.on_sample("V_300", "VirTraEvents", 1,
                                   ["Scenario Started", "", "", native_scenario])
            def custom(timestamp, name, reason, data):
                orchestrator.on_sample("virtra_lsl", "VirTra Events", timestamp,
                                       sample(name, reason, 1, data, timestamp))
            custom(2, "Round Started", "round_started", {})
            self.assertIn(orchestrator._custom_context["context_match_status"],
                          {"matched_name", "matched_alias"})
            custom(3, "Shot Fired", "shot_fired", {"shot_id": 7})
            self.assertEqual(orchestrator._active_custom_session.rounds_snapshot()[0]
                             ["metrics"]["pending_shots"], 1)
            orchestrator.on_sample("V_300", "VirTraEvents", 3.01,
                                   ["Shot Miss", "", "", json.dumps([["ShotID", 7]])])
            await asyncio.sleep(.01)
            self.assertEqual(orchestrator._active_custom_session.rounds_snapshot()[0]
                             ["metrics"]["shot_misses"], 1)
            self.assertTrue(any(message.get("type") == "assessment.round_projection"
                                for message in messages))
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "reconcile.db"))
            with custom_source_config():
                asyncio.run(run())

    def test_completed_round_is_durable_before_run_ends(self):
        async def run():
            messages = []
            async def send(message):
                messages.append(message)
            orchestrator = VirTraScenarioOrchestrator(
                inlets={}, broadcast_fn=send, send_fn=send,
                get_state_payload_fn=lambda: {}, error_fn=lambda *args: {},
                on_live_activate_fn=lambda: None, on_live_deactivate_fn=lambda: None,
                loop=asyncio.get_running_loop())
            orchestrator._participant_name = "Fitts Tester"
            stream = lambda ts, raw: orchestrator.on_sample(
                "virtra_lsl", "VirTra Events", ts, raw)
            stream(1, sample("Scenario Started", "scenario_started", 0, {}, 0))
            stream(2, sample("Round Started", "round_started", 1,
                             {"settings": {"active_distance_meters": 10}}, 1))
            stream(3, sample("Target Appeared", "target_appeared", 1,
                             {"target_id": "target-a", "presentation_index": 1}, 2))
            stream(4, sample("Shot Hit", "shot_hit", 1, {"shot": {
                "target": {"target_id": "target-a", "presentation_index": 1},
                "counted_as_first_target_hit": True,
                "reaction_time_seconds": .5}}, 2.5))
            stream(5, sample("Round Completed", "round_completed", 1,
                             {"metrics": {"targets_hit": 1}}, 3))
            from services import round_repository
            active = round_repository.get_run(orchestrator._custom_session_id)
            self.assertEqual(active["status"], "active")
            self.assertEqual(active["rounds"][0]["status"], "completed")
            self.assertEqual(active["rounds"][0]["save_status"], "draft")
            stream(6, sample("Scenario Stopped", "scenario_stopped", 1, {}, 4))
            for _ in range(50):
                await asyncio.sleep(.02)
                if any(m.get("type") == "session.metrics_ready" for m in messages):
                    break
            saved_uuid = next(m["session_uuid"] for m in messages
                              if m.get("type") == "session.metrics_ready")
            self.assertTrue(_db_confirm_fitts(
                saved_uuid, "", [{"round_number": 1, "attempt": 1,
                                   "participant_name": "Fitts Tester"}])[0])
            rows = round_repository.list_history("Fitts Tester")
            self.assertEqual(len(rows), 1)
            detail = rows[0]
            self.assertEqual(detail["metrics"]["hits"], 1)
            self.assertEqual(detail["metrics"]["rt_mean_ms"], 500)
            self.assertEqual(detail["settings"]["active_distance_meters"], 10)
            self.assertEqual(detail["trials"][0]["component"], "pvt_fitts")
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "fitts.db"))
            with custom_source_config():
                asyncio.run(run())

    def test_emitted_preset_labels_and_round_participants_drive_history(self):
        tracker = FittsPvtClayTracker(None, None)
        tracker.configure_components({
            "default_component": "pvt_fitts", "events": {},
            "components": {"pvt_fitts": {"phase_values": []}},
        })
        events = [
            sample("Scenario Started", "scenario_started", 0, {}, 0),
            sample("Round Started", "round_started", 1,
                   {"settings": {"preset_name": "Baseline"}}, 1),
            sample("Target Appeared", "target_appeared", 1,
                   {"target_id": "a", "presentation_index": 1}, 2),
            sample("Target Timed Out", "target_timeout", 1,
                   {"target": {"target_id": "a", "presentation_index": 1}}, 3),
            sample("Round Completed", "round_completed", 1, {}, 4),
            sample("Round Reset", "round_reset", 1, {}, 4.1),
            sample("Preset Changed", "preset_changed", 1, {}, 5,
                   description="Fitts-PVT_Clay preset changed to Hard"),
            sample("Round Started", "round_started", 2, {"settings": {}}, 6),
            sample("Target Appeared", "target_appeared", 2,
                   {"target_id": "b", "presentation_index": 1}, 7),
            sample("Shot Hit", "shot_hit", 2, {"shot": {
                "counted_as_first_target_hit": True, "reaction_time_seconds": .5,
                "target": {"target_id": "b", "presentation_index": 1}}}, 7.5),
            sample("Round Completed", "round_completed", 2, {}, 8),
        ]
        for index, raw in enumerate(events):
            tracker.handle_event(decode_custom_lsl_sample(raw), float(index))
        rounds = tracker.rounds_snapshot()
        self.assertEqual([r["preset_label"] for r in rounds], ["Baseline", "Hard"])
        self.assertEqual([r["ended_reason"] for r in rounds], ["completed", "completed"])

        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "rounds.db"))
            database.insert_session("", {
                "session_id": "mixed-run", "drill_key": "fitts_pvt_clay",
                "task_type": "PVT_FITTS", "scenario_name": "Fitts-PVT_Clay",
                "source": "custom", "rounds": rounds,
                "trials": tracker.get_completed_trials(),
            }, {"total_trials": 2})
            self.assertFalse(database.confirm_fitts_round_participants(
                "mixed-run", "", [{"round_number": 1, "participant_name": "Alice"}])[0])
            self.assertTrue(database.confirm_fitts_round_participants(
                "mixed-run", "", [
                    {"round_number": 1, "participant_name": "Alice"},
                    {"round_number": 2, "participant_name": "Bob"},
                ])[0])
            alice = database.get_sessions_for_participant("Alice")
            bob = database.get_sessions_for_participant("Bob")
            self.assertIn("Alice", database.get_all_participant_names())
            self.assertIn("Bob", database.get_all_participant_names())
            self.assertEqual([(r["preset_label"], r["round_number"]) for r in alice],
                             [("Baseline", 1)])
            self.assertEqual([(r["preset_label"], r["round_number"]) for r in bob],
                             [("Hard", 2)])
            detail = database.get_session_detail("mixed-run", "Bob", 2, 1)
            self.assertEqual(len(detail["rounds"]), 1)
            self.assertEqual(detail["rounds"][0]["participant_name"], "Bob")
            self.assertEqual(detail["rounds"][0]["preset_label"], "Hard")
            self.assertEqual(len(detail["trials"]), 1)
            self.assertIsNone(database.get_session_detail("mixed-run", "Bob", 1, 1))


if __name__ == "__main__":
    unittest.main()
