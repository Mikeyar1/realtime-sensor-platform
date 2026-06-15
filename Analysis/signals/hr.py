"""
signals/hr.py — Heart Rate signal extractor.

Queries polar_verity_sense_hr (primary) or polar_h10_heart_rate (fallback).
Returns (times, bpm_values) as numpy arrays.
"""

import sqlite3
import numpy as np


def _conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def extract(db_path: str, t_start: float, t_end: float) -> tuple[np.ndarray, np.ndarray]:
    """Heart Rate: returns (times, bpm_values)."""
    con = _conn(db_path)
    cur = con.cursor()

    # Try both column names used across device firmware versions
    for col in ("heart_rate_bpm", "beats_per_minute"):
        try:
            rows = cur.execute(
                f"SELECT unix_timestamp_seconds, {col} FROM polar_verity_sense_hr "
                f"WHERE unix_timestamp_seconds BETWEEN ? AND ? "
                f"ORDER BY unix_timestamp_seconds",
                (t_start, t_end),
            ).fetchall()
            if rows:
                t = np.array([r[0] for r in rows], dtype=float)
                v = np.array([r[1] for r in rows], dtype=float)
                con.close()
                return t, v
        except Exception:
            pass

    # Fallback: Polar H10
    try:
        rows = cur.execute(
            "SELECT unix_timestamp_seconds, beats_per_minute FROM polar_h10_heart_rate "
            "WHERE unix_timestamp_seconds BETWEEN ? AND ? ORDER BY unix_timestamp_seconds",
            (t_start, t_end),
        ).fetchall()
        t = np.array([r[0] for r in rows], dtype=float)
        v = np.array([r[1] for r in rows], dtype=float)
    except Exception:
        t, v = np.array([]), np.array([])

    con.close()
    return t, v
