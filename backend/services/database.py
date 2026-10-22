"""
services/database.py — LabReview SQLite persistence layer.

Provides:
  - init_db()                       Create schema (idempotent, call at startup)
  - get_or_create_participant(name) Lookup or insert a participant by name
  - next_unknown_name()             Generate "unknown_participant_N" suffix
  - insert_session(...)             Persist a full session (session + trials + biosignals)
  - delete_session(session_uuid)    Remove a session and its child rows (discard)
  - update_participant_name(...)    Rename a participant (case-insensitive unique enforced)
  - get_all_participant_names()     Return sorted list of all known names
  - get_sessions_for_participant()  Longitudinal query for Performance Review (phase 2)

All public functions are synchronous and thread-safe via a module-level
threading.Lock. Call them from asyncio via loop.run_in_executor().

Schema:
  participants (id TEXT PK uuid, participant_name TEXT UNIQUE NOCASE, created_at, notes)
  sessions     (id INTEGER PK, session_uuid TEXT UNIQUE, participant_id FK, drill_key,
                task_type, scenario_name, date, started_at, stopped_at, duration_s,
                active_streams TEXT json, notes, metrics_json, biosignals_json,
                record_path, saved_at)
  trials       (id INTEGER PK, session_id FK, trial_number, outcome, rt_ms,
                target_type, target_name, round_number, t0)
  biosignals   (id INTEGER PK, session_id FK, signal_type, value, n_samples)
"""

import datetime
from contextlib import contextmanager
import json
import os
import sqlite3
import threading
import uuid
from typing import Optional

# ── Module-level state ────────────────────────────────────────────────────────

_db_path: str = ""
_lock = threading.Lock()

# ── Schema DDL ────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS participants (
    id               TEXT PRIMARY KEY,              -- UUID, auto-generated
    participant_name TEXT NOT NULL UNIQUE
                         COLLATE NOCASE,            -- case-insensitive unique
    created_at       TEXT NOT NULL,                 -- ISO-8601
    notes            TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sessions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_uuid     TEXT UNIQUE,                   -- VirTra/custom LSL UUID
    participant_id   TEXT NOT NULL
                         REFERENCES participants(id) ON DELETE CASCADE,
    drill_key        TEXT NOT NULL DEFAULT '',      -- "pvt", "gng", ...
    task_type        TEXT NOT NULL DEFAULT '',      -- "PVT", "GNG", ...
    scenario_name    TEXT NOT NULL DEFAULT '',
    date             TEXT NOT NULL DEFAULT '',      -- YYYY-MM-DD
    started_at       REAL,                          -- LSL timestamp
    stopped_at       REAL,
    duration_s       REAL,
    active_streams   TEXT NOT NULL DEFAULT '[]',    -- JSON array
    notes            TEXT NOT NULL DEFAULT '',
    metrics_json     TEXT NOT NULL DEFAULT '{}',    -- full metrics dict
    biosignals_json  TEXT NOT NULL DEFAULT '{}',    -- biosignal snapshot
    record_path      TEXT NOT NULL DEFAULT '',      -- abs path to session_record.json
    saved_at         TEXT NOT NULL DEFAULT ''       -- ISO-8601 wall-clock of auto-save
);

CREATE INDEX IF NOT EXISTS idx_sessions_participant
    ON sessions(participant_id);
CREATE INDEX IF NOT EXISTS idx_sessions_drill_date
    ON sessions(drill_key, date);

CREATE TABLE IF NOT EXISTS trials (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL
                         REFERENCES sessions(id) ON DELETE CASCADE,
    trial_number     INTEGER NOT NULL,
    outcome          TEXT NOT NULL DEFAULT '',      -- "HIT","MISS","OMISSION","false_alarm",...
    rt_ms            REAL,                          -- nullable: omissions/timeouts
    target_type      TEXT NOT NULL DEFAULT '',      -- "go","no_go","neutral",""
    target_name      TEXT NOT NULL DEFAULT '',
    round_number     INTEGER NOT NULL DEFAULT 0,
    t0               REAL                           -- stimulus onset LSL timestamp
);

CREATE INDEX IF NOT EXISTS idx_trials_session
    ON trials(session_id);

