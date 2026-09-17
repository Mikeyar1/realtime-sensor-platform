"""
analysis_service_client.py — Post-scenario metric computation and session record management.

Replaces the former Analysis HTTP microservice with direct calls to the
cognitive_attributes metric functions. No external process needed.

Responsibilities:
  1. Compute summary metrics from the trial list via the attribute's metrics function.
  2. Save session.json to disk.
  3. Broadcast session.metrics_ready to all connected Frontend clients.
  4. Cache metrics for operator-initiated save (pending_metrics / clear_pending).
"""

import asyncio
import datetime
import json
import os
import traceback
from typing import Callable, Optional

from cognitive_attributes import ATTRIBUTE_REGISTRY

# Absolute path to the LiveSessions folder at the project root.
# This is 2 levels up from this file (services/ → backend/ → project root).
_PROJECT_ROOT      = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LIVE_SESSIONS_DIR = os.path.join(_PROJECT_ROOT, "LiveSessions")


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


def _save_session_json(session_record: dict, live_sessions_dir: str) -> Optional[str]:
    """
    Persist the session record to disk as a JSON file.

    File is written to:
        <live_sessions_dir>/<YYYY-MM-DD>/<session_id>_<scenario_name>.json

    Returns the full path to the saved file, or None on failure.
    """
    try:
        session_id    = session_record.get("session_id", "unknown")
        scenario_name = session_record.get("scenario_name", "session").replace(" ", "_")
        started_at    = session_record.get("started_at")

        # Build date-based subdirectory
        if started_at:
            date_str = datetime.datetime.fromtimestamp(started_at).strftime("%Y-%m-%d")
        else:
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")

        output_dir = os.path.join(live_sessions_dir, date_str)
        os.makedirs(output_dir, exist_ok=True)

        filename = f"{session_id}_{scenario_name}.json"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(session_record, f, indent=2, default=str)

        print(f"[AnalysisServiceClient] Session saved → {filepath}")
        return filepath

    except Exception as e:
        print(f"[AnalysisServiceClient] ✗ Failed to save session JSON: {e}")
        traceback.print_exc()
        return None


class AnalysisServiceClient:

    """
    Manages post-scenario metric computation and session record persistence.
    """

    def __init__(
        self,
        broadcast_fn: Callable,
        loop: asyncio.AbstractEventLoop,
        live_sessions_dir: str = _LIVE_SESSIONS_DIR,
    ):
        self._broadcast = broadcast_fn
        self._loop = loop
        self._live_sessions_dir = live_sessions_dir

        # Metrics cached in memory after computation, waiting for operator save/discard.
        self._pending_metrics: dict | None = None
        self._pending_session_metadata: dict | None = None

    async def run_after_live_scenario(
        self,
        drill_key: str,
        session_record: dict,
        scenario_name: str = "",
    ) -> None:
        """
        Post-scenario workflow:
          1. Save session.json to disk.
          2. Compute summary metrics directly from the trial list.
          3. Broadcast session.metrics_ready to all connected clients.
        """
        n_trials = len(session_record.get("trials", []))
        print(f"[AnalysisServiceClient] Post-scenario: drill={drill_key!r}"
              f"  scenario={scenario_name!r}  trials={n_trials}")

        # 1. Save session JSON to disk
        saved_path = await self._loop.run_in_executor(
            None, _save_session_json, session_record, self._live_sessions_dir
        )

        if n_trials == 0:
            print("[AnalysisServiceClient] No trials recorded — skipping metric computation")
            await self._broadcast({
                "type":      "session.metrics_ready",
                "drill_key": drill_key,
                "metrics":   None,
                "error":     "no_trials",
            })
            return

        # 2. Compute metrics directly (no HTTP round-trip)
        metrics = await self._loop.run_in_executor(
            None, _compute_metrics_for_session, session_record
        )

        if metrics is None:
            print("[AnalysisServiceClient] No metrics produced for this cognitive attribute")
            await self._broadcast({
                "type":       "session.metrics_ready",
                "drill_key":  drill_key,
                "metrics":    None,
                "error":      "metrics_unavailable",
                "saved_path": saved_path,
            })
            return

        # 3. Cache metrics for operator-initiated save, then broadcast
        self._pending_metrics          = metrics
        self._pending_session_metadata = {
            "drill_key":     drill_key,
            "scenario_name": scenario_name,
        }
        await self._broadcast({
            "type":          "session.metrics_ready",
            "drill_key":     drill_key,
            "scenario_name": scenario_name,
            "metrics":       metrics,
            "saved_path":    saved_path,
        })
        print(f"[AnalysisServiceClient] ✓ Metrics ready: drill={drill_key!r}  keys={list(metrics.keys())}")

    # ── Pending result accessors ──────────────────────────────────────────────

    def get_pending_metrics(self) -> dict | None:
        """Return cached metrics from the last Analysis call, or None if not available."""
        return self._pending_metrics

    def get_pending_session_metadata(self) -> dict | None:
        """Return cached session metadata from the last completed scenario, or None."""
        return self._pending_session_metadata

    def clear_pending(self) -> None:
        """Clear cached metrics and metadata. Called after operator saves or discards."""
        self._pending_metrics          = None
        self._pending_session_metadata = None
        print("[AnalysisServiceClient] Pending metrics cleared")

