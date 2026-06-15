"""
drills/dissociation/__init__.py — Dissociation drill package.

Exposes DRILL_KEY and TERMINOLOGY for DRILL_MAP registration.
Trial extraction is delegated to the behdisc algorithm (same events),
with PLI and dissociation classification added on top.
"""

from .algorithm import extract_trials, DRILL_KEY, TERMINOLOGY

__all__ = ["extract_trials", "DRILL_KEY", "TERMINOLOGY"]