CREATE TABLE IF NOT EXISTS rounds (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    round_number     INTEGER NOT NULL,
    attempt          INTEGER NOT NULL,
    participant_id   TEXT REFERENCES participants(id),
    preset_label     TEXT NOT NULL DEFAULT '',
    preset_mixed     INTEGER NOT NULL DEFAULT 0,
    ended_reason     TEXT NOT NULL DEFAULT '',
    started_at       REAL,
    ended_at         REAL,
    metrics_json     TEXT NOT NULL DEFAULT '{}',
    UNIQUE(session_id, round_number, attempt)
);

CREATE INDEX IF NOT EXISTS idx_rounds_participant_preset
    ON rounds(participant_id, preset_label);

CREATE TABLE IF NOT EXISTS biosignals (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       INTEGER NOT NULL
                         REFERENCES sessions(id) ON DELETE CASCADE,
    signal_type      TEXT NOT NULL,                 -- "hr_mean_bpm","hrv_rmssd_ms",...
    value            REAL NOT NULL,
    n_samples        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_biosignals_session
    ON biosignals(session_id);
"""

# Auto-saved sessions without a participant share this internal placeholder until
# the operator confirms a name. It is never offered as a participant name.
UNASSIGNED_PARTICIPANT = "__unassigned__"

# ── Connection helper ─────────────────────────────────────────────────────────

@contextmanager
def _connect():
    """Yield a connection with WAL mode and FK enforcement, then close it."""
    if not _db_path:
        raise RuntimeError("[database] init_db() has not been called")
    conn = sqlite3.connect(_db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()

# ── Public API ────────────────────────────────────────────────────────────────

def init_db(db_path: str) -> None:
    """
    Create the SQLite file and all tables (idempotent).
    Must be called once at application startup before any other DB function.
    """
    global _db_path
    from . import round_repository
    from audit import event_store
    _db_path = db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with _lock:
        with _connect() as conn:
            conn.executescript(_DDL)
            _migrate_round_columns(conn)
            round_repository.install(conn)
            event_store.install(conn)
            conn.execute(
                """DELETE FROM participants
                   WHERE (participant_name LIKE 'unknown_participant_%'
                          OR participant_name = ?)
                   AND NOT EXISTS (
                       SELECT 1 FROM sessions WHERE sessions.participant_id = participants.id
                   )""",
                (UNASSIGNED_PARTICIPANT,),
            )
            conn.commit()
    round_repository.recover_interrupted_runs()
    print(f"[database] Initialized: {db_path}")


def _migrate_round_columns(conn: sqlite3.Connection) -> None:
    additions = {
        "sessions": {
            "source": "TEXT NOT NULL DEFAULT 'native'",
            "capture_status": "TEXT NOT NULL DEFAULT 'complete'",
            "external_scenario_id": "TEXT NOT NULL DEFAULT ''",
            "rounds_json": "TEXT NOT NULL DEFAULT '[]'",
            "native_context_json": "TEXT NOT NULL DEFAULT '{}'",
            "custom_scenario_name": "TEXT NOT NULL DEFAULT ''",
        },
        "trials": {
            "round_attempt": "INTEGER NOT NULL DEFAULT 1",
            "component": "TEXT NOT NULL DEFAULT ''",
        },
    }
    for table, columns in additions.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def get_all_participant_names() -> list[str]:
    """Return all known participant names, sorted alphabetically (case-insensitive)."""
    with _lock:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT participant_name FROM participants p
                   WHERE participant_name <> ?
                   AND (EXISTS (SELECT 1 FROM sessions s WHERE s.participant_id = p.id)
                     OR EXISTS (SELECT 1 FROM rounds r WHERE r.participant_id = p.id)
                     OR EXISTS (SELECT 1 FROM assessment_rounds ar
                                WHERE ar.participant_id = p.id AND ar.save_status = 'saved'))
                   ORDER BY participant_name COLLATE NOCASE""",
                (UNASSIGNED_PARTICIPANT,),
            ).fetchall()
    return [r["participant_name"] for r in rows]


def next_unknown_name() -> str:
    """
    Return the next available "unknown_participant_N" name.
    Scans existing names with that prefix and increments the highest N.
    """
    with _lock:
        with _connect() as conn:
            rows = conn.execute(
                """SELECT participant_name FROM participants p
                   WHERE participant_name LIKE 'unknown_participant_%' COLLATE NOCASE
                   AND EXISTS (SELECT 1 FROM sessions s WHERE s.participant_id = p.id)"""
            ).fetchall()
    max_n = 0
    for row in rows:
        try:
            suffix = row["participant_name"].rsplit("_", 1)[-1]
            n = int(suffix)
            if n > max_n:
                max_n = n
        except (ValueError, IndexError):
            pass
    return f"unknown_participant_{max_n + 1}"


