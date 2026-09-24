"""
config.py — Backend configuration loader.
"""

import tomllib
from pathlib import Path

HERE = Path(__file__).parent

with open(HERE / "config.toml", "rb") as f:
    cfg = tomllib.load(f)

HOST = cfg["server"]["host"]
PORT = cfg["server"]["port"]

LSL_DISCOVERY_INTERVAL = cfg.get("lsl", {}).get("discovery_interval_seconds", 1.0)
LSL_PULL_TIMEOUT       = cfg.get("lsl", {}).get("pull_timeout_seconds", 1.0)

ANALYSIS_DIR          = str((HERE / cfg.get("analysis", {}).get("analysis_directory", "../analysis")).resolve())
SESSION_DIR           = str((HERE / cfg.get("analysis", {}).get("session_directory", "../total-recall/sessions")).resolve())
POST_SCENARIO_DELAY_S = float(cfg.get("analysis", {}).get("post_scenario_delay_s", 3.0))

# Where live session JSON files are saved (one sub-folder per scenario run)
LIVE_SESSIONS_DIR = str((HERE / cfg.get("analysis", {}).get("live_sessions_directory", "../data/LiveSessions")).resolve())

# Base URL of the running Analysis FastAPI service
ANALYSIS_API_URL = cfg.get("analysis", {}).get("api_url", "http://127.0.0.1:8081")

# Where permanent session records (metadata + processed metrics) are written after operator saves.
SESSION_DATA_DIR = str((HERE / cfg.get("session_data", {}).get("session_data_directory", "../data/SessionData")).resolve())

# SQLite database file path
DATABASE_PATH = str((HERE / cfg.get("database", {}).get("path", "../database/labreview.db")).resolve())

# Embedded frontend HTTP server
FRONTEND_PORT = int(cfg.get("frontend", {}).get("port", 3000))
OPEN_BROWSER  = bool(cfg.get("frontend", {}).get("open_browser", True))

