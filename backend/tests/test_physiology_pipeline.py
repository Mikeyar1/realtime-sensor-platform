"""Physiology source decoding, source isolation, and card selection contracts."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cognitive_skills.catalog import selected_for_skills
from metrics.cards import catalog_payload
from physiology.metrics import SignalWindow
from physiology.source_registry import SignalSourceRegistry
from services.drill_definition_registry import DrillDefinitionRegistry


class PhysiologyPipelineTests(unittest.TestCase):
    def setUp(self):
        self.sources = SignalSourceRegistry()

    def decode(self, name, kind, timestamp, data, source_id=""):
        spec = self.sources.match(name, kind, source_id)
        self.assertIsNotNone(spec)
        return self.sources.decode(spec, name, kind, timestamp, data, source_id=source_id)

    def test_known_layouts_and_invalid_values(self):
        hr = self.decode("Polar HR", "Heart Rate", 1, [81], "polar")
        self.assertEqual((hr.signal, hr.value, hr.unit, hr.source_key),
                         ("hr", 81, "bpm", "polar"))
        self.assertEqual(self.decode("Polar PPI", "PPI", 2, [0, 1010]).value, 1010)
        gaze = [0] * 22
        gaze[2], gaze[9] = 3.1, 3.8
        self.assertEqual(self.decode("Neon Gaze", "Gaze", 3, gaze).value_2, 3.8)
        gaze[2], gaze[9] = float("nan"), 100
        self.assertIsNone(self.decode("Neon Gaze", "Gaze", 4, gaze))
        self.assertIsNone(self.decode("Polar HR", "HR", float("nan"), [80]))
        self.assertIsNone(self.sources.match("Other Gaze", "Gaze"))

    def test_new_layout_requires_only_source_config(self):
        config = '''schema_version = 1
[[sources]]
id = "alternate_heart_rate"
signal = "hr"
decoder = "numeric_channels"
types = ["alternate_hr"]
channels = [3]
units = ["bpm"]
minimums = [30]
maximums = [250]
'''
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sources.toml"
            path.write_text(config, encoding="utf-8")
            registry = SignalSourceRegistry(path)
            spec = registry.match("sensor", "alternate_hr")
            observation = registry.decode(spec, "sensor", "alternate_hr", 12,
                                          [0, 0, 0, 72], source_id="unit-2")
            self.assertEqual((observation.value, observation.source_key), (72, "unit-2"))

    def test_ppi_never_pairs_different_sources_and_uses_lsl_order(self):
        window = SignalWindow()
        for timestamp, value, source in ((2, 900, "polar-a"), (1, 800, "polar-a"),
                                         (3, 1000, "polar-a"), (4, 1400, "polar-b")):
            window.add(self.decode("PPI", "PPI", timestamp, [0, value], source))
        summary, quality = window.summary()
        self.assertEqual(summary["hrv_rmssd_ms"], 100)
        self.assertEqual(summary["hrv_n_intervals"], 3)
        self.assertEqual(quality["selected_sources"]["ppi"], "polar-a")

    def test_skill_selection_is_adapter_compatible(self):
        selected = selected_for_skills(["pvt_fitts.total_trials"],
                                       ["reaction_latency"], "custom_fitts_pvt")
        self.assertIn("pvt_fitts.rt_median_ms", selected)
        self.assertFalse(any(item.startswith("pvt.") for item in selected))
        native = selected_for_skills(["pvt.total_trials"], ["reaction_latency"],
                                     "native_target_response")
        self.assertIn("pvt.rt_mean_ms", native)
        self.assertFalse(any(item.startswith("pvt_fitts.") for item in native))
        registry = DrillDefinitionRegistry()
        custom = registry.resolve_custom_lsl_drill("fitts_pvt_clay")
        self.assertIn("pvt_fitts.rt_median_ms", custom["selected_metric_ids"])
        self.assertNotIn("pvt.rt_mean_ms", custom["selected_metric_ids"])
        native_drill = registry.resolve_drill("basePVT_Fitts_Clay")
        self.assertIn("pvt.rt_mean_ms", native_drill["selected_metric_ids"])

    def test_cards_have_stable_live_and_history_paths(self):
        payload = catalog_payload()
        cards = {card["metric_id"]: card for card in payload["cards"]}
        self.assertEqual(cards["physio.hr_mean_bpm"]["live_event"],
                         "session.biosignal_update")
        self.assertEqual(cards["physio.hr_mean_bpm"]["round_history_path"],
                         ["physiology", "hr_mean_bpm"])
        self.assertIn("reaction_latency", cards["pvt.rt_median_ms"]["skills"])


if __name__ == "__main__":
    unittest.main()
