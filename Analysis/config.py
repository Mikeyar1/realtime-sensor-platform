"""
config.py — Analysis server configuration.

Single place for all path and environment settings.
All other modules import from here — no path strings duplicated elsewhere.
"""

from pathlib import Path

# Root of the LabReplay monorepo (two levels up from this file)
_REPO_ROOT = Path(__file__).parent.parent

# Session DB files — must match Backend/config.toml [replay] db_scan_directory
SESSIONS_DIR: Path = _REPO_ROOT / "total-recall" / "sessions"
