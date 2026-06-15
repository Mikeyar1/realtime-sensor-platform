"""
signals/registry.py — Signal registry.

Single source of truth for all available physiological signals.
Each entry maps a short key to display metadata and its extractor function.

To add a new signal:
  1. Create signals/<name>.py with an extract(db_path, t_start, t_end) function.
  2. Add one entry to SIGNALS below.
"""

from .hr     import extract as _hr
from .pupil  import extract as _pupil
from .motion import extract as _motion

SIGNALS: dict[str, dict] = {
    "hr": {
        "fn":    _hr,
        "label": "Heart Rate",
        "unit":  "bpm",
        "color": "#C94444",
    },
    "pupil": {
        "fn":    _pupil,
        "label": "Pupil Diameter",
        "unit":  "mm",
        "color": "#7C6AE8",
    },
    "motion": {
        "fn":    _motion,
        "label": "Motion Intensity",
        "unit":  "[0\u20131]",
        "color": "#F39C12",
    },
}
