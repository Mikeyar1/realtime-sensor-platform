"""
custom_lsl.py — Decodes raw custom VirTra LSL samples.

The custom stream ("virtra_lsl", type "VirTra Events") sends 4 string
channels per sample:
    0: event_name          e.g. "Target Appeared"
    1: event_description   human-readable text
    2: event_code          short code, e.g. "TA"
    3: event_fields_json    JSON payload (schema_version, scenario_name,
                            scenario_id, drill_type, round, state,
                            scenario_time_seconds, timestamp_utc, data)

decode_custom_lsl_sample() turns one raw sample into a CustomLslEvent so
downstream code never touches raw channels or JSON parsing directly.
"""

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CustomLslEvent:
    """One parsed sample from the custom VirTra LSL stream."""
    event_name:             str
    event_description:      str   = ""
    event_code:              str   = ""
    schema_version:          str   = ""
    reason:                  str   = ""
    scenario_name:           str   = ""
    scenario_id:             str   = ""
    scenario_display_name:   str   = ""
    drill_type:              str   = ""
    phase:                   str   = ""   # component/phase, when supplied by the drill
    round:                   int   = 0
    state:                   str   = ""
    scenario_time_s:         float = 0.0
    timestamp_utc:           str   = ""
    data:                    dict  = field(default_factory=dict)


def decode_custom_lsl_sample(sample: list) -> Optional[CustomLslEvent]:
    """Parse one raw 4-channel LSL sample into a CustomLslEvent, or None if malformed."""
    if len(sample) < 4:
        return None

    event_name = str(sample[0]).strip()
    if not event_name:
        return None

    event_description = str(sample[1]).strip() if len(sample) > 1 else ""
    event_code        = str(sample[2]).strip() if len(sample) > 2 else ""
    raw_payload       = str(sample[3]).strip() if len(sample) > 3 else ""

    payload: dict = {}
    if raw_payload:
        try:
            payload = json.loads(raw_payload)
        except (json.JSONDecodeError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        return None
    event_data = payload.get("data")
    if not isinstance(event_data, dict):
        event_data = {}
    raw_round = payload.get("round", 0)
    try:
        if isinstance(raw_round, bool):
            return None
        round_number = int(raw_round or 0)
        if round_number < 0:
            return None
        scenario_time = float(payload.get("scenario_time_seconds") or 0)
    except (ValueError, TypeError):
        return None

    return CustomLslEvent(
        event_name=event_name,
        event_description=event_description,
        event_code=event_code,
        schema_version=payload.get("schema_version", ""),
        reason=payload.get("reason", ""),
        scenario_name=payload.get("scenario_name", ""),
        scenario_id=payload.get("scenario_id", ""),
        scenario_display_name=payload.get("scenario_display_name", ""),
        drill_type=payload.get("drill_type", ""),
        phase=str(payload.get("phase") or event_data.get("phase") or ""),
        round=round_number,
        state=payload.get("state", ""),
        scenario_time_s=scenario_time,
        timestamp_utc=payload.get("timestamp_utc", ""),
        data=event_data,
    )
