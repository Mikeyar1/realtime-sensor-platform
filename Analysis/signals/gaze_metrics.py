"""
signals/gaze_metrics.py — Per-trial gaze metrics (Pupil Labs Neon).

Computes two scalar gaze metrics per trial, added directly to each trial dict:

  gaze_dispersion_px
    RMS spatial spread of gaze position in the 500 ms window after target onset.
    Source : neon_gaze.scene_x_pixels / scene_y_pixels  (~188 Hz continuous)
    Formula: sqrt( std(x)² + std(y)² )
    Interpretation: low = stable, locked-on gaze; high = searching / scattered

  ttff_ms
    Time to First Fixation — delay from target onset to the first stable fixation
    event logged by the Neon middleware.
    Source : neon_middleware_eye_events (event_type ∈ {0, 1} with valid gaze_x)
    Formula: first_fixation_unix_ts − t0  (milliseconds)
    Interpretation: low = eyes were already in position; high = needed to search

Public API
----------
  enrich_trials(db_path, trials) → None
      Modifies each trial dict in-place.  Safe to call even when the Neon
      tables are absent — fields default to None in that case.
"""

import math
import sqlite3
import statistics

# Fixed acquisition window for gaze dispersion (seconds after t0)
_GAZE_WINDOW_S   = 0.5
# Minimum gaze samples required to compute dispersion
_MIN_SAMPLES     = 3
# Fallback maximum window for TTFF when rt_s is None (lapses)
_TTFF_MAX_S      = 3.0


def enrich_trials(db_path: str, trials: list[dict]) -> None:
    """
    Add gaze_dispersion_px and ttff_ms fields to every trial dict.
    Called by both the /api/trials and /api/epoch routers so the
    same enriched data is available in the trial table and epoch response.
    """
    if not trials:
        return
    try:
        _enrich(db_path, trials)
    except Exception:
        # If anything goes wrong, just default to None for all trials
        for t in trials:
            t.setdefault("gaze_dispersion_px", None)
            t.setdefault("ttff_ms", None)


# ── Internal implementation ───────────────────────────────────────────────────

def _enrich(db_path: str, trials: list[dict]) -> None:
    con = sqlite3.connect(db_path)
    cur = con.cursor()

    for t in trials:
        t0 = float(t["t0"])

        # ── Gaze Dispersion ────────────────────────────────────────────────
        try:
            rows = cur.execute(
                """
                SELECT scene_x_pixels, scene_y_pixels
                FROM   neon_gaze
                WHERE  unix_timestamp_seconds BETWEEN ? AND ?
                  AND  scene_x_pixels > 0 AND scene_y_pixels > 0
                """,
                (t0, t0 + _GAZE_WINDOW_S),
            ).fetchall()

            if len(rows) >= _MIN_SAMPLES:
                xs    = [r[0] for r in rows]
                ys    = [r[1] for r in rows]
                std_x = statistics.stdev(xs)
                std_y = statistics.stdev(ys)
                t["gaze_dispersion_px"] = round(math.sqrt(std_x**2 + std_y**2), 1)
            else:
                t["gaze_dispersion_px"] = None

        except Exception:
            t["gaze_dispersion_px"] = None

        # ── Time to First Fixation ─────────────────────────────────────────
        try:
            rt_s  = t.get("rt_s") or _TTFF_MAX_S
            t_max = t0 + float(rt_s)

            row = cur.execute(
                """
                SELECT unix_timestamp_seconds
                FROM   neon_middleware_eye_events
                WHERE  unix_timestamp_seconds >= ?
                  AND  unix_timestamp_seconds <= ?
                  AND  event_type_event_enum IN (0, 1)
                  AND  mean_gaze_x_gaze_pixels > 0
                ORDER  BY unix_timestamp_seconds ASC
                LIMIT  1
                """,
                (t0, t_max),
            ).fetchone()

            t["ttff_ms"] = round((row[0] - t0) * 1000) if row else None

        except Exception:
            t["ttff_ms"] = None

    con.close()