def get_or_create_participant(name: str) -> str:
    """
    Look up a participant by name (NOCASE). If not found, create a new row.
    Returns the participant UUID.
    """
    name = (name or "").strip()
    if not name:
        name = UNASSIGNED_PARTICIPANT

    with _lock:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id FROM participants WHERE participant_name = ? COLLATE NOCASE",
                (name,),
            ).fetchone()
            if row:
                return row["id"]

            new_id = str(uuid.uuid4())
            now    = datetime.datetime.now(datetime.timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO participants (id, participant_name, created_at) VALUES (?, ?, ?)",
                (new_id, name, now),
            )
            conn.commit()
            print(f"[database] New participant created: {name!r} -> {new_id}")
            return new_id


def insert_session(
    participant_name:  str,
    session_metadata:  dict,
    metrics:           Optional[dict],
    record_path:       str = "",
) -> Optional[int]:
    """
    Insert a complete session record (session + trials + biosignals) in one transaction.

    Args:
        participant_name:  Name to link (uses a shared unassigned placeholder if empty).
        session_metadata:  Dict from VirTraScenarioOrchestrator._last_session_metadata.
                           Expected keys: session_id, drill_key, task_type, scenario_name,
                           started_at, stopped_at, duration_s, active_streams, notes,
                           biosignals (dict), trials (list[dict]).
        metrics:           Computed metrics dict (may be None if no trials).
        record_path:       Absolute path to the session_record.json file.

    Returns:
        The new sessions.id (integer), or None if the insert failed.
    """
    if not participant_name or not participant_name.strip():
        participant_name = UNASSIGNED_PARTICIPANT

    participant_uuid = get_or_create_participant(participant_name)

    biosignals_dict = session_metadata.get("biosignals", {}) or {}
    trials_list     = session_metadata.get("trials", []) or []
    now             = datetime.datetime.now(datetime.timezone.utc).isoformat()
    date_str        = now[:10]  # YYYY-MM-DD

    with _lock:
        with _connect() as conn:
            try:
                # ── sessions row ──────────────────────────────────────────────
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO sessions
                        (session_uuid, participant_id, drill_key, task_type,
                         scenario_name, date, started_at, stopped_at, duration_s,
                         active_streams, notes, metrics_json, biosignals_json,
                         record_path, saved_at, source, external_scenario_id,
                         rounds_json, capture_status, native_context_json,
                         custom_scenario_name)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_metadata.get("session_id", ""),
                        participant_uuid,
                        session_metadata.get("drill_key", ""),
                        session_metadata.get("task_type", ""),
                        session_metadata.get("scenario_name", ""),
                        date_str,
                        session_metadata.get("started_at"),
                        session_metadata.get("stopped_at"),
                        session_metadata.get("duration_s"),
                        json.dumps(session_metadata.get("active_streams", [])),
                        session_metadata.get("notes", ""),
                        json.dumps(metrics or {}),
                        json.dumps(biosignals_dict),
                        record_path,
                        now,
                        session_metadata.get("source", "native"),
                        session_metadata.get("external_scenario_id", ""),
                        json.dumps(session_metadata.get("rounds", [])),
                        session_metadata.get("capture_status", "complete"),
                        json.dumps(session_metadata.get("native_context", {})),
                        session_metadata.get("custom_scenario_name", ""),
                    ),
                )
                session_id = cur.lastrowid
                if cur.rowcount == 0:
                    # A repeated stop must not append duplicate child rows.
                    row = conn.execute(
                        "SELECT id FROM sessions WHERE session_uuid = ?",
                        (session_metadata.get("session_id", ""),),
                    ).fetchone()
                    return row["id"] if row else None

                if session_id is None:
                    conn.rollback()
                    return None

                if session_metadata.get("drill_key") == "fitts_pvt_clay":
                    round_rows = []
                    for round_record in session_metadata.get("rounds", []):
                        round_rows.append((
                            session_id, round_record.get("round_number"),
                            round_record.get("attempt", 1),
                            str(round_record.get("preset_label") or ""),
                            int(bool(round_record.get("preset_mixed"))),
                            str(round_record.get("ended_reason") or ""),
                            round_record.get("started_at"), round_record.get("ended_at"),
                            json.dumps(round_record.get("metrics") or {}),
                        ))
                    if round_rows:
                        conn.executemany(
                            """INSERT INTO rounds
                               (session_id, round_number, attempt, preset_label,
                                preset_mixed, ended_reason, started_at, ended_at, metrics_json)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", round_rows)

                # ── trials rows ───────────────────────────────────────────────
                trial_rows = []
                for t in trials_list:
                    trial_rows.append((
                        session_id,
                        t.get("trial_number") or t.get("trial"),
                        str(t.get("outcome") or t.get("result", "")),
                        t.get("rt_ms"),
                        str(t.get("target_type", "")),
                        str(t.get("target_name", "")),
                        t.get("round_number") or t.get("round", 0),
                        t.get("t0") or t.get("activated_at"),
                        t.get("round_attempt", 1),
                        str(t.get("component", "")),
                    ))
                if trial_rows:
                    conn.executemany(
                        """INSERT INTO trials
                           (session_id, trial_number, outcome, rt_ms,
                            target_type, target_name, round_number, t0,
                            round_attempt, component)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        trial_rows,
                    )

                # ── biosignals rows ───────────────────────────────────────────
                # biosignals_dict keys: hr_mean_bpm, hrv_rmssd_ms, pupil_l_mean_mm, ...
                # n_samples keys follow: hr_n_samples, hrv_n_intervals, pupil_l_n, ...
                _N_KEYS = {
                    "hr_mean_bpm":    "hr_n_samples",
                    "hrv_rmssd_ms":   "hrv_n_intervals",
                    "pupil_l_mean_mm": "pupil_l_n",
                    "pupil_r_mean_mm": "pupil_r_n",
                    "pupil_mean_mm":   "pupil_n",
                }
                bio_rows = []
                for key, n_key in _N_KEYS.items():
                    value = biosignals_dict.get(key)
                    if not isinstance(value, (int, float)):
                        continue
                    n_samples = biosignals_dict.get(n_key, 0)
                    bio_rows.append((session_id, key, float(value), int(n_samples)))
                if bio_rows:
                    conn.executemany(
                        """INSERT INTO biosignals (session_id, signal_type, value, n_samples)
                           VALUES (?, ?, ?, ?)""",
                        bio_rows,
                    )

                conn.commit()
                print(
                    f"[database] Session saved: id={session_id} "
                    f"participant={participant_name!r} drill={session_metadata.get('drill_key')!r} "
                    f"trials={len(trial_rows)} biosignals={len(bio_rows)}"
                )
                return session_id

            except Exception as e:
                conn.rollback()
                print(f"[database] ✗ insert_session failed: {e}")
                import traceback; traceback.print_exc()
                return None


