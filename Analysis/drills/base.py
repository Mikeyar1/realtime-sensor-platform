"""
drills/base.py — Abstract base class for all drill plugins.

Every drill package must expose:
  DRILL_KEY    str            — machine key, e.g. "behdisc"
  TERMINOLOGY  dict[str, str] — human labels for UI display
  extract_trials(db_path) -> list[dict]  — parse DB → trial list

To register a new drill:
  1. Create drills/<name>/__init__.py (or <name>.py) implementing this interface.
  2. Add it to DRILL_MAP in drills/__init__.py.
"""

from abc import ABC, abstractmethod


class DrillBase(ABC):
    """
    Interface contract for drill modules.

    Note: drills are currently implemented as plain modules (not classes)
    for simplicity. This ABC serves as documentation of the required
    module-level attributes and functions. A future refactor may convert
    them to class instances registered in DRILL_MAP.
    """

    @property
    @abstractmethod
    def DRILL_KEY(self) -> str:
        """Short machine identifier, e.g. 'behdisc'."""
        ...

    @property
    @abstractmethod
    def TERMINOLOGY(self) -> dict:
        """
        Human-readable labels for UI display. Required keys:
          drill_name, drill_short, anchor_label, hit_label,
          miss_label, fa_label, rt_label, trial_label, event_desc
        """
        ...

    @abstractmethod
    def extract_trials(self, db_path: str) -> list[dict]:
        """
        Parse a .db recording and return all detected trials.

        Each trial dict must contain at minimum:
          index   (int)       — 1-based trial number
          t0      (float)     — anchor Unix timestamp
          outcome (str)       — HIT | MISS | COMMISSION_ERROR | CORRECT_WITHHOLD | ...
          rt_s    (float|None)— reaction time in seconds, or None
        """
        ...
