"""Resolve decoded source events to versioned scenario contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScenarioMatch:
    scenario_id: str
    revision: int
    source: str
    role: str  # primary, secondary, disabled, unknown
    event_name: str


class ScenarioRegistry:
    def __init__(self, config):
        self.config = config
        self._generation = -1
        self._custom: dict[str, tuple] = {}
        self._native: dict[str, tuple] = {}

    def _refresh(self) -> None:
        if self._generation == self.config.generation:
            return
        custom, native = {}, {}
        for drill in self.config.drills.values():
            identity = (drill["scenario_id"], drill["scenario_revision"],
                        drill["metrics_source"])
            for alias in drill["custom_drill_types"]:
                custom[alias.casefold().strip()] = identity
            for alias in drill["native_scenarios"]:
                native[alias.casefold().strip()] = identity
        self._custom, self._native = custom, native
        self._generation = self.config.generation

    def resolve(self, event, *, active_scenario: str = "") -> ScenarioMatch | None:
        if event.status != "decoded":
            return None
        self._refresh()
        if event.source == "custom":
            identity = self._custom.get((event.decoded.drill_type or "").casefold().strip())
        elif event.source == "native":
            name = str(event.payload.get("Scenario") or "")
            if not name and event.event_name == "Scenario Started":
                name = event.decoded.description
            identity = self._native.get((name or active_scenario).casefold().strip())
        else:
            return None
        if not identity:
            return None
        scenario_id, revision, primary = identity
        role = "primary" if primary == event.source else (
            "disabled" if primary == "disabled" else "secondary")
        return ScenarioMatch(scenario_id, revision,
                             event.source, role, event.event_name)