def update_participant_name(session_uuid: str, new_name: str) -> tuple[bool, str]:
    """
    Update the participant name linked to a specific session_uuid.

    If new_name already exists as a different participant, that participant's UUID
    is used (merging the session into the existing participant).
    If new_name is brand new, it is created.

    Returns (success: bool, message: str).
    """
    new_name = (new_name or "").strip()
    if not new_name:
        return False, "Name cannot be empty"
    if new_name.casefold() == UNASSIGNED_PARTICIPANT.casefold():
        return False, "Choose a participant name"

    with _lock:
        with _connect() as conn:
            # Fetch current participant_id linked to this session
            sess_row = conn.execute(
                "SELECT id, participant_id FROM sessions WHERE session_uuid = ?",
                (session_uuid,),
            ).fetchone()
            if not sess_row:
                return False, f"Session {session_uuid!r} not found"

            session_id       = sess_row["id"]
            old_participant_id = sess_row["participant_id"]

            # Check if new_name already exists
            existing = conn.execute(
                "SELECT id FROM participants WHERE participant_name = ? COLLATE NOCASE",
                (new_name,),
            ).fetchone()

            if existing:
                new_participant_id = existing["id"]
            else:
                # Create a new participant
                new_participant_id = str(uuid.uuid4())
                now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                conn.execute(
                    "INSERT INTO participants (id, participant_name, created_at) VALUES (?, ?, ?)",
                    (new_participant_id, new_name, now),
                )

            # Re-point this session to the new participant
            conn.execute(
                "UPDATE sessions SET participant_id = ? WHERE id = ?",
                (new_participant_id, session_id),
            )

            # If the old participant now has no sessions, remove them
            leftover = conn.execute(
                "SELECT COUNT(*) AS cnt FROM sessions WHERE participant_id = ?",
                (old_participant_id,),
            ).fetchone()
            if leftover and leftover["cnt"] == 0:
                conn.execute("DELETE FROM participants WHERE id = ?", (old_participant_id,))
                print(f"[database] Removed orphaned participant {old_participant_id}")

            conn.commit()
            print(f"[database] Session {session_uuid!r} re-assigned to participant {new_name!r}")
            return True, f"Session updated to participant {new_name!r}"


