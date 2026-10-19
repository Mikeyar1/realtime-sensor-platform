"""
lsl_stream_inlet_manager.py — LabStreamingLayer stream discovery and worker thread manager.

Discovers active LSL streams on the local network, spawns a dedicated worker thread
for each discovered stream inlet, and notifies listeners on new samples and catalog changes.
"""

import threading
import time
from typing import Callable, Optional
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

    def run(self):
        rate = self.meta.get("sample_rate", 0)
        ch = self.meta.get("channel_count", "?")
        rate_str = f"{rate:.0f} Hz" if rate else "irregular"
        print(f"[LslInletWorkerThread] Started  {self.name_str!r:40s}  type={self.type_str}  ch={ch}  {rate_str}")
        while not self._stop_event.is_set():
            try:
                sample, timestamp = self._inlet.pull_sample(timeout=self._pull_timeout)
                if sample:
                    self._on_sample(self.name_str, self.type_str, timestamp, sample)
            except Exception as e:
                if not self._stop_event.is_set():
                    print(f"[LslInletWorkerThread] Error on {self.name_str!r}: {e}")
                break
        print(f"[LslInletWorkerThread] ■ Stopped  {self.name_str!r}")

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
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._discovery_loop, daemon=True)

    def start(self):
        """Start the background discovery loop."""
        self._thread.start()

    def stop(self):
        """Stop discovery and close all active inlets."""
        self._stop.set()
        with self._lock:
            for t in self._inlets.values():
                t.stop()

    def get_catalog(self) -> list[dict]:
        """Return the current metadata catalog of all active inlets."""
        with self._lock:
            return [t.meta for t in self._inlets.values()]

    def _discovery_loop(self):
        print(f"[LslStreamInletManager] Discovery loop started (resolve=0.3s + sleep={self._discovery_interval}s per cycle)")
        while not self._stop.is_set():
            self._refresh_streams()
            time.sleep(self._discovery_interval)

    def _refresh_streams(self):
        try:
            found = pylsl.resolve_streams(wait_time=0.3)
        except Exception as e:
            print(f"[LslStreamInletManager] ✗ resolve_streams error: {e}")
            return

        found_names = {info.name() for info in found}
        changed = False

        with self._lock:
            known_names = set(self._inlets.keys())

            for info in found:
                name = info.name()
                if name not in known_names:
                    print(f"[LslStreamInletManager] + New stream discovered: {name!r}  (type={info.type()})")
                    thread = LslInletWorkerThread(
                        stream_info=info,
                        on_sample=self._on_sample,
                        pull_timeout=self._pull_timeout,
                    )
                    self._inlets[name] = thread
                    thread.start()
                    changed = True

            for name in list(known_names):
                if name not in found_names:
                    print(f"[LslStreamInletManager] - Stream lost: {name!r}")
                    self._inlets[name].stop()
                    del self._inlets[name]
                    changed = True

            catalog = [t.meta for t in self._inlets.values()]

        if changed:
            active = len(catalog)
            print(f"[LslStreamInletManager] Catalog updated — {active} active stream(s)")
            self._on_catalog_changed(catalog)

