"""Round-owned assessment persistence and participant assignment.

The legacy session tables remain readable during the staged migration. These
tables are authoritative for new round assessments and never require a fake
session participant. Every public mutation commits before returning.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import uuid
from pathlib import Path

from . import database


DDL = """
CREATE TABLE IF NOT EXISTS assessment_runs (
    id TEXT PRIMARY KEY,
    drill_key TEXT NOT NULL,
    category_id TEXT NOT NULL,
    assessment_profile TEXT NOT NULL,
    assessment_version INTEGER NOT NULL,
    scenario_name TEXT NOT NULL,
    external_scenario_id TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    ended_reason TEXT NOT NULL DEFAULT '',
    ended_source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS assessment_rounds (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES assessment_runs(id) ON DELETE CASCADE,
    round_number INTEGER NOT NULL,
    attempt INTEGER NOT NULL,
    participant_id TEXT REFERENCES participants(id),
    draft_participant_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    save_status TEXT NOT NULL DEFAULT 'draft',
    started_at REAL NOT NULL,
    ended_at REAL,
    duration_s REAL,
    performed_at TEXT NOT NULL DEFAULT '',
    preset_label TEXT NOT NULL DEFAULT '',
    preset_provenance TEXT NOT NULL DEFAULT '',
    preset_mixed INTEGER NOT NULL DEFAULT 0,
    settings_json TEXT NOT NULL DEFAULT '{}',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    quality_json TEXT NOT NULL DEFAULT '{}',
    physiology_json TEXT NOT NULL DEFAULT '{}',
    revision INTEGER NOT NULL DEFAULT 1,
    saved_at TEXT,
    UNIQUE(run_id, round_number, attempt)
);
CREATE INDEX IF NOT EXISTS idx_assessment_rounds_owner
    ON assessment_rounds(participant_id, save_status, started_at);
CREATE INDEX IF NOT EXISTS idx_assessment_rounds_run
    ON assessment_rounds(run_id, round_number, attempt);
CREATE INDEX IF NOT EXISTS idx_assessment_rounds_preset
    ON assessment_rounds(preset_label, started_at);
CREATE TABLE IF NOT EXISTS assessment_presentations (
    id INTEGER PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES assessment_rounds(id) ON DELETE CASCADE,
    presentation_index INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assessment_presentations_round
    ON assessment_presentations(round_id);
CREATE TABLE IF NOT EXISTS assessment_shots (
    id INTEGER PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES assessment_rounds(id) ON DELETE CASCADE,
    shot_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(round_id, shot_key)
);
CREATE TABLE IF NOT EXISTS assessment_physiology_samples (
    id INTEGER PRIMARY KEY,
    round_id TEXT NOT NULL REFERENCES assessment_rounds(id) ON DELETE CASCADE,
    signal TEXT NOT NULL,
    source_key TEXT NOT NULL,
    lsl_timestamp REAL NOT NULL,
    value REAL,
    value_2 REAL,
    UNIQUE(round_id, signal, source_key, lsl_timestamp)
);
CREATE INDEX IF NOT EXISTS idx_assessment_physiology_round
    ON assessment_physiology_samples(round_id, signal, lsl_timestamp);
CREATE TABLE IF NOT EXISTS assessment_command_receipts (
    request_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    committed_at TEXT NOT NULL
);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def available() -> bool:
    """The test harness sometimes runs an orchestrator without a database."""
    return bool(database._db_path and Path(database._db_path).is_file())


def install(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(assessment_runs)")}
    if "ended_source" not in columns:
        conn.execute("ALTER TABLE assessment_runs ADD COLUMN ended_source TEXT NOT NULL DEFAULT ''")
    round_columns = {row[1] for row in conn.execute("PRAGMA table_info(assessment_rounds)")}
    if "physiology_json" not in round_columns:
        conn.execute("ALTER TABLE assessment_rounds ADD COLUMN physiology_json TEXT NOT NULL DEFAULT '{}'")
    if "performed_at" not in round_columns:
        conn.execute("ALTER TABLE assessment_rounds ADD COLUMN performed_at TEXT NOT NULL DEFAULT ''")
        conn.execute("""UPDATE assessment_rounds SET performed_at =
            COALESCE(saved_at, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""")


def _participant_id(conn: sqlite3.Connection, name: str) -> str:
    name = " ".join(name.split())
    if not name or name.casefold() == database.UNASSIGNED_PARTICIPANT.casefold():
        raise ValueError("A participant name is required")
    row = conn.execute("SELECT id FROM participants WHERE participant_name = ? COLLATE NOCASE",
                       (name,)).fetchone()
    if row:
        return row["id"]
    participant_id = str(uuid.uuid4())
    conn.execute("INSERT INTO participants (id, participant_name, created_at) VALUES (?, ?, ?)",
                 (participant_id, name, _now()))
    return participant_id


def capture_run(run_id: str, *, drill_key: str, category_id: str,
                profile_id: str, profile_version: int, scenario_name: str,
                external_scenario_id: str, source: str, started_at: float) -> None:
    with database._lock:
        with database._connect() as conn:
            conn.execute("""INSERT OR IGNORE INTO assessment_runs
                (id, drill_key, category_id, assessment_profile, assessment_version,
                 scenario_name, external_scenario_id, source, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (run_id, drill_key, category_id, profile_id, profile_version,
                 scenario_name, external_scenario_id, source, started_at))


def capture_round(run_id: str, round_record: dict, trials: list[dict] | None = None,
                  shots: list[dict] | None = None) -> dict:
    """Checkpoint an active round or finalize one attempt exactly once."""
    round_id = round_record["round_id"]
    ended_at = round_record.get("ended_at")
    status = round_record.get("ended_reason") or "active"
    started_at = round_record["started_at"]
    duration = max(0, ended_at - started_at) if ended_at is not None else None
    with database._lock:
        with database._connect() as conn:
            existing = conn.execute("SELECT status FROM assessment_rounds WHERE id = ?",
                                    (round_id,)).fetchone()
            if existing is not None and existing["status"] != "active":
                return get_round(round_id, conn=conn)
            conn.execute("""INSERT INTO assessment_rounds
                (id, run_id, round_number, attempt, status, started_at, ended_at,
                 duration_s, performed_at, preset_label, preset_provenance, preset_mixed,
                 settings_json, metrics_json, quality_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status=excluded.status, ended_at=excluded.ended_at,
                    duration_s=excluded.duration_s, preset_label=excluded.preset_label,
                    preset_provenance=excluded.preset_provenance,
                    preset_mixed=excluded.preset_mixed, settings_json=excluded.settings_json,
                    metrics_json=excluded.metrics_json,
                    quality_json=excluded.quality_json,
                    revision=assessment_rounds.revision+1""",
                (round_id, run_id, round_record["round_number"], round_record.get("attempt", 1),
                 status, started_at, ended_at, duration, _now(),
                 round_record.get("preset_label") or "",
                 round_record.get("preset_provenance") or "",
                 int(bool(round_record.get("preset_mixed"))),
                 json.dumps(round_record.get("settings") or {}),
                 json.dumps(round_record.get("metrics") or {}),
                 json.dumps(round_record.get("quality") or {})))
            if ended_at is not None:
                conn.execute("DELETE FROM assessment_presentations WHERE round_id = ?", (round_id,))
                for index, trial in enumerate(trials or [], 1):
                    conn.execute("""INSERT INTO assessment_presentations
                        (round_id, presentation_index, payload_json) VALUES (?, ?, ?)""",
                        (round_id, index, json.dumps(trial)))
                conn.execute("DELETE FROM assessment_shots WHERE round_id = ?", (round_id,))
                for index, shot in enumerate(shots or [], 1):
                    conn.execute("""INSERT INTO assessment_shots
                        (round_id, shot_key, payload_json) VALUES (?, ?, ?)""",
                        (round_id, str(shot.get("shot_id") or f"event-{index}"), json.dumps(shot)))
            if status == "completed":
                draft = conn.execute("""SELECT draft_participant_name FROM assessment_rounds
                    WHERE id = ? AND save_status = 'draft'""", (round_id,)).fetchone()
                if draft and draft["draft_participant_name"].strip():
                    _save_one(conn, round_id, draft["draft_participant_name"])
            return get_round(round_id, conn=conn)


def close_run(run_id: str, ended_at: float, reason: str,
              source: str = "custom") -> None:
    with database._lock:
        with database._connect() as conn:
            conn.execute("""UPDATE assessment_runs SET ended_at = ?, ended_reason = ?,
                ended_source = ?, status = 'ended'
                WHERE id = ? AND status = 'active'""", (ended_at, reason, source, run_id))


def recover_interrupted_runs() -> int:
    """Mark unfinished attempts after a process restart without inventing closure time."""
    with database._lock:
        with database._connect() as conn:
            conn.execute("""UPDATE assessment_rounds SET status = 'interrupted', revision = revision + 1
                WHERE status = 'active' AND run_id IN
                (SELECT id FROM assessment_runs WHERE status = 'active')""")
            changed = conn.execute("SELECT changes()").fetchone()[0]
            conn.execute("""UPDATE assessment_runs SET status = 'interrupted',
                ended_reason = 'process_restarted' WHERE status = 'active'""")
            return changed


def _row(row: sqlite3.Row) -> dict:
    result = dict(row)
    for key in ("settings_json", "metrics_json", "quality_json", "physiology_json"):
        if key in result:
            result[key.removesuffix("_json")] = json.loads(result.pop(key) or "{}")
    result["preset_mixed"] = bool(result["preset_mixed"])
    result["ended_reason"] = result["status"] if result["status"] != "active" else ""
    return result


def get_round(round_id: str, *, conn: sqlite3.Connection | None = None) -> dict | None:
    if conn is None:
        with database._lock:
            with database._connect() as owned:
                return get_round(round_id, conn=owned)
    row = conn.execute("""SELECT r.*, p.participant_name, ar.scenario_name,
        ar.drill_key, ar.category_id, ar.assessment_profile, ar.assessment_version
        FROM assessment_rounds r JOIN assessment_runs ar ON ar.id = r.run_id
        LEFT JOIN participants p ON p.id = r.participant_id WHERE r.id = ?""",
        (round_id,)).fetchone()
    if not row:
        return None
    result = _row(row)
    result["trials"] = [json.loads(item[0]) for item in conn.execute(
        "SELECT payload_json FROM assessment_presentations WHERE round_id = ? ORDER BY id",
        (round_id,))]
    result["shots"] = [json.loads(item[0]) for item in conn.execute(
        "SELECT payload_json FROM assessment_shots WHERE round_id = ? ORDER BY id",
        (round_id,))]
    return result


def get_run(run_id: str) -> dict | None:
    with database._lock:
        with database._connect() as conn:
            row = conn.execute("SELECT * FROM assessment_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["rounds"] = [get_round(item[0], conn=conn) for item in conn.execute(
                "SELECT id FROM assessment_rounds WHERE run_id = ? ORDER BY started_at, round_number, attempt",
                (run_id,))]
            return result


def append_physiology_samples(round_id: str, rows: list[tuple]) -> int:
    """Commit a bounded batch of (signal, source, timestamp, value, value_2)."""
    if not rows:
        return 0
    with database._lock:
        with database._connect() as conn:
            before = conn.total_changes
            conn.executemany("""INSERT OR IGNORE INTO assessment_physiology_samples
                (round_id, signal, source_key, lsl_timestamp, value, value_2)
                VALUES (?, ?, ?, ?, ?, ?)""",
                [(round_id, *row) for row in rows])
            return conn.total_changes - before


def get_physiology_samples(round_id: str) -> list[dict]:
    with database._lock:
        with database._connect() as conn:
            return [dict(row) for row in conn.execute("""SELECT signal, source_key,
                lsl_timestamp, value, value_2 FROM assessment_physiology_samples
                WHERE round_id = ? ORDER BY signal, lsl_timestamp, id""", (round_id,))]


def finalize_physiology(round_id: str, summary: dict, quality: dict) -> dict:
    with database._lock:
        with database._connect() as conn:
            row = conn.execute("SELECT quality_json FROM assessment_rounds WHERE id = ?",
                               (round_id,)).fetchone()
            if row is None:
                raise ValueError("Round not found")
            merged = {**json.loads(row["quality_json"] or "{}"), **quality}
            conn.execute("""UPDATE assessment_rounds SET physiology_json = ?,
                quality_json = ?, revision = revision + 1 WHERE id = ?""",
                (json.dumps(summary), json.dumps(merged), round_id))
            return get_round(round_id, conn=conn)


def _receipt(conn: sqlite3.Connection, request_id: str) -> dict | None:
    row = conn.execute("SELECT result_json FROM assessment_command_receipts WHERE request_id = ?",
                       (request_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _save_one(conn: sqlite3.Connection, round_id: str, name: str,
              expected_revision: int | None = None) -> dict:
    row = conn.execute("SELECT * FROM assessment_rounds WHERE id = ?", (round_id,)).fetchone()
    if row is None:
        raise ValueError("Round not found")
    if row["status"] == "active":
        raise ValueError("An active round cannot be saved")
    if expected_revision is not None and row["revision"] != expected_revision:
        raise ValueError("Round changed; reload before saving")
    if row["save_status"] == "saved":
        raise ValueError("Round is already saved; use explicit reassignment")
    owner = _participant_id(conn, name)
    conn.execute("""UPDATE assessment_rounds SET participant_id = ?, draft_participant_name = '',
        save_status = 'saved', saved_at = ?, revision = revision + 1 WHERE id = ?""",
        (owner, _now(), round_id))
    return get_round(round_id, conn=conn)


def save_round(round_id: str, participant_name: str, request_id: str,
               expected_revision: int | None = None) -> dict:
    if not request_id:
        raise ValueError("request_id is required")
    with database._lock:
        with database._connect() as conn:
            previous = _receipt(conn, request_id)
            if previous is not None:
                return previous
            result = _save_one(conn, round_id, participant_name, expected_revision)
            conn.execute("INSERT INTO assessment_command_receipts VALUES (?, ?, ?)",
                         (request_id, json.dumps(result), _now()))
            return result


def reassign_round(round_id: str, participant_name: str, request_id: str,
                   expected_revision: int | None = None) -> dict:
    """Explicitly correct ownership of one saved round without changing evidence."""
    if not request_id:
        raise ValueError("request_id is required")
    with database._lock:
        with database._connect() as conn:
            previous = _receipt(conn, request_id)
            if previous is not None:
                return previous
            row = conn.execute("SELECT revision, save_status FROM assessment_rounds WHERE id = ?",
                               (round_id,)).fetchone()
            if row is None:
                raise ValueError("Round not found")
            if row["save_status"] != "saved":
                raise ValueError("Round must be saved before reassignment")
            if expected_revision is not None and row["revision"] != expected_revision:
                raise ValueError("Round changed; reload before reassigning")
            owner = _participant_id(conn, participant_name)
            conn.execute("""UPDATE assessment_rounds SET participant_id = ?,
                revision = revision + 1 WHERE id = ?""", (owner, round_id))
            result = get_round(round_id, conn=conn)
            conn.execute("INSERT INTO assessment_command_receipts VALUES (?, ?, ?)",
                         (request_id, json.dumps(result), _now()))
            return result


def save_remaining(run_id: str, fallback_name: str, request_id: str,
                   explicit_names: dict[str, str] | None = None) -> list[dict]:
    if not request_id:
        raise ValueError("request_id is required")
    explicit_names = explicit_names or {}
    with database._lock:
        with database._connect() as conn:
            previous = _receipt(conn, request_id)
            if previous is not None:
                return previous
            rows = conn.execute("""SELECT id, draft_participant_name FROM assessment_rounds
                WHERE run_id = ? AND status = 'completed' AND save_status = 'draft'
                ORDER BY started_at, round_number, attempt""", (run_id,)).fetchall()
            if not conn.execute("SELECT 1 FROM assessment_runs WHERE id = ?", (run_id,)).fetchone():
                raise ValueError("Run not found")
            results = []
            for row in rows:
                name = explicit_names.get(row["id"]) or row["draft_participant_name"] or fallback_name
                if not name.strip():
                    raise ValueError("Each remaining round needs a participant")
                results.append(_save_one(conn, row["id"], name))
            conn.execute("INSERT INTO assessment_command_receipts VALUES (?, ?, ?)",
                         (request_id, json.dumps(results), _now()))
            return results


def set_draft_participant(round_id: str, participant_name: str,
                          expected_revision: int | None = None) -> dict:
    with database._lock:
        with database._connect() as conn:
            row = conn.execute("SELECT revision, save_status FROM assessment_rounds WHERE id = ?",
                               (round_id,)).fetchone()
            if row is None:
                raise ValueError("Round not found")
            if row["save_status"] != "draft":
                raise ValueError("Saved rounds require explicit reassignment")
            if expected_revision is not None and row["revision"] != expected_revision:
                raise ValueError("Round changed; reload before editing")
            conn.execute("""UPDATE assessment_rounds SET draft_participant_name = ?,
                revision = revision + 1 WHERE id = ?""",
                (" ".join(participant_name.split()), round_id))
            return get_round(round_id, conn=conn)


def list_participants() -> list[dict]:
    with database._lock:
        with database._connect() as conn:
            return [dict(row) for row in conn.execute("""SELECT id, participant_name
                FROM participants WHERE participant_name <> ?
                ORDER BY participant_name COLLATE NOCASE""",
                (database.UNASSIGNED_PARTICIPANT,))]


def list_history(participant_name: str, *, scenario_name: str = "",
                 preset_label: str = "", limit: int = 100) -> list[dict]:
    if not participant_name.strip():
        return []
    with database._lock:
        with database._connect() as conn:
            rows = conn.execute("""SELECT r.id FROM assessment_rounds r
                JOIN participants p ON p.id = r.participant_id
                JOIN assessment_runs ar ON ar.id = r.run_id
                WHERE p.participant_name = ? COLLATE NOCASE AND r.save_status = 'saved'
                AND (? = '' OR ar.scenario_name = ?)
                AND (? = '' OR r.preset_label = ?)
                ORDER BY r.performed_at DESC, r.id DESC LIMIT ?""",
                (participant_name, scenario_name, scenario_name,
                 preset_label, preset_label, max(1, min(limit, 1000)))).fetchall()
            return [get_round(row["id"], conn=conn) for row in reversed(rows)]


def get_saved_round_for_participant(round_id: str, participant_name: str) -> dict | None:
    if not participant_name.strip():
        return None
    with database._lock:
        with database._connect() as conn:
            row = conn.execute("""SELECT r.id FROM assessment_rounds r
                JOIN participants p ON p.id = r.participant_id
                WHERE r.id = ? AND r.save_status = 'saved'
                AND p.participant_name = ? COLLATE NOCASE""",
                (round_id, participant_name)).fetchone()
            return get_round(row["id"], conn=conn) if row else None


def delete_round(round_id: str, participant_name: str) -> bool:
    """Delete only a saved result owned by the named participant."""
    with database._lock:
        with database._connect() as conn:
            cursor = conn.execute("""DELETE FROM assessment_rounds WHERE id = ?
                AND save_status = 'saved' AND participant_id IN
                (SELECT id FROM participants WHERE participant_name = ? COLLATE NOCASE)""",
                (round_id, participant_name))
            return cursor.rowcount == 1


def delete_participant_history(participant_name: str) -> int:
    with database._lock:
        with database._connect() as conn:
            cursor = conn.execute("""DELETE FROM assessment_rounds WHERE save_status = 'saved'
                AND participant_id IN
                (SELECT id FROM participants WHERE participant_name = ? COLLATE NOCASE)""",
                (participant_name,))
            return cursor.rowcount


def discard_unsaved_run(run_id: str) -> bool:
    with database._lock:
        with database._connect() as conn:
            if conn.execute("""SELECT 1 FROM assessment_rounds WHERE run_id = ?
                AND save_status = 'saved'""", (run_id,)).fetchone():
                raise ValueError("Saved rounds cannot be discarded with the run")
            return conn.execute("DELETE FROM assessment_runs WHERE id = ?", (run_id,)).rowcount == 1