def confirm_fitts_round_participants(
    session_uuid: str, session_name: str, assignments: list[dict]
) -> tuple[bool, str]:
    """Confirm every completed Fitts round's owner in one transaction."""
    if not isinstance(assignments, list):
        return False, "Round participants must be a list"
    provided = {}
    for item in assignments:
        if not isinstance(item, dict):
            return False, "Invalid round participant entry"
        try:
            key = (int(item["round_number"]), int(item.get("attempt", 1)))
        except (KeyError, TypeError, ValueError):
            return False, "Invalid round number or attempt"
        name = str(item.get("participant_name") or "").strip()
        if not name or name.casefold() == UNASSIGNED_PARTICIPANT.casefold():
            return False, "Every completed round needs a participant name"
        if key in provided:
            return False, "Duplicate round participant entry"
        provided[key] = name
    with _lock:
        with _connect() as conn:
            session = conn.execute(
                "SELECT id, drill_key, rounds_json FROM sessions WHERE session_uuid = ?", (session_uuid,)
            ).fetchone()
            if session is None or session["drill_key"] != "fitts_pvt_clay":
                return False, "Fitts-PVT session not found"
            rows = conn.execute(
                "SELECT id, round_number, attempt, ended_reason FROM rounds WHERE session_id = ?",
                (session["id"],),
            ).fetchall()
            valid = {(row["round_number"], row["attempt"]) for row in rows}
            required = {(row["round_number"], row["attempt"]) for row in rows
                        if row["ended_reason"] == "completed"}
            if not required <= provided.keys():
                return False, "Assign a participant to each completed round"
            if not provided.keys() <= valid:
                return False, "Round participant entry does not match this session"

            def participant_id(name: str) -> str:
                row = conn.execute(
                    "SELECT id FROM participants WHERE participant_name = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()
                if row:
                    return row["id"]
                new_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO participants (id, participant_name, created_at) VALUES (?, ?, ?)",
                    (new_id, name, datetime.datetime.now(datetime.timezone.utc).isoformat()),
                )
                return new_id

            for row in rows:
                key = (row["round_number"], row["attempt"])
                if key in provided:
                    conn.execute("UPDATE rounds SET participant_id = ? WHERE id = ?",
                                 (participant_id(provided[key]), row["id"]))
            round_snapshots = json.loads(session["rounds_json"] or "[]")
            for snapshot in round_snapshots:
                key = (snapshot.get("round_number"), snapshot.get("attempt", 1))
                if key in provided:
                    snapshot["participant_name"] = provided[key]
            conn.execute("UPDATE sessions SET rounds_json = ? WHERE id = ?",
                         (json.dumps(round_snapshots), session["id"]))
            if session_name.strip():
                conn.execute("UPDATE sessions SET participant_id = ? WHERE id = ?",
                             (participant_id(session_name.strip()), session["id"]))
            conn.commit()
    return True, "Round participants confirmed"


def delete_session(session_uuid: str) -> bool:
    """
    Delete a session (and cascade-delete its trials and biosignals) by session_uuid.
    Also deletes the session_record.json file from disk if record_path is set.
    Also deletes the containing folder if it becomes empty.
    Returns True if a row was deleted.
    """
    with _lock:
        with _connect() as conn:
            row = conn.execute(
                "SELECT id, record_path, participant_id FROM sessions WHERE session_uuid = ?",
                (session_uuid,),
            ).fetchone()
            if not row:
                print(f"[database] delete_session: session {session_uuid!r} not found")
                return False

            session_id     = row["id"]
            record_path    = row["record_path"]
            participant_id = row["participant_id"]

            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

            # If participant has no more sessions, remove them too
            leftover = conn.execute(
                "SELECT COUNT(*) AS cnt FROM sessions WHERE participant_id = ?",
                (participant_id,),
            ).fetchone()
            if leftover and leftover["cnt"] == 0:
                conn.execute("DELETE FROM participants WHERE id = ?", (participant_id,))

            conn.commit()

    # Delete JSON file and folder from disk (outside the lock)
    if record_path and os.path.isfile(record_path):
        try:
            os.remove(record_path)
            folder = os.path.dirname(record_path)
            if os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
            print(f"[database] Deleted session file: {record_path}")
        except OSError as e:
            print(f"[database] Warning: could not delete {record_path}: {e}")

    print(f"[database] Session {session_uuid!r} discarded")
    return True


