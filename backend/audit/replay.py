"""Replay stored scenario samples in ingestion order without live LSL outlets."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from event_decoders.source_event import decode_source_event


def replay_capture(event_store, capture_id: str, on_sample, *, batch_size: int = 1000) -> dict:
    """Send original channels/timestamps to a caller and compare parser outputs.

    Pass an isolated orchestrator and temporary database when replaying for score
    comparison. This function never writes back into the source capture.
    """
    batch_size = max(1, min(int(batch_size), 1000))
    sequence = 0
    count = 0
    differences = []
    while True:
        rows = event_store.get_capture_events(capture_id, limit=batch_size,
                                              after_sequence=sequence)
        if not rows:
            break
        for row in rows:
            sequence = row["ingest_sequence"]
            channels = json.loads(row["raw_channels_json"])
            decoded = decode_source_event(row["source_kind"], channels)
            saved = json.loads(row["decoded_event_json"])
            if (decoded.parser_version != row["decoder_version"] or
                    decoded.status != row["decode_status"] or
                    decoded.event_name != saved.get("event_name") or
                    decoded.payload != saved.get("payload")):
                differences.append(row["id"])
            # Live ingestion never routes malformed source events to scoring.
            # Some legacy decoders accept their envelopes permissively, so
            # replay must preserve the audited routing decision.
            if decoded.status == "decoded":
                on_sample(row["stream_name"], row["stream_type"],
                          row["lsl_timestamp"], channels)
            count += 1
        if len(rows) < batch_size:
            break
    return {"capture_id": capture_id, "replayed": count,
            "decoder_mismatches": differences}


def compare_round_metrics(original_db: str, replay_db: str,
                          original_run_id: str, replay_run_id: str) -> dict:
    """Compare saved round metric snapshots by round number and attempt."""
    def snapshots(db_path, run_id):
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            rows = conn.execute("""SELECT round_number, attempt, metrics_json
                FROM assessment_rounds WHERE run_id = ?
                ORDER BY round_number, attempt""", (run_id,)).fetchall()
        return {(number, attempt): json.loads(metrics)
                for number, attempt, metrics in rows}

    original = snapshots(original_db, original_run_id)
    replayed = snapshots(replay_db, replay_run_id)
    keys = sorted(set(original) | set(replayed))
    return {"matching": original == replayed,
            "different_rounds": [key for key in keys if original.get(key) != replayed.get(key)]}
