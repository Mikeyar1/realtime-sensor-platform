"""
config_mgr.py — Load Total Recall configuration from a TOML file.

Exposes:
  config       dict  — loaded from config.toml in this directory by default
  load_config(path)  — load from an explicit path (used by external callers)
"""

import os
import tomllib

_DEFAULT_CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.toml")


def load_config(path: str = _DEFAULT_CONFIG) -> dict:
    """Load and return the TOML config from the given path."""
    with open(path, "rb") as f:
        return tomllib.load(f)


# Module-level dict — populated on import from the local config.toml.
# External callers (e.g., replay_engine) may override this by calling
# load_config() with their own path and injecting the result.
config: dict = load_config()
