"""Bounded raw LSL event and physiology writer for later session review."""

from __future__ import annotations

import datetime as dt
import json
import math
import queue
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager, closing
from pathlib import Path


DDL = """
CREATE TABLE IF NOT EXISTS capture_sessions (
    id TEXT PRIMARY KEY,
    provenance TEXT NOT NULL,
    software_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    lost_events INTEGER NOT NULL DEFAULT 0,
    lost_signals INTEGER NOT NULL DEFAULT 0,
    writer_errors INTEGER NOT NULL DEFAULT 0,
    processing_errors INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS lsl_event_log (
    id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL REFERENCES capture_sessions(id),
    ingest_sequence INTEGER NOT NULL,
    outlet_uid TEXT NOT NULL,
    stream_name TEXT NOT NULL,
    stream_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    lsl_timestamp REAL NOT NULL,
    received_at TEXT NOT NULL,
    raw_channels_json TEXT NOT NULL,
    decode_status TEXT NOT NULL,
    decode_error TEXT NOT NULL,
    decoded_event_json TEXT NOT NULL,
    decoder_version INTEGER NOT NULL,
    UNIQUE(capture_id, ingest_sequence)
);
CREATE INDEX IF NOT EXISTS idx_lsl_events_capture_time
    ON lsl_event_log(capture_id, ingest_sequence, lsl_timestamp);
CREATE TABLE IF NOT EXISTS assessment_event_links (
    id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES lsl_event_log(id),
    run_id TEXT NOT NULL,
    round_id TEXT,
    evidence_role TEXT NOT NULL,
    attribution_decision TEXT NOT NULL,
    attribution_reason TEXT NOT NULL DEFAULT '',
    scenario_id TEXT NOT NULL DEFAULT '',
    scenario_revision INTEGER,
    mapping_version INTEGER NOT NULL DEFAULT 1,
    UNIQUE(event_id, run_id, evidence_role)
);
CREATE INDEX IF NOT EXISTS idx_assessment_event_links_run
    ON assessment_event_links(run_id, round_id, event_id);
CREATE TABLE IF NOT EXISTS lsl_signal_log (
    id TEXT PRIMARY KEY,
    capture_id TEXT NOT NULL REFERENCES capture_sessions(id),
    ingest_sequence INTEGER NOT NULL,
    session_id TEXT REFERENCES assessment_runs(id) ON DELETE SET NULL,
    outlet_uid TEXT NOT NULL,
    source_id TEXT NOT NULL,
    stream_name TEXT NOT NULL,
    stream_type TEXT NOT NULL,
    signal_config_id TEXT NOT NULL,
    lsl_timestamp REAL NOT NULL,
    received_at TEXT NOT NULL,
    raw_channels_json TEXT NOT NULL,
    UNIQUE(capture_id, ingest_sequence)
);
CREATE INDEX IF NOT EXISTS idx_lsl_signal_session_time
    ON lsl_signal_log(session_id, lsl_timestamp, ingest_sequence);
CREATE INDEX IF NOT EXISTS idx_lsl_signal_capture_time
    ON lsl_signal_log(capture_id, lsl_timestamp);
"""


