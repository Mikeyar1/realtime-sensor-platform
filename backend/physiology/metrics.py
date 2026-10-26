"""Pure window summaries shared by live, final, and historical physiology."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .observation import SignalObservation


@dataclass
class _SourceStats:
    count: int = 0
    total: float = 0.0
    count_2: int = 0
    total_2: float = 0.0
    latest: float | None = None
    ppi: dict[float, float] = field(default_factory=dict)


class SignalWindow:
    """Constant-space HR/pupil means and timestamp-ordered PPI intervals."""

    def __init__(self):
        self._by_signal: dict[str, dict[str, _SourceStats]] = defaultdict(dict)

    def add(self, observation: SignalObservation) -> None:
        sources = self._by_signal[observation.signal]
        stats = sources.setdefault(observation.source_key, _SourceStats())
        if observation.signal == "ppi":
            if observation.value is not None:
                stats.ppi[observation.lsl_timestamp] = observation.value
            return
        if observation.value is not None:
            stats.count += 1
            stats.total += observation.value
            stats.latest = observation.value
        if observation.value_2 is not None:
            stats.count_2 += 1
            stats.total_2 += observation.value_2

    def _primary(self, signal: str) -> tuple[str, _SourceStats] | None:
        sources = self._by_signal.get(signal, {})
        if not sources:
            return None
        return max(sources.items(), key=lambda item: (
            len(item[1].ppi) if signal == "ppi" else
            item[1].count + item[1].count_2 if signal == "gaze" else item[1].count,
            item[0]))

    def summary(self) -> tuple[dict, dict]:
        hr, ppi, gaze = (self._primary(signal) for signal in ("hr", "ppi", "gaze"))
        h = hr[1] if hr else _SourceStats()
        p = ppi[1] if ppi else _SourceStats()
        g = gaze[1] if gaze else _SourceStats()
        intervals = [value for _, value in sorted(p.ppi.items())]
        differences = [(intervals[i + 1] - intervals[i]) ** 2
                       for i in range(len(intervals) - 1)]
        left = round(g.total / g.count, 3) if g.count else None
        right = round(g.total_2 / g.count_2, 3) if g.count_2 else None
        data = {
            "hr_mean_bpm": round(h.total / h.count, 1) if h.count else None,
            "hr_n_samples": h.count,
            "hrv_rmssd_ms": round(math.sqrt(sum(differences) / len(differences)), 1)
                            if differences else None,
            "hrv_n_intervals": len(intervals),
            "pupil_l_mean_mm": left,
            "pupil_r_mean_mm": right,
            "pupil_mean_mm": round((left + right) / 2, 3)
                             if left is not None and right is not None else None,
            "pupil_l_n": g.count,
            "pupil_r_n": g.count_2,
            "pupil_n": min(g.count, g.count_2),
        }
        present = sum(bool(v) for v in (h.count, len(intervals), g.count, g.count_2))
        quality = {
            "physiology_status": "complete" if h.count and differences and g.count and g.count_2
                                  else "partial" if present else "no_data",
            "physiology_sources": sorted({source for sources in self._by_signal.values()
                                          for source in sources}),
            "selected_sources": {
                signal: entry[0] if entry else None
                for signal, entry in (("hr", hr), ("ppi", ppi), ("gaze", gaze))
            },
            "hr_status": "available" if h.count else "missing",
            "hrv_status": "available" if differences else
                          "insufficient_intervals" if intervals else "missing",
            "pupil_status": "available" if left is not None and right is not None else
                            "partial" if left is not None or right is not None else "missing",
            "quality_rule": "finite_plausible_range_same_source_ppi",
        }
        return data, quality

    def live_payload(self, timestamp: float) -> dict:
        values, _ = self.summary()
        result: dict = {"timestamp": timestamp}
        hr = self._primary("hr")
        if values["hr_mean_bpm"] is not None:
            result["hr"] = {"mean": values["hr_mean_bpm"],
                            "latest": round(hr[1].latest, 1), "n": values["hr_n_samples"]}
        if values["hrv_rmssd_ms"] is not None:
            result["hrv"] = {"rmssd_ms": values["hrv_rmssd_ms"],
                             "n_intervals": values["hrv_n_intervals"]}
        if values["pupil_mean_mm"] is not None:
            result["pupil"] = {"left_mean_mm": values["pupil_l_mean_mm"],
                               "right_mean_mm": values["pupil_r_mean_mm"],
                               "mean_mm": values["pupil_mean_mm"],
                               "n": values["pupil_l_n"]}
        return result
