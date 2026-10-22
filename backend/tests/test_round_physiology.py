"""Round-window physiology is isolated and reloadable by participant."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import database, round_repository as repo
from physiology.windows import RoundPhysiologyCapture, rebuild_round_physiology
from physiology.observation import SignalObservation


class RoundPhysiologyTests(unittest.TestCase):
    @staticmethod
    def sample(signal, source, timestamp, data):
        value = data[1] if signal == "ppi" else data[2] if signal == "gaze" else data[0]
        second = data[9] if signal == "gaze" else None
        return SignalObservation(signal, source, timestamp, timestamp, value, second)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        database.init_db(str(Path(self.folder.name) / "physio.db"))
        repo.capture_run("run", drill_key="fitts_pvt_clay", category_id="pvt_fitts",
                         profile_id="pvt_fitts_core", profile_version=1,
                         scenario_name="Fitts-PVT_Clay", external_scenario_id="script",
                         source="custom", started_at=0)
        for number, start, end in ((1, 1, 5), (2, 10, 15)):
            repo.capture_round("run", {
                "round_id": f"r{number}", "round_number": number, "attempt": 1,
                "started_at": start, "ended_at": end, "ended_reason": "completed",
                "metrics": {"hits": number},
            })

    def tearDown(self):
        self.folder.cleanup()

    def test_distinct_rounds_exclude_wait_and_cross_round_ppi(self):
        capture = RoundPhysiologyCapture(batch_size=2)
        capture.begin("r1", 1)
        capture.ingest(self.sample("hr", "polar:hr", 1.5, [60]))
        capture.ingest(self.sample("hr", "polar:hr", 2, [80]))
        capture.ingest(self.sample("ppi", "polar:ppi", 2, [0, 1000]))
        capture.ingest(self.sample("ppi", "polar:ppi", 3, [0, 1100]))
        gaze = [0] * 10
        gaze[2], gaze[9] = 3.0, 4.0
        capture.ingest(self.sample("gaze", "neon:gaze", 4, gaze))
        self.assertTrue(capture.close("r1", 5))
        capture.ingest(self.sample("hr", "polar:hr", 4.9, [100]))  # late delivery
        capture.ingest(self.sample("hr", "polar:hr", 7, [200]))    # between rounds
        first = capture.finalize("r1")
        self.assertEqual(first["physiology"]["hr_mean_bpm"], 80)
        self.assertEqual(first["physiology"]["hr_n_samples"], 3)
        self.assertEqual(first["physiology"]["hrv_rmssd_ms"], 100)
        self.assertEqual(first["physiology"]["pupil_mean_mm"], 3.5)
        self.assertEqual(first["quality"]["physiology_status"], "complete")

        capture.begin("r2", 10)
        capture.ingest(self.sample("hr", "polar:hr", 11, [120]))
        capture.ingest(self.sample("ppi", "polar:ppi", 11, [0, 800]))
        capture.ingest(self.sample("ppi", "polar:ppi", 12, [0, 820]))
        capture.close("r2", 15)
        second = capture.finalize("r2")
        self.assertEqual(second["physiology"]["hr_mean_bpm"], 120)
        self.assertEqual(second["physiology"]["hrv_rmssd_ms"], 20)
        self.assertEqual(second["quality"]["pupil_status"], "missing")
        repo.save_round("r1", "Alex", "save-1")
        repo.save_round("r2", "Jordan", "save-2")
        self.assertEqual(repo.list_history("Alex")[0]["physiology"]["hr_mean_bpm"], 80)
        self.assertEqual(repo.list_history("Jordan")[0]["physiology"]["hr_mean_bpm"], 120)
        self.assertEqual(len(repo.get_physiology_samples("r1")), 6)
        self.assertEqual(len(repo.get_physiology_samples("r2")), 3)
        reconstructed = rebuild_round_physiology("r1")
        self.assertEqual(reconstructed["summary"], first["physiology"])
        self.assertEqual(reconstructed["quality"]["physiology_status"], "complete")

    def test_missing_signals_are_explicit_nulls(self):
        capture = RoundPhysiologyCapture()
        capture.begin("r1", 1)
        capture.close("r1", 5)
        row = capture.finalize("r1")
        self.assertIsNone(row["physiology"]["hr_mean_bpm"])
        self.assertIsNone(row["physiology"]["hrv_rmssd_ms"])
        self.assertEqual(row["quality"]["physiology_status"], "no_data")


if __name__ == "__main__":
    unittest.main()
