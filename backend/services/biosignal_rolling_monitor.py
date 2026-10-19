"""
biosignal_rolling_monitor.py — Physiological signal rolling buffers and metrics.

Maintains rolling temporal windows for:
  - Heart Rate (BPM from Polar ECG/HR)
  - Inter-Beat Intervals (PPI ms for RMSSD HRV calculation)
  - Pupil Diameter (left & right mm from Neon Gaze)

Live broadcasts use a SHORT rolling window (last N samples) for responsiveness.
End-of-session snapshots use FULL SESSION accumulators so reported averages
reflect the entire scenario, not just the most recent window.
"""

import asyncio
import collections
import math
from typing import Callable, Optional

# Gaze 22-channel indices
GAZE_IDX_PUPIL_LEFT = 2
GAZE_IDX_PUPIL_RIGHT = 9

DEFAULT_WINDOW_SIZE = 10


class BiosignalRollingMonitor:
    """
    Ingests raw physiological LSL samples, computes rolling window statistics,
    and periodically broadcasts biosignal status over WebSocket.
    """

    def __init__(
        self,
        broadcast_fn: Callable,
        loop: asyncio.AbstractEventLoop,
        window_size: int = DEFAULT_WINDOW_SIZE,
    ):
        self._broadcast = broadcast_fn
        self._loop = loop
        self._window = window_size

        # ── Rolling buffers (live display) ─────────────────────────────────
        # HR: single float channel (BPM)
        self._hr_buf: collections.deque = collections.deque(maxlen=self._window)
        # PPI: PP_interval ms (index 1) — used for RMSSD HRV
        self._ppi_buf: collections.deque = collections.deque(maxlen=30)  # ~30 beats
        # Gaze: pupil diameter left + right (mm)
        self._pupil_l_buf: collections.deque = collections.deque(maxlen=self._window * 20)
        self._pupil_r_buf: collections.deque = collections.deque(maxlen=self._window * 20)

        # ── Session accumulators (end-of-session snapshot) ───────────────────
        # Unbounded — hold every valid sample for the whole scenario duration.
        self._hr_session: list      = []
        self._ppi_session: list     = []   # all PPI intervals (for RMSSD)
        self._pupil_l_session: list = []
        self._pupil_r_session: list = []
        self._record_session = True

    def reset(self, record_session: bool = True):
        """Clear all rolling buffers AND session accumulators for a new scenario."""
        self._record_session = record_session
        self._hr_buf.clear()
        self._ppi_buf.clear()
        self._pupil_l_buf.clear()
        self._pupil_r_buf.clear()
        self._hr_session.clear()
        self._ppi_session.clear()
        self._pupil_l_session.clear()
        self._pupil_r_session.clear()

    def handle_hr_sample(self, timestamp: float, data: list):
        """Polar HR stream: single float BPM."""
        try:
            bpm = float(data[0])
            if 30 < bpm < 250:  # physiological sanity filter
                self._hr_buf.append(bpm)
                if self._record_session:
                    self._hr_session.append(bpm)
                self.broadcast_biosignal(timestamp)
        except (IndexError, ValueError, TypeError):
            pass

    def handle_ppi_sample(self, timestamp: float, data: list):
        """
        Polar PPI stream (4 channels):
          data[0] = heart_rate_bpm
          data[1] = PP_interval ms
          data[2] = PP_error_estimate ms
          data[3] = PP_flags
        """
        try:
            ppi_ms = float(data[1])
            if 200 < ppi_ms < 2500:  # sanity: 24–300 BPM range
                self._ppi_buf.append(ppi_ms)
                if self._record_session:
                    self._ppi_session.append(ppi_ms)
                self.broadcast_biosignal(timestamp)
        except (IndexError, ValueError, TypeError):
            pass

    def handle_gaze_sample(self, timestamp: float, data: list):
        """
        Neon Gaze stream (22 channels):
          data[2] = PupilDiameter left (mm)
          data[9] = PupilDiameter right (mm)
        """
        try:
            pl = float(data[GAZE_IDX_PUPIL_LEFT])
            pr = float(data[GAZE_IDX_PUPIL_RIGHT])
            if pl > 0:
                self._pupil_l_buf.append(pl)
                if self._record_session:
                    self._pupil_l_session.append(pl)
            if pr > 0:
                self._pupil_r_buf.append(pr)
                if self._record_session:
                    self._pupil_r_session.append(pr)
            # Broadcast every 20 gaze samples (200Hz → ~10 Hz update rate)
            if len(self._pupil_l_buf) % 20 == 0:
                self.broadcast_biosignal(timestamp)
        except (IndexError, ValueError, TypeError):
            pass

    def broadcast_biosignal(self, timestamp: float):
        """Compute live stats and broadcast session.biosignal_update."""
        stats: dict = {"timestamp": timestamp}

        # HR
        if self._hr_buf:
            hr_vals = list(self._hr_buf)
            stats["hr"] = {
                "mean":   round(sum(hr_vals) / len(hr_vals), 1),
                "latest": round(hr_vals[-1], 1),
                "n":      len(hr_vals),
            }

        # HRV — RMSSD from successive PPI differences
        if len(self._ppi_buf) >= 2:
            ppi = list(self._ppi_buf)
            diffs_sq = [(ppi[i+1] - ppi[i]) ** 2 for i in range(len(ppi) - 1)]
            rmssd = math.sqrt(sum(diffs_sq) / len(diffs_sq))
            stats["hrv"] = {
                "rmssd_ms": round(rmssd, 1),
                "n_intervals": len(ppi),
            }

        # Pupil
        if self._pupil_l_buf and self._pupil_r_buf:
            l_vals = list(self._pupil_l_buf)[-self._window * 20:]
            r_vals = list(self._pupil_r_buf)[-self._window * 20:]
            pl_mean = sum(l_vals) / len(l_vals)
            pr_mean = sum(r_vals) / len(r_vals)
            stats["pupil"] = {
                "left_mean_mm":  round(pl_mean, 3),
                "right_mean_mm": round(pr_mean, 3),
                "mean_mm":       round((pl_mean + pr_mean) / 2, 3),
                "n":             len(l_vals),
            }

        if len(stats) > 1:  # has at least one signal beyond timestamp
            msg = {"type": "session.biosignal_update", **stats}
            asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    def snapshot_biosignals(self) -> dict:
        """
        Capture SESSION-LEVEL biosignal averages at Scenario Stopped.

        Uses the FULL SESSION accumulators (not the rolling buffers) to
        guarantee these values are true means over the entire scenario
        duration, not just the final N samples.
        """
        snap: dict = {}

        # HR — mean of every valid BPM sample received during the scenario
        if self._hr_session:
            vals = self._hr_session
            snap["hr_mean_bpm"]  = round(sum(vals) / len(vals), 1)
            snap["hr_n_samples"] = len(vals)

        # HRV — RMSSD from ALL PPI intervals collected during the scenario
        if len(self._ppi_session) >= 2:
            ppi = self._ppi_session
            diffs_sq = [(ppi[i+1] - ppi[i]) ** 2 for i in range(len(ppi) - 1)]
            snap["hrv_rmssd_ms"]    = round(math.sqrt(sum(diffs_sq) / len(diffs_sq)), 1)
            snap["hrv_n_intervals"] = len(ppi)

        # Pupil — mean of every valid sample received during the scenario
        if self._pupil_l_session and self._pupil_r_session:
            l_vals = self._pupil_l_session
            r_vals = self._pupil_r_session
            left_mean = sum(l_vals) / len(l_vals)
            right_mean = sum(r_vals) / len(r_vals)
            snap["pupil_l_mean_mm"] = round(left_mean, 3)
            snap["pupil_r_mean_mm"] = round(right_mean, 3)
            snap["pupil_mean_mm"]   = round((left_mean + right_mean) / 2, 3)
            snap["pupil_l_n"]       = len(l_vals)
            snap["pupil_r_n"]       = len(r_vals)
            snap["pupil_n"]         = min(len(l_vals), len(r_vals))

        return snap