def delete_history_session(session_uuid: str, participant_name: str) -> bool:
    """Delete one saved session only when it belongs to the named participant."""
    if not session_uuid or not participant_name:
        return False
    with _lock:
        with _connect() as conn:
            row = conn.execute(
                """SELECT s.id, s.record_path, s.participant_id
                   FROM sessions s JOIN participants p ON p.id = s.participant_id
                   WHERE s.session_uuid = ? AND p.participant_name = ? COLLATE NOCASE""",
                (session_uuid, participant_name),
            ).fetchone()
            if row is None:
                return False
            shared_round = conn.execute("""SELECT 1 FROM rounds
                WHERE session_id = ? AND participant_id IS NOT NULL
                AND participant_id <> ? LIMIT 1""",
                (row["id"], row["participant_id"])).fetchone()
            if shared_round:
                raise ValueError("This session contains rounds saved to another participant.")
            conn.execute("DELETE FROM sessions WHERE id = ?", (row["id"],))
            conn.execute(
                """DELETE FROM participants WHERE id = ?
                   AND NOT EXISTS (SELECT 1 FROM sessions WHERE participant_id = ?)
                   AND NOT EXISTS (SELECT 1 FROM rounds WHERE participant_id = ?)
                   AND NOT EXISTS (SELECT 1 FROM assessment_rounds WHERE participant_id = ?)""",
                (row["participant_id"],) * 4,
            )
            conn.commit()
            record_path = row["record_path"]
    _delete_record_files([record_path])
    return True


def delete_history_round(round_id: int, participant_name: str) -> bool:
    """Delete one participant-owned legacy round and its trials."""
    if not round_id or not participant_name:
        return False
    record_path = ""
    with _lock:
        with _connect() as conn:
            row = conn.execute("""SELECT r.id, r.session_id, r.round_number, r.attempt,
                       s.rounds_json, s.record_path
                FROM rounds r JOIN sessions s ON s.id = r.session_id
                JOIN participants p ON p.id = r.participant_id
                WHERE r.id = ? AND p.participant_name = ? COLLATE NOCASE
                  AND s.drill_key = 'fitts_pvt_clay' AND s.source = 'custom'""",
                (round_id, participant_name)).fetchone()
            if row is None:
                return False
            conn.execute("""DELETE FROM trials WHERE session_id = ?
                AND round_number = ? AND round_attempt = ?""",
                (row["session_id"], row["round_number"], row["attempt"]))
            conn.execute("DELETE FROM rounds WHERE id = ?", (row["id"],))
            remaining = conn.execute("SELECT 1 FROM rounds WHERE session_id = ? LIMIT 1",
                                     (row["session_id"],)).fetchone()
            if remaining:
                snapshots = json.loads(row["rounds_json"] or "[]")
                snapshots = [item for item in snapshots
                             if (item.get("round_number"), item.get("attempt", 1)) !=
                             (row["round_number"], row["attempt"])]
                conn.execute("UPDATE sessions SET rounds_json = ? WHERE id = ?",
                             (json.dumps(snapshots), row["session_id"]))
            else:
                conn.execute("DELETE FROM sessions WHERE id = ?", (row["session_id"],))
                record_path = row["record_path"]
            conn.commit()
    if record_path:
        _delete_record_files([record_path])
    return True


