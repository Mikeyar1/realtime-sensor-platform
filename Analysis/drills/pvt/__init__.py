"""
drills/pvt/__init__.py — PVT drill package.

Re-exports the public API so that `from drills import pvt` works
identically to the old flat-file import.

Submodule layout:
  algorithm.py  — DB parsing + trial extraction (core logic)
  router.py     — FastAPI router: /api/pvt/* endpoints  (future)
"""

from .algorithm import (  # noqa: F401
    DRILL_KEY,
    TERMINOLOGY,
    extract_trials,
)

__all__ = ["DRILL_KEY", "TERMINOLOGY", "extract_trials"]
