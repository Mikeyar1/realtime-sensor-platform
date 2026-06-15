"""
drills/__init__.py — Drill registry.

DRILL_MAP is the single source of truth for all registered drills.
Keys are the machine identifiers used in API query params (e.g. ?drill=behdisc).

To add a new drill:
  1. Create drills/<name>/__init__.py implementing DRILL_KEY, TERMINOLOGY, extract_trials.
  2. Import it below and add one entry to DRILL_MAP.
"""

from . import behdisc
from . import pvt

DRILL_MAP: dict[str, object] = {
    behdisc.DRILL_KEY: behdisc,
    pvt.DRILL_KEY:     pvt,
    # "l2gonogo": l2gonogo,  ← add here when ready
}

__all__ = ["DRILL_MAP", "behdisc", "pvt"]
