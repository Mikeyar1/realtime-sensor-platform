"""
drills/behdisc/__init__.py — BehDisc drill package.

Re-exports the public API so that `from drills import behdisc` works
identically to the old flat-file import.

Submodule layout:
  algorithm.py  — DB parsing + trial extraction (core logic)
  router.py     — FastAPI router: /api/behdisc/* endpoints
"""

from .algorithm import (  # noqa: F401 — re-exported public surface
    DRILL_KEY,
    TERMINOLOGY,
    extract_trials,
)

__all__ = ["DRILL_KEY", "TERMINOLOGY", "extract_trials"]
