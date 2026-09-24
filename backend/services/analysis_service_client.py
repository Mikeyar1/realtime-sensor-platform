"""
analysis_service_client.py — Post-scenario metric computation and session record management.

Replaces the former Analysis HTTP microservice with direct calls to the
cognitive_attributes metric functions. No external process needed.

Responsibilities:
  1. Compute summary metrics from the trial list via the attribute's metrics function.
  2. Auto-save session to the SQLite database (sole persistence layer).
  3. Broadcast session.metrics_ready to all connected Frontend clients.
  4. Cache session_record + metrics for operator-driven confirm/discard flow.
     - db.confirm_session — updates participant name in DB
     - db.discard_session — deletes session from DB
"""

import asyncio
import traceback
from typing import Callable, Optional

from cognitive_attributes import ATTRIBUTE_REGISTRY



def _compute_metrics_for_session(session_record: dict) -> Optional[dict]:
    """
    Compute summary metrics directly from the session record trial list.
    Uses the metrics function registered in ATTRIBUTE_REGISTRY for this task_type.
    Returns None if the task_type has no registered metrics function.
    """
    task_type = (session_record.get("task_type") or "").upper()
    drill_key = session_record.get("drill_key", "")

    # Look up by task_type first; fall back to drill_key (uppercase)
    entry = ATTRIBUTE_REGISTRY.get(task_type) or ATTRIBUTE_REGISTRY.get(drill_key.upper())
    if entry is None:
        print(f"[AnalysisServiceClient] No metrics function for task_type={task_type!r} — skipping")
        return None

    _, metrics_fn = entry
    try:
        trials  = session_record.get("trials", [])
        metrics = metrics_fn(trials, session_record)
        return metrics
    except Exception as e:
        print(f"[AnalysisServiceClient] ✗ Metrics computation error: {e}")
        traceback.print_exc()
        return None





class AnalysisServiceClient:

    """
    Manages post-scenario metric computation, auto-save to DB and disk,
    and the operator confirm/discard flow.
    """

    def __init__(
        self,
        broadcast_fn: Callable,
        loop: asyncio.AbstractEventLoop,
    ):
        self._broadcast = broadcast_fn
        self._loop = loop

        # Pending state — populated after auto-save, cleared after confirm or discard.
        # These allow the operator to correct the participant name or discard the session.
        self._pending_metrics:          dict | None = None
        self._pending_session_record:   dict | None = None
        self._pending_session_metadata: dict | None = None

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
          3. Cache everything for the operator confirm/discard panel.
          4. Broadcast session.metrics_ready to all connected clients.

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

        if n_trials == 0:
            print("[AnalysisServiceClient] No trials recorded — skipping metric computation")
            await self._broadcast({
                "type":      "session.metrics_ready",
                "drill_key": drill_key,
                "metrics":   None,
                "error":     "no_trials",
            })
            return

        # 2. Compute metrics
        metrics = await self._loop.run_in_executor(
            None, _compute_metrics_for_session, session_record
        )

        if metrics is None:
            print("[AnalysisServiceClient] No metrics produced for this cognitive attribute")
            await self._broadcast({
                "type":      "session.metrics_ready",
                "drill_key": drill_key,
                "metrics":   None,
                "error":     "metrics_unavailable",
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
                "metrics": metrics,
                "error": "save_failed",
            })
            return

        display_name = participant_name or await self._loop.run_in_executor(
            None, _get_auto_name
        )

        # 3. Cache for operator confirm/discard
        self._pending_metrics          = metrics
        self._pending_session_record   = session_record
        self._pending_session_metadata = {
            "drill_key":        drill_key,
            "scenario_name":    scenario_name,
            "session_uuid":     session_uuid,
            "participant_name": display_name,
        }

        # 4. Broadcast metrics_ready — frontend shows Confirm/Discard panel
        await self._broadcast({
            "type":             "session.metrics_ready",
            "drill_key":        drill_key,
            "scenario_name":    scenario_name,
            "metrics":          metrics,
            "session_uuid":     session_uuid,
            "participant_name": self._pending_session_metadata["participant_name"],
            "auto_saved":       db_session_id is not None,
        })
        print(f"[AnalysisServiceClient] Metrics ready + auto-saved: "
              f"drill={drill_key!r}  participant={participant_name!r}  db_id={db_session_id}")

    # ── Pending result accessors ──────────────────────────────────────────────

    def get_pending_metrics(self) -> dict | None:
        """Return cached metrics from the last Analysis call, or None if not available."""
        return self._pending_metrics

    def get_pending_session_metadata(self) -> dict | None:
        """Return cached session metadata from the last completed scenario, or None."""
        return self._pending_session_metadata

    def clear_pending(self) -> None:
        """Clear cached metrics and metadata. Called after operator confirms or discards."""
        self._pending_metrics          = None
        self._pending_session_record   = None
        self._pending_session_metadata = None
        print("[AnalysisServiceClient] Pending metrics cleared")


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
