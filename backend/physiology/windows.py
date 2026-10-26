"""Round-owned physiology capture using normalized observations."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from services import round_repository

from .metrics import SignalWindow
from .observation import SignalObservation
from .catalog import projection


@dataclass
class _RoundWindow:
    round_id: str
    started_at: float
    ended_at: float | None = None
    metrics: SignalWindow = field(default_factory=SignalWindow)
    pending: list[SignalObservation] = field(default_factory=list)


class RoundPhysiologyCapture:
    def __init__(self, batch_size: int = 64):
        self._lock = threading.RLock()
        self._windows: dict[str, _RoundWindow] = {}
        self._finalized: set[str] = set()
        self._batch_size = batch_size

    def begin(self, round_id: str, started_at: float) -> None:
        with self._lock:
            if round_id not in self._finalized:
                self._windows.setdefault(round_id, _RoundWindow(round_id, started_at))

    def close(self, round_id: str, ended_at: float) -> bool:
        with self._lock:
            window = self._windows.get(round_id)
            if window is None or window.ended_at is not None:
                return False
            window.ended_at = ended_at
            return True

    def ingest(self, observation: SignalObservation) -> dict | None:
        with self._lock:
            candidates = [window for window in self._windows.values()
                          if observation.lsl_timestamp >= window.started_at and
                          (window.ended_at is None or observation.lsl_timestamp <= window.ended_at)]
            if not candidates:
                return None
            window = max(candidates, key=lambda item: item.started_at)
            window.metrics.add(observation)
            window.pending.append(observation)
            if len(window.pending) >= self._batch_size:
                self._flush(window)
            values, quality = window.metrics.summary()
            return {"round_id": window.round_id, **values, "quality": quality}

    @staticmethod
    def _flush(window: _RoundWindow) -> None:
        if window.pending and round_repository.available():
            round_repository.append_signal_chunk(window.round_id, window.pending)
            window.pending.clear()

    def finalize(self, round_id: str) -> dict | None:
        with self._lock:
            window = self._windows.get(round_id)
            if window is None:
                return None
            self._flush(window)
            if not round_repository.available():
                return None
            summary, quality = window.metrics.summary()
            summary["_assessment"] = projection(summary, quality)
            result = round_repository.finalize_physiology(round_id, summary, quality)
            self._windows.pop(round_id, None)
            self._finalized.add(round_id)
            return result


def rebuild_round_physiology(round_id: str) -> dict:
    """Recompute a historical summary from persisted old rows and new chunks."""
    window = SignalWindow()
    for row in round_repository.get_physiology_samples(round_id):
        window.add(SignalObservation(
            signal=row["signal"], source_key=row["source_key"],
            lsl_timestamp=row["lsl_timestamp"],
            received_at=row.get("received_at", row["lsl_timestamp"]),
            value=row.get("value"), value_2=row.get("value_2"),
            unit=row.get("unit", ""),
            decoder_version=row.get("decoder_version", 1)))
    summary, quality = window.summary()
    summary["_assessment"] = projection(summary, quality)
    return {"summary": summary, "quality": quality}
