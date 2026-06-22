"""
bridge/batch_router.py — High-Hz sample batching and dispatch.

Functionalities:
  Buffer samples from high-rate streams (e.g. Neon Gaze at 200 Hz) and
  flush them in bulk every BATCH_FLUSH_MS milliseconds, reducing WebSocket
  frame rate and browser main-thread pressure.

  Low-Hz streams are dispatched immediately (one WS frame per sample).
  High-Hz threshold: BATCH_HZ_THRESHOLD (default 100 Hz).

  The DB write path (SessionService.on_sample) always happens before batching
  so no recorded data is ever lost.
"""

import asyncio
import json
import threading
from typing import Callable, Optional


class BatchRouter:
    BATCH_HZ_THRESHOLD = 100     # Hz — streams above this are batched
    BATCH_FLUSH_MS     = 50      # ms — flush interval (50 ms → 20 frames/s)

    def __init__(
        self,
        clients_lock: threading.Lock,
        clients: dict,
        get_service_fn: Callable,
    ):
        self._lock = clients_lock
        self._clients = clients
        self._get_service = get_service_fn

        # { stream_name: [(timestamp, data, elapsed_s), ...] }
        self._sample_buffers: dict = {}
        # { stream_name: sample_rate_hz }
        self._stream_rates:   dict = {}

        self._flush_task: Optional[asyncio.Task] = None

    # Lifecycle

    def start(self, loop: asyncio.AbstractEventLoop):
        self._flush_task = loop.create_task(self._flush_loop())

    # Sample ingress

    def route_sample(self, stream_name: str, timestamp: float, data: list):
        """
        Called from the event loop thread (via call_soon_threadsafe).
        Decides: buffer (high-Hz) or dispatch immediately (low-Hz).
        DB writes happen upstream before this call.
        """
        rate = self._stream_rates.get(stream_name, 0)
        if rate >= self.BATCH_HZ_THRESHOLD:
            self._buffer_sample(stream_name, timestamp, data)
        else:
            svc = self._get_service()
            elapsed_s = svc.get_elapsed_s(timestamp) if svc else None
            asyncio.ensure_future(
                self._dispatch_single(stream_name, timestamp, data, elapsed_s)
            )

    def _buffer_sample(self, stream_name: str, timestamp: float, data: list):
        # Append to per-stream buffer (runs on event loop thread).
        if stream_name not in self._sample_buffers:
            self._sample_buffers[stream_name] = []
        svc = self._get_service()
        elapsed_s = svc.get_elapsed_s(timestamp) if svc else None
        self._sample_buffers[stream_name].append((timestamp, data, elapsed_s))

    # Flush loop
    async def _flush_loop(self):
        # Periodic coroutine: flush all high-Hz sample buffers.
        interval = self.BATCH_FLUSH_MS / 1000.0
        while True:
            await asyncio.sleep(interval)
            if not self._sample_buffers:
                continue
            pending = {}
            for name, buf in self._sample_buffers.items():
                if buf:
                    pending[name] = buf
                    self._sample_buffers[name] = []
            for stream_name, samples in pending.items():
                await self._dispatch_batch(stream_name, samples)

    async def _dispatch_batch(self, stream_name: str, samples: list):
        # Send a 'stream.samples' batch message.
        svc = self._get_service()
        if svc and svc.is_paused():
            return
        payload = json.dumps({
            "type":      "stream.samples",
            "stream_id": stream_name,
            "samples": [
                {"timestamp": ts, "elapsed_s": el, "data": d}
                for ts, d, el in samples
            ],
        })
        await self._fanout(stream_name, payload)

    async def _dispatch_single(
        self,
        stream_name: str,
        timestamp: float,
        data: list,
        elapsed_s,
    ):
        # Send a 'stream.sample' message (low-Hz streams).
        svc = self._get_service()
        if svc and svc.is_paused():
            return
        payload = json.dumps({
            "type":      "stream.sample",
            "stream_id": stream_name,
            "timestamp": timestamp,
            "elapsed_s": elapsed_s,
            "data":      data,
        })
        await self._fanout(stream_name, payload)

    async def _fanout(self, stream_name: str, payload: str):
        # Send a pre-serialised payload to all subscribed clients.
        with self._lock:
            targets = [ws for ws, subs in self._clients.items()
                       if stream_name in subs]
        for ws in targets:
            try:
                await ws.send(payload)
            except Exception:
                pass

    # Catalog update
    def update_stream_rates(self, catalog: list):
        # Cache sample rates from catalog so routing decisions need no service call.
        for stream in catalog:
            name = stream.get("name", "")
            rate = stream.get("sample_rate", 0) or 0
            if name:
                self._stream_rates[name] = rate
