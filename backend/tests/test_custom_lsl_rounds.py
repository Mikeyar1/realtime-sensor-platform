"""Focused round, payload, and persistence checks for custom LSL delivery."""

import sys
import asyncio
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scenarios.fitts_gng_clay_colors.custom_trials import FittsGngClayColorsTracker
from services import database
from services.virtra_stream_config import VirtraStreamConfig, _upgrade_v1, _validate
from services.virtra_scenario_orchestrator import VirTraScenarioOrchestrator
from event_decoders.custom_lsl import CustomLslEvent, decode_custom_lsl_sample
from virtra_custom_lsl.session import CustomLslSessionBase


class CaptureSession(CustomLslSessionBase):
    def on_drill_event(self, event):
        pass


class QuietGngTracker(FittsGngClayColorsTracker):
    def _broadcast_update(self, trial):
        pass


class CustomLslRoundsTest(unittest.TestCase):
    def test_v1_operator_config_migrates_without_auto_wait(self):
        legacy = copy.deepcopy(VirtraStreamConfig()._data)
        legacy["schema_version"] = 1
        legacy["source_selection"] = {
            "default": "auto", "custom_wait_seconds": 2.0,
            "allow_round_start_without_scenario_start": True,
        }
        legacy.pop("custom_lifecycle")
        for drill in legacy["drills"].values():
            source = drill.pop("metrics_source")
            drill["native_scoring_enabled"] = source == "native"
            drill["custom_scoring_enabled"] = source == "custom"
            drill["preferred_source"] = "custom" if source == "custom" else "native"
        pvt_fitts = legacy["drills"]["pvt_fitts"]
        pvt = legacy["drills"]["pvt"]
        pvt["native_scenarios"].extend(pvt_fitts["native_scenarios"])
        pvt_fitts["native_scenarios"] = []
        pvt_fitts["native_scoring_enabled"] = False
        pvt_fitts["custom_scoring_enabled"] = True
        migrated = _upgrade_v1(legacy)
        _validate(migrated)
        self.assertNotIn("source_selection", migrated)
        self.assertEqual(migrated["drills"]["pvt_fitts"]["metrics_source"], "native")
        self.assertEqual(migrated["drills"]["pvt_fitts"]["native_scenarios"],
                         ["LVL1_PVT_FITTS_ASSESSMENT"])
        self.assertEqual(migrated["drills"]["basepvt_fitts_clay"]["native_scenarios"],
                         ["basePVT_Fitts_Clay"])

    def test_pvt_fitts_requires_real_custom_contract_before_source_flip(self):
        config = VirtraStreamConfig()
        for alias in ("LVL1_PVT_FITTS_ASSESSMENT", "basePVT_Fitts_Clay"):
            drill = config.drill_for_native_scenario(alias)
            self.assertEqual(drill["metrics_source"], "native")
            self.assertEqual(drill["native_result_key"], "pvt")
        with tempfile.TemporaryDirectory() as folder:
            candidate = Path(folder) / "virtra_streams.toml"
            contents = config.path.read_text(encoding="utf-8")
            contents += '\n[drills.basepvt_fitts_clay]\nmetrics_source = "custom"\n'
            self.assertNotEqual(contents, config.path.read_text(encoding="utf-8"))
            candidate.write_text(contents, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "before its components are implemented"):
                VirtraStreamConfig(candidate)

    def _orchestrator(self, loop, messages):
        async def broadcast(message):
            messages.append(message)
        return VirTraScenarioOrchestrator(
            inlets={}, broadcast_fn=broadcast, send_fn=broadcast,
            get_state_payload_fn=lambda: {},
            error_fn=lambda code, message, context: {},
            on_live_activate_fn=lambda: None, on_live_deactivate_fn=lambda: None,
            loop=loop,
        )

    def test_native_pvt_fitts_starts_immediately_and_custom_placeholder_is_ignored(self):
        async def run():
            messages = []
            orchestrator = self._orchestrator(asyncio.get_running_loop(), messages)
            metadata = json.dumps([["Scenario", "LVL1_PVT_FITTS_ASSESSMENT"],
                                   ["Simulator", "V-AIMS"], ["SimulatorSystem", "V_300"]])
            orchestrator.on_sample("V_300", "VirTraEvents", 1,
                                   ["Scenario Started", "", "", metadata])
            self.assertEqual(orchestrator._run_source, "native")
            self.assertEqual(orchestrator._active_drill["drill_key"], "pvt")
            payload = json.dumps({"drill_type": "pvt_fitts", "scenario_name": "PVT / Fitts",
                                  "round": 1, "data": {}})
            orchestrator.on_sample("virtra_lsl", "VirTra Events", 2,
                                   ["Round Started", "", "RS", payload])
            await asyncio.sleep(0.01)
            self.assertIsNone(orchestrator._active_custom_session)
            self.assertEqual(len([m for m in messages if m.get("type") == "session.scenario_started"]), 1)
        asyncio.run(run())

    def test_custom_gng_keeps_native_context_without_native_scoring(self):
        async def run():
            messages = []
            orchestrator = self._orchestrator(asyncio.get_running_loop(), messages)
            native = json.dumps([["Scenario", "Multicolor GNG"], ["SessionID", "native-1"],
                                 ["Simulator", "V-AIMS"], ["SimulatorSystem", "V_300"]])
            orchestrator.on_sample("V_300", "VirTraEvents", 1,
                                   ["Scenario Started", "", "", native])
            self.assertIsNone(orchestrator._active_drill)
            payload = json.dumps({"drill_type": "multicolor_gng", "scenario_id": "test-1",
                                  "scenario_name": "Multicolor GNG", "round": 1, "data": {}})
            orchestrator.on_sample("another_custom_name", "VirTra Events", 2,
                                   ["Round Started", "", "RS", payload])
            self.assertEqual(orchestrator._run_source, "custom")
            self.assertEqual(orchestrator._custom_context["native_session_id"], "native-1")
            orchestrator.on_sample("V_300", "VirTraEvents", 3,
                                   ["Shot Fired", "", "", json.dumps([["WeaponType", "Pistol"]])])
            orchestrator.on_sample("V_300", "VirTraEvents", 4,
                                   ["Scenario Stopped", "", "", native])
            self.assertIsNotNone(orchestrator._active_custom_session)
            self.assertEqual(orchestrator._custom_context["weapon_type"], "Pistol")
            await asyncio.sleep(0.01)
            start = next(m for m in messages if m.get("type") == "session.scenario_started")
            self.assertEqual(start["simulator"], "V-AIMS")
            self.assertEqual(start["metrics_source"], "custom")
        asyncio.run(run())

    def test_native_context_arriving_after_custom_start_updates_same_session(self):
        async def run():
            messages = []
            orchestrator = self._orchestrator(asyncio.get_running_loop(), messages)
            payload = json.dumps({"drill_type": "multicolor_gng", "scenario_id": "reused-id",
                                  "scenario_name": "Multicolor GNG", "round": 1, "data": {}})
            orchestrator.on_sample("virtra_lsl", "VirTra Events", 1,
                                   ["Round Started", "", "RS", payload])
            custom_session_id = orchestrator._custom_session_id
            native = json.dumps([["Scenario", "Multicolor GNG"],
                                 ["Simulator", "V-AIMS"], ["SessionID", "native-1"]])
            orchestrator.on_sample("V_300", "VirTraEvents", 2,
                                   ["Scenario Started", "", "", native])
            await asyncio.sleep(0.01)
            updates = [m for m in messages if m.get("type") == "session.context_updated"]
            self.assertEqual(len(updates), 1)
            self.assertEqual(updates[0]["session_id"], custom_session_id)
            self.assertEqual(updates[0]["simulator"], "V-AIMS")
            self.assertIsNone(orchestrator._active_drill)
        asyncio.run(run())

    def test_disabled_custom_drill_does_not_interrupt_native(self):
        async def run():
            orchestrator = self._orchestrator(asyncio.get_running_loop(), [])
            metadata = json.dumps([["Scenario", "PVT"]])
            orchestrator.on_sample("V_300", "VirTraEvents", 1,
                                   ["Scenario Started", "", "", metadata])
            payload = json.dumps({"drill_type": "basepvt_fitts_gng_clay",
                                  "scenario_name": "Composite", "round": 1, "data": {}})
            orchestrator.on_sample("virtra_lsl", "VirTra Events", 2,
                                   ["Round Started", "", "RS", payload])
            self.assertEqual(orchestrator._run_source, "native")
            self.assertIsNone(orchestrator._active_custom_session)
        asyncio.run(run())

    def test_destroyed_auto_saves_distinct_runs_with_reused_external_id(self):
        async def run():
            messages = []
            async def broadcast(message):
                messages.append(message)
            loop = asyncio.get_running_loop()
            orchestrator = VirTraScenarioOrchestrator(
                inlets={}, broadcast_fn=broadcast, send_fn=broadcast,
                get_state_payload_fn=lambda: {},
                error_fn=lambda code, message, context: {},
                on_live_activate_fn=lambda: None, on_live_deactivate_fn=lambda: None,
                loop=loop,
            )
            orchestrator._participant_name = "Auto Tester"
            for run_number in (1, 2):
                if run_number == 1:
                    native = json.dumps([["Scenario", "Multicolor GNG"],
                                         ["SessionID", "native-run-1"], ["Simulator", "V-AIMS"]])
                    orchestrator.on_sample("V_300", "VirTraEvents", 9,
                                           ["Scenario Started", "", "", native])
                payload = {"drill_type": "multicolor_gng", "scenario_id": "same-external-id",
                           "scenario_name": "Multicolor GNG", "round": 1, "data": {}}
                def send(name, timestamp, data=None):
                    payload["data"] = data or {}
                    orchestrator.on_sample("virtra_lsl", "VirTra Events", timestamp,
                                           [name, "", "", json.dumps(payload)])
                send("Round Started", run_number * 10)
                if run_number == 1:
                    orchestrator.on_sample("V_300", "VirTraEvents", 10.1,
                                           ["Shot Fired", "", "",
                                            json.dumps([["WeaponType", "Pistol"]])])
                send("Target Appeared", run_number * 10 + 0.2,
                     {"target_id": "one", "expected_action": "shoot"})
                send("Shot Hit", run_number * 10 + 0.5,
                     {"entity_id": "one", "reaction_time_seconds": 0.3})
                send("Round Completed", run_number * 10 + 1, {"metrics": {"score": 5}})
                send("Scenario Destroyed", run_number * 10 + 2)
                for _ in range(50):
                    await asyncio.sleep(0.02)
                    if len([m for m in messages if m.get("type") == "session.metrics_ready"]) == run_number:
                        break
            saved = database.get_sessions_for_participant("Auto Tester")
            self.assertEqual(len(saved), 2)
            self.assertEqual(len({s["session_uuid"] for s in saved}), 2)
            self.assertTrue(all(s["capture_status"] == "partial" for s in saved))
            self.assertTrue(all(s["external_scenario_id"] == "same-external-id" for s in saved))
            self.assertEqual(saved[0]["native_context"]["weapon_type"], "Pistol")
            self.assertEqual(saved[1]["native_context"]["context_match_status"], "unmatched")
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "autosave.db"))
            asyncio.run(run())

    def test_recorded_custom_streams_decode(self):
        root = Path(__file__).resolve().parents[2]
        for filename in ("dnlc.db", "dnlc2.db"):
            path = root / "data" / filename
            if not path.exists():
                continue
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
                rows = conn.execute("SELECT sample_json FROM lsl_unmapped_samples").fetchall()
            decoded = []
            for (sample_json,) in rows:
                channels = json.loads(sample_json)
                if len(channels) != 4 or channels[0].get("label") != "event_name":
                    continue
                event = decode_custom_lsl_sample([item["value"] for item in channels])
                self.assertIsNotNone(event, filename)
                decoded.append(event)
            self.assertGreater(len(decoded), 0, filename)
            self.assertTrue(any(event.event_name == "Round Started" for event in decoded))

    def test_round_attempts_and_pvt_then_fitts(self):
        session = CaptureSession()
        session.configure_components({
            "default_component": "pvt",
            "components": {
                "pvt": {"phase_values": ["pvt"]},
                "fitts": {"phase_values": ["fitts"], "events": {"start": ["Fitts Begins"]}},
            },
        })
        session.handle_event(CustomLslEvent(event_name="Scenario Started"), 1)
        session.handle_event(CustomLslEvent(event_name="Round Started", round=1,
                                            phase="pvt"), 2)
        session.handle_event(CustomLslEvent(event_name="Metrics Updated", round=1,
                                            phase="pvt", data={"metrics": {"hits": 2}}), 3)
        session.handle_event(CustomLslEvent(event_name="Fitts Begins", round=1), 4)
        session.handle_event(CustomLslEvent(event_name="Round Completed", round=1,
                                            data={"metrics": {"score": 8}}), 5)
        session.handle_event(CustomLslEvent(event_name="Round Started", round=1,
                                            phase="pvt"), 6)
        session.handle_event(CustomLslEvent(event_name="Scenario Destroyed"), 7)
        rounds = session.rounds_snapshot()
        self.assertEqual([r["attempt"] for r in rounds], [1, 2])
        self.assertEqual([r["ended_reason"] for r in rounds], ["completed", "incomplete"])
        self.assertEqual(list(rounds[0]["components"]), ["pvt", "fitts"])
        self.assertEqual(rounds[0]["components"]["pvt"]["metrics"]["hits"], 2)
        self.assertEqual(rounds[0]["metrics"]["score"], 8)

    def test_multicolor_recording_replays_without_losing_hits(self):
        path = Path(__file__).resolve().parents[2] / "data" / "dnlc.db"
        if not path.exists():
            self.skipTest("sample recording is not installed")
        tracker = QuietGngTracker(broadcast_fn=None, loop=None)
        tracker.reset()
        tracker.handle_event(CustomLslEvent(event_name="Scenario Started"), 0)
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT lsl_timestamp, sample_json FROM lsl_unmapped_samples "
                                "ORDER BY lsl_unmapped_samples_id").fetchall()
        for timestamp, sample_json in rows:
            channels = json.loads(sample_json)
            if len(channels) != 4 or channels[0].get("label") != "event_name":
                continue
            event = decode_custom_lsl_sample([item["value"] for item in channels])
            if event and event.drill_type == "multicolor_gng":
                tracker.handle_event(event, timestamp)
        self.assertGreater(len(tracker.get_completed_trials()), 0)
        self.assertGreater(sum(t["result"] == "hit" for t in tracker.get_completed_trials()), 0)
        self.assertTrue(any(r.round_number == 4 for r in tracker.round_history))

    def test_recorded_combined_drill_keeps_pvt_and_gng_separate(self):
        path = Path(__file__).resolve().parents[2] / "data" / "dnlc2.db"
        if not path.exists():
            self.skipTest("sample recording is not installed")
        drill = VirtraStreamConfig().drill_for_custom_type(
            "basepvt_fitts_gng_clay", include_disabled=True)
        session = CaptureSession()
        session.configure_components(drill)
        session.handle_event(CustomLslEvent(event_name="Scenario Started"), 0)
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
            rows = conn.execute("SELECT lsl_timestamp, sample_json FROM lsl_unmapped_samples "
                                "ORDER BY lsl_unmapped_samples_id").fetchall()
        for timestamp, sample_json in rows:
            channels = json.loads(sample_json)
            if len(channels) != 4 or channels[0].get("label") != "event_name":
                continue
            event = decode_custom_lsl_sample([item["value"] for item in channels])
            if event and event.drill_type == "basepvt_fitts_gng_clay":
                session.handle_event(event, timestamp)
        components = {name for round_record in session.rounds_snapshot()
                      for name in round_record["components"]}
        self.assertIn("pvt", components)
        self.assertIn("gng", components)
        self.assertNotIn("fitts", components)

    def test_top_level_shot_hit_closes_gng_trial(self):
        tracker = QuietGngTracker(broadcast_fn=None, loop=None)
        tracker.reset()
        tracker.handle_event(CustomLslEvent(event_name="Round Started", round=4), 1)
        tracker.handle_event(CustomLslEvent(event_name="Target Appeared", round=4,
                                            data={"target_id": "one", "expected_action": "shoot"}), 2)
        tracker.handle_event(CustomLslEvent(event_name="Shot Hit", round=4,
                                            data={"entity_id": "one", "reaction_time_seconds": 0.5}), 3)
        trials = tracker.get_completed_trials()
        self.assertEqual(len(trials), 1)
        self.assertEqual(trials[0]["result"], "hit")
        self.assertEqual(trials[0]["rt_ms"], 500)

    def test_rounds_survive_database_save_and_detail_query(self):
        with tempfile.TemporaryDirectory() as folder:
            database.init_db(str(Path(folder) / "test.db"))
            metadata = {
                "session_id": "run-1", "drill_key": "pvt_fitts",
                "task_type": "PVT_FITTS", "scenario_name": "PVT / Fitts",
                "source": "custom", "external_scenario_id": "external-1",
                "native_context": {"native_session_id": "native-1", "simulator": "V-AIMS",
                                   "weapon_type": "Pistol", "context_match_status": "matched_name"},
                "custom_scenario_name": "Multicolor GNG",
                "rounds": [{"round_number": 1, "attempt": 1,
                            "ended_reason": "completed", "components": {"pvt": {}, "fitts": {}}}],
                "trials": [{"trial_number": 1, "result": "hit", "round_number": 1,
                            "round_attempt": 1, "component": "pvt"}],
            }
            self.assertIsNotNone(database.insert_session("Tester", metadata, {"completed_rounds": 1}))
            detail = database.get_session_detail("run-1", "Tester")
            self.assertIsNone(database.get_session_detail("run-1", "Another participant"))
            self.assertEqual(detail["source"], "custom")
            self.assertEqual(detail["metrics_source"], "custom")
            self.assertEqual(detail["native_context"]["weapon_type"], "Pistol")
            self.assertEqual(detail["custom_scenario_name"], "Multicolor GNG")
            self.assertEqual(detail["rounds"][0]["components"].keys(), {"pvt", "fitts"})
            self.assertEqual(detail["trials"][0]["component"], "pvt")


if __name__ == "__main__":
    unittest.main()
