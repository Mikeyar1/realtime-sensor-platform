"""Frozen metric projections for three current native, no-round drill models."""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cognitive_attributes.pvt.metrics import compute_metrics as score_pvt
from cognitive_attributes.multicolor_gng.metrics import compute_metrics as score_gng
from cognitive_attributes.discrimination.metrics import compute_metrics as score_disc


class NativeMetricBaselineTests(unittest.TestCase):
    def test_synthetic_native_trial_projections(self):
        fixture = json.loads((Path(__file__).with_name("fixtures") /
                              "native_metric_baselines.json").read_text(encoding="utf-8"))
        for name, scorer in (("pvt", score_pvt), ("gng", score_gng),
                             ("discrimination", score_disc)):
            with self.subTest(name=name):
                case = fixture[name]
                result = scorer(case["trials"])
                for key, expected in case["expected"].items():
                    actual = result
                    for part in key.split("."):
                        actual = actual[part]
                    self.assertEqual(actual, expected, key)


if __name__ == "__main__":
    unittest.main()
