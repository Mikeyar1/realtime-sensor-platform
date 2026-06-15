"""
signals/hrv.py — RMSSD and LF/HF ratio extractors.

Two separate extract functions:
  extract_rmssd(db_path, t_start, t_end) → (times, rmssd_values)
  extract_lfhf(db_path, t_start, t_end)  → (times, lfhf_values)

Both query HRV_Live_HRV which is computed in real-time by the Backend
and stored at ~1 sample/5s. RMSSD is used per-trial (PLI component).
LF/HF is used session-level only (Type D fatigue trend).

NOTE: LF/HF per-trial is NOT valid — requires ≥30s of PPI data for
reliable frequency-domain estimation. Use extract_rmssd() for per-trial
PLI computation.
"""

import sqlite3
import numpy as np


def _conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def extract_rmssd(db_path: str, t_start: float, t_end: float) -> tuple[np.ndarray, np.ndarray]:
    """
    RMSSD (ms) from HRV_Live_HRV table.
    Returns (times, rmssd_values).
    """
    con = _conn(db_path)
    cur = con.cursor()
    try:
        rows = cur.execute(
            "SELECT unix_timestamp_seconds, rmssd_ms FROM hrv_live_hrv "
            "WHERE unix_timestamp_seconds BETWEEN ? AND ? ORDER BY unix_timestamp_seconds",
            (t_start, t_end),
        ).fetchall()
        t = np.array([r[0] for r in rows], dtype=float)
        v = np.array([r[1] for r in rows], dtype=float)
    except Exception:
        t, v = np.array([]), np.array([])
    con.close()
    return t, v


def extract_lfhf(db_path: str, t_start: float, t_end: float) -> tuple[np.ndarray, np.ndarray]:
    """
    LF/HF ratio from HRV_Live_HRV table.
    Returns (times, lfhf_values).
    For session-level fatigue trend (Type D) only — not per-trial.
    """
    con = _conn(db_path)
    cur = con.cursor()
    try:
        rows = cur.execute(
            "SELECT unix_timestamp_seconds, lf_hf_ratio FROM hrv_live_hrv "
            "WHERE unix_timestamp_seconds BETWEEN ? AND ? ORDER BY unix_timestamp_seconds",
            (t_start, t_end),
        ).fetchall()
        t = np.array([r[0] for r in rows], dtype=float)
        v = np.array([r[1] for r in rows], dtype=float)
    except Exception:
        t, v = np.array([]), np.array([])
    con.close()
    return t, v
