"""Format and replay checks that do not require VirTra hardware."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import virtra_lsl_capture as capture


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "raw.jsonl"
        self.native = ["Scenario Started", "Native description", "Scenario",
                       '[["Scenario","Fitts-PVT_Clay"]]']
        self.custom = ["Round Started", "Custom description", "RS",
                       json.dumps({"schema_version": "1.0", "reason": "round_started",
                                   "scenario_name": "Fitts-PVT_Clay", "scenario_id": "test-id",
                                   "drill_type": "fitts_pvt", "round": 1,
                                   "state": "Playing", "scenario_time_seconds": 1.5,
                                   "timestamp_utc": "2026-10-15T00:00:01Z",
                                   "data": {"preset": "Original"}}, separators=(",", ":"))]
        with self.path.open("w", encoding="utf-8") as handle:
            capture.write_row(handle, {"format": capture.FORMAT,
                                       "recorded_at_utc": "2026-10-15T00:00:00Z"})
            capture.write_row(handle, {"kind": "stream", "stream_id": "native",
                                       "info": self._info("V_300", "VirTraEvents", "n")})
            capture.write_row(handle, {"kind": "stream", "stream_id": "custom",
                                       "info": self._info("virtra_lsl", "VirTra Events", "c")})
            capture.write_row(handle, {"kind": "sample", "stream_id": "native",
                                       "elapsed_s": 1.0, "lsl_timestamp": 400.0,
                                       "channels": self.native})
            capture.write_row(handle, {"kind": "sample", "stream_id": "custom",
                                       "elapsed_s": 2.5, "lsl_timestamp": 401.5,
                                       "channels": self.custom})

    @staticmethod
    def _info(name, stream_type, source_id):
        return {"name": name, "type": stream_type, "source_id": source_id,
                "channel_count": 4, "nominal_srate": 0.0,
                "channel_format": "string", "channels": [], "info_xml": "<info/>"}

    def test_round_trip_preserves_wire_values_and_stream_order(self):
        streams, samples = capture.load_capture(self.path)
        self.assertEqual(list(streams), ["native", "custom"])
        self.assertEqual([row["stream_id"] for row in samples], ["native", "custom"])
        self.assertEqual(samples[0]["channels"], self.native)
        self.assertEqual(samples[1]["channels"][3], self.custom[3])
        self.assertEqual(samples[1]["lsl_timestamp"], 401.5)

    def test_replay_routes_each_sample_to_its_original_stream(self):
        sent = []
        class Outlet:
            def __init__(self, info, chunk_size):
                self.name = info.name
            def push_sample(self, values, timestamp):
                sent.append((self.name, values, timestamp))
        class Info:
            def __init__(self, name, *args):
                self.name = name
            def desc(self):
                raise AssertionError("fixture has no channel metadata")
        fake_pylsl = types.SimpleNamespace(StreamInfo=Info, StreamOutlet=Outlet,
                                           local_clock=lambda: 777.0)
        with patch.dict(sys.modules, {"pylsl": fake_pylsl}), patch.object(capture.time, "sleep"):
            self.assertEqual(capture.replay(self.path, 1.0, 0), 0)
        self.assertEqual(sent, [("V_300", self.native, 777.0),
                                ("virtra_lsl", self.custom, 777.0)])

    def test_custom_export_matches_generator_fixture_shape(self):
        output = Path(self.temp.name) / "generator.jsonl"
        self.assertEqual(capture.export_custom(self.path, output, None), 0)
        lines = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(lines[0]["stream"], {"name": "virtra_lsl",
                                               "type": "VirTra Events", "source_id": "c"})
        self.assertEqual(lines[1]["relative_time"], 0)
        self.assertEqual(lines[1]["event_name"], "Round Started")
        self.assertEqual(lines[1]["fields"], json.loads(self.custom[3]))
        generator_root = Path(__file__).resolve().parents[1] / "Labreview-data-generator-python"
        if not generator_root.is_dir():
            self.skipTest("optional Labreview-data-generator-python checkout is absent")
        with patch.object(sys, "path", [str(generator_root), *sys.path]):
            from src.parser.virtra_jsonl import parse_virtra_jsonl
            parsed = parse_virtra_jsonl(output)
        self.assertTrue(parsed.is_valid, parsed.errors)
        self.assertEqual(parsed.records[0].channels[:3], tuple(self.custom[:3]))

    def test_restarted_custom_stream_uses_one_replay_outlet(self):
        with self.path.open("a", encoding="utf-8") as handle:
            capture.write_row(handle, {"kind": "stream", "stream_id": "custom-restarted",
                                       "info": self._info("virtra_lsl", "VirTra Events", "c")})
            capture.write_row(handle, {"kind": "sample", "stream_id": "custom-restarted",
                                       "elapsed_s": 2.501, "lsl_timestamp": 401.501,
                                       "channels": self.custom})
        created = []
        sent = []
        class Outlet:
            def __init__(self, info, chunk_size):
                self.name = info.name
                created.append(self.name)
            def push_sample(self, values, timestamp):
                sent.append(self.name)
        class Info:
            def __init__(self, name, *args):
                self.name = name
            def desc(self):
                raise AssertionError("fixture has no channel metadata")
        fake_pylsl = types.SimpleNamespace(StreamInfo=Info, StreamOutlet=Outlet,
                                           local_clock=lambda: 777.0)
        with patch.dict(sys.modules, {"pylsl": fake_pylsl}), patch.object(capture.time, "sleep"):
            self.assertEqual(capture.replay(self.path, 1.0, 0), 0)
        self.assertEqual(created, ["V_300", "virtra_lsl"])
        self.assertEqual(sent, ["V_300", "virtra_lsl"])

    def test_replay_can_start_just_before_scenario(self):
        sent = []
        class Outlet:
            def __init__(self, info, chunk_size):
                self.name = info.name
            def push_sample(self, values, timestamp):
                sent.append(self.name)
        class Info:
            def __init__(self, name, *args):
                self.name = name
            def desc(self):
                raise AssertionError("fixture has no channel metadata")
        fake_pylsl = types.SimpleNamespace(StreamInfo=Info, StreamOutlet=Outlet,
                                           local_clock=lambda: 777.0)
        with patch.dict(sys.modules, {"pylsl": fake_pylsl}), patch.object(capture.time, "sleep"):
            self.assertEqual(capture.replay(self.path, 1.0, 0, 2.0), 0)
        self.assertEqual(sent, ["virtra_lsl"])


if __name__ == "__main__":
    unittest.main()
