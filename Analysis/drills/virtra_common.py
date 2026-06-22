"""
drills/virtra_common.py — Shared VirTra event-stream parsing utilities.

All drill algorithms that read from the lsl_unmapped_samples table should use
these helpers instead of copy-pasting the same SQLite + JSON logic.

Public API
----------
parse_virtra_event(raw_json)  → (event_type: str, description: str, meta: dict)
get_all_virtra_events(db_path)→ list[(ts: float, event_type: str, desc: str, meta: dict)]
"""

from __future__ import annotations

import json
import sqlite3
from typing import Optional


# ── JSON row parser ───────────────────────────────────────────────────────────

def parse_virtra_event(raw_json: str) -> tuple[str, str, dict]:
    """
    Parse one row from lsl_unmapped_samples.sample_json.

    VirTra events are stored as a JSON array:
      [{"value": "<event_type>"}, {"value": "<description>"}, ..., {"value": "<meta_json>"}]

    Returns:
        event_type  — e.g. "Shot Fired", "Target Activated"
        description — e.g. "Trainee 1 fired LID3_P1"
        meta        — flat dict of key→value pairs from the trailing JSON blob
    """
    try:
        parsed = json.loads(raw_json)
    except Exception:
        return "", "", {}

    if not isinstance(parsed, list) or len(parsed) < 1:
        return "", "", {}

    event_type = parsed[0].get("value", "") if isinstance(parsed[0], dict) else str(parsed[0])
    description = parsed[1].get("value", "") if len(parsed) > 1 and isinstance(parsed[1], dict) else ""

    # Metadata is in the 4th element (index 3) as a nested JSON string
    meta: dict = {}
    if len(parsed) > 3 and isinstance(parsed[3], dict):
        raw_meta = parsed[3].get("value", "")
        try:
            pairs = json.loads(raw_meta)
            if isinstance(pairs, list):
                meta = {str(p[0]): str(p[1]) for p in pairs if len(p) >= 2}
        except Exception:
            pass

    return str(event_type), str(description), meta


# ── DB stream detector ────────────────────────────────────────────────────────

_VIRTRA_SENTINEL_EVENTS = {
    "Shot Fired",
    "Shot Hit",
    "Shot Miss",
    "Target Activated",
    "Target Deactivated",
    "Actor Event",
    "Actor IsVisible Changed",
    "Scenario Started",
    "Scenario Stopped",
}


def _detect_virtra_stream_id(cur: sqlite3.Cursor) -> Optional[int]:
    """
    Find the lsl_metadata_id of the VirTra event stream by scanning the first
    200 rows of each distinct stream for sentinel event types.
    """
    stream_ids = [r[0] for r in cur.execute(
        "SELECT DISTINCT lsl_metadata_id FROM lsl_unmapped_samples"
    ).fetchall()]

    for sid in stream_ids:
        rows = cur.execute(
            "SELECT sample_json FROM lsl_unmapped_samples "
            "WHERE lsl_metadata_id=? LIMIT 200", (sid,)
        ).fetchall()
        for (raw,) in rows:
            etype, _, _ = parse_virtra_event(raw)
            if etype in _VIRTRA_SENTINEL_EVENTS:
                return sid
    return None


# ── Main public helper ────────────────────────────────────────────────────────

def _read_virtra_events_table(
    cur: sqlite3.Cursor,
    event_filter: Optional[set[str]],
) -> list[tuple[float, str, str, dict]]:
    """
    Read from the virtra_events fallback table (populated by CSV import).
    Used when lsl_unmapped_samples is absent or empty.
    """
    tables = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "virtra_events" not in tables:
        return []

    rows = cur.execute(
        "SELECT unix_timestamp_seconds, event_type, description, meta_json "
        "FROM virtra_events ORDER BY unix_timestamp_seconds"
    ).fetchall()

    events: list[tuple[float, str, str, dict]] = []
    for ts, etype, desc, meta_str in rows:
        if not etype:
            continue
        meta: dict = {}
        if meta_str:
            try:
                pairs = json.loads(meta_str)
                if isinstance(pairs, list):
                    meta = {str(p[0]): str(p[1]) for p in pairs if len(p) >= 2}
            except Exception:
                pass
        if event_filter is None or etype in event_filter:
            events.append((float(ts), etype, desc, meta))
    return events


def get_all_virtra_events(
    db_path: str,
    event_filter: Optional[set[str]] = None,
) -> list[tuple[float, str, str, dict]]:
    """
    Return all VirTra events from a LabReplay .db file, sorted by timestamp.

    Primary source: lsl_unmapped_samples (streamed VirTra events).
    Fallback:       virtra_events table (imported from companion CSV).

    Args:
        db_path:      Absolute path to the SQLite database.
        event_filter: Optional set of event_type strings to keep. If None,
                      all events are returned.

    Returns:
        List of (unix_timestamp_s, event_type, description, meta_dict) tuples.
    """
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # ── Primary: lsl_unmapped_samples ────────────────────────────────────────
    cols = [c[1] for c in cur.execute(
        "PRAGMA table_info(lsl_unmapped_samples)"
    ).fetchall()]

    if cols:
        json_col = "sample_json" if "sample_json" in cols else (cols[6] if len(cols) > 6 else cols[-1])
        sid = _detect_virtra_stream_id(cur)
        if sid is not None:
            rows = cur.execute(
                f"SELECT unix_timestamp_seconds, {json_col} "
                f"FROM lsl_unmapped_samples "
                f"WHERE lsl_metadata_id=? "
                f"ORDER BY unix_timestamp_seconds",
                (sid,),
            ).fetchall()
            con.close()
            events: list[tuple[float, str, str, dict]] = []
            for ts, raw in rows:
                etype, desc, meta = parse_virtra_event(raw)
                if not etype:
                    continue
                if event_filter is None or etype in event_filter:
                    events.append((float(ts), etype, desc, meta))
            return events

    # ── Fallback: virtra_events table (CSV-imported) ─────────────────────────
    events = _read_virtra_events_table(cur, event_filter)
    con.close()
    return events
