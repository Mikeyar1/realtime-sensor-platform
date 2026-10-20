"""Compatibility import for the shared custom VirTra LSL decoder."""

from event_decoders.custom_lsl import CustomLslEvent, decode_custom_lsl_sample

__all__ = ["CustomLslEvent", "decode_custom_lsl_sample"]
