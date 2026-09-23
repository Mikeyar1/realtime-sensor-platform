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

# ── Connection helper ─────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    """Return a new connection with WAL mode and FK enforcement."""
    if not _db_path:
        raise RuntimeError("[database] init_db() has not been called")
    conn = sqlite3.connect(_db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn

# ── Public API ────────────────────────────────────────────────────────────────

def init_db(db_path: str) -> None:
    """
    Create the SQLite file and all tables (idempotent).
    Must be called once at application startup before any other DB function.
    """
    global _db_path
    _db_path = db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    with _lock:
        with _connect() as conn:
            conn.executescript(_DDL)
    print(f"[database] Initialized: {db_path}")


def get_all_participant_names() -> list[str]:
    """Return all known participant names, sorted alphabetically (case-insensitive)."""
    with _lock:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT participant_name FROM participants ORDER BY participant_name COLLATE NOCASE"
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
                "SELECT participant_name FROM participants "
                "WHERE participant_name LIKE 'unknown_participant_%' COLLATE NOCASE"
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
        name = next_unknown_name()

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
        participant_name:  Name to link (will be created if new / unknown).
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
        participant_name = next_unknown_name()

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
                         record_path, saved_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                session_id = cur.lastrowid
                if session_id == 0:
                    # session_uuid already existed — IGNORE'd, fetch existing id
                    row = conn.execute(
                        "SELECT id FROM sessions WHERE session_uuid = ?",
                        (session_metadata.get("session_id", ""),),
                    ).fetchone()
                    session_id = row["id"] if row else None
                    print(f"[database] Session uuid already exists — using existing id={session_id}")

                if session_id is None:
                    conn.rollback()
                    return None

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
                    ))
                if trial_rows:
                    conn.executemany(
                        """INSERT INTO trials
                           (session_id, trial_number, outcome, rt_ms,
                            target_type, target_name, round_number, t0)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
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
                }
                bio_rows = []
                for key, value in biosignals_dict.items():
                    if not isinstance(value, (int, float)):
                        continue
                    n_key = _N_KEYS.get(key, "")
                    n_samples = biosignals_dict.get(n_key, 0) if n_key else 0
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


def get_sessions_for_participant(
    participant_name: str,
    drill_key: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[dict]:
    """
    Retrieve sessions for a participant, optionally filtered by drill_key.
    Returns list of dicts sorted by date ascending.
    Used by the Performance Review tab (phase 2).
    """
    with _lock:
        with _connect() as conn:
            sql = """
                SELECT s.id, s.session_uuid, s.drill_key, s.task_type,
                       s.scenario_name, s.date, s.started_at, s.stopped_at,
                       s.duration_s, s.metrics_json, s.biosignals_json,
                       s.notes, s.saved_at, s.record_path
                FROM sessions s
                JOIN participants p ON p.id = s.participant_id
                WHERE p.participant_name = ? COLLATE NOCASE
            """
            params: list = [participant_name]
            if drill_key:
                sql += " AND s.drill_key = ?"
                params.append(drill_key)
            sql += " ORDER BY s.date ASC, s.started_at ASC"
            if limit:
                sql += f" LIMIT {int(limit)}"

            rows = conn.execute(sql, params).fetchall()

    result = []
    for row in rows:
        d = dict(row)
        d["metrics"]    = json.loads(d.pop("metrics_json", "{}") or "{}")
        d["biosignals"] = json.loads(d.pop("biosignals_json", "{}") or "{}")
        result.append(d)
    return result
