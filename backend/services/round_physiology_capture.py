"""Timestamp-windowed physiological capture for independently owned rounds.

Raw accepted samples are committed in bounded batches. A short closure grace
allows delayed LSL delivery with timestamps inside the round window; samples
between rounds and cross-round PPI pairs are excluded by construction.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field

from . import round_repository


@dataclass
class _Window:
    round_id: str
    started_at: float
    ended_at: float | None = None
    pending: list[tuple] = field(default_factory=list)
    count: dict[str, int] = field(default_factory=lambda: {"hr": 0, "ppi": 0, "pupil_l": 0, "pupil_r": 0})
    total: dict[str, float] = field(default_factory=lambda: {"hr": 0, "pupil_l": 0, "pupil_r": 0})
    ppi_previous: float | None = None
    ppi_diff_sq: float = 0.0


def _finite(value, lower: float, upper: float) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) and lower < number < upper else None
    except (TypeError, ValueError):
        return None


def _values(signal: str, data: list) -> tuple[float | None, float | None]:
    if signal == "hr":
        return (_finite(data[0], 30, 250) if data else None), None
    if signal == "ppi":
        return (_finite(data[1], 200, 2500) if len(data) > 1 else None), None
    if signal == "gaze":
        left = _finite(data[2], 0, 15) if len(data) > 2 else None
        right = _finite(data[9], 0, 15) if len(data) > 9 else None
        return left, right
    return None, None


def _summary(samples: list[dict]) -> tuple[dict, dict]:
    hr = [row["value"] for row in samples if row["signal"] == "hr" and row["value"] is not None]
    ppi = [row["value"] for row in samples if row["signal"] == "ppi" and row["value"] is not None]
    left = [row["value"] for row in samples if row["signal"] == "gaze" and row["value"] is not None]
    right = [row["value_2"] for row in samples if row["signal"] == "gaze" and row["value_2"] is not None]
    mean = lambda values, digits: round(sum(values) / len(values), digits) if values else None
    diffs = [(ppi[i + 1] - ppi[i]) ** 2 for i in range(len(ppi) - 1)]
    hr_mean = mean(hr, 1)
    left_mean = mean(left, 3)
    right_mean = mean(right, 3)
    summary = {
        "hr_mean_bpm": hr_mean, "hr_n_samples": len(hr),
        "hrv_rmssd_ms": round(math.sqrt(sum(diffs) / len(diffs)), 1) if diffs else None,
        "hrv_n_intervals": len(ppi),
        "pupil_l_mean_mm": left_mean, "pupil_r_mean_mm": right_mean,
        "pupil_mean_mm": round((left_mean + right_mean) / 2, 3)
                         if left_mean is not None and right_mean is not None else None,
        "pupil_l_n": len(left), "pupil_r_n": len(right),
    }
    present = sum(bool(values) for values in (hr, ppi, left, right))
    quality = {
        "physiology_status": "complete" if hr and diffs and left and right else
                              "partial" if present else "no_data",
        "physiology_sources": sorted({row["source_key"] for row in samples}),
        "hr_status": "available" if hr else "missing",
        "hrv_status": "available" if diffs else "insufficient_intervals" if ppi else "missing",
        "pupil_status": "available" if left and right else "partial" if left or right else "missing",
    }
    return summary, quality


class RoundPhysiologyCapture:
    def __init__(self, batch_size: int = 64):
        self._lock = threading.RLock()
        self._windows: dict[str, _Window] = {}
        self._finalized: set[str] = set()
        self._batch_size = batch_size

    def begin(self, round_id: str, started_at: float) -> None:
        with self._lock:
            if round_id in self._finalized:
                return
            self._windows.setdefault(round_id, _Window(round_id, started_at))

    def close(self, round_id: str, ended_at: float) -> bool:
        """Mark the window closed; callers schedule finalize after delivery grace."""
        with self._lock:
            window = self._windows.get(round_id)
            if window is None or window.ended_at is not None:
                return False
            window.ended_at = ended_at
            return True

    def ingest(self, signal: str, source_key: str, timestamp: float, data: list) -> dict | None:
        value, value_2 = _values(signal, data)
        if value is None and value_2 is None:
            return None
        with self._lock:
            candidates = [window for window in self._windows.values()
                          if timestamp >= window.started_at and
                          (window.ended_at is None or timestamp <= window.ended_at)]
            if not candidates:
                return None
            window = max(candidates, key=lambda item: item.started_at)
            window.pending.append((signal, source_key, timestamp, value, value_2))
            if signal == "hr":
                window.count["hr"] += 1
                window.total["hr"] += value
            elif signal == "ppi":
                if window.ppi_previous is not None:
                    window.ppi_diff_sq += (value - window.ppi_previous) ** 2
                window.ppi_previous = value
                window.count["ppi"] += 1
            else:
                for key, sample in (("pupil_l", value), ("pupil_r", value_2)):
                    if sample is not None:
                        window.count[key] += 1
                        window.total[key] += sample
            if len(window.pending) >= self._batch_size:
                self._flush(window)
            return self._preview(window)

    def _flush(self, window: _Window) -> None:
        if window.pending and round_repository.available():
            round_repository.append_physiology_samples(window.round_id, window.pending)
        window.pending.clear()

    @staticmethod
    def _preview(window: _Window) -> dict:
        c = window.count
        left = window.total["pupil_l"] / c["pupil_l"] if c["pupil_l"] else None
        right = window.total["pupil_r"] / c["pupil_r"] if c["pupil_r"] else None
        return {
            "round_id": window.round_id,
            "hr_mean_bpm": round(window.total["hr"] / c["hr"], 1) if c["hr"] else None,
            "hr_n_samples": c["hr"],
            "hrv_rmssd_ms": round(math.sqrt(window.ppi_diff_sq / (c["ppi"] - 1)), 1)
                             if c["ppi"] > 1 else None,
            "hrv_n_intervals": c["ppi"],
            "pupil_l_mean_mm": round(left, 3) if left is not None else None,
            "pupil_r_mean_mm": round(right, 3) if right is not None else None,
            "pupil_mean_mm": round((left + right) / 2, 3)
                             if left is not None and right is not None else None,
            "pupil_l_n": c["pupil_l"], "pupil_r_n": c["pupil_r"],
        }

    def finalize(self, round_id: str) -> dict | None:
        with self._lock:
            window = self._windows.pop(round_id, None)
            if window is None:
                return None
            self._flush(window)
            self._finalized.add(round_id)
        if not round_repository.available():
            return None
        summary, quality = _summary(round_repository.get_physiology_samples(round_id))
        return round_repository.finalize_physiology(round_id, summary, quality)
