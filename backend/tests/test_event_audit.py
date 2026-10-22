"""Append-only event evidence, links, and deterministic replay."""

import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.event_store import EventStore
from audit.replay import replay_capture, compare_round_metrics
from event_decoders.source_event import decode_source_event
from services import database, round_repository
from services.session_lifecycle_coordinator import SessionLifecycleCoordinator
from services.virtra_scenario_orchestrator import VirTraScenarioOrchestrator


def custom(name, number=0, scenario_id="script-1", data=None, scenario_time=0):
    return [name, "", "", json.dumps({
        "schema_version": "1.0", "drill_type": "fitts_pvt",
        "reason": name.casefold().replace(" ", "_"),
        "scenario_name": "Fitts-PVT_Clay", "scenario_id": scenario_id,
        "scenario_time_seconds": scenario_time,
        "round": number, "data": (data if data is not None else
                                  {"settings": {"preset": {"name": "Default"}}}),
    })]


def native(name, scenario="PVT"):
    return [name, "", "", json.dumps([["Scenario", scenario],
                                       ["SessionID", "native-session"]])]


class EventAuditTests(unittest.TestCase):
    def test_append_link_query_and_replay_are_ordered(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "audit.db")
            database.init_db(path)
            store = EventStore(path, max_queue=8, provenance="test")
            samples = [custom("Scenario Started"), custom("Round Started", 1)]
            event_ids = []
            for index, sample in enumerate(samples, 1):
                event_id = store.append(
                    outlet_uid="uid-1", stream_name="virtra_lsl",
                    stream_type="VirTra Events", source_id="script",
                    lsl_timestamp=float(index), channels=sample,
                    source_event=decode_source_event("custom", sample))
                event_ids.append(event_id)
                self.assertTrue(store.link(event_id, run_id="run-1",
                                           round_id="round-1" if index == 2 else None,
                                           evidence_role="round_start" if index == 2 else "scenario_start",
                                           decision="routed", scenario_id="fitts_pvt_clay",
                                           scenario_revision=1))
            self.assertTrue(store.flush())
            rows = store.get_capture_events(store.capture_id)
            self.assertEqual([row["id"] for row in rows], event_ids)
            self.assertEqual([row["ingest_sequence"] for row in rows], [1, 2])
            self.assertEqual(store.get_run_events("run-1")[1]["round_id"], "round-1")
            replayed = []
            result = replay_capture(store, store.capture_id,
                                    lambda *args: replayed.append(args))
            self.assertEqual((result["replayed"], result["decoder_mismatches"]), (2, []))
            self.assertEqual([row[3] for row in replayed], samples)
            store.close()
            with closing(sqlite3.connect(path)) as conn:
                self.assertEqual(conn.execute(
                    "SELECT status FROM capture_sessions WHERE id = ?",
                    (store.capture_id,)).fetchone()[0], "complete")

    def test_queue_overflow_is_reported_and_not_scored(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "audit.db")
            database.init_db(path)
            store = EventStore(path, max_queue=1)
            sample = custom("Scenario Started")
            with patch.object(store._queue, "put_nowait", side_effect=__import__("queue").Full):
                self.assertIsNone(store.append(
                    outlet_uid="", stream_name="virtra_lsl", stream_type="VirTra Events",
                    source_id="", lsl_timestamp=1, channels=sample,
                    source_event=decode_source_event("custom", sample)))
            self.assertEqual(store.health()["lost_events"], 1)
            self.assertFalse(store.health()["complete"])
            store.close()

    def test_custom_round_and_native_no_round_link_to_raw_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "audit.db")
            database.init_db(path)
            store = EventStore(path)
            replies = []

            async def send(*args):
                replies.append(args[-1])

            async def run():
                coordinator = SessionLifecycleCoordinator(
                    inlet_manager={}, broadcast_fn=send, send_fn=send,
                    loop=asyncio.get_running_loop(), event_store=store)
                ingest = coordinator.on_sample
                ingest("V_300", "VirTraEvents", 1,
                       native("Scenario Started", "Fitts-PVT_Clay"), outlet_uid="native-uid")
                ingest("virtra_lsl", "VirTra Events", 2, custom("Scenario Started"),
                       outlet_uid="custom-uid")
                run_id = coordinator._orchestrator._custom_session_id
                ingest("virtra_lsl", "VirTra Events", 3,
                       custom("Round Started", 1, scenario_time=1),
                       outlet_uid="custom-uid")
                ingest("virtra_lsl", "VirTra Events", 4,
                       custom("Target Appeared", 1, data={
                           "target_id": "clay-1", "presentation_index": 1},
                           scenario_time=2),
                       outlet_uid="custom-uid")
                ingest("virtra_lsl", "VirTra Events", 4.4,
                       custom("Shot Hit", 1, data={"shot": {
                           "shot_id": "shot-1",
                           "counted_as_first_target_hit": True,
                           "reaction_time_seconds": 0.4,
                           "target": {"target_id": "clay-1", "presentation_index": 1}}},
                           scenario_time=2.4),
                       outlet_uid="custom-uid")
                ingest("virtra_lsl", "VirTra Events", 5,
                       custom("Round Completed", 1, scenario_time=3),
                       outlet_uid="custom-uid")
                ingest("V_300", "VirTraEvents", 6,
                       native("Scenario Stopped", "Fitts-PVT_Clay"), outlet_uid="native-uid")
                ingest("virtra_lsl", "VirTra Events", 7,
                       ["Shot Fired", "", "", "{bad"], outlet_uid="custom-uid")
                ingest("V_300", "VirTraEvents", 8,
                       native("Scenario Started", "PVT"), outlet_uid="native-uid")
                ingest("V_300", "VirTraEvents", 9,
                       native("Scenario Stopped", "PVT"), outlet_uid="native-uid")
                ingest("V_300", "VirTraEvents", 10,
                       native("Scenario Started", "Unknown Scenario"),
                       outlet_uid="native-uid")
                await asyncio.sleep(0.05)
                self.assertTrue(store.flush())
                custom_rows = store.get_run_events(run_id)
                self.assertEqual(len(custom_rows), 7)  # native context and backstop plus scored custom round
                self.assertEqual(custom_rows[0]["evidence_role"], "native_context")
                self.assertEqual(custom_rows[-1]["evidence_role"], "scenario_stopped")
                self.assertIsNotNone(next(row["round_id"] for row in custom_rows
                                          if row["evidence_role"] == "round_start"))
                scored_round = round_repository.get_run(run_id)["rounds"][0]
                self.assertEqual(scored_round["metrics"]["hits"], 1)
                all_rows = store.get_capture_events(store.capture_id)
                self.assertEqual(len(all_rows), 11)
                self.assertEqual(all_rows[7]["decode_status"], "malformed")
                self.assertIsNone(coordinator._orchestrator._active_drill)
                with closing(sqlite3.connect(path)) as conn:
                    native_runs = conn.execute("""SELECT DISTINCT run_id FROM assessment_event_links
                        WHERE scenario_id = 'pvt'""").fetchall()
                self.assertEqual(len(native_runs), 1)
                native_rows = store.get_run_events(native_runs[0][0])
                self.assertEqual(len(native_rows), 2)
                self.assertTrue(all(row["round_id"] is None for row in native_rows))
                self.assertEqual(store.get_capture_runs(store.capture_id)[0]["round_count"], 1)
                await coordinator.handle_message(None, {
                    "type": "audit.capture.runs", "capture_id": store.capture_id})
                self.assertEqual(replies[-1]["type"], "audit.events")
                self.assertEqual(len(replies[-1]["events"]), 2)
                return run_id, scored_round["metrics"]["hits"]

            original_run_id, original_hits = asyncio.run(run())
            store.close()
            replay_path = str(Path(folder) / "replayed.db")
            database.init_db(replay_path)

            async def replay():
                messages = []
                async def send(message):
                    messages.append(message)
                router = VirTraScenarioOrchestrator(
                    inlets={}, broadcast_fn=send, send_fn=send,
                    get_state_payload_fn=lambda: {}, error_fn=lambda *args: {},
                    on_live_activate_fn=lambda: None,
                    on_live_deactivate_fn=lambda: None,
                    loop=asyncio.get_running_loop())
                result = replay_capture(store, store.capture_id, router.on_sample)
                await asyncio.sleep(0.05)
                run_id = next(message["session_id"] for message in messages
                              if message.get("type") == "session.scenario_started" and
                              message.get("source") == "custom")
                self.assertEqual(round_repository.get_run(run_id)["rounds"][0]
                                 ["metrics"]["hits"], original_hits)
                self.assertEqual(result["decoder_mismatches"], [])
                self.assertEqual(compare_round_metrics(path, replay_path,
                                                       original_run_id, run_id),
                                 {"matching": True, "different_rounds": []})
            asyncio.run(replay())

    def test_unlogged_event_cannot_start_a_scored_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "audit.db")
            database.init_db(path)
            store = EventStore(path)

            async def run():
                async def send(message):
                    pass
                coordinator = SessionLifecycleCoordinator(
                    inlet_manager={}, broadcast_fn=send, send_fn=send,
                    loop=asyncio.get_running_loop(), event_store=store)
                with patch.object(store, "append", return_value=None):
                    outcome = coordinator.on_sample(
                        "virtra_lsl", "VirTra Events", 1,
                        custom("Scenario Started"))
                self.assertEqual(outcome, "dropped")
                self.assertIsNone(coordinator._orchestrator._active_custom_session)
            asyncio.run(run())
            store.close()


if __name__ == "__main__":
    unittest.main()
