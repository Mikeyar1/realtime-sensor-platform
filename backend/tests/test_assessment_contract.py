"""Golden arithmetic and category binding checks for PVT/Fitts v1."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from assessments.catalog import CATEGORIES, DRILLS, PVT_FITTS_V1
from assessments.pvt_fitts import score, score_live, score_completed_run
from assessments.events import EventKind, normalize_custom_event
from services.virtra_stream_config import VirtraStreamConfig, _validate
from virtra_custom_lsl.event_decoder import CustomLslEvent


class AssessmentContractTests(unittest.TestCase):
    def test_catalog_has_stable_category_and_profile(self):
        self.assertEqual(DRILLS["fitts_pvt_clay"].category_id, "pvt_fitts")
        self.assertIs(CATEGORIES["pvt_fitts"].profile, PVT_FITTS_V1)
        self.assertEqual(len(PVT_FITTS_V1.metrics), 20)
        config = VirtraStreamConfig()
        self.assertEqual(config.drills["fitts_pvt_clay"]["assessment_profile"],
                         PVT_FITTS_V1.id)

    def test_missing_or_conflicting_binding_is_rejected(self):
        import copy
        config = VirtraStreamConfig()
        for value in (None, "gng"):
            with self.subTest(value=value):
                data = copy.deepcopy(config._data)
                if value is None:
                    del data["drills"]["fitts_pvt_clay"]["category_id"]
                else:
                    data["drills"]["fitts_pvt_clay"]["category_id"] = value
                with self.assertRaises(ValueError):
                    _validate(data)

    def test_terminal_aliases_normalize_to_one_kind(self):
        config = VirtraStreamConfig()
        for name in ("Scenario Stopped", "Scenario Ended", "Scenario Destroyed"):
            envelope = normalize_custom_event(CustomLslEvent(event_name=name,
                                            scenario_id="script", round=3), 12, config)
            self.assertEqual(envelope.kind, EventKind.SCENARIO_STOP)
            self.assertEqual((envelope.external_scenario_id, envelope.round_number),
                             ("script", 3))

    def test_golden_counts_and_sample_stdev(self):
        trials = [
            {"result": "hit", "rt_ms": 200},
            {"result": "miss", "rt_ms": 400},
            {"result": "omission", "rt_ms": None},
            {"result": "hit", "rt_ms": 600},
        ]
        shots = [{"shot_id": "a", "result": "miss"},
                 {"shot_id": "b", "result": "hit"},
                 {"shot_id": "c", "result": "miss"},
                 {"shot_id": "d", "result": "miss"},
                 {"shot_id": "e", "result": "hit"}]
        metrics = score(trials, shots, round_duration_s=12)
        self.assertEqual((metrics["total_trials"], metrics["hits"], metrics["misses"],
                          metrics["omissions"], metrics["total_shots_fired"],
                          metrics["shot_misses"]), (4, 2, 1, 1, 5, 3))
        self.assertEqual((metrics["rt_count"], metrics["rt_mean_ms"],
                          metrics["rt_median_ms"], metrics["rt_min_ms"],
                          metrics["rt_max_ms"]), (3, 400, 400, 200, 600))
        self.assertEqual(metrics["rt_stdev_ms"], 200)
        self.assertEqual((metrics["hits_pct"], metrics["response_rate_pct"],
                          metrics["omission_rate_pct"], metrics["hit_accuracy_pct"]),
                         (50, 75, 25, 40))

    def test_null_denominators_and_unresolved_attribution(self):
        empty = score([], [])
        self.assertIsNone(empty["hits_pct"])
        self.assertIsNone(empty["hit_accuracy_pct"])
        uncertain = score([{"result": "unresolved"}], None)
        self.assertEqual(uncertain["scoring_status"], "partial_attribution")
        self.assertIsNone(uncertain["response_rate_pct"])
        self.assertIsNone(uncertain["total_shots_fired"])
        self.assertIsNone(score([{"result": "hit", "rt_ms": 100}],
                                [{"result": "unknown"}])["shot_misses"])

    def test_live_rates_remain_defined_while_new_target_is_open(self):
        metrics = score_live([
            {"result": "hit", "rt_ms": 200},
            {"result": "open", "rt_ms": None},
        ], [{"shot_id": "a", "result": "hit"}])
        self.assertEqual(metrics["total_trials"], 2)
        self.assertEqual(metrics["hits_pct"], 50)
        self.assertEqual(metrics["response_rate_pct"], 100)
        self.assertEqual(metrics["omission_rate_pct"], 0)
        self.assertEqual(metrics["pending_presentations"], 1)
        self.assertEqual(metrics["rate_basis"], "resolved_presentations")
        open_shot = score_live([{"result": "open"}], [
            {"result": "miss"}, {"result": "unknown"}])
        self.assertEqual(open_shot["shot_misses"], 1)
        self.assertEqual(open_shot["pending_shots"], 1)

    def test_unequal_rounds_pool_presentations_and_exclude_wait(self):
        rounds = [
            {"round_number": 1, "attempt": 1, "ended_reason": "completed",
             "started_at": 10, "ended_at": 14},
            {"round_number": 2, "attempt": 1, "ended_reason": "reset",
             "started_at": 20, "ended_at": 22},
            {"round_number": 3, "attempt": 1, "ended_reason": "completed",
             "started_at": 100, "ended_at": 110},
        ]
        trials = ([{"round_number": 1, "result": "hit", "rt_ms": 100}] +
                  [{"round_number": 2, "result": "miss", "rt_ms": 1000}] +
                  [{"round_number": 3, "result": "omission"} for _ in range(3)])
        metrics = score_completed_run(rounds, trials)
        self.assertEqual((metrics["total_trials"], metrics["hits"],
                          metrics["completed_rounds"], metrics["session_duration_s"]),
                         (4, 1, 2, 14))
        self.assertEqual(metrics["hits_pct"], 25)
        self.assertEqual(metrics["excluded_rounds"], 1)


if __name__ == "__main__":
    unittest.main()
