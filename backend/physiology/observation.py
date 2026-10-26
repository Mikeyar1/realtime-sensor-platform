"""Normalized physiological observation with explicit provenance and quality."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SignalObservation:
    signal: str
    source_key: str
    lsl_timestamp: float
    received_at: float
    value: float | None
    value_2: float | None = None
    unit: str = ""
    decoder_version: int = 1
