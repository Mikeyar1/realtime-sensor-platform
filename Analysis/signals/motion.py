"""
signals/motion.py — Motion Intensity signal extractor.

Fuses linear acceleration + gyroscope into a [0–1] intensity score.
Formula (matches Frontend chart-motion.js):
    intensity = ACC_WEIGHT*(acc_mag/ACC_MAX) + GYRO_WEIGHT*(gyro_mag/GYRO_MAX)

Returns (times, intensity_values) as numpy arrays.
"""

import math
import sqlite3
import numpy as np


# ── Constants (must stay in sync with Frontend/js/charts/chart-motion.js) ─────
ACC_WEIGHT  = 0.4
GYRO_WEIGHT = 0.6
ACC_MAX     = 9.0   # m/s² ceiling for normalization
GYRO_MAX    = 3.0   # rad/s ceiling


def _conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def extract(db_path: str, t_start: float, t_end: float) -> tuple[np.ndarray, np.ndarray]:
    """Motion Intensity [0–1]: fuses linear acceleration + gyroscope."""
    con = _conn(db_path)
    cur = con.cursor()

    # Build acc lookup: ts → (x, y, z)
    acc: dict[float, tuple] = {}
    try:
        rows = cur.execute(
            "SELECT unix_timestamp_seconds, x_meters_per_sec_squared, "
            "y_meters_per_sec_squared, z_meters_per_sec_squared "
            "FROM phone_sensor_linear_acceleration "
            "WHERE unix_timestamp_seconds BETWEEN ? AND ? ORDER BY unix_timestamp_seconds",
            (t_start, t_end),
        ).fetchall()
        for r in rows:
            acc[r[0]] = (r[1], r[2], r[3])
    except Exception:
        pass

    # Build gyro lookup: ts → (x, y, z)
    gyro: dict[float, tuple] = {}
    try:
        rows = cur.execute(
            "SELECT unix_timestamp_seconds, x_radians_per_sec, "
            "y_radians_per_sec, z_radians_per_sec "
            "FROM phone_sensor_gyroscope "
            "WHERE unix_timestamp_seconds BETWEEN ? AND ? ORDER BY unix_timestamp_seconds",
            (t_start, t_end),
        ).fetchall()
        for r in rows:
            gyro[r[0]] = (r[1], r[2], r[3])
    except Exception:
        pass

    con.close()

    if not acc and not gyro:
        return np.array([]), np.array([])

    # Merge on acc time axis; gyro matched by nearest timestamp within 100 ms
    gyro_ts = sorted(gyro.keys())
    t_out, v_out = [], []

    for ts, (ax, ay, az) in sorted(acc.items()):
        acc_mag = math.sqrt(ax**2 + ay**2 + az**2)
        gx = gy = gz = 0.0
        if gyro_ts:
            nearest = min(gyro_ts, key=lambda g: abs(g - ts))
            if abs(nearest - ts) < 0.1:
                gx, gy, gz = gyro[nearest]
        gyro_mag = math.sqrt(gx**2 + gy**2 + gz**2)

        intensity = (
            ACC_WEIGHT  * min(1.0, acc_mag  / ACC_MAX) +
            GYRO_WEIGHT * min(1.0, gyro_mag / GYRO_MAX)
        )
        t_out.append(ts)
        v_out.append(intensity)

    return np.array(t_out, dtype=float), np.array(v_out, dtype=float)
