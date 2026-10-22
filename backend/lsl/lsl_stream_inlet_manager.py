"""
lsl_stream_inlet_manager.py — LabStreamingLayer stream discovery and worker thread manager.

Discovers active LSL streams on the local network, spawns a dedicated worker thread
for each discovered stream inlet, and notifies listeners on new samples and catalog changes.
"""

import threading
import time
from typing import Callable
import pylsl

from .lsl_stream_metadata_parser import parse_lsl_stream_metadata

# Import default timeouts from config if available
try:
    from config import LSL_DISCOVERY_INTERVAL, LSL_PULL_TIMEOUT
except ImportError:
    LSL_DISCOVERY_INTERVAL = 0.5
    LSL_PULL_TIMEOUT = 1.0


class LslInletWorkerThread(threading.Thread):
    """
    Dedicated worker thread for an individual LSL StreamInlet.
    Continuously pulls samples and forwards them to the provided sample callback.
    """

    def __init__(
        self,
        stream_info: pylsl.StreamInfo,
        on_sample: Callable[[str, str, float, list], None],
        pull_timeout: float = LSL_PULL_TIMEOUT,
    ):
        super().__init__(daemon=True)
        self.name_str: str = stream_info.name()
        self.type_str: str = stream_info.type()   # e.g. "VirTraEvents", "HR", "Gaze"
        self.meta: dict = parse_lsl_stream_metadata(stream_info)
        self._inlet: pylsl.StreamInlet = pylsl.StreamInlet(stream_info)
        self._on_sample: Callable = on_sample
        self._pull_timeout: float = pull_timeout
        self._stop_event: threading.Event = threading.Event()
        self.received = 0
        self.malformed = 0
        self.dropped = 0
        self.unrecognized = 0
        self.inlet_errors = 0
        self.last_sample_at: float | None = None

    def run(self):
        rate = self.meta.get("sample_rate", 0)
        ch = self.meta.get("channel_count", "?")
        rate_str = f"{rate:.0f} Hz" if rate else "irregular"
        print(f"[LslInletWorkerThread] Started  {self.name_str!r:40s}  type={self.type_str}  ch={ch}  {rate_str}")
        while not self._stop_event.is_set():
            try:
                sample, timestamp = self._inlet.pull_sample(timeout=self._pull_timeout)
                if sample:
                    self.received += 1
                    self.last_sample_at = time.time()
                    try:
                        outcome = self._on_sample(self.name_str, self.type_str, timestamp, sample)
                    except Exception as e:
                        self.dropped += 1
                        if self.dropped == 1:
                            print(f"[LslInletWorkerThread] Processing error on {self.name_str!r}: {e}")
                        continue
                    if outcome == "malformed":
                        self.malformed += 1
                    elif outcome == "dropped":
                        self.dropped += 1
                    elif outcome == "unrecognized":
                        self.unrecognized += 1
            except Exception as e:
                if not self._stop_event.is_set():
                    self.inlet_errors += 1
                    print(f"[LslInletWorkerThread] Error on {self.name_str!r}: {e}")
                break
        print(f"[LslInletWorkerThread] Stopped  {self.name_str!r}")

    def stop(self):
        self._stop_event.set()
        try:
            self._inlet.close_stream()
        except Exception:
            pass


