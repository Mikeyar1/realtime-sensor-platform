import asyncio
import gc
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import database
from services.session_lifecycle_coordinator import SessionLifecycleCoordinator


class _Inlets:
    def get_catalog(self):
        return []


class _PvtTracker:
    def get_session_record(self, **context):
        return {
            **context,
            "task_type": "PVT",
            "duration_s": 0.4,
            "trials": [{
                "trial_number": 1,
                "result": "hit",
                "rt_ms": 400,
                "activated_at": 10.0,
                "first_activated_at": 10.0,
                "completed_at": 10.4,
            }],
        }


class _GngTracker:
    def get_session_record(self, **context):
        return {
            **context,
            "task_type": "GNG",
            "duration_s": 0.5,
            "trials": [{
                "trial_number": 1,
                "target_type": "go",
                "result": "hit",
                "rt_ms": 500,
                "shot_at": 10.5,
            }],
        }


class ManualStopAndParticipantTests(unittest.TestCase):
    def test_manual_stop_saves_metrics_without_persisting_suggested_name(self):
        original_db_path = database._db_path
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                database.init_db(str(Path(temp_dir) / "sessions.db"))
                try:
                    asyncio.run(self._run_manual_stop())
                finally:
                    # database._connect() uses transaction context managers;
                    # collect connections before Windows removes the temp DB.
                    gc.collect()
        finally:
            database._db_path = original_db_path

    async def _run_manual_stop(self):
        loop = asyncio.get_running_loop()
        messages = []
        metrics_ready = asyncio.Event()

        async def broadcast(message):
            messages.append(message)
            if message.get("type") == "session.metrics_ready":
                metrics_ready.set()

        async def send(_websocket, message):
            messages.append(message)

        coordinator = SessionLifecycleCoordinator(_Inlets(), broadcast, send, loop)
        orchestrator = coordinator._orchestrator
        session_uuid = str(uuid.uuid4())
        orchestrator._active_drill = {"drill_key": "pvt", "task_type": "PVT"}
        orchestrator._active_scenario = "PVT"
        orchestrator._session_id = session_uuid
        orchestrator._session_started_at = 10.0
        orchestrator._active_tracker = _PvtTracker()

        await coordinator.handle_message(None, {"type": "session.stop"})
        await asyncio.wait_for(metrics_ready.wait(), timeout=5)

        stopped = next(m for m in messages if m.get("type") == "session.scenario_stopped")
        ready = next(m for m in messages if m.get("type") == "session.metrics_ready")
        self.assertTrue(stopped["manual"])
        self.assertEqual(ready["session_uuid"], session_uuid)
        self.assertTrue(ready["auto_saved"])
        self.assertEqual(ready["metrics"]["total_trials"], 1)
        self.assertEqual(ready["participant_name"], "unknown_participant_1")
        self.assertEqual(database.get_all_participant_names(), [])
        self.assertEqual(database.next_unknown_name(), "unknown_participant_1")

        await coordinator.handle_message(None, {
            "type": "db.confirm_session",
            "session_uuid": session_uuid,
            "participant_name": "Jane",
        })
        self.assertEqual(database.get_all_participant_names(), ["Jane"])
        self.assertEqual(database.next_unknown_name(), "unknown_participant_1")

        second_uuid = str(uuid.uuid4())
        database.insert_session("", {"session_id": second_uuid, "drill_key": "pvt"}, {})
        self.assertEqual(database.get_all_participant_names(), ["Jane"])
        self.assertEqual(database.next_unknown_name(), "unknown_participant_1")
        success, _ = database.update_participant_name(second_uuid, "unknown_participant_1")
        self.assertTrue(success)
        self.assertEqual(database.get_all_participant_names(), ["Jane", "unknown_participant_1"])
        self.assertEqual(database.next_unknown_name(), "unknown_participant_2")

        database.get_or_create_participant("unknown_participant_99")
        database.init_db(database._db_path)
        self.assertNotIn("unknown_participant_99", database.get_all_participant_names())
        self.assertEqual(database.next_unknown_name(), "unknown_participant_2")

        messages.clear()
        metrics_ready.clear()
        coordinator = SessionLifecycleCoordinator(_Inlets(), broadcast, send, loop)
        orchestrator = coordinator._orchestrator
        orchestrator._active_custom_drill = {
            "drill_key": "gng", "task_type": "GNG", "label": "Go/No-Go"
        }
        orchestrator._active_custom_scenario = "Multi-Color GNG"
        orchestrator._custom_session_id = str(uuid.uuid4())
        orchestrator._custom_session_started_at = 10.0
        orchestrator._active_custom_session = _GngTracker()

        await coordinator.handle_message(None, {"type": "session.stop"})
        await asyncio.wait_for(metrics_ready.wait(), timeout=5)
        stopped = next(m for m in messages if m.get("type") == "session.scenario_stopped")
        ready = next(m for m in messages if m.get("type") == "session.metrics_ready")
        self.assertTrue(stopped["manual"])
        self.assertTrue(ready["auto_saved"])
        self.assertEqual(ready["participant_name"], "unknown_participant_2")
        self.assertEqual(database.next_unknown_name(), "unknown_participant_2")
