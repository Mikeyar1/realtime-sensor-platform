"""Load and validate the editable VirTra stream and component configuration.

The loader keeps stream identity, source policy, event names, and JSON paths in
one place. A configured drill only becomes scorable when its Python adapter is
registered; disabled templates can still describe an observed stream.
"""

from __future__ import annotations

import copy
import os
import sys
import tomllib
from pathlib import Path
from typing import Any
from assessments.catalog import CATEGORIES, DRILLS


def default_config_path() -> Path:
    """Use an operator-edited installed copy when available."""
    override = os.environ.get("LABREVIEW_VIRTRA_STREAMS_CONFIG")
    if override:
        return Path(override).expanduser().resolve()

    if getattr(sys, "frozen", False):
        appdata = os.environ.get("APPDATA")
        if appdata:
            operator_copy = Path(appdata) / "LabReview" / "config" / "virtra_streams.toml"
            if operator_copy.is_file():
                return operator_copy
        return Path(sys._MEIPASS) / "config" / "virtra_streams.toml"

    return Path(__file__).resolve().parents[2] / "config" / "virtra_streams.toml"


def _strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _upgrade_v1(data: dict) -> dict:
    """Read an installed v1 operator copy without restoring its timed Auto policy."""
    upgraded = copy.deepcopy(data)
    old_policy = upgraded.pop("source_selection", {})
    upgraded["schema_version"] = 2
    upgraded["custom_lifecycle"] = {
        "allow_round_start_without_scenario_start": old_policy.get(
            "allow_round_start_without_scenario_start", True),
    }
    drills = upgraded.get("drills", {})
    fitts_clay = drills.get("fitts_pvt_clay")
    if fitts_clay is not None:
        binding = DRILLS["fitts_pvt_clay"]
        fitts_clay.setdefault("category_id", binding.category_id)
        fitts_clay.setdefault("assessment_profile", CATEGORIES[binding.category_id].profile.id)
        fitts_clay.setdefault("default_preset_label", "Default")
        fitts_clay.setdefault("default_settings", dict(binding.default_settings))
    for name, drill in drills.items():
        native_enabled = drill.pop("native_scoring_enabled", False)
        custom_enabled = drill.pop("custom_scoring_enabled", False)
        preferred = drill.pop("preferred_source", "native")
        if name == "pvt_fitts":
            drill["metrics_source"] = "native"
            drill["native_adapter"] = "PVT"
            drill["native_result_key"] = "pvt"
        elif native_enabled and custom_enabled:
            drill["metrics_source"] = preferred
        elif native_enabled:
            drill["metrics_source"] = "native"
        elif custom_enabled:
            drill["metrics_source"] = "custom"
        else:
            drill["metrics_source"] = "disabled"
    pvt = drills.get("pvt", {})
    pvt_fitts = drills.get("pvt_fitts", {})
    aliases = {"lvl1_pvt_fitts_assessment", "basepvt_fitts_clay"}
    moved = [alias for alias in pvt.get("native_scenarios", [])
             if alias.casefold() in aliases]
    pvt["native_scenarios"] = [alias for alias in pvt.get("native_scenarios", [])
                                if alias.casefold() not in aliases]
    pvt_fitts["native_scenarios"] = list(dict.fromkeys(
        pvt_fitts.get("native_scenarios", []) + moved))
    return upgraded


