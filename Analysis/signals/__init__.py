"""
signals/__init__.py — Physiological signal extraction package.

Public surface (backward-compatible with the old flat signals.py):
  SIGNALS         — dict of signal metadata
  extract_epochs  — main windowing entry point
  grand_average   — stacking + CI helper
  _clean          — JSON nan/inf sanitizer
  _scalar         — single-value JSON sanitizer
"""

from .registry import SIGNALS                        # noqa: F401
from .extractor import extract_epochs, grand_average  # noqa: F401
from .extractor import _clean, _scalar               # noqa: F401
