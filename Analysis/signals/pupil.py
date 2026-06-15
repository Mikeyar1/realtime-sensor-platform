"""
signals/pupil.py — Pupil Diameter signal extractor.

Queries neon_gaze for left/right pupil diameter, returns binocular average.
Returns (times, mm_values) as numpy arrays.
"""

import sqlite3
import numpy as np


def _conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def extract(db_path: str, t_start: float, t_end: float) -> tuple[np.ndarray, np.ndarray]:
    """Average pupil diameter in mm: returns (times, mm_values)."""
    con = _conn(db_path)
    cur = con.cursor()
    try:
        rows = cur.execute(
            "SELECT unix_timestamp_seconds, pupil_diameter_left_millimeters, "
            "pupil_diameter_right_millimeters FROM neon_gaze "
            "WHERE unix_timestamp_seconds BETWEEN ? AND ? ORDER BY unix_timestamp_seconds",
            (t_start, t_end),
        ).fetchall()
        t, v = [], []
        for r in rows:
            lv, rv = r[1], r[2]
            if lv is not None and rv is not None and lv > 0 and rv > 0:
                t.append(r[0])
                v.append((lv + rv) / 2)
            elif lv is not None and lv > 0:
                t.append(r[0]); v.append(lv)
            elif rv is not None and rv > 0:
                t.append(r[0]); v.append(rv)
    except Exception:
        t, v = [], []
    con.close()
    return np.array(t, dtype=float), np.array(v, dtype=float)
