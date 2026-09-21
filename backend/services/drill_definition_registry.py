"""
drill_definition_registry.py — Cognitive attribute configuration loader and scenario resolver.

Loads config/drill_registry.toml and provides:
  - resolve_drill(scenario_name) -> dict  (includes params, targets, metric_module)
  - get_tracked_events(drill_key) -> list[str]
  - get_all_drill_keys() -> list[str]
  - get_general_events() -> list[str]
"""

import os
import tomllib
from typing import Optional

# Path to the registry file — relative to the project root
_SERVICES_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR  = os.path.dirname(_SERVICES_DIR)
_REPO_ROOT    = os.path.dirname(_BACKEND_DIR)

# Support both 'Config' and 'config' directory casings
_CANDIDATE_PATHS = [
    os.path.join(_REPO_ROOT, "config", "drill_registry.toml"),
    os.path.join(_REPO_ROOT, "Config", "drill_registry.toml"),  # legacy casing fallback
]
_TOML_PATH = next((p for p in _CANDIDATE_PATHS if os.path.exists(p)), _CANDIDATE_PATHS[0])


def _load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


class DrillDefinitionRegistry:
    """
    Loads drill_registry.toml and builds a lookup table:
        scenario_name (lowercase stripped) → cognitive attribute metadata dict

    The returned drill metadata dict contains everything the orchestrator needs:
        drill_key, label, task_type, metric_module, params, targets, ...
    """

    def __init__(self, toml_path: str = _TOML_PATH):
        self._path = toml_path
        self._data: dict = {}
        self._scenario_map: dict[str, dict] = {}   # scenario_name_lower → drill dict
        self._custom_lsl_map: dict[str, dict] = {}  # drill_type_lower → drill dict
        self._general_events: list[str] = []
        self._load()

    def _load(self):
        if not os.path.exists(self._path):
            print(f"[DrillDefinitionRegistry] Warning: registry file not found at {self._path}")
            return

        self._data = _load_toml(self._path)

        # General events — captured for every session regardless of cognitive attribute
        self._general_events = self._data.get("general", {}).get("events", [])

        # Build scenario → cognitive attribute lookup
        attributes: dict = self._data.get("cognitive_attributes", {})
        for drill_key, attr_cfg in attributes.items():
            # Per-attribute defaults
            drill_meta = {
                "drill_key":         drill_key,
                "label":             attr_cfg.get("label", drill_key),
                "description":       attr_cfg.get("description", ""),
                "task_type":         attr_cfg.get("task_type", "GENERAL"),
                "metric_module":     attr_cfg.get("metric_module", ""),
                "targets":           attr_cfg.get("targets", {}),
                "params":            attr_cfg.get("params", {}),
                "tracked_events":    attr_cfg.get("tracked_events", []),
                "lane_start_events": attr_cfg.get("lane_start_events", []),
                "lane_stop_events":  attr_cfg.get("lane_stop_events", []),
                # Per-scenario param overrides — keyed by exact scenario name
                "scenario_params":   attr_cfg.get("scenario_params", {}),
            }

            for scenario_name in attr_cfg.get("scenarios", []):
                self._scenario_map[scenario_name.lower().strip()] = {
                    **drill_meta,
                    "scenario_name": scenario_name,
                }

        # Custom, script-driven VirTra LSL drills — resolved by the stream's
        # own drill_type field instead of by native VirTra scenario name.
        custom_drills: dict = self._data.get("custom_lsl_drills", {})
        for drill_type, custom_cfg in custom_drills.items():
            self._custom_lsl_map[drill_type.lower().strip()] = {
                "drill_key":       custom_cfg.get("drill_key", drill_type),
                "label":           custom_cfg.get("label", drill_type),
                "description":     custom_cfg.get("description", ""),
                "task_type":       custom_cfg.get("task_type", "GENERAL"),
                "metric_module":   custom_cfg.get("metric_module", ""),
                "targets":         custom_cfg.get("targets", {}),
                "params":          custom_cfg.get("params", {}),
                "custom_lsl_type": drill_type,
            }

        mapped = len(self._scenario_map)
        print(
            f"[DrillDefinitionRegistry] Loaded {len(attributes)} cognitive attribute "
            f"categories, {mapped} scenario mappings, {len(custom_drills)} custom LSL mappings | {self._path}"
        )

    def resolve_drill(self, scenario_name: str) -> Optional[dict]:
        """
        Look up a scenario name and return its cognitive attribute metadata dict.

        The returned dict includes params merged with any per-scenario overrides,
        so the orchestrator can pass them directly to tracker.reset(params=...).

        Returns None if the scenario is not registered — callers must check for None
        and skip processing. Unknown scenarios are NOT silently promoted to a catch-all.
        """
        key    = scenario_name.lower().strip()
        result = self._scenario_map.get(key)
        if result is None:
            print(
                f"[DrillDefinitionRegistry] Unrecognized scenario: {scenario_name!r} "
                f"— ignoring (not registered in drill_registry.toml)"
            )
            return None

        # Merge attribute-level params with any per-scenario overrides
        base_params     = dict(result.get("params", {}))
        scenario_params = result.get("scenario_params", {}).get(scenario_name, {})
        merged_params   = {**base_params, **scenario_params}

        return {**result, "params": merged_params}

    def resolve_custom_lsl_drill(self, drill_type: str) -> Optional[dict]:
        """
        Resolve a custom VirTra LSL drill from its payload drill_type field.
        Returns None if the custom drill is not configured.
        """
        key = (drill_type or "").lower().strip()
        result = self._custom_lsl_map.get(key)
        if result is None:
            print(
                f"[DrillDefinitionRegistry] Unrecognized custom LSL drill_type: {drill_type!r} "
                f"— ignoring (not registered in drill_registry.toml)"
            )
            return None
        return dict(result)

    def get_tracked_events(self, drill_key: str) -> list[str]:
        """Return the tracked_events list for a given drill key."""
        attrs = self._data.get("cognitive_attributes", {})
        return attrs.get(drill_key, {}).get("tracked_events", [])

    def get_general_events(self) -> list[str]:
        """Return events always captured for all cognitive attributes."""
        return list(self._general_events)

    def get_all_drill_keys(self) -> list[str]:
        """Return all registered (non-calibration) drill keys."""
        return list(self._data.get("cognitive_attributes", {}).keys())

    def get_drill_info(self, drill_key: str) -> Optional[dict]:
        """Return full config for a drill key, or None if not found."""
        attrs = self._data.get("cognitive_attributes", {})
        cfg   = attrs.get(drill_key)
        if cfg is None:
            return None
        return {"drill_key": drill_key, **cfg}

    def all_tracked_events_for(self, drill_key: str) -> list[str]:
        """Return general_events + drill-specific tracked_events combined."""
        drill_events = self.get_tracked_events(drill_key)
        combined     = list(self._general_events)
        for ev in drill_events:
            if ev not in combined:
                combined.append(ev)
        return combined

    def reload(self):
        """Hot-reload the TOML file without restarting."""
        self._scenario_map.clear()
        self._custom_lsl_map.clear()
        self._load()

