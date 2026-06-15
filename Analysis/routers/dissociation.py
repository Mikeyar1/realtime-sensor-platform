"""
routers/dissociation.py — POST /api/dissociation/analyze

Runs the full dissociation analysis pipeline on a session.
The drill parameter determines which trial extractor is used — each drill
has its own event schema and extract_trials() implementation.

Pipeline:
  1. Look up drill module from DRILL_MAP using req.drill
  2. Extract trials using that drill's extract_trials()
  3. Compute session baseline (first baseline_s seconds of recording)
  4. Per-trial PLI + Type A/B/C classification
  5. Session-level Type D detection (LF/HF trajectory)
  6. Return sorted event reel + summary + drill metadata
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import SESSIONS_DIR
from drills import DRILL_MAP
from drills.dissociation.algorithm import analyze_session

router = APIRouter()


class AnalyzeRequest(BaseModel):
    session:              str   = Field(..., description="DB filename (e.g. 'session_001.db')")
    drill:                str   = Field("behdisc", description="Drill key: behdisc | pvt | gonogo | ...")
    baseline_s:           float = Field(60.0, ge=10.0,  le=300.0, description="Pre-session baseline duration (s)")
    analysis_s:           float = Field(2.0,  ge=0.5,   le=10.0,  description="Post-event analysis window (s)")
    lfhf_slope_threshold: float = Field(0.3,  ge=0.05,  le=2.0,   description="LF/HF slope threshold for Type D")


@router.post("/api/dissociation/analyze")
def analyze(req: AnalyzeRequest):
    """
    Run dissociation analysis on a session using the specified drill's
    trial extraction logic.

    The drill key determines how VirTra/task events are parsed into trials:
      - behdisc: BehDisc actor visibility events
      - pvt:     PVT TARGET_APPEAR / DRILL_END events
      - gonogo:  Go/No-Go stimulus events (when implemented)

    Returns classified event reel sorted by dissociation score (descending),
    session baseline stats, and summary counts per dissociation type.
    """
    # Validate drill
    if req.drill not in DRILL_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown drill '{req.drill}'. Available: {sorted(DRILL_MAP.keys())}"
        )

    # Validate session file
    db = SESSIONS_DIR / req.session
    if not db.exists():
        raise HTTPException(status_code=404, detail=f"Session not found: {req.session}")

    drill_module = DRILL_MAP[req.drill]

    try:
        result = analyze_session(
            db_path               = str(db),
            drill_module          = drill_module,
            baseline_s            = req.baseline_s,
            analysis_s            = req.analysis_s,
            lfhf_slope_threshold  = req.lfhf_slope_threshold,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Analysis failed: {e}")

    return result
