"""Load and validate the editable VirTra stream and component configuration.

The loader keeps stream identity, source policy, event names, and JSON paths in
one place. A configured drill only becomes scorable when its Python adapter is
registered; disabled templates can still describe an observed stream.
"""

from __future__ import annotations

import copy
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any
from assessments.catalog import CATEGORIES, DRILLS
from cognitive_skills.catalog import SKILLS
from metrics.catalog import METRICS
from trial_models.registry import TRIAL_ADAPTERS


_TRIAL_MODELS = {
    "PVT": "target_response", "PVT_FITTS": "target_response",
    "GNG": "go_no_go", "DISCRIMINATION": "discrimination",
    "COMPOSITE": "composite",
}
_IMPLEMENTED_METRIC_SETS = {"PVT", "GNG", "DISCRIMINATION", "PVT_FITTS"}
_FIELD_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


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


def default_scenarios_path() -> Path:
    """Find the bundled scenario contracts, or an explicit operator copy."""
    override = os.environ.get("LABREVIEW_SCENARIOS_CONFIG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "scenarios"
    return Path(__file__).resolve().parents[1] / "scenarios"


def _merge_tables(base: dict, overrides: dict) -> dict:
    """Merge explicit operator overrides without deleting scenario defaults."""
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_tables(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _partition_legacy_native_aliases(drills: dict) -> None:
    """Keep old operator files readable after one native alias became one folder."""
    for old_key, new_key, alias in (
            ("pvt", "snake_drill", "Snake Drill"),
            ("behavioral_discrimination", "drill_3b", "Drill 3B"),
            ("pvt_fitts", "basepvt_fitts_clay", "basePVT_Fitts_Clay")):
        old = drills.get(old_key)
        if not isinstance(old, dict) or "native_scenarios" not in old:
            continue
        names = old["native_scenarios"]
        if not isinstance(names, list):
            continue  # The normal validator reports the malformed value.
        moved = [name for name in names if isinstance(name, str) and
                 name.casefold().strip() == alias.casefold()]
        if not moved:
            continue
        old["native_scenarios"] = [name for name in names if name not in moved]
        target = drills.setdefault(new_key, {})
        target["native_scenarios"] = list(dict.fromkeys(
            target.get("native_scenarios", []) + moved))
        if "metrics_source" in old and "metrics_source" not in target:
            target["metrics_source"] = old["metrics_source"]


def _load_scenario_drills(root: Path) -> dict:
    """Load exactly one versioned drill definition from each scenario folder."""
    if not root.is_dir():
        raise ValueError(f"scenario config directory does not exist: {root}")
    definitions: dict = {}
    for path in sorted(root.glob("*/scenario.toml")):
        with path.open("rb") as handle:
            candidate = tomllib.load(handle)
        scenario = candidate.get("scenario")
        if candidate.get("schema_version") != 1 or not isinstance(scenario, dict):
            raise ValueError(f"{path} requires schema_version = 1 and [scenario]")
        name = scenario.get("id")
        revision = scenario.get("revision")
        if name != path.parent.name or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError(f"{path} has an invalid scenario id or revision")
        drills = candidate.get("drills")
        if not isinstance(drills, dict) or set(drills) != {name}:
            raise ValueError(f"{path} must define exactly [drills.{name}]")
        if name in definitions:
            raise ValueError(f"duplicate scenario id {name!r}")
        definition = copy.deepcopy(drills[name])
        for kind in ("native", "custom"):
            source_path = path.parent / f"{kind}.toml"
            if not source_path.exists():
                continue
            with source_path.open("rb") as handle:
                source_config = tomllib.load(handle)
            if (source_config.get("schema_version") != 1 or
                    source_config.get("source") != {"kind": kind} or
                    not isinstance(source_config.get("drills"), dict) or
                    set(source_config["drills"]) != {name}):
                raise ValueError(f"{source_path} has an invalid {kind} source contract")
            definition = _merge_tables(definition, source_config["drills"][name])
        definition.setdefault("native_scenarios", [])
        definition.setdefault("custom_drill_types", [])
        definition.setdefault("trial_model", _TRIAL_MODELS.get(definition.get("task_type")))
        definitions[name] = {**definition, "scenario_id": name,
                             "scenario_revision": revision}
    if not definitions:
        raise ValueError(f"no scenario configs found in {root}")
    return definitions


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
    combined = list(dict.fromkeys(pvt_fitts.get("native_scenarios", []) + moved))
    pvt_fitts["native_scenarios"] = [alias for alias in combined
                                     if alias.casefold() != "basepvt_fitts_clay"]
    base_pvt_fitts = drills.setdefault("basepvt_fitts_clay", {})
    base_pvt_fitts["native_scenarios"] = list(dict.fromkeys(
        base_pvt_fitts.get("native_scenarios", []) +
        [alias for alias in combined if alias.casefold() == "basepvt_fitts_clay"]))
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
        for key in ("names", "types"):
            values = [item.casefold().strip() for item in stream[key]]
            if any(not item for item in values) or len(values) != len(set(values)):
                raise ValueError(f"streams.{source}.{key} contains an empty or duplicate alias")
    native, custom = streams["native"], streams["custom"]
    for key, matching_modes in (("names", {"name", "any", "all"}),
                                ("types", {"type", "any", "all"})):
        if native["match"] in matching_modes and custom["match"] in matching_modes:
            if {item.casefold() for item in native[key]} & {item.casefold() for item in custom[key]}:
                raise ValueError(f"native and custom stream {key} overlap")

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
        if any(not _FIELD_PATH.fullmatch(path) for path in paths):
            raise ValueError(f"fields.{name} contains an invalid dotted path")

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
        expected_model = _TRIAL_MODELS.get(drill["task_type"])
        if expected_model is None or drill.get("trial_model", expected_model) != expected_model:
            raise ValueError(f"drills.{name}.trial_model is incompatible with task_type")
        binding = DRILLS.get(drill.get("canonical_key"))
        if binding is not None:
            category_id = _required_text(drill.get("category_id"), f"drills.{name}.category_id")
            profile_id = _required_text(drill.get("assessment_profile"), f"drills.{name}.assessment_profile")
            if category_id not in CATEGORIES or category_id != binding.category_id:
                raise ValueError(f"drills.{name} has an invalid category binding")
            profile = CATEGORIES[category_id].profile
            if profile_id != profile.id:
                raise ValueError(f"drills.{name} has an invalid assessment profile binding")
            selected_metrics = _strings(
                drill.get("metric_ids", [metric.id for metric in profile.metrics]),
                f"drills.{name}.metric_ids")
            known_metrics = {metric.id for metric in profile.metrics}
            if (not selected_metrics or len(selected_metrics) != len(set(selected_metrics)) or
                    not set(selected_metrics) <= known_metrics):
                raise ValueError(f"drills.{name}.metric_ids contains an unknown or duplicate metric")
        elif "metric_ids" in drill:
            raise ValueError(f"drills.{name}.metric_ids requires a catalog profile")
        source = drill.get("metrics_source")
        if source not in {"native", "custom", "disabled"}:
            raise ValueError(f"drills.{name}.metrics_source must be native, custom, or disabled")
        adapter = drill.get("trial_adapter")
        if source != "disabled" and adapter not in TRIAL_ADAPTERS:
            raise ValueError(f"drills.{name}.trial_adapter is not implemented")
        if source == "native" and adapter and not adapter.startswith("native_") and drill.get("native_adapter") != "PVT":
            raise ValueError(f"drills.{name}.trial_adapter is incompatible with source")
        skills = _strings(drill.get("cognitive_skills", []), f"drills.{name}.cognitive_skills")
        if len(skills) != len(set(skills)) or not set(skills) <= SKILLS.keys():
            raise ValueError(f"drills.{name}.cognitive_skills has an unknown or duplicate skill")
        selected = _strings(drill.get(
            "native_selected_metric_ids", drill.get("selected_metric_ids", []))
            if source == "native" else drill.get("selected_metric_ids", []),
            f"drills.{name}.selected_metric_ids")
        if len(selected) != len(set(selected)) or not set(selected) <= METRICS.keys():
            raise ValueError(f"drills.{name}.selected_metric_ids has an unknown or duplicate metric")
        effective_adapter = ("native_target_response" if source == "native" and
                             adapter == "custom_fitts_pvt" else adapter)
        prefixes = {
            "native_target_response": ("pvt.",),
            "native_threat_discrimination": ("pvt.", "discrimination."),
            "custom_go_no_go": ("gng.",),
            "custom_fitts_pvt": ("pvt_fitts.",),
        }.get(effective_adapter, ())
        if source != "disabled" and any(not metric.startswith(prefixes) for metric in selected):
            raise ValueError(f"drills.{name}.selected_metric_ids is incompatible with trial adapter")
        if source != "disabled" and (not skills or not selected):
            raise ValueError(f"drills.{name} needs cognitive_skills and selected_metric_ids")
        native_scenarios = _strings(drill.get("native_scenarios"),
                                    f"drills.{name}.native_scenarios")
        for alias in native_scenarios:
            key = alias.casefold().strip()
            if not key or key in native_aliases:
                raise ValueError(f"duplicate or empty native scenario {alias!r}")
            native_aliases.add(key)
        aliases = _strings(drill.get("custom_drill_types"), f"drills.{name}.custom_drill_types")
        if not isinstance(drill.get("native_params", {}), dict):
            raise ValueError(f"drills.{name}.native_params must be a table")
        if not isinstance(drill.get("native_targets", {}), dict):
            raise ValueError(f"drills.{name}.native_targets must be a table")
        for key, values in drill.get("native_targets", {}).items():
            _strings(values, f"drills.{name}.native_targets.{key}")
        for key in ("go_actions", "no_go_actions"):
            if key in drill:
                _strings(drill[key], f"drills.{name}.{key}")
        if set(drill.get("go_actions", [])) & set(drill.get("no_go_actions", [])):
            raise ValueError(f"drills.{name} has overlapping go/no-go actions")
        formats = _strings(drill.get("native_metadata_formats", []),
                           f"drills.{name}.native_metadata_formats")
        if not set(formats) <= {"pairs_v1", "object_v1", "empty"}:
            raise ValueError(f"drills.{name}.native_metadata_formats has an unknown format")
        if source == "native" and not drill["native_scenarios"]:
            raise ValueError(f"drills.{name} enables native scoring without scenarios")
        if source == "custom" and not aliases:
            raise ValueError(f"drills.{name} enables custom scoring without drill types")
        if source == "custom" and any(
                component.get("scoring_status") != "implemented"
                for component in drill.get("components", {}).values()):
            raise ValueError(f"drills.{name} cannot select custom metrics before its components are implemented")
        if source == "custom" and adapter and not adapter.startswith("custom_"):
            raise ValueError(f"drills.{name}.trial_adapter is incompatible with source")
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
            metric_set = _required_text(component.get("metric_set"),
                                        f"drills.{name}.components.{component_name}.metric_set")
            if component.get("scoring_status") == "implemented" and metric_set not in _IMPLEMENTED_METRIC_SETS:
                raise ValueError(f"drills.{name}.components.{component_name} has no implemented metric set")
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
                    if section == "fields" and any(not _FIELD_PATH.fullmatch(path) for path in local_values):
                        raise ValueError(f"drills.{name}.components.{component_name}.fields.{local_key} has an invalid path")
        for section in ("events", "fields"):
            local = drill.get(section, {})
            if not isinstance(local, dict):
                raise ValueError(f"drills.{name}.{section} must be a table")
            for key, value in local.items():
                _strings(value, f"drills.{name}.{section}.{key}")
                if section == "fields" and any(not _FIELD_PATH.fullmatch(path) for path in value):
                    raise ValueError(f"drills.{name}.fields.{key} has an invalid path")


class VirtraStreamConfig:
    """Validated policy with helpers for stream, drill, phase, and field lookup."""

    def __init__(self, path: str | Path | None = None,
                 scenarios_path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_config_path()
        self.scenarios_path = (Path(scenarios_path) if scenarios_path is not None
                               else default_scenarios_path())
        self._data: dict = {}
        self.generation = 0
        self.reload()

    def reload(self) -> None:
        with self.path.open("rb") as handle:
            candidate = tomllib.load(handle)
        if candidate.get("schema_version") == 1:
            candidate = _upgrade_v1(candidate)
        _partition_legacy_native_aliases(candidate.setdefault("drills", {}))
        # A legacy operator file may still contain complete [drills.*] tables.
        # New files can override only the keys they intend to change.
        candidate["drills"] = _merge_tables(
            _load_scenario_drills(self.scenarios_path), candidate.get("drills", {}))
        _validate(candidate)
        self._data = candidate
        self.generation += 1

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
