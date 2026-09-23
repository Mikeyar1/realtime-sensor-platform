"""
session_data_writer.py — Permanent session record writer.

Assembles a single session_record.json from:
  - Session metadata captured during the scenario (drill, timing, active streams)
  - Computed metrics returned by the Analysis service
  - Operator-supplied participant information (participant_id, participant_name, notes)

Called exactly once per session when the operator clicks "Save" on the Frontend.
The resulting file is the sole permanent artifact for longitudinal comparison.

Directory structure:
  <session_data_dir>/<drill_key>_<participant_id>_<YYYY-MM-DD_HH-MM-SS>/session_record.json
"""

import datetime
import json
import os

SCHEMA_VERSION = 1


def write_session_record(
    session_metadata: dict,
    computed_metrics: dict | None,
    operator_input: dict,
    session_data_dir: str,
) -> str | None:
    """
    Assemble and write session_record.json to disk.

    Args:
        session_metadata:  Captured by VirTraScenarioOrchestrator (drill_key, timing, streams, etc.)
        computed_metrics:  Dict returned from Analysis service, or None if Analysis failed.
        operator_input:    Fields from the Frontend save form: participant_id, participant_name, notes.
        session_data_dir:  Root directory for all session records (from config.SESSION_DATA_DIR).

    Returns:
        Absolute path of the written file, or None if write failed.
    """
    participant_id   = str(operator_input.get("participant_id",   "")).strip()
    participant_name = str(operator_input.get("participant_name", "")).strip()
    notes            = str(operator_input.get("notes", "")).strip()

    drill_key = session_metadata.get("drill_key", "session")
    ts_str    = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    # Build a clean folder name — use participant_id if provided, else just timestamp
    pid_slug    = participant_id.replace(" ", "_") or "unknown"
    folder_name = f"{drill_key}_{pid_slug}_{ts_str}"
    folder_path = os.path.join(session_data_dir, folder_name)

    record = {
        "schema_version": SCHEMA_VERSION,
        "metadata": {
            "participant_id":   participant_id,
            "participant_name": participant_name,
            "notes":            notes,
            # Scenario / session context
            "date":             ts_str[:10],  # YYYY-MM-DD
            "started_at":       session_metadata.get("started_at"),
            "stopped_at":       session_metadata.get("stopped_at"),
            "duration_s":       session_metadata.get("duration_s"),
            "drill_key":        drill_key,
            "task_type":        session_metadata.get("task_type", ""),
            "scenario_name":    session_metadata.get("scenario_name", ""),
            "session_id":       session_metadata.get("session_id", ""),
            "active_streams":   session_metadata.get("active_streams", []),
        },
        "metrics": computed_metrics or {},
    }

    try:
        os.makedirs(folder_path, exist_ok=True)
        file_path = os.path.join(folder_path, "session_record.json")
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        print(f"[SessionDataWriter] OK session_record.json -> {file_path}")
        return file_path
    except OSError as e:
        print(f"[SessionDataWriter] x Could not write session_record.json: {e}")
        return None
