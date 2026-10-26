"""Replay lifecycle probes through the production router and round store."""

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import database, round_repository
from services.virtra_scenario_orchestrator import VirTraScenarioOrchestrator


CASES = Path(__file__).with_name("fixtures") / "fitts_lifecycle_cases.json"


def custom(name, number, scenario_time, instance):
    return [name, "", "", json.dumps({
        "schema_version": "1.0", "drill_type": "fitts_pvt",
        "scenario_name": "Fitts-PVT_Clay", "scenario_id": instance,
        "round": number, "scenario_time_seconds": scenario_time,
        "data": {"settings": {"preset": {"name": "Default"}}},
    })]


def native(name, scenario="Fitts-PVT_Clay", session="native-1"):
    return [name, "", "", json.dumps([["Scenario", scenario], ["SessionID", session]])]


class RoundLifecycleReplayTests(unittest.TestCase):
    def _orchestrator(self, messages):
        async def send(message):
            messages.append(message)
        return VirTraScenarioOrchestrator(
            inlets={}, broadcast_fn=send, send_fn=send,
            get_state_payload_fn=lambda: {}, error_fn=lambda *args: {},
            on_live_activate_fn=lambda: None, on_live_deactivate_fn=lambda: None,
            loop=asyncio.get_running_loop())

    def test_synthetic_lifecycle_cases_and_duplicate_outlet(self):
        cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as folder:
                database.init_db(str(Path(folder) / "run.db"))

                async def run():
                    messages = []
                    router = self._orchestrator(messages)
                    instance = "script-1"
                    first_run = None
                    current_run = None
                    for index, item in enumerate(case["events"], 1):
                        name, number, scenario_time = item
                        if name == "NEW_INSTANCE":
                            instance = "script-2"
                            continue
                        raw = custom(name, number, scenario_time, instance)
                        router.on_sample("virtra_lsl", "VirTra Events", float(index), raw)
                        router.on_sample("second-outlet", "VirTra Events", float(index), raw)
                        if name == "Scenario Started":
                            if current_run:
                                first_run = current_run
                            current_run = router._custom_session_id
                    await asyncio.sleep(0.05)
                    self.assertIsNotNone(current_run)
                    snapshot = round_repository.get_run(current_run)
                    self.assertEqual([(r["round_number"], r["attempt"], r["status"])
                                      for r in snapshot["rounds"]],
                                     [tuple(expected) for expected in case["expected_rounds"]])
                    self.assertEqual(router._active_custom_session is not None,
                                     case["expected_active"])
                    if first_run:
                        first = round_repository.get_run(first_run)
                        self.assertEqual([(r["round_number"], r["attempt"], r["status"])
                                          for r in first["rounds"]],
                                         [tuple(row) for row in case["expected_first_run_rounds"]])
                    if case["expected_active"]:
                        state = router.get_active_drill_state()
                        self.assertEqual(state["assessment_run"]["id"], current_run)
                    else:
                        self.assertEqual(sum(m.get("type") == "session.scenario_stopped"
                                             for m in messages),
                                         2 if first_run else 1)
                asyncio.run(run())

    def test_unrelated_native_stop_cannot_close_custom_run(self):
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "run.db"))

            async def run():
                messages = []
                router = self._orchestrator(messages)
                router.on_sample("virtra_lsl", "VirTra Events", 1,
                                 custom("Scenario Started", 0, 0, "script-1"))
                run_id = router._custom_session_id
                router.on_sample("V_300", "VirTraEvents", 2,
                                 native("Scenario Started", "Unrelated"))
                router.on_sample("V_300", "VirTraEvents", 3,
                                 native("Scenario Stopped", "Unrelated"))
                self.assertIsNotNone(router._active_custom_session)
                self.assertEqual(round_repository.get_run(run_id)["status"], "active")
                router.on_sample("virtra_lsl", "VirTra Events", 4,
                                 custom("Scenario Ended", 0, 3, "script-1"))
                self.assertIsNone(router._active_custom_session)
                self.assertEqual(round_repository.get_run(run_id)["status"], "ended")
            asyncio.run(run())

    def test_matching_native_stop_is_recorded_as_native_backstop(self):
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "run.db"))

            async def run():
                messages = []
                router = self._orchestrator(messages)
                router.on_sample("virtra_lsl", "VirTra Events", 1,
                                 custom("Scenario Started", 0, 0, "script-1"))
                run_id = router._custom_session_id
                router.on_sample("V_300", "VirTraEvents", 2,
                                 native("Scenario Started"))
                router.on_sample("virtra_lsl", "VirTra Events", 3,
                                 custom("Round Started", 1, 2, "script-1"))
                router.on_sample("V_300", "VirTraEvents", 4,
                                 native("Scenario Stopped", session="other-native-run"))
                self.assertEqual(round_repository.get_run(run_id)["status"], "active")
                router.on_sample("V_300", "VirTraEvents", 5,
                                 native("Scenario Stopped"))
                snapshot = round_repository.get_run(run_id)
                self.assertEqual(snapshot["status"], "ended")
                self.assertEqual(snapshot["ended_source"], "native_correlated")
                self.assertEqual(snapshot["rounds"][0]["status"], "incomplete")
            asyncio.run(run())

    def test_other_custom_instance_cannot_close_active_run(self):
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "run.db"))

            async def run():
                router = self._orchestrator([])
                router.on_sample("virtra_lsl", "VirTra Events", 1,
                                 custom("Scenario Started", 0, 0, "script-1"))
                run_id = router._custom_session_id
                router.on_sample("virtra_lsl", "VirTra Events", 2,
                                 custom("Round Started", 1, 1, "script-1"))
                router.on_sample("virtra_lsl", "VirTra Events", 3,
                                 custom("Scenario Destroyed", 1, 2, "script-2"))
                self.assertEqual(round_repository.get_run(run_id)["status"], "active")
                self.assertIsNotNone(router._active_custom_session)
            asyncio.run(run())

    def test_same_capture_can_start_a_new_run_after_it_ends(self):
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "run.db"))

            async def run():
                messages = []
                router = self._orchestrator(messages)
                first_id = None
                second_id = None
                for offset in (0, 100):
                    router.on_sample("virtra_lsl", "VirTra Events", offset + 1,
                                     custom("Scenario Started", 0, 0, "same-script-id"))
                    run_id = router._custom_session_id
                    if offset == 0:
                        first_id = run_id
                    else:
                        second_id = run_id
                    router.on_sample("virtra_lsl", "VirTra Events", offset + 2,
                                     custom("Round Started", 1, 1, "same-script-id"))
                    router.on_sample("second-outlet", "VirTra Events", offset + 2,
                                     custom("Round Started", 1, 1, "same-script-id"))
                    router.on_sample("virtra_lsl", "VirTra Events", offset + 3,
                                     custom("Round Completed", 1, 2, "same-script-id"))
                    router.on_sample("virtra_lsl", "VirTra Events", offset + 4,
                                     custom("Scenario Ended", 1, 3, "same-script-id"))
                await asyncio.sleep(0.05)
                self.assertNotEqual(first_id, second_id)
                self.assertEqual(round_repository.get_run(first_id)["status"], "ended")
                self.assertEqual(round_repository.get_run(second_id)["status"], "ended")
                self.assertEqual(len(round_repository.get_run(second_id)["rounds"]), 1)
                self.assertEqual(sum(m.get("type") == "session.scenario_started"
                                     for m in messages), 2)
            asyncio.run(run())

    def test_mirrored_custom_event_with_small_timestamp_skew_is_dropped(self):
        from services.round_assessment_service import RoundAssessmentService

        service = RoundAssessmentService()
        raw = custom("Target Appeared", 1, 3, "script-1")
        self.assertTrue(service.accepts(10, raw, scenario_id="script-1",
                                        is_explicit_start=False,
                                        drill_key="fitts_pvt_clay"))
        self.assertFalse(service.accepts(10.005, raw, scenario_id="script-1",
                                         is_explicit_start=False,
                                         drill_key="fitts_pvt_clay"))
        self.assertTrue(service.accepts(10.02, raw, scenario_id="script-1",
                                        is_explicit_start=False,
                                        drill_key="fitts_pvt_clay"))


if __name__ == "__main__":
    unittest.main()
