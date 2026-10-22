"""Source decoding and scenario resolution without metric inference."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from event_decoders.source_event import decode_source_event
from scenarios.registry import ScenarioRegistry
from services.virtra_stream_config import VirtraStreamConfig, _validate


class SourceEventContractTests(unittest.TestCase):
    def test_custom_primary_and_native_secondary_resolve_same_scenario(self):
        registry = ScenarioRegistry(VirtraStreamConfig())
        custom = decode_source_event("custom", ["Scenario Started", "", "", json.dumps({
            "drill_type": "fitts_pvt", "scenario_name": "Fitts-PVT_Clay",
            "scenario_id": "run-1", "data": {}})])
        native = decode_source_event("native", ["Scenario Started", "", "", json.dumps([
            ["Scenario", "Fitts-PVT_Clay"], ["SessionID", "native-1"]])])
        self.assertEqual((custom.status, native.status), ("decoded", "decoded"))
        self.assertEqual((registry.resolve(custom).scenario_id,
                          registry.resolve(custom).role), ("fitts_pvt_clay", "primary"))
        self.assertEqual((registry.resolve(native).scenario_id,
                          registry.resolve(native).role), ("fitts_pvt_clay", "secondary"))

    def test_native_object_and_pairs_formats_and_unknown_scenario(self):
        registry = ScenarioRegistry(VirtraStreamConfig())
        for payload in ({"Scenario": "PVT"}, [["Scenario", "PVT"]]):
            event = decode_source_event("native", ["Scenario Started", "", "", json.dumps(payload)])
            self.assertEqual(event.status, "decoded")
            self.assertEqual(registry.resolve(event).scenario_id, "pvt")
        unknown = decode_source_event("native", ["Scenario Started", "", "",
                                                        '{"Scenario":"Unknown"}'])
        self.assertIsNone(registry.resolve(unknown))

    def test_malformed_event_cannot_resolve(self):
        registry = ScenarioRegistry(VirtraStreamConfig())
        for source, channels in (("custom", ["Shot Fired", "", "", "{bad"]),
                                 ("native", ["", "", "", "{}"])):
            event = decode_source_event(source, channels)
            self.assertEqual(event.status, "malformed")
            self.assertIsNone(registry.resolve(event))

    def test_invalid_field_path_or_metric_set_is_rejected(self):
        config = VirtraStreamConfig()
        import copy
        for modify in (
                lambda data: data["drills"]["fitts_pvt_clay"]["fields"].update(
                    target_id=["data.target[0]"]),
                lambda data: data["drills"]["fitts_pvt_clay"]["components"]["pvt_fitts"].update(
                    metric_set="INVENTED"),
                lambda data: data["drills"]["fitts_pvt_clay"].update(trial_model="go_no_go"),
                lambda data: data["drills"]["fitts_pvt_clay"].update(metric_ids=["invented"])):
            data = copy.deepcopy(config._data)
            modify(data)
            with self.assertRaises(ValueError):
                _validate(data)


if __name__ == "__main__":
    unittest.main()