def install(conn: sqlite3.Connection) -> None:
    conn.executescript(DDL)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(capture_sessions)")}
    if "processing_errors" not in columns:
        conn.execute("""ALTER TABLE capture_sessions ADD COLUMN processing_errors
            INTEGER NOT NULL DEFAULT 0""")
    if "lost_signals" not in columns:
        conn.execute("ALTER TABLE capture_sessions ADD COLUMN lost_signals INTEGER NOT NULL DEFAULT 0")
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(lsl_event_log)")}
    if "session_id" not in event_columns:
        conn.execute("ALTER TABLE lsl_event_log ADD COLUMN session_id TEXT")
    conn.executescript("""
        CREATE INDEX IF NOT EXISTS idx_lsl_event_session_time
            ON lsl_event_log(session_id, lsl_timestamp, ingest_sequence);
        CREATE VIEW IF NOT EXISTS v_session_event_log AS
            SELECT session_id, id AS event_id, capture_id, ingest_sequence,
                   outlet_uid, source_id, stream_name, stream_type, source_kind,
                   lsl_timestamp, received_at, raw_channels_json, decode_status,
                   decode_error, decoded_event_json, decoder_version
            FROM lsl_event_log WHERE session_id IS NOT NULL;
        CREATE VIEW IF NOT EXISTS v_round_event_log AS
            SELECT r.id AS round_id, r.round_number, r.attempt,
                   e.session_id, e.event_id, e.capture_id, e.ingest_sequence,
                   e.outlet_uid, e.source_id, e.stream_name, e.stream_type,
                   e.source_kind, e.lsl_timestamp, e.received_at,
                   e.raw_channels_json, e.decode_status, e.decoded_event_json
            FROM v_session_event_log e JOIN assessment_rounds r
              ON r.run_id = e.session_id AND e.lsl_timestamp >= r.started_at
             AND (r.ended_at IS NULL OR e.lsl_timestamp <= r.ended_at);
        CREATE VIEW IF NOT EXISTS v_session_signal_log AS
            SELECT session_id, id AS sample_id, capture_id, ingest_sequence,
                   outlet_uid, source_id, stream_name, stream_type,
                   signal_config_id, lsl_timestamp, received_at, raw_channels_json
            FROM lsl_signal_log WHERE session_id IS NOT NULL;
        CREATE VIEW IF NOT EXISTS v_round_signal_log AS
            SELECT r.id AS round_id, r.round_number, r.attempt,
                   s.session_id, s.sample_id, s.capture_id, s.ingest_sequence,
                   s.outlet_uid, s.source_id, s.stream_name, s.stream_type,
                   s.signal_config_id, s.lsl_timestamp, s.received_at,
                   s.raw_channels_json
            FROM v_session_signal_log s JOIN assessment_rounds r
              ON r.run_id = s.session_id AND s.lsl_timestamp >= r.started_at
             AND (r.ended_at IS NULL OR s.lsl_timestamp <= r.ended_at);
    """)


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class EventStore:
    def __init__(self, db_path: str, *, max_queue: int = 8192,
                 provenance: str = "lsl_ingest", software_version: str = "0.1.0"):
        if max_queue < 1:
            raise ValueError("max_queue must be positive")
        self.db_path = str(Path(db_path).resolve())
        self.capture_id = str(uuid.uuid4())
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue)
        self._lock = threading.Lock()
        self._sequence = 0
        self._signal_sequence = 0
        self._lost = 0
        self._lost_signals = 0
        self._errors = 0
        self._processing_errors = 0
        self._last_error = ""
        self._closed = False
        with self._connection() as conn:
            conn.execute("""UPDATE assessment_runs SET log_status = 'incomplete'
                WHERE log_status = 'recording' AND log_capture_id IN
                (SELECT id FROM capture_sessions WHERE status = 'active')""")
            conn.execute("""UPDATE capture_sessions SET status = 'interrupted',
                ended_at = ? WHERE status = 'active'""", (_utc_now(),))
            conn.execute("""INSERT INTO capture_sessions
                (id, provenance, software_version, started_at) VALUES (?, ?, ?, ?)""",
                (self.capture_id, provenance, software_version, _utc_now()))
        self._worker = threading.Thread(target=self._write_loop, daemon=True,
                                        name="lsl-event-writer")
        self._worker.start()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def append(self, *, outlet_uid: str, stream_name: str, stream_type: str,
               source_id: str, lsl_timestamp: float, channels: list,
               source_event) -> str | None:
        """Reserve an event ID before routing; return None if evidence was lost."""
        event_id = str(uuid.uuid4())
        with self._lock:
            if self._closed or self._errors:
                self._lost += 1
                return None
            sequence = self._sequence + 1
            row = (event_id, self.capture_id, sequence, outlet_uid or "",
                   stream_name, stream_type, source_id or "", source_event.source,
                   lsl_timestamp, _utc_now(), json.dumps(channels, ensure_ascii=False),
                   source_event.status, source_event.error,
                   json.dumps({"event_name": source_event.event_name,
                               "payload": source_event.payload}, ensure_ascii=False),
                   source_event.parser_version)
            try:
                self._queue.put_nowait(("event", row))
            except queue.Full:
                self._lost += 1
                return None
            self._sequence = sequence
        return event_id

    def link(self, event_id: str, *, run_id: str, round_id: str | None,
             evidence_role: str, decision: str, reason: str = "",
             scenario_id: str = "", scenario_revision: int | None = None) -> bool:
        if not event_id or not run_id:
            return False
        row = (event_id, run_id, round_id, evidence_role, decision, reason,
               scenario_id, scenario_revision)
        with self._lock:
            if self._closed or self._errors:
                self._lost += 1
                return False
            try:
                self._queue.put_nowait(("link", row))
            except queue.Full:
                self._lost += 1
                return False
        return True

    def append_signal(self, *, outlet_uid: str, stream_name: str, stream_type: str,
                      source_id: str, signal_config_id: str, lsl_timestamp: float,
                      channels: list, session_id: str = "") -> bool:
        """Queue raw physiological channels before validity filtering."""
        def json_value(value):
            if isinstance(value, float) and not math.isfinite(value):
                return str(value)
            return value

        with self._lock:
            if self._closed or self._errors:
                self._lost_signals += 1
                return False
            sequence = self._signal_sequence + 1
            row = (str(uuid.uuid4()), self.capture_id, sequence,
                   session_id or None, outlet_uid or "", source_id or "",
                   stream_name, stream_type, signal_config_id, lsl_timestamp,
                   _utc_now(), json.dumps([json_value(item) for item in channels],
                                          ensure_ascii=False, allow_nan=False))
            try:
                self._queue.put_nowait(("signal", row))
            except queue.Full:
                self._lost_signals += 1
                return False
            self._signal_sequence = sequence
        return True

    def assign_event_session(self, event_id: str, session_id: str) -> bool:
        return self._enqueue("event_session", (session_id, event_id))

    def assign_signal_window(self, session_id: str, started_at: float,
                             ended_at: float) -> bool:
        """Retroactively include sensor samples captured before run recognition."""
        return self._enqueue("signal_window", (session_id, self.capture_id,
                                                started_at, ended_at))

    def assign_event_window(self, session_id: str, source_kind: str,
                            started_at: float, ended_at: float) -> bool:
        return self._enqueue("event_window", (session_id, self.capture_id,
                                               source_kind, started_at, ended_at))

    def _enqueue(self, kind: str, row: tuple) -> bool:
        with self._lock:
            if self._closed or self._errors:
                self._lost += 1
                return False
            try:
                self._queue.put_nowait((kind, row))
            except queue.Full:
                self._lost += 1
                return False
        return True

    def _write_loop(self):
        while True:
            command = self._queue.get()
            if command is None:
                self._queue.task_done()
                return
            batch = [command]
            while len(batch) < 128:
                try:
                    item = self._queue.get_nowait()
                except queue.Empty:
                    break
                if item is None:
                    # The sentinel remains queued until this batch commits.
                    self._queue.task_done()
                    self._queue.put(None)
                    break
                batch.append(item)
            try:
                with self._connection() as conn:
                    for kind, row in batch:
                        if kind == "event":
                            conn.execute("""INSERT INTO lsl_event_log
                                (id, capture_id, ingest_sequence, outlet_uid, stream_name,
                                 stream_type, source_id, source_kind, lsl_timestamp,
                                 received_at, raw_channels_json, decode_status,
                                 decode_error, decoded_event_json, decoder_version)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
                        elif kind == "link":
                            conn.execute("""INSERT OR IGNORE INTO assessment_event_links
                                (event_id, run_id, round_id, evidence_role,
                                 attribution_decision, attribution_reason,
                                 scenario_id, scenario_revision)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", row)
                        elif kind == "signal":
                            conn.execute("""INSERT INTO lsl_signal_log
                                (id, capture_id, ingest_sequence, session_id, outlet_uid,
                                 source_id, stream_name, stream_type, signal_config_id,
                                 lsl_timestamp, received_at, raw_channels_json)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", row)
                        elif kind == "event_session":
                            conn.execute("""UPDATE lsl_event_log SET session_id = ?
                                WHERE id = ? AND session_id IS NULL""", row)
                        elif kind == "signal_window":
                            conn.execute("""UPDATE lsl_signal_log SET session_id = ?
                                WHERE capture_id = ? AND lsl_timestamp BETWEEN ? AND ?
                                  AND session_id IS NULL""", row)
                        elif kind == "event_window":
                            conn.execute("""UPDATE lsl_event_log SET session_id = ?
                                WHERE capture_id = ? AND source_kind = ?
                                  AND lsl_timestamp BETWEEN ? AND ?
                                  AND session_id IS NULL""", row)
            except Exception as exc:
                with self._lock:
                    self._errors += 1
                    self._lost += sum(kind == "event" for kind, _ in batch)
                    self._lost_signals += sum(kind == "signal" for kind, _ in batch)
                    self._last_error = str(exc)
            finally:
                for _ in batch:
                    self._queue.task_done()

    def flush(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while self._queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)
        return self._queue.unfinished_tasks == 0 and not self._errors

    def health(self) -> dict:
        with self._lock:
            db_file = Path(self.db_path)
            stored_bytes = 0
            for path in (db_file, Path(self.db_path + "-wal")):
                try:
                    stored_bytes += path.stat().st_size
                except FileNotFoundError:
                    pass  # WAL may disappear during a checkpoint.
            return {"capture_id": self.capture_id, "queued": self._queue.qsize(),
                    "lost_events": self._lost, "lost_signals": self._lost_signals,
                    "writer_errors": self._errors,
                    "processing_errors": self._processing_errors,
                    "database_bytes": stored_bytes,
                    "last_error": self._last_error,
                    "complete": self._lost == 0 and self._lost_signals == 0 and
                                self._errors == 0 and
                                self._processing_errors == 0}

    def mark_processing_error(self, exc: Exception) -> None:
        with self._lock:
            self._processing_errors += 1
            self._last_error = str(exc)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.flush()
        self._queue.put(None)
        self._worker.join(timeout=5)
        status = self.health()
        with self._connection() as conn:
            conn.execute("""UPDATE capture_sessions SET ended_at = ?, status = ?,
                lost_events = ?, lost_signals = ?, writer_errors = ?,
                processing_errors = ? WHERE id = ?""",
                (_utc_now(), "complete" if status["complete"] else "incomplete",
                 status["lost_events"], status["lost_signals"], status["writer_errors"],
                 status["processing_errors"], self.capture_id))
            conn.execute("""UPDATE assessment_runs SET log_status = 'incomplete'
                WHERE log_capture_id = ? AND log_status = 'recording'""",
                (self.capture_id,))

    def get_capture_events(self, capture_id: str, *, limit: int = 1000,
                           after_sequence: int = 0) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        uri = Path(self.db_path).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""SELECT * FROM lsl_event_log
                WHERE capture_id = ? AND ingest_sequence > ?
                ORDER BY ingest_sequence LIMIT ?""",
                (capture_id, after_sequence, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_run_events(self, run_id: str, *, limit: int = 1000) -> list[dict]:
        limit = max(1, min(int(limit), 1000))
        uri = Path(self.db_path).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""SELECT e.*, l.round_id, l.evidence_role,
                    l.attribution_decision, l.attribution_reason,
                    l.scenario_id, l.scenario_revision
                FROM assessment_event_links l JOIN lsl_event_log e ON e.id = l.event_id
                WHERE l.run_id = ? ORDER BY e.ingest_sequence LIMIT ?""",
                (run_id, limit)).fetchall()
        return [dict(row) for row in rows]

    def get_capture_runs(self, capture_id: str) -> list[dict]:
        """List run IDs needed to inspect round or session-only evidence."""
        uri = Path(self.db_path).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""SELECT l.run_id, l.scenario_id,
                    COUNT(DISTINCT l.event_id) AS event_count,
                    COUNT(DISTINCT l.round_id) AS round_count,
                    MIN(e.ingest_sequence) AS first_sequence,
                    MAX(e.ingest_sequence) AS last_sequence
                FROM assessment_event_links l JOIN lsl_event_log e ON e.id = l.event_id
                WHERE e.capture_id = ?
                GROUP BY l.run_id, l.scenario_id
                ORDER BY first_sequence""", (capture_id,)).fetchall()
        return [dict(row) for row in rows]
