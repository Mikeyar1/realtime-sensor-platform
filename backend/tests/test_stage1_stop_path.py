"""Document the current stop gap seen in the October 15 Fitts capture.

This is a baseline characterization test. Stage 4 should change its expected
result when a correlated native stop becomes authoritative for a custom run.
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services.virtra_scenario_orchestrator import VirTraScenarioOrchestrator  # noqa: E402
from virtra_custom_lsl.event_decoder import decode_custom_lsl_sample  # noqa: E402
from virtra_custom_lsl.session import _preset_label  # noqa: E402


def custom(name: str, round_number: int, t: float) -> list[str]:
    payload = {
        "schema_version": "1.0", "drill_type": "fitts_pvt",
        "scenario_name": "Fitts-PVT_Clay", "scenario_id": "fixture-run",
        "round": round_number, "scenario_time_seconds": t,
        "data": {"settings": {"active_distance_meters": 10}},
    }
    return [name, "", "", json.dumps(payload)]


def native(name: str) -> list[str]:
    return [name, "", "", json.dumps([["Scenario", "Fitts-PVT_Clay"],
                                      ["SessionID", "fixture-run"]])]


class CurrentStopPathTests(unittest.TestCase):
    def test_preset_reader_recognizes_observed_nested_round_preset(self):
        payload = {
            "drill_type": "fitts_pvt", "round": 1,
            "data": {"settings": {"preset": {"name": "Default", "is_default": True},
                                  "active_distance_meters": 10}},
        }
        event = decode_custom_lsl_sample(["Round Started", "", "", json.dumps(payload)])
        self.assertIsNotNone(event)
        self.assertEqual(_preset_label(event), "Default")

    def test_matching_native_stop_ends_current_custom_run(self):
        async def run():
            messages = []

            async def send(message):
                messages.append(message)

            orchestrator = VirTraScenarioOrchestrator(
                inlets={}, broadcast_fn=send, send_fn=send,
                get_state_payload_fn=lambda: {}, error_fn=lambda *args: {},
                on_live_activate_fn=lambda: None, on_live_deactivate_fn=lambda: None,
                loop=asyncio.get_running_loop(),
            )
            orchestrator.on_sample("virtra_lsl", "VirTra Events", 1,
                                   custom("Scenario Started", 0, 0))
            orchestrator.on_sample("V_300", "VirTraEvents", 2,
                                   native("Scenario Started"))
            orchestrator.on_sample("virtra_lsl", "VirTra Events", 3,
                                   custom("Round Started", 1, 2))
            orchestrator.on_sample("virtra_lsl", "VirTra Events", 4,
                                   custom("Round Completed", 1, 3))
            orchestrator.on_sample("V_300", "VirTraEvents", 5,
                                   native("Scenario Stopped"))
            await asyncio.sleep(0.05)
            self.assertIsNone(orchestrator._active_custom_session)
            self.assertEqual(sum(msg.get("type") == "session.scenario_stopped"
                                 for msg in messages), 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