def _validate(data: dict) -> None:
    if data.get("schema_version") != 2:
        raise ValueError("virtra_streams.toml requires schema_version = 2; update the operator config copy")

    streams = data.get("streams")
    if not isinstance(streams, dict):
        raise ValueError("[streams] is required")
    for source in ("native", "custom"):
        stream = streams.get(source)
        if not isinstance(stream, dict):
            raise ValueError(f"[streams.{source}] is required")
        _strings(stream.get("names"), f"streams.{source}.names")
        _strings(stream.get("types"), f"streams.{source}.types")
        if stream.get("match") not in {"name", "type", "any", "all"}:
            raise ValueError(f"streams.{source}.match must be name, type, any, or all")

    lifecycle_policy = data.get("custom_lifecycle")
    if not isinstance(lifecycle_policy, dict) or not isinstance(
            lifecycle_policy.get("allow_round_start_without_scenario_start"), bool):
        raise ValueError("custom_lifecycle.allow_round_start_without_scenario_start must be boolean")

    lifecycle = data.get("lifecycle")
    if not isinstance(lifecycle, dict):
        raise ValueError("[lifecycle] is required")
    for name in ("scenario_start", "scenario_stop", "round_start", "round_complete",
                 "round_reset", "component_change"):
        values = _strings(lifecycle.get(name), f"lifecycle.{name}")
        if name != "component_change" and not values:
            raise ValueError(f"lifecycle.{name} needs at least one event name")

    rounds = data.get("rounds")
    if not isinstance(rounds, dict):
        raise ValueError("[rounds] is required")
    statuses = _strings(rounds.get("aggregate_statuses"), "rounds.aggregate_statuses")
    if not set(statuses) <= {"completed", "reset", "incomplete"}:
        raise ValueError("rounds.aggregate_statuses contains an unknown status")

    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise ValueError("[fields] is required")
    for name, paths in fields.items():
        _strings(paths, f"fields.{name}")

    drills = data.get("drills")
    if not isinstance(drills, dict):
        raise ValueError("[drills] is required")
    custom_aliases: set[str] = set()
    native_aliases: set[str] = set()
    for name, drill in drills.items():
        if not isinstance(drill, dict):
            raise ValueError(f"drills.{name} must be a table")
        _required_text(drill.get("canonical_key"), f"drills.{name}.canonical_key")
        _required_text(drill.get("label"), f"drills.{name}.label")
        _required_text(drill.get("task_type"), f"drills.{name}.task_type")
        binding = DRILLS.get(drill.get("canonical_key"))
        if binding is not None:
            category_id = _required_text(drill.get("category_id"), f"drills.{name}.category_id")
            profile_id = _required_text(drill.get("assessment_profile"), f"drills.{name}.assessment_profile")
            if category_id not in CATEGORIES or category_id != binding.category_id:
                raise ValueError(f"drills.{name} has an invalid category binding")
            profile = CATEGORIES[category_id].profile
            if profile_id != profile.id:
                raise ValueError(f"drills.{name} has an invalid assessment profile binding")
        source = drill.get("metrics_source")
        if source not in {"native", "custom", "disabled"}:
            raise ValueError(f"drills.{name}.metrics_source must be native, custom, or disabled")
        native_scenarios = _strings(drill.get("native_scenarios"),
                                    f"drills.{name}.native_scenarios")
        for alias in native_scenarios:
            key = alias.casefold().strip()
            if not key or key in native_aliases:
                raise ValueError(f"duplicate or empty native scenario {alias!r}")
            native_aliases.add(key)
        aliases = _strings(drill.get("custom_drill_types"), f"drills.{name}.custom_drill_types")
        if source == "native" and not drill["native_scenarios"]:
            raise ValueError(f"drills.{name} enables native scoring without scenarios")
        if source == "custom" and not aliases:
            raise ValueError(f"drills.{name} enables custom scoring without drill types")
        if source == "custom" and any(
                component.get("scoring_status") != "implemented"
                for component in drill.get("components", {}).values()):
            raise ValueError(f"drills.{name} cannot select custom metrics before its components are implemented")
        for alias in aliases:
            key = alias.casefold().strip()
            if not key or key in custom_aliases:
                raise ValueError(f"duplicate or empty custom drill_type {alias!r}")
            custom_aliases.add(key)

        components = drill.get("components")
        if not isinstance(components, dict) or not components:
            raise ValueError(f"drills.{name}.components must define at least one component")
        if drill.get("default_component") not in components:
            raise ValueError(f"drills.{name}.default_component is not defined")
        phases: set[str] = set()
        for component_name, component in components.items():
            if not isinstance(component, dict):
                raise ValueError(f"drills.{name}.components.{component_name} must be a table")
            _required_text(component.get("label"),
                           f"drills.{name}.components.{component_name}.label")
            values = _strings(component.get("phase_values"),
                              f"drills.{name}.components.{component_name}.phase_values")
            for value in values:
                key = value.casefold().strip()
                if not key or key in phases:
                    raise ValueError(f"duplicate or empty phase value {value!r} in {name}")
                phases.add(key)
            for section in ("events", "fields"):
                local = component.get(section, {})
                if not isinstance(local, dict):
                    raise ValueError(f"drills.{name}.components.{component_name}.{section} must be a table")
                for local_key, local_values in local.items():
                    _strings(local_values,
                             f"drills.{name}.components.{component_name}.{section}.{local_key}")
        for section in ("events", "fields"):
            local = drill.get(section, {})
            if not isinstance(local, dict):
                raise ValueError(f"drills.{name}.{section} must be a table")
            for key, value in local.items():
                _strings(value, f"drills.{name}.{section}.{key}")


