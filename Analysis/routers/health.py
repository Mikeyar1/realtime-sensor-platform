"""
routers/health.py — GET /api/health
"""

from fastapi import APIRouter
from config import SESSIONS_DIR

router = APIRouter()


@router.get("/api/health")
def health():
    return {"status": "ok", "sessions_dir": str(SESSIONS_DIR)}
