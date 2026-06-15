"""
routers/sessions.py — GET /api/sessions

Lists all .db session files in the sessions directory.
Optionally filters by drill= query parameter.
"""

import re
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from config import SESSIONS_DIR
from drills import DRILL_MAP

router = APIRouter()

# Filename keywords that identify each drill
DRILL_PATTERNS: dict[str, re.Pattern] = {
    "behdisc":  re.compile(r"behdisc",           re.IGNORECASE),
    "pvt":      re.compile(r"_pvt_",             re.IGNORECASE),
    "l2gonogo": re.compile(r"l2gonogo|go_?no_?go", re.IGNORECASE),
}


def detect_drill(filename: str) -> Optional[str]:
    """Infer drill type from filename pattern. Returns None if unrecognised."""
    for drill, pat in DRILL_PATTERNS.items():
        if pat.search(filename):
            return drill
    return None


def session_meta(db_file: Path, drill: Optional[str]) -> dict:
    """Extract basic metadata from a .db file."""
    try:
        con = sqlite3.connect(db_file)
        cur = con.cursor()

        # Row count as proxy for data richness
        hr_rows = 0
        for tbl in ("polar_verity_sense_hr", "polar_h10_heart_rate"):
            try:
                hr_rows = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                if hr_rows:
                    break
            except Exception:
                pass

        # Time span
        t_start = t_end = None
        for tbl in ("polar_verity_sense_hr", "neon_gaze", "phone_sensor_linear_acceleration"):
            try:
                row = cur.execute(
                    f"SELECT MIN(unix_timestamp_seconds), MAX(unix_timestamp_seconds) FROM {tbl}"
                ).fetchone()
                if row and row[0]:
                    t_start, t_end = float(row[0]), float(row[1])
                    break
            except Exception:
                pass

        duration_s = round(t_end - t_start, 1) if (t_start and t_end) else None
        con.close()

        return {
            "filename":   db_file.name,
            "drill":      drill or detect_drill(db_file.name),
            "duration_s": duration_s,
            "hr_rows":    hr_rows,
        }
    except Exception as e:
        return {"filename": db_file.name, "drill": drill, "error": str(e)}


@router.get("/api/sessions")
def list_sessions(drill: Optional[str] = Query(None)):
    """
    List all .db session files. If drill= is provided, filter to sessions
    matching that drill by filename pattern.
    """
    if not SESSIONS_DIR.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Sessions directory not found: {SESSIONS_DIR}",
        )

    files = sorted(SESSIONS_DIR.glob("*.db"))
    results = []
    for f in files:
        detected = detect_drill(f.name)
        if drill and detected != drill:
            continue
        results.append(session_meta(f, detected))

    return results
