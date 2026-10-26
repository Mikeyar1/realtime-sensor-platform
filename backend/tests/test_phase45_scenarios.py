"""Scenario-owned trials and catalog selection must preserve scoring semantics."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from event_decoders.custom_lsl import CustomLslEvent
from metrics.catalog import METRICS, assessment_snapshot
from metrics.go_no_go import compute_metrics as score_gng
from metrics.pvt import compute_metrics as score_pvt
from metrics.discrimination import compute_metrics as score_discrimination
from metrics.fitts_pvt import compute_metrics as score_fitts_pvt
from scenarios.behavioral_discrimination.native_trials import DiscriminationTracker
from scenarios.fitts_gng_clay_colors.custom_trials import FittsGngClayColorsTracker
from services.drill_definition_registry import DrillDefinitionRegistry
from services.virtra_stream_config import VirtraStreamConfig
from trial_models.registry import tracker_for


class QuietGng(FittsGngClayColorsTracker):
    def _broadcast_update(self, trial):
        pass


class Phase45ScenarioTests(unittest.TestCase):
    def test_scenario_selects_adapter_and_keeps_old_stream_alias(self):
        registry = DrillDefinitionRegistry()
        current = registry.resolve_custom_lsl_drill("Fitts-GNG_ClayColors")
        old = registry.resolve_custom_lsl_drill("multicolor_gng")
        self.assertEqual(current["scenario_id"], "fitts_gng_clay_colors")
        self.assertEqual(old["scenario_id"], current["scenario_id"])
        self.assertIs(tracker_for(current), FittsGngClayColorsTracker)
        self.assertEqual(current["drill_key"], "gng")

    def test_recorded_withhold_action_is_correct_rejection_and_unknown_is_unresolved(self):
        drill = VirtraStreamConfig().drills["fitts_gng_clay_colors"]
        tracker = QuietGng(None, None)
        tracker.configure_components(drill)
        tracker.handle_event(CustomLslEvent(event_name="Round Started", round=1), 0)
        tracker.handle_event(CustomLslEvent(event_name="Target Appeared", round=1,
            scenario_time_s=1, data={"target_id": "A", "expected_action": "withhold_fire"}), 1)
        tracker.handle_event(CustomLslEvent(event_name="Target Timed Out", round=1,
            scenario_time_s=2, data={"target": {"target_id": "A"}}), 2)
        tracker.handle_event(CustomLslEvent(event_name="Target Appeared", round=1,
            scenario_time_s=3, data={"target_id": "B", "expected_action": "mystery"}), 3)
        tracker.handle_event(CustomLslEvent(event_name="Target Timed Out", round=1,
            scenario_time_s=4, data={"target": {"target_id": "B"}}), 4)
        trials = tracker.get_completed_trials()
        self.assertEqual([trial["result"] for trial in trials],
                         ["correct_rejection", "unresolved"])
        scored = score_gng(trials)
        self.assertEqual((scored["no_go_trials"], scored["unresolved_trials"]), (1, 1))

    def test_config_field_path_changes_without_adapter_change(self):
        drill = VirtraStreamConfig().drills["fitts_gng_clay_colors"]
        drill["fields"]["target_id"] = ["data.script_target"]
        tracker = QuietGng(None, None)
        tracker.configure_components(drill)
        tracker.handle_event(CustomLslEvent(event_name="Round Started", round=1), 0)
        tracker.handle_event(CustomLslEvent(event_name="Target Appeared", round=1,
            scenario_time_s=1, data={"script_target": "X", "expected_action": "shoot"}), 1)
        tracker.handle_event(CustomLslEvent(event_name="Shot Hit", round=1,
            scenario_time_s=1.5, data={"script_target": "X", "reaction_time_seconds": .5}), 1.5)
        self.assertEqual(tracker.get_completed_trials()[0]["rt_ms"], 500)

    def test_script_confirmed_background_miss_does_not_become_no_go_false_alarm(self):
        tracker = QuietGng(None, None)
        tracker.configure_components(VirtraStreamConfig().drills["fitts_gng_clay_colors"])
        tracker.handle_event(CustomLslEvent(event_name="Round Started", round=1), 0)
        tracker.handle_event(CustomLslEvent(event_name="Target Appeared", round=1,
            scenario_time_s=1, data={"target_id": "A", "expected_action": "withhold_fire"}), 1)
        tracker.handle_event(CustomLslEvent(event_name="Shot Fired", round=1,
            scenario_time_s=1.2, data={"shot_id": "s1"}), 1.2)
        tracker.handle_event(CustomLslEvent(event_name="Shot Missed", round=1,
            scenario_time_s=1.3, data={"shot": {"shot_id": "s1",
                "counts_toward_gng_classification": False}}), 1.3)
        tracker.handle_event(CustomLslEvent(event_name="Target Timed Out", round=1,
            scenario_time_s=2, data={"target": {"target_id": "A"}}), 2)
        self.assertEqual(tracker.get_completed_trials()[0]["result"], "correct_rejection")
        self.assertEqual(tracker._wild_shots, 1)

    def test_unclassified_shot_fired_cannot_create_no_go_false_alarm(self):
        tracker = QuietGng(None, None)
        tracker.configure_components(VirtraStreamConfig().drills["fitts_gng_clay_colors"])
        tracker.handle_event(CustomLslEvent(event_name="Round Started", round=1), 0)
        tracker.handle_event(CustomLslEvent(event_name="Target Appeared", round=1,
            scenario_time_s=1, data={"target_id": "A", "expected_action": "withhold_fire"}), 1)
        tracker.handle_event(CustomLslEvent(event_name="Shot Fired", round=1,
            scenario_time_s=1.2, data={"shot_id": "s1",
                "counts_toward_gng_classification": False}), 1.2)
        tracker.handle_event(CustomLslEvent(event_name="Target Timed Out", round=1,
            scenario_time_s=2, data={"target": {"target_id": "A"}}), 2)
        self.assertEqual(tracker.get_completed_trials()[0]["result"], "correct_rejection")

    def test_native_discrimination_target_rules_are_scenario_parameters(self):
        drill = DrillDefinitionRegistry().resolve_drill("basePVT_GNG_Actor")
        tracker = DiscriminationTracker(None, None)
        tracker.reset(params={**drill["params"], "threat_suffixes": ["_hostile"],
                              "nonthreat_suffixes": ["_civilian"]}, targets=drill["targets"])
        self.assertEqual(tracker._resolve_phase("actor_hostile"), ("discrimination", "threat"))
        self.assertEqual(tracker._resolve_phase("actor_civilian"), ("discrimination", "nonthreat"))
        self.assertIsNone(tracker._resolve_phase("actor_threat"))

    def test_metric_selection_uses_existing_formula_and_records_missing_evidence(self):
        drill = DrillDefinitionRegistry().resolve_custom_lsl_drill("Fitts-GNG_ClayColors")
        metrics = score_gng([{"target_type": "no_go", "result": "correct_rejection"}])
        snapshot = assessment_snapshot(metrics, drill)
        self.assertEqual(set(snapshot["metrics"]), set(drill["selected_metric_ids"]))
        self.assertEqual(snapshot["metrics"]["gng.correct_rejection_rate_pct"]["value"], 100.0)
        self.assertEqual(snapshot["metrics"]["gng.rt_median_ms"]["unavailable_reason"],
                         "insufficient_evidence")
        self.assertIn("response_inhibition", snapshot["skill_links"]["gng.correct_rejection_rate_pct"])
        self.assertNotIn("fitts_throughput", METRICS)

    def test_catalog_paths_exist_in_current_formula_outputs(self):
        fixture = json.loads((Path(__file__).parent / "fixtures" /
                              "native_metric_baselines.json").read_text(encoding="utf-8"))
        samples = {
            "pvt": score_pvt(fixture["pvt"]["trials"]),
            "gng": score_gng(fixture["gng"]["trials"]),
            "discrimination": score_discrimination(fixture["discrimination"]["trials"]),
            "pvt_fitts": score_fitts_pvt(fixture["pvt"]["trials"]),
        }
        for metric_id, spec in METRICS.items():
            prefix = metric_id.split(".")[0]
            output = samples[prefix]
            for part in spec.output_path:
                self.assertIn(part, output, metric_id)
                output = output[part]


if __name__ == "__main__":
    unittest.main()
