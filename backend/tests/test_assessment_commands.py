"""WebSocket command contract for independently saved round assessments."""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import database, round_repository as repo
from services.session_lifecycle_coordinator import SessionLifecycleCoordinator


class AssessmentCommandTests(unittest.TestCase):
    def test_save_query_and_revision_error(self):
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "commands.db"))
            repo.capture_run(
                "run", drill_key="fitts_pvt_clay", category_id="pvt_fitts",
                profile_id="pvt_fitts_core", profile_version=1,
                scenario_name="Fitts-PVT_Clay", external_scenario_id="script",
                source="custom", started_at=1)
            repo.capture_round("run", {
                "round_id": "r1", "round_number": 1, "attempt": 1,
                "started_at": 2, "ended_at": 5, "ended_reason": "completed",
                "preset_label": "Default", "metrics": {"hits": 1},
            })

            async def run():
                sent = []

                async def send(_socket, message):
                    sent.append(message)

                coordinator = SessionLifecycleCoordinator(
                    inlet_manager=object(), broadcast_fn=send, send_fn=send,
                    loop=asyncio.get_running_loop())
                socket = object()
                await coordinator.handle_message(socket, {
                    "type": "round.save", "round_id": "r1",
                    "participant_name": "Alex", "request_id": "one",
                })
                self.assertTrue(sent[-1]["success"])
                self.assertEqual(sent[-1]["result"]["participant_name"], "Alex")
                await coordinator.handle_message(socket, {
                    "type": "round.save", "round_id": "r1",
                    "participant_name": "Alex", "request_id": "one",
                })
                self.assertTrue(sent[-1]["success"])
                await coordinator.handle_message(socket, {
                    "type": "history.rounds.query", "participant_name": "Alex",
                    "scenario_name": "Fitts-PVT_Clay", "preset_label": "Default",
                })
                self.assertEqual(len(sent[-1]["result"]), 1)
                self.assertTrue(sent[-1]["result"][0]["performed_at"])
                await coordinator.handle_message(socket, {
                    "type": "history.round.get", "round_id": "r1",
                    "participant_name": "Jordan",
                })
                self.assertIsNone(sent[-1]["result"])
                await coordinator.handle_message(socket, {
                    "type": "history.round.get", "round_id": "r1",
                    "participant_name": "Alex",
                })
                self.assertEqual(sent[-1]["result"]["id"], "r1")
                await coordinator.handle_message(socket, {
                    "type": "round.assignment.update", "round_id": "r1",
                    "participant_name": "Jordan", "expected_revision": 1,
                })
                self.assertFalse(sent[-1]["success"])
                await coordinator.handle_message(socket, {
                    "type": "run.snapshot.get", "run_id": "run",
                })
                self.assertEqual(sent[-1]["result"]["rounds"][0]["save_status"], "saved")

            asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
