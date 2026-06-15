"""
inlet_manager.py — Manages all LSL stream inlets.
Discovers active LSL streams, opens one InletThread per stream,
and fires callbacks when samples arrive or the stream list changes.
"""

import os
import threading
import time
import pylsl
import tomllib
from stream_meta_parser import parse_stream_info

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, 'config.toml'), 'rb') as f:
    config = tomllib.load(f)

DISCOVERY_INTERVAL = config['lsl']['discovery_interval_seconds']
PULL_TIMEOUT       = config['lsl']['pull_timeout_seconds']


class InletThread(threading.Thread):

    def __init__(self, stream_info, on_sample):
        super().__init__(daemon=True)
        self.name_str = stream_info.name()
        self.meta = parse_stream_info(stream_info)
        self._inlet = pylsl.StreamInlet(stream_info)
        self._on_sample = on_sample
        self._stop_event = threading.Event()

    def run(self):
        print(f"[InletThread] Started: {self.name_str}")
        while not self._stop_event.is_set():
            try:
                sample, timestamp = self._inlet.pull_sample(timeout=PULL_TIMEOUT)
                if sample:
                    self._on_sample(self.name_str, timestamp, sample)
            except Exception as e:
                if not self._stop_event.is_set():
                    print(f"[InletThread] '{self.name_str}' error: {e}")
                break
        print(f"[InletThread] Stopped: {self.name_str}")

    def stop(self):
        self._stop_event.set()
        try:
            self._inlet.close_stream()
        except Exception:
            pass


class InletManager:
    """Discovers LSL streams and manages one InletThread per stream."""

    def __init__(self, on_sample, on_catalog_changed):
        self._on_sample = on_sample
        self._on_catalog_changed = on_catalog_changed
        self._inlets: dict[str, InletThread] = {}
        self._lock   = threading.Lock()
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._discovery_loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        with self._lock:
            for t in self._inlets.values():
                t.stop()

    def get_catalog(self) -> list[dict]:
        with self._lock:
            return [t.meta for t in self._inlets.values()]

    def _discovery_loop(self):
        print("[InletManager] Discovery loop started")
        while not self._stop.is_set():
            self._refresh_streams()
            time.sleep(DISCOVERY_INTERVAL)

    def _refresh_streams(self):
        try:
            found = pylsl.resolve_streams(wait_time=1.0)
        except Exception as e:
            print(f"[InletManager] resolve_streams error: {e}")
            return

        found_names = {info.name() for info in found}
        changed = False

        with self._lock:
            known_names = set(self._inlets.keys())

            for info in found:
                name = info.name()
                if name not in known_names:
                    print(f"[InletManager] New stream: {name}")
                    thread = InletThread(info, self._on_sample)
                    self._inlets[name] = thread
                    thread.start()
                    changed = True

            for name in list(known_names):
                if name not in found_names:
                    print(f"[InletManager] Stream lost: {name}")
                    self._inlets[name].stop()
                    del self._inlets[name]
                    changed = True

            catalog = [t.meta for t in self._inlets.values()]

        if changed:
            self._on_catalog_changed(catalog)
