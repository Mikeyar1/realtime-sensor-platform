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

# SQLite database file path
DATABASE_PATH = str((HERE / cfg.get("database", {}).get("path", "../database/labreview.db")).resolve())

# Embedded frontend HTTP server
FRONTEND_PORT = int(cfg.get("frontend", {}).get("port", 3000))
OPEN_BROWSER  = bool(cfg.get("frontend", {}).get("open_browser", True))