class VirtraStreamConfig:
    """Validated policy with helpers for stream, drill, phase, and field lookup."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_config_path()
        self._data: dict = {}
        self.reload()

    def reload(self) -> None:
        with self.path.open("rb") as handle:
            candidate = tomllib.load(handle)
        if candidate.get("schema_version") == 1:
            candidate = _upgrade_v1(candidate)
        _validate(candidate)
        self._data = candidate

    @property
    def custom_lifecycle(self) -> dict:
        return copy.deepcopy(self._data["custom_lifecycle"])

    @property
    def round_policy(self) -> dict:
        return copy.deepcopy(self._data["rounds"])

    @property
    def drills(self) -> dict:
        return copy.deepcopy(self._data["drills"])

    def stream_source(self, name: str, stream_type: str) -> str | None:
        """Classify custom first so it cannot be mistaken for a native stream."""
        for source in ("custom", "native"):
            spec = self._data["streams"][source]
            name_matches = name.casefold() in {item.casefold() for item in spec["names"]}
            type_matches = stream_type.casefold() in {item.casefold() for item in spec["types"]}
            mode = spec["match"]
            matched = {
                "name": name_matches,
                "type": type_matches,
                "any": name_matches or type_matches,
                "all": name_matches and type_matches,
            }[mode]
            if matched:
                return source
        return None

    def drill_for_custom_type(self, drill_type: str, *, include_disabled: bool = False) -> dict | None:
        key = (drill_type or "").casefold().strip()
        for drill in self._data["drills"].values():
            if drill["metrics_source"] != "custom" and not include_disabled:
                continue
            if any(alias.casefold().strip() == key for alias in drill["custom_drill_types"]):
                return copy.deepcopy(drill)
        return None

    def drill_for_native_scenario(self, scenario_name: str,
                                  *, include_disabled: bool = False) -> dict | None:
        key = (scenario_name or "").casefold().strip()
        for drill in self._data["drills"].values():
            if drill["metrics_source"] != "native" and not include_disabled:
                continue
            if any(alias.casefold().strip() == key for alias in drill["native_scenarios"]):
                return copy.deepcopy(drill)
        return None

    def component_for_phase(self, drill: dict, phase: str | None) -> str | None:
        """A missing phase uses the configured default; unknown phases stay unknown."""
        key = (phase or "").casefold().strip()
        if not key:
            return drill["default_component"]
        for name, component in drill["components"].items():
            if key in {value.casefold().strip() for value in component["phase_values"]}:
                return name
        return None

    def event_names(self, event_key: str, drill: dict | None = None,
                    component: str | None = None) -> list[str]:
        if drill and component:
            local = drill["components"].get(component, {}).get("events", {})
            if event_key in local:
                return list(local[event_key])
        if drill and event_key in drill.get("events", {}):
            return list(drill["events"][event_key])
        return list(self._data["lifecycle"].get(event_key, []))

    def field_paths(self, field_key: str, drill: dict | None = None,
                    component: str | None = None) -> list[str]:
        if drill and component:
            local = drill["components"].get(component, {}).get("fields", {})
            if field_key in local:
                return list(local[field_key])
        if drill and field_key in drill.get("fields", {}):
            return list(drill["fields"][field_key])
        return list(self._data["fields"].get(field_key, []))

    @staticmethod
    def extract(payload: dict, paths: list[str]) -> Any:
        """Return the first present, non-null value at a configured dotted path."""
        for path in paths:
            value: Any = payload
            for segment in path.split("."):
                if not isinstance(value, dict) or segment not in value:
                    value = None
                    break
                value = value[segment]
            if value is not None:
                return value
        return None
