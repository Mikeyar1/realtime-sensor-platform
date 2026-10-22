"""Outlet identity, reconnect, and transport health regression tests."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lsl.lsl_stream_inlet_manager import LslInletWorkerThread, LslStreamInletManager


class StreamInfo:
    def __init__(self, uid, name="virtra_lsl"):
        self._uid = uid
        self._name = name

    def uid(self): return self._uid
    def name(self): return self._name
    def type(self): return "VirTra Events"
    def source_id(self): return "script"
    def channel_count(self): return 4
    def nominal_srate(self): return 0
    def channel_format(self): return 3
    def as_xml(self): return "<info/>"


class FakeWorker:
    def __init__(self, stream_info, on_sample, pull_timeout):
        self.meta = {"outlet_uid": stream_info.uid(), "name": stream_info.name()}
        self.name_str = stream_info.name()
        self.on_sample = on_sample
        self.received = self.malformed = self.dropped = 0
        self.unrecognized = self.inlet_errors = 0
        self.last_sample_at = None
        self.alive = False
        self.stopped = False

    def start(self): self.alive = True
    def stop(self):
        self.alive = False
        self.stopped = True
    def is_alive(self): return self.alive


class InletManagerTests(unittest.TestCase):
    def test_same_name_outlets_are_independent_and_reconnect(self):
        catalogs = []
        received = []
        manager = LslStreamInletManager(lambda *args: received.append(args), catalogs.append)
        first, second = StreamInfo("uid-1"), StreamInfo("uid-2")
        with patch("lsl.lsl_stream_inlet_manager.LslInletWorkerThread", FakeWorker), \
             patch("lsl.lsl_stream_inlet_manager.pylsl.resolve_streams") as resolve:
            resolve.return_value = [first, second]
            manager._refresh_streams()
            self.assertEqual({row["outlet_uid"] for row in manager.get_catalog()},
                             {"uid-1", "uid-2"})
            manager._inlets["uid-1"].on_sample("virtra_lsl", "VirTra Events", 1, ["first"])
            manager._inlets["uid-2"].on_sample("virtra_lsl", "VirTra Events", 2, ["second"])
            self.assertEqual([row[3][0] for row in received], ["first", "second"])
            self.assertEqual(manager.get_health()["discovered"], 2)
            lost_worker = manager._inlets["uid-1"]

            resolve.return_value = [second]
            manager._refresh_streams()
            self.assertTrue(lost_worker.stopped)
            self.assertEqual(set(manager._inlets), {"uid-2"})

            resolve.return_value = [first, second]
            manager._refresh_streams()
            self.assertEqual(manager.get_health()["reconnects"], 1)

            manager._inlets["uid-2"].alive = False
            manager._refresh_streams()
            self.assertTrue(manager._inlets["uid-2"].is_alive())
            self.assertEqual(manager.get_health()["reconnects"], 2)
            self.assertEqual(len(catalogs), 4)

    def test_worker_counts_decode_outcomes_and_callback_failures(self):
        class FakeInlet:
            def __init__(self, info):
                self.samples = iter([(["ok"], 1), (["bad"], 2),
                                     (["unknown"], 3), (["throw"], 4)])
            def pull_sample(self, timeout):
                try:
                    return next(self.samples)
                except StopIteration:
                    raise RuntimeError("disconnected")
            def close_stream(self): pass

        def route(name, stream_type, timestamp, sample):
            if sample[0] == "throw":
                raise ValueError("routing failed")
            return {"bad": "malformed", "unknown": "unrecognized"}.get(sample[0])

        with patch("lsl.lsl_stream_inlet_manager.pylsl.StreamInlet", FakeInlet):
            worker = LslInletWorkerThread(StreamInfo("uid-1"), route)
            worker.run()
        self.assertEqual((worker.received, worker.malformed, worker.unrecognized,
                          worker.dropped, worker.inlet_errors), (4, 1, 1, 1, 1))


if __name__ == "__main__":
    unittest.main()
