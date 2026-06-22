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

SESSION_DIR = str((HERE / cfg.get("replay", {}).get("session_directory", ".")).resolve())
TOTAL_RECALL_DIR = str((HERE / cfg.get("replay", {}).get("total_recall_directory", "../total-recall")).resolve())
