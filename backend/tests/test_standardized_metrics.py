import asyncio
import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cognitive_attributes.pvt.metrics import compute_metrics as pvt_metrics
from cognitive_attributes.multicolor_gng.metrics import compute_metrics as gng_metrics
from cognitive_attributes.discrimination.metrics import compute_metrics as discrimination_metrics
from cognitive_attributes.discrimination.tracker import DiscriminationTracker
from cognitive_attributes.pvt.tracker import PvtTracker
from cognitive_attributes.multicolor_gng.tracker import MulticolorGngTracker
from virtra_custom_lsl.event_decoder import CustomLslEvent
from services.drill_definition_registry import DrillDefinitionRegistry


class StandardizedMetricsTests(unittest.TestCase):
    def test_empty_drills_have_unavailable_rates(self):
        self.assertIsNone(pvt_metrics([])["hits_pct"])
        self.assertIsNone(gng_metrics([])["overall_accuracy_pct"])
        self.assertIsNone(discrimination_metrics([])["discrimination"]["threat_hit_rate_pct"])

    def test_pvt_denominators_rt_pool_and_trial_duration(self):
        trials = [
            {"trial_number": 1, "result": "hit", "rt_ms": 400, "activated_at": 10,
             "first_activated_at": 10, "completed_at": 10.4},
            {"trial_number": 2, "result": "miss", "rt_ms": 600, "activated_at": 11,
             "first_activated_at": 11, "completed_at": 11.8},
            {"trial_number": 3, "result": "omission", "rt_ms": None, "activated_at": 12,
             "first_activated_at": 12, "completed_at": 13},
        ]
        result = pvt_metrics(trials)
        self.assertEqual(result["hits_pct"], 33.3)
        self.assertEqual(result["response_rate_pct"], 66.7)
        self.assertEqual(result["omission_rate_pct"], 33.3)
        self.assertEqual(result["rt_median_ms"], 500)
        self.assertEqual(result["rt_min_ms"], 400)
        self.assertEqual(result["rt_max_ms"], 600)
        self.assertEqual(result["duration_s"], 3)

    def test_gng_separates_hit_shot_miss_and_no_shot(self):
        trials = [
            {"target_type": "go", "result": "hit", "rt_ms": 420, "shot_at": 1.42},
            {"target_type": "go", "result": "miss", "rt_ms": 450, "shot_at": 2.45},
            {"target_type": "go", "result": "omission", "rt_ms": None, "shot_at": None},
            {"target_type": "no_go", "result": "false_alarm", "rt_ms": 300, "shot_at": 4.3},
            {"target_type": "no_go", "result": "correct_rejection", "rt_ms": None, "shot_at": None},
        ]
        result = gng_metrics(trials)
        self.assertEqual(result["total_trials"], 5)
        self.assertEqual(result["omissions"], 1)
        self.assertEqual(result["commission_errors"], 1)
        self.assertEqual(result["hit_rate_pct"], 33.3)
        self.assertEqual(result["correct_rejection_rate_pct"], 50)
        self.assertEqual(result["overall_accuracy_pct"], 40)
        self.assertEqual(result["rt_n"], 1)
        self.assertEqual(result["rt_median_ms"], 420)
        self.assertIsNone(result["rt_stdev_ms"])

    def test_discrimination_excludes_pvt_trials_and_distinguishes_omission(self):
        trials = [
            {"phase": "pvt", "result": "hit", "rt_ms": 500, "activated_at": 10,
             "first_activated_at": 10, "completed_at": 10.5},
            {"phase": "discrimination", "target_class": "threat", "result": "hit", "rt_ms": 600,
             "activated_at": 12, "first_activated_at": 12, "completed_at": 12.6},
            {"phase": "discrimination", "target_class": "threat", "result": "miss", "rt_ms": 700,
             "shot_at": 13.7, "activated_at": 13, "first_activated_at": 13, "completed_at": 14},
            {"phase": "discrimination", "target_class": "threat", "result": "omission", "rt_ms": None,
             "activated_at": 15, "first_activated_at": 15, "completed_at": 16},
            {"phase": "discrimination", "target_class": "nonthreat", "result": "false_alarm", "rt_ms": 450,
             "activated_at": 17, "first_activated_at": 17, "completed_at": 17.45},
            {"phase": "discrimination", "target_class": "nonthreat", "result": "correct_rejection", "rt_ms": None,
             "activated_at": 18, "first_activated_at": 18, "completed_at": 19},
        ]
        result = discrimination_metrics(trials)
        disc = result["discrimination"]
        self.assertEqual(result["total_trials"], 5)
        self.assertEqual(result["duration_s"], 9)
        self.assertEqual(disc["threat_hit_rate_pct"], 33.3)
        self.assertEqual(disc["threat_omission_rate_pct"], 33.3)
        self.assertEqual(disc["commission_error_rate_pct"], 50)
        self.assertEqual(disc["discrimination_accuracy_pct"], 40)
        self.assertEqual(disc["threat_rt_median_ms"], 600)

    def test_unknown_scenarios_and_former_marksmanship_are_unscored(self):
        registry = DrillDefinitionRegistry()
        self.assertEqual(registry.get_all_drill_keys(), ["pvt", "behavioral_discrimination", "gng"])
        self.assertIsNone(registry.resolve_drill("Running Man"))
        self.assertIsNone(registry.resolve_drill("unknown scenario"))
        self.assertIsNone(registry.resolve_custom_lsl_drill("unknown"))
        self.assertTrue(registry.get_shared_events())

    def test_discrimination_tracker_scores_no_shot_at_stop(self):
        loop = asyncio.new_event_loop()
        try:
            tracker = DiscriminationTracker(lambda message: None, loop)
            tracker._broadcast_update = lambda trial: None
            tracker.reset(targets={"pvt_targets": ["neutral"], "ignore_targets": []})
            tracker.on_virtra_event("Target Activated", 10.0, {"ID": "t1", "Name": "actor_threat"})
            record = tracker.get_session_record("test", "s1", 1.0, 12.0)
            self.assertEqual(record["trials"][0]["result"], "omission")
            self.assertEqual(record["duration_s"], 2)
        finally:
            loop.close()

    def test_discrimination_ambiguous_shot_excludes_unscorable_trials(self):
        loop = asyncio.new_event_loop()
        try:
            tracker = DiscriminationTracker(lambda message: None, loop)
            tracker._broadcast_update = lambda trial: None
            tracker.reset()
            tracker.on_virtra_event("Target Activated", 10.0, {"ID": "t1", "Name": "actor_threat"})
            tracker.on_virtra_event("Target Activated", 10.1, {"ID": "n1", "Name": "actor_nonthreat"})
            tracker.on_virtra_event("Shot Fired", 10.4, {"ShotID": "s1"})
            tracker.on_virtra_event("Shot Miss", 10.5, {"ShotID": "s1"})
            record = tracker.get_session_record("test", "s1", 1.0, 12.0)
            self.assertEqual({t["result"] for t in record["trials"]}, {"unresolved"})
            result = discrimination_metrics(record["trials"])
            self.assertEqual(result["unresolved_trials"], 2)
            self.assertEqual(result["discrimination"]["total_trials"], 0)
            self.assertIsNone(result["discrimination"]["correct_rejection_rate_pct"])
        finally:
            loop.close()

    def test_pvt_tracker_finalizes_open_trial_and_uses_trial_duration(self):
        loop = asyncio.new_event_loop()
        try:
            tracker = PvtTracker(lambda message: None, loop)
            tracker._broadcast_update = lambda trial: None
            tracker.reset(targets={"valid_targets": ["neutral"], "ignore_targets": []})
            tracker.on_virtra_event("Target Activated", 10.0, {"ID": "a", "Name": "neutral"})
            tracker.on_virtra_event("Shot Fired", 10.4, {"ShotID": "s1"})
            tracker.on_virtra_event("Shot Hit", 10.42, {"ShotID": "s1", "TargetID": "a"})
            tracker.on_virtra_event("Target Activated", 11.0, {"ID": "b", "Name": "neutral"})
            record = tracker.get_session_record("test", "session", 1.0, 12.0)
            self.assertEqual(record["duration_s"], 2)
            self.assertEqual([trial["result"] for trial in record["trials"]], ["hit", "omission"])
            self.assertEqual(pvt_metrics(record["trials"])["hits_pct"], 50)
        finally:
            loop.close()

    def test_gng_tracker_timeout_is_omission(self):
        loop = asyncio.new_event_loop()
        try:
            tracker = MulticolorGngTracker(lambda message: None, loop)
            tracker._broadcast_update = lambda trial: None
            tracker.handle_event(CustomLslEvent(event_name="Target Appeared", scenario_time_s=1.0,
                data={"target_id": "a", "expected_action": "shoot"}), 1.0)
            tracker.handle_event(CustomLslEvent(event_name="Target Timed Out", scenario_time_s=2.0,
                data={"target": {"target_id": "a"}, "classification": "false_negative"}), 2.0)
            self.assertEqual(tracker.get_completed_trials()[0]["result"], "omission")
            self.assertEqual(tracker._compute_running_metrics()["omissions"], 1)
        finally:
            loop.close()

    def test_gng_single_target_missed_shot_is_not_omission(self):
        loop = asyncio.new_event_loop()
        try:
            tracker = MulticolorGngTracker(lambda message: None, loop)
            tracker._broadcast_update = lambda trial: None
            tracker.handle_event(CustomLslEvent(event_name="Target Appeared", scenario_time_s=1.0,
                data={"target_id": "a", "expected_action": "withhold"}), 1.0)
            tracker.handle_event(CustomLslEvent(event_name="Shot Fired", scenario_time_s=1.3,
                data={"shot_id": 7, "fired_time_since_start_seconds": 1.3}), 1.3)
            tracker.handle_event(CustomLslEvent(event_name="Shot Missed", scenario_time_s=1.4,
                data={"shot_id": 7}), 1.4)
            self.assertEqual(tracker.get_completed_trials()[0]["result"], "false_alarm")
            self.assertEqual(tracker._compute_running_metrics()["commission_errors"], 1)
        finally:
            loop.close()

    def test_gng_ambiguous_shot_does_not_create_false_correct_rejections(self):
        loop = asyncio.new_event_loop()
        try:
            tracker = MulticolorGngTracker(lambda message: None, loop)
            tracker._broadcast_update = lambda trial: None
            for target_id in ("a", "b"):
                tracker.handle_event(CustomLslEvent(event_name="Target Appeared", scenario_time_s=1.0,
                    data={"target_id": target_id, "expected_action": "withhold"}), 1.0)
            tracker.handle_event(CustomLslEvent(event_name="Shot Fired", scenario_time_s=1.3,
                data={"shot_id": 7}), 1.3)
            tracker.handle_event(CustomLslEvent(event_name="Shot Missed", scenario_time_s=1.4,
                data={"shot_id": 7}), 1.4)
            for target_id in ("a", "b"):
                tracker.handle_event(CustomLslEvent(event_name="Target Timed Out", scenario_time_s=2.0,
                    data={"target": {"target_id": target_id}}), 2.0)
            result = tracker._compute_running_metrics()
            self.assertEqual(result["unresolved_trials"], 2)
            self.assertEqual(result["correct_rejections"], 0)
            self.assertIsNone(result["correct_rejection_rate_pct"])
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
