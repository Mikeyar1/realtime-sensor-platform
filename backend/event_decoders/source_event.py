"""Source-level parsing result shared by routing, audit, and replay.

Legacy decoded objects remain available to existing trackers during migration.
No scenario meaning or metric is inferred by this layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .custom_lsl import decode_custom_lsl_sample
from .native_virtra import decode_native_lsl_sample


@dataclass(frozen=True)
class SourceEvent:
    source: str
    status: str  # decoded, malformed, unrecognized
    parser_version: int
    event_name: str
    payload: dict[str, Any]
    decoded: Any = None
    error: str = ""


def decode_source_event(source: str | None, channels: list) -> SourceEvent:
    """Parse an event envelope without choosing a scenario or score."""
    if source == "custom":
        if not isinstance(channels, (list, tuple)) or len(channels) != 4:
            return SourceEvent("custom", "malformed", 1, "", {}, error="expected four channels")
        raw_payload = channels[3]
        try:
            payload = json.loads(raw_payload) if raw_payload else {}
        except (ValueError, TypeError):
            return SourceEvent("custom", "malformed", 1, str(channels[0]), {},
                               error="invalid custom JSON")
        if not isinstance(payload, dict):
            return SourceEvent("custom", "malformed", 1, str(channels[0]), {},
                               error="custom payload is not an object")
        decoded = decode_custom_lsl_sample(list(channels))
        if decoded is None:
            return SourceEvent("custom", "malformed", 1, str(channels[0]), payload,
                               error="invalid custom event fields")
        return SourceEvent("custom", "decoded", 1, decoded.event_name, payload, decoded)

    if source == "native":
        decoded = decode_native_lsl_sample(channels)
        if decoded is None:
            return SourceEvent("native", "malformed", 1, "", {},
                               error="missing native event name")
        if decoded.metadata_format == "unrecognized":
            return SourceEvent("native", "unrecognized", 1, decoded.event_name, {},
                               decoded, "unrecognized native metadata format")
        return SourceEvent("native", "decoded", 1, decoded.event_name,
                           dict(decoded.metadata), decoded)

    return SourceEvent("unknown", "unrecognized", 1,
                       str(channels[0]) if channels else "", {},
                       error="stream has no registered event decoder")
