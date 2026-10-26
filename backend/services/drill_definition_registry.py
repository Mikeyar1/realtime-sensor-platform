"""Resolve native scenario names and custom drill types from scenario contracts.

Trial parameters and target filters belong to each scenario's source TOML.
The shared-event file only supplies the native event allowlist shown to clients.
"""

from __future__ import annotations

import os
import sys
import tomllib
from typing import Optional

from trial_models.registry import TRIAL_ADAPTERS
from cognitive_skills.catalog import selected_for_skills
from .virtra_stream_config import VirtraStreamConfig


if getattr(sys, "frozen", False):
    _TOML_PATH = os.path.join(sys._MEIPASS, "config", "drill_registry.toml")
else:
    _TOML_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "config", "drill_registry.toml")


class DrillDefinitionRegistry:
    def __init__(self, toml_path: str = _TOML_PATH):
        self._path = toml_path
        self.stream_config = VirtraStreamConfig()
        self._scenario_map: dict[str, dict] = {}
        self._custom_lsl_map: dict[str, dict] = {}
        self._shared_events: list[str] = []
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._path):
            with open(self._path, "rb") as handle:
                self._shared_events = tomllib.load(handle).get("shared_events", {}).get("events", [])

        for drill in self.stream_config.drills.values():
            source = drill["metrics_source"]
            if source == "native":
                adapter = drill["trial_adapter"]
                task_type = drill.get("native_adapter", drill["task_type"])
                # Explicit operator switch for Fitts-PVT: native uses PVT trials.
                if not adapter.startswith("native_") and task_type == "PVT":
                    adapter = "native_target_response"
                if adapter not in TRIAL_ADAPTERS:
                    raise ValueError(f"No native trial adapter for {drill['scenario_id']!r}")
                for alias in drill["native_scenarios"]:
                    self._scenario_map[alias.casefold().strip()] = {
                        "drill_key": drill.get("native_result_key", drill["canonical_key"]),
                        "label": drill["label"], "description": drill.get("description", ""),
                        "scenario_name": alias, "scenario_id": drill["scenario_id"],
                        "scenario_revision": drill["scenario_revision"],
                        "task_type": task_type, "trial_adapter": adapter,
                        "params": dict(drill.get("native_params", {})),
                        "targets": dict(drill.get("native_targets", {})),
                        "tracked_events": list(drill.get("tracked_events", [])),
                        "selected_metric_ids": selected_for_skills(
                            list(drill.get("native_selected_metric_ids", drill.get("selected_metric_ids", []))),
                            list(drill.get("cognitive_skills", [])), adapter),
                        "cognitive_skills": list(drill.get("cognitive_skills", [])),
                        "metrics_source": source, "stream_policy": drill,
                    }
            elif source == "custom":
                adapter = drill["trial_adapter"]
                if adapter not in TRIAL_ADAPTERS:
                    raise ValueError(f"No custom trial adapter for {drill['scenario_id']!r}")
                for alias in drill["custom_drill_types"]:
                    self._custom_lsl_map[alias.casefold().strip()] = {
                        "drill_key": drill.get("custom_result_key", drill["canonical_key"]),
                        "label": drill["label"], "description": drill.get("description", ""),
                        "scenario_id": drill["scenario_id"], "task_type": drill["task_type"],
                        "scenario_revision": drill["scenario_revision"],
                        "trial_adapter": adapter, "custom_lsl_type": alias,
                        "params": dict(drill.get("custom_params", {})),
                        "targets": dict(drill.get("custom_targets", {})),
                        "selected_metric_ids": selected_for_skills(
                            list(drill.get("selected_metric_ids", [])),
                            list(drill.get("cognitive_skills", [])), adapter),
                        "cognitive_skills": list(drill.get("cognitive_skills", [])),
                        "metrics_source": source, "stream_policy": drill,
                    }
        print(f"[DrillDefinitionRegistry] Loaded {len(self._scenario_map)} native and "
              f"{len(self._custom_lsl_map)} custom scenario aliases")

    def resolve_drill(self, scenario_name: str) -> Optional[dict]:
        result = self._scenario_map.get((scenario_name or "").casefold().strip())
        return dict(result) if result else None

    def resolve_custom_lsl_drill(self, drill_type: str) -> Optional[dict]:
        result = self._custom_lsl_map.get((drill_type or "").casefold().strip())
        return dict(result) if result else None

    def get_tracked_events(self, drill_key: str) -> list[str]:
        for drill in self._scenario_map.values():
            if drill["drill_key"] == drill_key:
                return list(drill["tracked_events"])
        return []

    def all_tracked_events_for(self, drill_key: str) -> list[str]:
        return list(dict.fromkeys(self._shared_events + self.get_tracked_events(drill_key)))

    def reload(self) -> None:
        self.stream_config.reload()
        self._scenario_map.clear()
        self._custom_lsl_map.clear()
        self._load()