def delete_participant_history(participant_name: str) -> int:
    """Delete a participant's sessions and saved rounds without deleting others' rounds."""
    if not participant_name or participant_name == UNASSIGNED_PARTICIPANT:
        return 0
    orphan_record_paths = []
    with _lock:
        with _connect() as conn:
            participant = conn.execute(
                "SELECT id FROM participants WHERE participant_name = ? COLLATE NOCASE",
                (participant_name,),
            ).fetchone()
            if participant is None:
                return 0
            rows = conn.execute(
                "SELECT id, record_path FROM sessions WHERE participant_id = ?",
                (participant["id"],),
            ).fetchall()
            shared_round = conn.execute("""SELECT 1 FROM rounds r
                JOIN sessions s ON s.id = r.session_id
                WHERE s.participant_id = ? AND r.participant_id IS NOT NULL
                  AND r.participant_id <> ? LIMIT 1""",
                (participant["id"], participant["id"])).fetchone()
            if shared_round:
                raise ValueError("A session contains rounds saved to another participant. Delete those rounds separately.")
            assessment_count = conn.execute("""SELECT COUNT(*) FROM assessment_rounds
                WHERE participant_id = ? AND save_status = 'saved'""",
                (participant["id"],)).fetchone()[0]
            legacy_rows = conn.execute("""SELECT r.session_id, r.round_number, r.attempt,
                       s.rounds_json
                FROM rounds r JOIN sessions s ON s.id = r.session_id
                WHERE r.participant_id = ? AND s.participant_id <> ?""",
                (participant["id"], participant["id"])).fetchall()
            by_session = {}
            for row in legacy_rows:
                conn.execute("""DELETE FROM trials WHERE session_id = ?
                    AND round_number = ? AND round_attempt = ?""",
                    (row["session_id"], row["round_number"], row["attempt"]))
                if row["session_id"] not in by_session:
                    by_session[row["session_id"]] = [row["rounds_json"], set()]
                by_session[row["session_id"]][1].add((row["round_number"], row["attempt"]))
            for session_id, (raw_snapshots, removed_keys) in by_session.items():
                snapshots = [item for item in json.loads(raw_snapshots or "[]")
                             if (item.get("round_number"), item.get("attempt", 1))
                             not in removed_keys]
                conn.execute("UPDATE sessions SET rounds_json = ? WHERE id = ?",
                             (json.dumps(snapshots), session_id))
            conn.execute("DELETE FROM assessment_rounds WHERE participant_id = ?",
                         (participant["id"],))
            conn.execute("DELETE FROM rounds WHERE participant_id = ?",
                         (participant["id"],))
            for session_id in by_session:
                if conn.execute("SELECT 1 FROM rounds WHERE session_id = ? LIMIT 1",
                                (session_id,)).fetchone() is None:
                    orphan = conn.execute("SELECT record_path, drill_key, source FROM sessions WHERE id = ?",
                                          (session_id,)).fetchone()
                    if orphan and orphan["drill_key"] == "fitts_pvt_clay" and orphan["source"] == "custom":
                        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
                        orphan_record_paths.append(orphan["record_path"])
            conn.execute("DELETE FROM participants WHERE id = ?", (participant["id"],))
            conn.commit()
    _delete_record_files([row["record_path"] for row in rows] + orphan_record_paths)
    return len(rows) + assessment_count + len(legacy_rows)


def _delete_record_files(paths: list[str]) -> None:
    """Remove persisted session records after the database transaction commits."""
    for record_path in paths:
        if not record_path or not os.path.isfile(record_path):
            continue
        try:
            os.remove(record_path)
            folder = os.path.dirname(record_path)
            if os.path.isdir(folder) and not os.listdir(folder):
                os.rmdir(folder)
        except OSError as exc:
            print(f"[database] Warning: could not delete {record_path}: {exc}")


