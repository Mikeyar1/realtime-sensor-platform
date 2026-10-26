"""Attach source events to runs and rounds after the existing lifecycle routes them."""

from __future__ import annotations

from assessments.events import normalize_custom_event


def _custom_round(orchestrator, event, timestamp: float) -> str | None:
    tracker = orchestrator._active_custom_session
    if tracker is None or not event.decoded or not event.decoded.round:
        return None
    matches = [row for row in tracker.rounds_snapshot()
               if row["round_number"] == event.decoded.round and
               row["started_at"] <= timestamp and
               (row.get("ended_at") is None or timestamp <= row["ended_at"])]
    return matches[-1]["round_id"] if matches else None


class EventAttributor:
    def __init__(self, store, registry):
        self.store = store
        self.registry = registry
        self._pending_native: list[tuple[str, object]] = []

    def route(self, event_id: str, event, outcome: str | None,
              orchestrator, timestamp: float, *, before_custom_run: str = "",
              before_custom_scenario_id: str = "",
              before_native_run_id: str = "",
              before_native_scenario: str = "",
              before_context_matched: bool = False,
              before_round_id: str | None = None) -> None:
        if event.status != "decoded":
            return
        active_name = (orchestrator._active_scenario or
                       orchestrator._native_context.get("native_scenario_name", ""))
        match = self.registry.resolve(
            event, active_scenario=active_name or before_native_scenario)
        if not match or match.role == "disabled":
            return

        active_custom_id = (orchestrator._active_custom_drill or {}).get(
            "stream_policy", {}).get("scenario_id") or before_custom_scenario_id
        if event.source == "custom" and active_custom_id and match.scenario_id != active_custom_id:
            return

        if event.source == "native" and match.role == "primary":
            run_id = orchestrator._session_id or before_native_run_id
        elif event.source == "custom":
            run_id = orchestrator._custom_session_id or before_custom_run
        else:
            matched = (before_context_matched or
                       orchestrator._custom_context.get("context_match_status") in {
                           "matched_name", "matched_alias"})
            matched = matched and (not active_custom_id or match.scenario_id == active_custom_id)
            run_id = (orchestrator._custom_session_id or before_custom_run) if matched else ""
            if not run_id and event.event_name == "Scenario Started":
                self._pending_native.append((event_id, match))
                self._pending_native = self._pending_native[-512:]

        if run_id:
            if event.source == "custom":
                role = normalize_custom_event(event.decoded, 0, self.registry.config).kind.value
                round_id = _custom_round(orchestrator, event, timestamp) or before_round_id
            else:
                role = event.event_name.casefold().replace(" ", "_")
                round_id = None
            decision = ("ignored" if outcome in {"dropped", "unrecognized", "malformed"}
                        else "enrichment" if match.role == "secondary" and
                        event.event_name in {"Shot Hit", "Shot Miss"} else
                        "context" if match.role == "secondary" else "routed")
            self.store.link(event_id, run_id=run_id, round_id=round_id,
                            evidence_role=role, decision=decision,
                            scenario_id=match.scenario_id,
                            scenario_revision=match.revision)

        if event.source == "custom" and orchestrator._custom_session_id:
            matched = orchestrator._custom_context.get("context_match_status") in {
                "matched_name", "matched_alias"}
            if matched and self._pending_native:
                run_id = orchestrator._custom_session_id
                for pending_id, pending_match in self._pending_native:
                    if pending_match.scenario_id == match.scenario_id:
                        self.store.link(pending_id, run_id=run_id, round_id=None,
                                        evidence_role="native_context", decision="context",
                                        scenario_id=match.scenario_id,
                                        scenario_revision=match.revision)
                self._pending_native.clear()
