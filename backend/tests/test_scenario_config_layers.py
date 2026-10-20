"""Source parsing and scenario-config loading contracts."""

import json
import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from event_decoders.native_virtra import decode_native_lsl_sample
from event_decoders.custom_lsl import CustomLslEvent
from cognitive_attributes.fitts_pvt_clay import FittsPvtClayTracker
from services.virtra_stream_config import (
    VirtraStreamConfig, _load_scenario_drills, _partition_legacy_native_aliases,
    _merge_tables, _validate,
)


class ScenarioConfigLayersTest(unittest.TestCase):
    def test_native_parser_preserves_different_metadata_formats(self):
        pairs = decode_native_lsl_sample([
            "Shot Fired", "Trainee fired", "", json.dumps([
                ["ShotID", "42"], ["FiredOrientation", "(1, 0, 0)"],
            ]),
        ])
        self.assertEqual(pairs.metadata_format, "pairs_v1")
        self.assertEqual(pairs.metadata["ShotID"], "42")

        actor = decode_native_lsl_sample([
            "Actor State Changed", "", "", json.dumps({"ActorID": "a", "State": "visible"}),
        ])
        self.assertEqual(actor.metadata_format, "object_v1")
        self.assertEqual(actor.metadata["State"], "visible")

        unknown = decode_native_lsl_sample(["Event Triggered", "", "", "not json"])
        self.assertEqual(unknown.metadata_format, "unrecognized")
        self.assertEqual(unknown.metadata, {})
        self.assertEqual(unknown.metadata_raw, "not json")

    def test_scenario_folders_supply_the_live_drill_definitions(self):
        config = VirtraStreamConfig()
        self.assertEqual(set(config.drills), {
            "pvt", "behavioral_discrimination", "multicolor_gng",
            "basepvt_fitts_gng_clay", "pvt_fitts", "fitts_pvt_clay",
            "snake_drill", "drill_3b", "basepvt_fitts_clay",
        })
        fitts = config.drill_for_custom_type("fitts_pvt")
        self.assertEqual(fitts["scenario_id"], "fitts_pvt_clay")
        self.assertEqual(fitts["scenario_revision"], 1)
        self.assertEqual(fitts["events"]["trial_start"], ["Target Appeared"])
        self.assertEqual(fitts["fields"]["trial_index"][0],
                         "data.target.presentation_index")
        self.assertEqual(config.drills["pvt"]["native_metadata_formats"], ["pairs_v1"])
        self.assertFalse((config.scenarios_path / "pvt" / "custom.toml").exists())

    def test_custom_tracker_uses_its_scenario_event_alias(self):
        tracker = FittsPvtClayTracker(None, None)
        drill = VirtraStreamConfig().drills["fitts_pvt_clay"]
        drill["events"]["trial_start"] = ["Clay Spawned"]
        tracker.configure_components(drill)
        tracker.handle_event(CustomLslEvent(event_name="Round Started", round=1), 0)
        tracker.handle_event(CustomLslEvent(
            event_name="Clay Spawned", round=1, scenario_time_s=1,
            data={"target_id": "c", "presentation_index": 1}), 1)
        self.assertEqual(len(tracker.get_completed_trials()), 1)

    def test_operator_override_changes_source_without_copying_scenario_contract(self):
        original = VirtraStreamConfig()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "virtra_streams.toml"
            path.write_text(original.path.read_text(encoding="utf-8") +
                            '\n[drills.fitts_pvt_clay]\nmetrics_source = "native"\n',
                            encoding="utf-8")
            changed = VirtraStreamConfig(path)
        self.assertEqual(changed.drill_for_native_scenario("Fitts-PVT_Clay")
                         ["metrics_source"], "native")
        self.assertEqual(changed.drills["fitts_pvt_clay"]["events"],
                         original.drills["fitts_pvt_clay"]["events"])

    def test_mismatched_scenario_folder_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "one" / "scenario.toml"
            path.parent.mkdir()
            path.write_text('schema_version = 1\n[scenario]\nid = "other"\nrevision = 1\n'
                            '[drills.other]\ncanonical_key = "other"\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid scenario id"):
                _load_scenario_drills(Path(folder))

    def test_old_grouped_native_aliases_remain_readable(self):
        config = VirtraStreamConfig()
        old = copy.deepcopy(config.drills)
        for parent, child in (("pvt", "snake_drill"),
                              ("behavioral_discrimination", "drill_3b"),
                              ("pvt_fitts", "basepvt_fitts_clay")):
            old[parent]["native_scenarios"] += old[child]["native_scenarios"]
            del old[child]
        _partition_legacy_native_aliases(old)
        combined = copy.deepcopy(config._data)
        combined["drills"] = _merge_tables(_load_scenario_drills(config.scenarios_path), old)
        _validate(combined)
        self.assertEqual(old["snake_drill"]["native_scenarios"], ["Snake Drill"])
        self.assertEqual(old["drill_3b"]["native_scenarios"], ["Drill 3B"])
        self.assertEqual(old["basepvt_fitts_clay"]["native_scenarios"],
                         ["basePVT_Fitts_Clay"])


if __name__ == "__main__":
    unittest.main()