class LslStreamInletManager:
    """
    Coordinates discovery of LSL streams and manages the lifecycle
    of LslInletWorkerThread instances for each active stream.
    """

    def __init__(
        self,
        on_sample: Callable[[str, str, float, list], None],
        on_catalog_changed: Callable[[list[dict]], None],
        discovery_interval: float = LSL_DISCOVERY_INTERVAL,
        pull_timeout: float = LSL_PULL_TIMEOUT,
    ):
        self._on_sample = on_sample
        self._on_catalog_changed = on_catalog_changed
        self._discovery_interval = discovery_interval
        self._pull_timeout = pull_timeout
        self._inlets: dict[str, LslInletWorkerThread] = {}
        self._totals = dict(discovered=0, received=0, malformed=0,
                            dropped=0, unrecognized=0, inlet_errors=0,
                            reconnects=0)
        self._seen_outlets: set[str] = set()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._discovery_loop, daemon=True)

    def start(self):
        """Start the background discovery loop."""
        self._thread.start()

    def stop(self):
        """Stop discovery and close all active inlets."""
        self._stop.set()
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=1.0)
        with self._lock:
            for t in self._inlets.values():
                t.stop()
                self._retain_counts(t)
            self._inlets.clear()

    def get_catalog(self) -> list[dict]:
        """Return the current metadata catalog of all active inlets."""
        with self._lock:
            return [dict(t.meta) for t in self._inlets.values()]

    def get_health(self) -> dict:
        """Cumulative ingest counts plus one snapshot per active outlet."""
        with self._lock:
            counters = self._totals.copy()
            outlets = []
            for key, worker in self._inlets.items():
                for field in ("received", "malformed", "dropped", "unrecognized", "inlet_errors"):
                    counters[field] += getattr(worker, field, 0)
                outlets.append({"outlet_uid": key, "name": worker.name_str,
                                "alive": worker.is_alive(),
                                "received": worker.received,
                                "last_sample_at": worker.last_sample_at})
            return {**counters, "active": len(outlets), "outlets": outlets}

    def _retain_counts(self, worker):
        for field in ("received", "malformed", "dropped", "unrecognized", "inlet_errors"):
            self._totals[field] += getattr(worker, field, 0)

    @staticmethod
    def _outlet_key(info) -> str:
        # LSL UID changes when an outlet restarts. A source ID may be reused.
        uid = info.uid()
        if uid:
            return uid
        return f"fallback:{info.name()}:{info.type()}:{info.source_id()}"

    def _discovery_loop(self):
        print(f"[LslStreamInletManager] Discovery loop started (resolve=0.3s + sleep={self._discovery_interval}s per cycle)")
        while not self._stop.is_set():
            self._refresh_streams()
            self._stop.wait(self._discovery_interval)

    def _refresh_streams(self):
        try:
            found = pylsl.resolve_streams(wait_time=0.3)
        except Exception as e:
            print(f"[LslStreamInletManager] resolve_streams error: {e}")
            return

        found_by_uid = {self._outlet_key(info): info for info in found}
        changed = False

        with self._lock:
            if self._stop.is_set():
                return
            for uid, worker in list(self._inlets.items()):
                if uid not in found_by_uid or not worker.is_alive():
                    print(f"[LslStreamInletManager] - Stream lost: {worker.name_str!r} ({uid})")
                    worker.stop()
                    self._retain_counts(worker)
                    del self._inlets[uid]
                    changed = True

            for uid, info in found_by_uid.items():
                if uid in self._inlets:
                    continue
                print(f"[LslStreamInletManager] + New stream discovered: {info.name()!r} (type={info.type()}, uid={uid})")
                try:
                    def forward(name, stream_type, timestamp, sample,
                                *, _uid=uid, _source_id=info.source_id()):
                        return self._on_sample(name, stream_type, timestamp, sample,
                                               outlet_uid=_uid, source_id=_source_id)
                    thread = LslInletWorkerThread(
                        stream_info=info, on_sample=forward,
                        pull_timeout=self._pull_timeout)
                    thread.start()
                except Exception as e:
                    print(f"[LslStreamInletManager] Inlet open failed for {uid}: {e}")
                    self._totals["inlet_errors"] += 1
                    continue
                self._inlets[uid] = thread
                if uid in self._seen_outlets:
                    self._totals["reconnects"] += 1
                else:
                    self._seen_outlets.add(uid)
                    self._totals["discovered"] += 1
                changed = True

            catalog = [t.meta for t in self._inlets.values()]

        if changed:
            active = len(catalog)
            print(f"[LslStreamInletManager] Catalog updated: {active} active stream(s)")
            self._on_catalog_changed(catalog)

