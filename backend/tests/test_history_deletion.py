import asyncio
import gc
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import database
from services.session_lifecycle_coordinator import SessionLifecycleCoordinator


class HistoryDeletionTests(unittest.TestCase):
    def test_session_ownership_and_participant_cascade(self):
        original_path = database._db_path
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                database.init_db(str(root / "history.db"))

                def save(name, session_id, scenario):
                    folder = root / session_id
                    folder.mkdir()
                    record = folder / "session_record.json"
                    record.write_text("{}", encoding="utf-8")
                    database.insert_session(name, {
                        "session_id": session_id,
                        "drill_key": "pvt",
                        "scenario_name": scenario,
                        "trials": [{"trial_number": 1, "outcome": "hit", "rt_ms": 400}],
                        "biosignals": {"hr_mean_bpm": 70},
                    }, {"hits_pct": 100}, str(record))
                    return record

                alice_one = save("Alice", "alice-1", "Scenario A")
                alice_two = save("Alice", "alice-2", "Scenario B")
                bob_one = save("Bob", "bob-1", "Scenario A")

                self.assertFalse(database.delete_history_session("alice-1", "Bob"))
                self.assertTrue(alice_one.exists())
                self.assertEqual(len(database.get_sessions_for_participant("Alice")), 2)

                self.assertTrue(database.delete_history_session("alice-1", "Alice"))
                self.assertFalse(alice_one.exists())
                self.assertFalse(alice_one.parent.exists())
                self.assertEqual(len(database.get_sessions_for_participant("Alice")), 1)
                self.assertIn("Alice", database.get_all_participant_names())

                self.assertEqual(database.delete_participant_history("Alice"), 1)
                self.assertFalse(alice_two.exists())
                self.assertEqual(database.get_sessions_for_participant("Alice"), [])
                self.assertEqual(database.get_all_participant_names(), ["Bob"])
                self.assertTrue(bob_one.exists())

                connection = database._connect()
                try:
                    self.assertEqual(connection.execute("SELECT count(*) FROM trials").fetchone()[0], 1)
                    self.assertEqual(connection.execute("SELECT count(*) FROM biosignals").fetchone()[0], 1)
                finally:
                    connection.close()
                gc.collect()
        finally:
            database._db_path = original_path

    def test_websocket_delete_reports_result(self):
        original_path = database._db_path
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                database.init_db(str(Path(temp_dir) / "history.db"))
                database.insert_session("Carla", {
                    "session_id": "carla-1", "drill_key": "pvt",
                    "scenario_name": "Assessment",
                }, {"hits_pct": 50})

                async def run():
                    sent = []

                    async def send(_websocket, message):
                        sent.append(message)

                    coordinator = object.__new__(SessionLifecycleCoordinator)
                    coordinator._loop = asyncio.get_running_loop()
                    coordinator._send = send
                    await coordinator.handle_message(None, {
                        "type": "perf.delete_session",
                        "participant_name": "Wrong participant",
                        "session_uuid": "carla-1",
                    })
                    self.assertFalse(sent[-1]["success"])
                    await coordinator.handle_message(None, {
                        "type": "perf.delete_session",
                        "participant_name": "Carla",
                        "session_uuid": "carla-1",
                    })
                    self.assertEqual(sent[-1]["type"], "perf.delete_result")
                    self.assertTrue(sent[-1]["success"])
                    self.assertEqual(sent[-1]["deleted_count"], 1)

                asyncio.run(run())
                self.assertEqual(database.get_all_participant_names(), [])
                gc.collect()
        finally:
            database._db_path = original_path


if __name__ == "__main__":
    unittest.main()