def get_sessions_for_participant(
    participant_name: str,
    drill_key: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """
    Retrieve sessions for a participant, optionally filtered by drill_key.
    Returns list of dicts sorted by save time ascending.
    Used by the Performance Review tab (phase 2).
    """
    with _lock:
        with _connect() as conn:
            sql = """
                SELECT s.id, s.session_uuid, s.drill_key, s.task_type,
                       s.scenario_name, s.date, s.started_at, s.stopped_at,
                       s.duration_s, s.metrics_json, s.biosignals_json,
                       s.notes, s.saved_at, s.record_path, s.source,
                       s.external_scenario_id, s.capture_status,
                       s.native_context_json, s.custom_scenario_name,
                       p.participant_name AS session_participant_name
                FROM sessions s
                JOIN participants p ON p.id = s.participant_id
                WHERE p.participant_name = ? COLLATE NOCASE
                  AND NOT (s.drill_key = 'fitts_pvt_clay' AND s.source = 'custom')
            """
            params: list = [participant_name]
            if drill_key:
                sql += " AND s.drill_key = ?"
                params.append(drill_key)
            sql += " ORDER BY s.saved_at ASC, s.id ASC"
            rows = conn.execute(sql, params).fetchall()
            round_rows = conn.execute("""
                SELECT r.id AS legacy_round_id, s.session_uuid, s.drill_key, s.task_type, s.scenario_name,
                       s.date, s.started_at, s.stopped_at, s.saved_at, s.source,
                       s.capture_status, r.round_number, r.attempt AS round_attempt,
                       r.preset_label, r.preset_mixed, r.ended_reason,
                       r.started_at AS round_started_at, r.ended_at AS round_ended_at,
                       r.metrics_json
                FROM rounds r
                JOIN sessions s ON s.id = r.session_id
                JOIN participants p ON p.id = r.participant_id
                WHERE p.participant_name = ? COLLATE NOCASE
                  AND s.drill_key = 'fitts_pvt_clay' AND s.source = 'custom'
                  AND r.ended_reason = 'completed'
                  AND (? IS NULL OR s.drill_key = ?)
                ORDER BY s.saved_at, r.started_at, r.id
            """, (participant_name, drill_key, drill_key)).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["metrics"]    = json.loads(d.pop("metrics_json", "{}") or "{}")
        d["biosignals"] = json.loads(d.pop("biosignals_json", "{}") or "{}")
        d["native_context"] = json.loads(d.pop("native_context_json", "{}") or "{}")
        d["metrics_source"] = d["source"]
        result.append(d)
    for row in round_rows:
        d = dict(row)
        d["metrics"] = json.loads(d.pop("metrics_json") or "{}")
        d["biosignals"] = {}
        d["metrics_source"] = d["source"]
        d["result_scope"] = "round"
        d["duration_s"] = (d["round_ended_at"] - d["round_started_at"]
                           if d["round_ended_at"] is not None and d["round_started_at"] is not None
                           else None)
        result.append(d)
    result.sort(key=lambda item: (item.get("saved_at") or "", item.get("round_started_at") or 0))
    if limit:
        result = result[-limit:]
    return result


def get_session_detail(session_uuid: str, participant_name: str,
                       round_number: int | None = None, attempt: int = 1) -> dict | None:
    """Return one participant-owned session with its saved trials and round detail."""
    with _lock:
        with _connect() as conn:
            row = conn.execute("""
                SELECT s.session_uuid, s.drill_key, s.task_type, s.scenario_name,
                       s.date, s.started_at, s.stopped_at, s.duration_s,
                       s.metrics_json, s.biosignals_json, s.rounds_json,
                       s.source, s.external_scenario_id, s.capture_status,
                       s.native_context_json, s.custom_scenario_name,
                       p.participant_name AS session_participant_name
                FROM sessions s JOIN participants p ON p.id = s.participant_id
                WHERE s.session_uuid = ? AND (
                    p.participant_name = ? COLLATE NOCASE OR EXISTS (
                        SELECT 1 FROM rounds r JOIN participants rp ON rp.id = r.participant_id
                        WHERE r.session_id = s.id AND rp.participant_name = ? COLLATE NOCASE))
            """, (session_uuid, participant_name, participant_name)).fetchone()
            if row is None:
                return None
            trials = [dict(trial) for trial in conn.execute("""
                SELECT t.trial_number, t.outcome, t.rt_ms, t.target_type,
                       t.target_name, t.round_number, t.round_attempt,
                       t.component, t.t0
                FROM trials t JOIN sessions s ON s.id = t.session_id
                WHERE s.session_uuid = ? ORDER BY t.id
            """, (session_uuid,)).fetchall()]
    detail = dict(row)
    for field in ("metrics", "biosignals", "rounds", "native_context"):
        detail[field] = json.loads(detail.pop(field + "_json"))
    detail["metrics_source"] = detail["source"]
    detail["trials"] = trials
    if (detail["drill_key"] == "fitts_pvt_clay" and round_number is None
            and detail["session_participant_name"].casefold() != participant_name.casefold()):
        return None
    if round_number is not None and detail["drill_key"] == "fitts_pvt_clay":
        selected = [item for item in detail["rounds"]
                    if item.get("round_number") == round_number
                    and item.get("attempt", 1) == attempt]
        if not selected:
            return None
        with _lock:
            with _connect() as conn:
                owner = conn.execute("""
                    SELECT 1 FROM rounds r JOIN sessions s ON s.id = r.session_id
                    JOIN participants p ON p.id = r.participant_id
                    WHERE s.session_uuid = ? AND r.round_number = ? AND r.attempt = ?
                      AND p.participant_name = ? COLLATE NOCASE
                """, (session_uuid, round_number, attempt, participant_name)).fetchone()
        if owner is None:
            return None
        detail["rounds"] = selected
        detail["trials"] = [trial for trial in trials
                            if trial["round_number"] == round_number
                            and trial["round_attempt"] == attempt]
        detail["metrics"] = selected[0].get("metrics") or {}
        detail["biosignals"] = {}
        detail["result_scope"] = "round"
    return detail
