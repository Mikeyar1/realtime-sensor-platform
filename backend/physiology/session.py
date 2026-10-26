"""Session physiology summaries and bounded live windows."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque

from .metrics import SignalWindow
from .observation import SignalObservation


class PhysiologySession:
    def __init__(self, broadcast_fn, loop: asyncio.AbstractEventLoop):
        self._broadcast = broadcast_fn
        self._loop = loop
        self.reset()

    def reset(self, record_session: bool = True) -> None:
        self._record_session = record_session
        self._session = SignalWindow()
        self._rolling: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=200))
        self._gaze_count = 0

    def ingest(self, observation: SignalObservation) -> None:
        if self._record_session:
            self._session.add(observation)
        key = (observation.signal, observation.source_key)
        limit = 30 if observation.signal == "ppi" else 10 if observation.signal == "hr" else 200
        if self._rolling[key].maxlen != limit:
            self._rolling[key] = deque(self._rolling[key], maxlen=limit)
        self._rolling[key].append(observation)
        if observation.signal == "gaze":
            self._gaze_count += 1
            if self._gaze_count % 20:
                return
        live = SignalWindow()
        for observations in self._rolling.values():
            for item in observations:
                live.add(item)
        payload = live.live_payload(observation.lsl_timestamp)
        if len(payload) > 1:
            asyncio.run_coroutine_threadsafe(
                self._broadcast({"type": "session.biosignal_update", **payload}), self._loop)

    def snapshot_biosignals(self) -> dict:
        values, _ = self._session.summary()
        return {key: value for key, value in values.items() if value is not None and
                (not key.endswith("_n") and not key.endswith("_samples") and
                 not key.endswith("_intervals") or value)}

    def snapshot_quality(self) -> dict:
        return self._session.summary()[1]

    def final_payload(self) -> dict | None:
        values, _ = self._session.summary()
        payload: dict = {"type": "session.biosignal_update", "is_final": True}
        if values["hr_mean_bpm"] is not None:
            payload["hr"] = {"mean": values["hr_mean_bpm"],
                             "latest": values["hr_mean_bpm"], "n": values["hr_n_samples"]}
        if values["hrv_rmssd_ms"] is not None:
            payload["hrv"] = {"rmssd_ms": values["hrv_rmssd_ms"],
                              "n_intervals": values["hrv_n_intervals"]}
        if values["pupil_mean_mm"] is not None:
            payload["pupil"] = {"left_mean_mm": values["pupil_l_mean_mm"],
                                "right_mean_mm": values["pupil_r_mean_mm"],
                                "mean_mm": values["pupil_mean_mm"],
                                "n": values["pupil_l_n"]}
        return payload if len(payload) > 2 else None
