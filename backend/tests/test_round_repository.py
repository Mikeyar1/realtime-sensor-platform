"""Round-owned capture and assignment transaction checks."""

from __future__ import annotations

import sys
import json
import tempfile
import unittest
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services import database, round_repository as repo  # noqa: E402


class RoundRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = str(Path(self.folder.name) / "rounds.db")
        database.init_db(self.path)
        repo.capture_run(
            "run-1", drill_key="fitts_pvt_clay", category_id="pvt_fitts",
            profile_id="pvt_fitts_core", profile_version=1,
            scenario_name="Fitts-PVT_Clay", external_scenario_id="script-1",
            source="custom", started_at=1.0,
        )

    def tearDown(self):
        self.folder.cleanup()

    @staticmethod
    def round(number: int, *, ended=True) -> dict:
        return {
            "round_id": f"round-{number}", "round_number": number, "attempt": 1,
            "started_at": float(number * 10),
            "ended_at": float(number * 10 + 5) if ended else None,
            "ended_reason": "completed" if ended else "",
            "preset_label": "Default", "preset_provenance": "defaulted",
            "settings": {"active_distance_meters": 10},
            "metrics": {"hits": 1},
        }

    def test_completed_round_is_durable_before_run_ends_and_saves_independently(self):
        active = repo.capture_round("run-1", self.round(1, ended=False))
        self.assertEqual(active["status"], "active")
        performed_at = active["performed_at"]
        closed = repo.capture_round("run-1", self.round(1),
                                    [{"result": "hit", "rt_ms": 100}],
                                    [{"shot_id": "s1", "result": "hit"}])
        self.assertEqual(closed["duration_s"], 5)
        self.assertEqual(closed["save_status"], "draft")
        self.assertEqual(len(repo.get_round("round-1")["trials"]), 1)
        self.assertEqual(len(repo.get_round("round-1")["shots"]), 1)
        saved = repo.save_round("round-1", "Alex", "save-1", closed["revision"])
        self.assertEqual(saved["participant_name"], "Alex")
        self.assertEqual(saved["performed_at"], performed_at)
        self.assertEqual(repo.save_round("round-1", "Alex", "save-1"), saved)
        self.assertEqual(repo.get_run("run-1")["status"], "active")
        self.assertEqual(len(repo.list_history("Alex", preset_label="Default")), 1)

    def test_bulk_fallback_is_atomic_and_keeps_previously_saved_owner(self):
        for number in (1, 2, 3):
            repo.capture_round("run-1", self.round(number))
        repo.save_round("round-1", "Alex", "save-1")
        repo.set_draft_participant("round-2", "Jordan")
        with self.assertRaisesRegex(ValueError, "needs a participant"):
            repo.save_remaining("run-1", "", "bad-batch")
        self.assertEqual(repo.get_round("round-2")["save_status"], "draft")
        saved = repo.save_remaining("run-1", "Taylor", "good-batch")
        self.assertEqual([(r["round_number"], r["participant_name"]) for r in saved],
                         [(2, "Jordan"), (3, "Taylor")])
        self.assertEqual(repo.save_remaining("run-1", "Someone", "good-batch"), saved)
        self.assertEqual(len(repo.list_history("Alex")), 1)
        self.assertEqual(len(repo.list_history("Jordan")), 1)
        self.assertEqual(len(repo.list_history("Taylor")), 1)
        with database._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0], 0)

    def test_name_set_during_active_round_saves_it_on_completion(self):
        active = repo.capture_round("run-1", self.round(1, ended=False))
        repo.set_draft_participant("round-1", "Alex", active["revision"])
        closed = repo.capture_round("run-1", self.round(1))
        self.assertEqual(closed["save_status"], "saved")
        self.assertEqual(closed["participant_name"], "Alex")
        self.assertEqual(len(repo.list_history("Alex")), 1)

    def test_restart_preserves_completed_draft_and_marks_active_attempt_interrupted(self):
        repo.capture_round("run-1", self.round(1))
        repo.capture_round("run-1", self.round(2, ended=False))
        database.init_db(self.path)
        self.assertEqual(repo.get_round("round-1")["status"], "completed")
        self.assertEqual(repo.get_round("round-1")["save_status"], "draft")
        self.assertEqual(repo.get_round("round-2")["status"], "interrupted")
        self.assertEqual(repo.get_run("run-1")["status"], "interrupted")

    def test_duplicate_completed_capture_does_not_duplicate_evidence(self):
        row = self.round(1)
        repo.capture_round("run-1", row, [{"result": "hit"}], [{"shot_id": "s1"}])
        repo.capture_round("run-1", row, [{"result": "miss"}], [{"shot_id": "s2"}])
        stored = repo.get_round("round-1")
        self.assertEqual(stored["trials"], [{"result": "hit"}])
        self.assertEqual(stored["shots"], [{"shot_id": "s1"}])

    def test_participant_scoped_delete_preserves_other_rounds_and_run(self):
        repo.capture_round("run-1", self.round(1))
        second = self.round(2)
        second["preset_label"] = "Hard"
        repo.capture_round("run-1", second)
        first = repo.save_round("round-1", "Alex", "save-a")
        repo.save_round("round-2", "Jordan", "save-b")
        reassigned = repo.reassign_round("round-1", "Taylor", "reassign-a",
                                          first["revision"])
        self.assertEqual(repo.reassign_round("round-1", "Someone", "reassign-a"),
                         reassigned)
        self.assertEqual(len(repo.list_history("Taylor")), 1)
        self.assertEqual(repo.list_history("Alex"), [])
        self.assertEqual(repo.list_history("Jordan", preset_label="Default"), [])
        self.assertEqual(len(repo.list_history("Jordan", scenario_name="Fitts-PVT_Clay",
                                               preset_label="Hard")), 1)
        self.assertFalse(repo.delete_round("round-2", "Taylor"))
        self.assertTrue(repo.delete_round("round-1", "Taylor"))
        self.assertEqual(repo.list_history("Taylor"), [])
        self.assertEqual(len(repo.list_history("Jordan")), 1)
        self.assertEqual(repo.get_run("run-1")["status"], "active")

    def test_stale_assignment_revision_is_rejected_without_mutation(self):
        closed = repo.capture_round("run-1", self.round(1))
        repo.set_draft_participant("round-1", "Alex", closed["revision"])
        with self.assertRaisesRegex(ValueError, "changed"):
            repo.save_round("round-1", "Jordan", "stale", closed["revision"])
        self.assertEqual(repo.get_round("round-1")["draft_participant_name"], "Alex")
        self.assertEqual(repo.get_round("round-1")["save_status"], "draft")

    def test_delete_participant_history_removes_only_their_saved_rounds(self):
        repo.capture_round("run-1", self.round(1))
        repo.capture_round("run-1", self.round(2))
        repo.save_round("round-1", "Alex", "save-a")
        repo.save_round("round-2", "Jordan", "save-b")

        self.assertEqual(database.delete_participant_history("Alex"), 1)
        self.assertEqual(repo.list_history("Alex"), [])
        self.assertEqual(len(repo.list_history("Jordan")), 1)
        self.assertIsNone(repo.get_round("round-1"))
        self.assertEqual(repo.get_run("run-1")["status"], "active")

    def test_deleting_session_owner_does_not_remove_another_persons_round(self):
        with database._connect() as conn:
            conn.execute("INSERT INTO participants (id, participant_name, created_at) VALUES (?, ?, ?)",
                         ("alex", "Alex", "2026-01-01"))
            conn.execute("INSERT INTO participants (id, participant_name, created_at) VALUES (?, ?, ?)",
                         ("jordan", "Jordan", "2026-01-01"))
            session_id = conn.execute("""INSERT INTO sessions
                (session_uuid, participant_id) VALUES (?, ?)""",
                ("legacy-run", "alex")).lastrowid
            conn.execute("""INSERT INTO rounds
                (session_id, round_number, attempt, participant_id)
                VALUES (?, ?, ?, ?)""", (session_id, 1, 1, "jordan"))
            conn.execute("INSERT INTO trials (session_id, trial_number, round_number) VALUES (?, ?, ?)",
                         (session_id, 1, 1))

        with self.assertRaisesRegex(ValueError, "another participant"):
            database.delete_participant_history("Alex")
        with self.assertRaisesRegex(ValueError, "another participant"):
            database.delete_history_session("legacy-run", "Alex")
        with database._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0], 1)
        self.assertEqual(database.delete_participant_history("Jordan"), 1)
        with database._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0], 0)

    def test_delete_legacy_round_preserves_other_participants_round(self):
        with database._connect() as conn:
            for name in ("Alex", "Jordan"):
                conn.execute("INSERT INTO participants (id, participant_name, created_at) VALUES (?, ?, ?)",
                             (name, name, "2026-01-01"))
            session_id = conn.execute("""INSERT INTO sessions
                (session_uuid, participant_id, drill_key, source, rounds_json)
                VALUES (?, ?, ?, ?, ?)""",
                ("legacy-run", "Alex", "fitts_pvt_clay", "custom",
                 json.dumps([{"round_number": 1, "attempt": 1},
                             {"round_number": 2, "attempt": 1}]))).lastrowid
            first_id = conn.execute("""INSERT INTO rounds
                (session_id, round_number, attempt, participant_id, ended_reason)
                VALUES (?, ?, ?, ?, ?)""",
                (session_id, 1, 1, "Alex", "completed")).lastrowid
            conn.execute("""INSERT INTO rounds
                (session_id, round_number, attempt, participant_id, ended_reason)
                VALUES (?, ?, ?, ?, ?)""",
                (session_id, 2, 1, "Jordan", "completed"))
            for number in (1, 2):
                conn.execute("INSERT INTO trials (session_id, trial_number, round_number) VALUES (?, ?, ?)",
                             (session_id, number, number))

        self.assertFalse(database.delete_history_round(first_id, "Jordan"))
        self.assertTrue(database.delete_history_round(first_id, "Alex"))
        self.assertEqual(len(database.get_sessions_for_participant("Jordan")), 1)
        self.assertTrue(database.get_sessions_for_participant("Jordan")[0]["legacy_round_id"])
        with database._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT round_number FROM trials").fetchone()[0], 2)
            snapshots = json.loads(conn.execute("SELECT rounds_json FROM sessions").fetchone()[0])
            self.assertEqual([row["round_number"] for row in snapshots], [2])


if __name__ == "__main__":
    unittest.main()
