"""Parse native VirTra LSL envelopes without assigning scenario meaning.

V_300 recordings use several event vocabularies and metadata key sets. The
decoder identifies the wire representation and preserves unknown fields;
scenario trackers decide whether a target, actor, or shot is scorable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class NativeVirtraEvent:
    event_name: str
    description: str = ""
    category: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    metadata_raw: str = ""
    metadata_format: str = "empty"
    raw_channels: tuple = ()


def parse_native_metadata(raw: str) -> tuple[dict[str, Any], str]:
    """Decode known metadata representations, retaining unknown raw text."""
    if not raw:
        return {}, "empty"
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}, "unrecognized"
    if isinstance(payload, dict):
        return payload, "object_v1"
    if isinstance(payload, list) and all(
            isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)
            for pair in payload):
        return {key: value for key, value in payload}, "pairs_v1"
    return {}, "unrecognized"


def decode_native_lsl_sample(sample: list) -> NativeVirtraEvent | None:
    """Parse the native event envelope; do not infer task or trial semantics."""
    if not sample:
        return None
    event_name = str(sample[0]).strip()
    if not event_name:
        return None
    raw = str(sample[3]).strip() if len(sample) > 3 and sample[3] is not None else ""
    metadata, metadata_format = parse_native_metadata(raw)
    return NativeVirtraEvent(
        event_name=event_name,
        description=str(sample[1]).strip() if len(sample) > 1 else "",
        category=str(sample[2]).strip() if len(sample) > 2 else "",
        metadata=metadata,
        metadata_raw=raw,
        metadata_format=metadata_format,
        raw_channels=tuple(sample),
    )
