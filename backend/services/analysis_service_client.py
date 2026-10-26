"""
analysis_service_client.py — Post-scenario metric computation and session record management.

Replaces the former Analysis HTTP microservice with direct calls to the
metrics registry functions. No external process needed.

Responsibilities:
  1. Compute summary metrics from the trial list via the attribute's metrics function.
  2. Auto-save legacy sessions; round assessments are durable at round closure.
  3. Broadcast session.metrics_ready to all connected Frontend clients.
  4. Broadcast the saved session ID for operator confirmation or discard.
"""

import asyncio
import traceback
from typing import Callable, Optional

from metrics.registry import score_session
from metrics.catalog import assessment_snapshot
from assessments.catalog import DRILLS



def _compute_metrics_for_session(session_record: dict) -> Optional[dict]:
    """
    Compute summary metrics directly from the session record trial list.
    Uses the metrics function registered in ATTRIBUTE_REGISTRY for this task_type.
    Returns None if the task_type has no registered metrics function.
    """
    try:
        return score_session(session_record)
    except Exception as e:
        print(f"[AnalysisServiceClient] ✗ Metrics computation error: {e}")
        traceback.print_exc()
        return None





class AnalysisServiceClient:

    """
    Manages post-scenario metric computation and auto-save to DB.
    """

    def __init__(
        self,
        broadcast_fn: Callable,
        loop: asyncio.AbstractEventLoop,
    ):
        self._broadcast = broadcast_fn
        self._loop = loop

    async def run_after_live_scenario(
        self,
        drill_key: str,
        session_record: dict,
        scenario_name: str = "",
        session_metadata: dict | None = None,
    ) -> None:
        """
        Post-scenario workflow (runs after scenario stops):
          1. Compute summary metrics from the trial list.
          2. Auto-save to SQLite DB (sole persistence layer).
          3. Broadcast session.metrics_ready for the operator confirm/discard panel.

        Args:
            drill_key:        The cognitive attribute key ("pvt", "gng", ...).
            session_record:   Full record built by the tracker (has "trials", "biosignals").
            scenario_name:    Display name of the completed scenario.
            session_metadata: Metadata from VirTraScenarioOrchestrator._last_session_metadata.
                              Contains participant_name, biosignals, active_streams, etc.
        """
        meta     = session_metadata or {}
        n_trials = len(session_record.get("trials", []))
        print(f"[AnalysisServiceClient] Post-scenario: drill={drill_key!r}"
              f"  scenario={scenario_name!r}  trials={n_trials}")

        if n_trials == 0 and not session_record.get("rounds"):
            print("[AnalysisServiceClient] No trials recorded — skipping metric computation")
            await self._broadcast({
                "type":      "session.metrics_ready",
                "drill_key": drill_key,
                "session_uuid": session_record.get("session_id", ""),
                "metrics":   None,
                "error":     "no_trials",
            })
            return

        # 2. Compute metrics
        metric_record = session_record
        if session_record.get("source") == "custom":
            completed = {(r.get("round_number"), r.get("attempt", 1))
                         for r in session_record.get("rounds", [])
                         if r.get("ended_reason") == "completed"}
            metric_record = dict(session_record)
            metric_record["trials"] = [trial for trial in session_record.get("trials", [])
                                       if (trial.get("round_number"),
                                           trial.get("round_attempt", 1)) in completed]
            metric_record["wild_shots"] = sum(
                item.get("count", 0) for item in session_record.get("wild_shots_by_round", [])
                if (item.get("round_number"), item.get("round_attempt", 1)) in completed)
        metrics = await self._loop.run_in_executor(
            None, _compute_metrics_for_session, metric_record
        ) if (metric_record.get("trials") or
              (drill_key in DRILLS and session_record.get("rounds"))) else None
        if metrics is None and session_record.get("source") == "custom":
            completed_rounds = sum(r.get("ended_reason") == "completed"
                                   for r in session_record.get("rounds", []))
            if session_record.get("task_type") == "PVT_FITTS":
                metrics = {"duration_s": None,
                           "duration_basis": "sum_completed_round_trial_windows",
                           "completed_rounds": completed_rounds,
                           "scoring_status": "no_scored_trials"}
            else:
                metrics = {"duration_s": session_record.get("duration_s"),
                           "duration_basis": "scenario_elapsed",
                           "completed_rounds": completed_rounds,
                           "scoring_status": ("pending_stream_contract" if completed_rounds
                                              else "no_completed_rounds")}

        if metrics is None:
            print("[AnalysisServiceClient] No metrics produced for this cognitive attribute")
            await self._broadcast({
                "type":      "session.metrics_ready",
                "drill_key": drill_key,
                "session_uuid": session_record.get("session_id", ""),
                "metrics":   None,
                "error":     "metrics_unavailable",
            })
            return

        selection = session_record.get("assessment_selection") or {}
        if selection.get("selected_metric_ids"):
            metrics["_assessment"] = assessment_snapshot(metrics, selection)

        # PVT/Fitts rounds are checkpointed at their own boundaries. A legacy
        # session insert here would create a second, conflicting result owner.
        if drill_key in DRILLS:
            from . import round_repository
            run_id = session_record.get("session_id", "")
            snapshot = await self._loop.run_in_executor(
                None, round_repository.get_run, run_id) if round_repository.available() else None
            await self._broadcast({
                "type": "session.metrics_ready", "drill_key": drill_key,
                "scenario_name": scenario_name, "metrics": metrics,
                "session_uuid": run_id, "participant_name": "",
                "auto_saved": bool(snapshot),
                "capture_status": session_record.get("capture_status", "complete"),
                "rounds": snapshot["rounds"] if snapshot else session_record.get("rounds", []),
                "assessment_run": snapshot,
            })
            return

        # 2. Auto-save to DB
        # Build the merged metadata dict that database.insert_session() expects.
        # Merge session_record fields + orchestrator metadata for a complete picture.
        db_metadata = {
            "session_id":    session_record.get("session_id", meta.get("session_id", "")),
            "drill_key":     drill_key,
            "task_type":     session_record.get("task_type", meta.get("task_type", "")),
            "scenario_name": scenario_name or session_record.get("scenario_name", ""),
            "started_at":    session_record.get("started_at", meta.get("started_at")),
            "stopped_at":    session_record.get("stopped_at", meta.get("stopped_at")),
            "duration_s":    session_record.get("duration_s", meta.get("duration_s")),
            "active_streams": meta.get("active_streams", []),
            "notes":          "",
            # Biosignals from orchestrator metadata (threaded in via _last_session_metadata)
            "biosignals":    meta.get("biosignals", session_record.get("biosignals", {})),
            # Trials for per-trial DB rows
            "trials":        session_record.get("trials", []),
            "rounds":        session_record.get("rounds", []),
            "source":        session_record.get("source", "native"),
            "native_context": session_record.get("native_context", {}),
            "custom_scenario_name": session_record.get("custom_scenario_name", ""),
            "capture_status": session_record.get("capture_status", "complete"),
            "external_scenario_id": session_record.get("external_scenario_id", ""),
        }

        participant_name = (
            meta.get("participant_name", "")
            or session_record.get("participant_name", "")
        ).strip()

        db_session_id = await self._loop.run_in_executor(
            None,
            _db_insert_session,
            participant_name,
            db_metadata,
            metrics,
            "",   # record_path — no file written; DB is the sole persistence layer
        )

        session_uuid = db_metadata["session_id"]
        if db_session_id is None:
            await self._broadcast({
                "type": "session.metrics_ready",
                "drill_key": drill_key,
                "session_uuid": session_record.get("session_id", ""),
                "metrics": metrics,
                "error": "save_failed",
            })
            return

        display_name = participant_name or await self._loop.run_in_executor(
            None, _get_auto_name
        )

        # Broadcast metrics_ready — frontend shows Confirm/Discard panel.
        await self._broadcast({
            "type":             "session.metrics_ready",
            "drill_key":        drill_key,
            "scenario_name":    scenario_name,
            "metrics":          metrics,
            "session_uuid":     session_uuid,
            "participant_name": display_name,
            "auto_saved":       db_session_id is not None,
            "capture_status":   session_record.get("capture_status", "complete"),
            "rounds":           session_record.get("rounds", []),
        })
        print(f"[AnalysisServiceClient] Metrics ready + auto-saved: "
              f"drill={drill_key!r}  participant={participant_name!r}  db_id={db_session_id}")



# ── DB helper (called via run_in_executor so it's off the event loop) ─────────

def _db_insert_session(
    participant_name: str,
    db_metadata: dict,
    metrics: dict,
    record_path: str,
) -> Optional[int]:
    """Thin wrapper — import database lazily to avoid circular import at module load."""
    try:
        from services.database import insert_session  # type: ignore
        return insert_session(
            participant_name  = participant_name,
            session_metadata  = db_metadata,
            metrics           = metrics,
            record_path       = record_path,
        )
    except Exception as e:
        print(f"[AnalysisServiceClient] ✗ DB insert failed: {e}")
        traceback.print_exc()
        return None


def _get_auto_name() -> str:
    """Suggest the next unknown name without creating it in the database."""
    try:
        from services.database import next_unknown_name  # type: ignore
        return next_unknown_name()
    except Exception:
        return ""
