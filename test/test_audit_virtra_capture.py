"""Capture audit and lifecycle evidence tests; no LSL hardware required."""

from __future__ import annotations

import json
import hashlib
import unittest
from pathlib import Path

from backend.virtra_custom_lsl.event_decoder import decode_custom_lsl_sample
from backend.virtra_custom_lsl.session import CustomLslSessionBase
from tools.audit_virtra_capture import distinct_events, lifecycle_fixture, read_capture, summarize, custom_events


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "backend" / "tests" / "fixtures"


class QuietSession(CustomLslSessionBase):
    def on_drill_event(self, event):
        pass


def feed(session: QuietSession, name: str, round_number: int, timestamp: float) -> None:
    payload = {
        "drill_type": "fitts_pvt", "scenario_name": "Fitts-PVT_Clay",
        "scenario_id": "fixture-run", "round": round_number,
        "scenario_time_seconds": timestamp, "data": {},
    }
    event = decode_custom_lsl_sample([name, "", "", json.dumps(payload)])
    assert event is not None
    session.handle_event(event, timestamp)


def round_rows(session: QuietSession) -> list[list]:
    return [
        [row["round_number"], row["attempt"],
         row["ended_reason"] if row["ended_at"] is not None else "active"]
        for row in session.rounds_snapshot()
    ]


class CaptureEvidenceTests(unittest.TestCase):
    def test_sanitized_capture_fixture_has_expected_observed_boundaries(self):
        fixture = json.loads((FIXTURES / "fitts_lifecycle_capture.json").read_text(encoding="utf-8"))
        rows = fixture["events"]
        self.assertEqual(fixture["format"], "labreview.sanitized_lifecycle.v1")
        self.assertEqual(rows[0]["event_name"], "Scenario Destroyed")
        self.assertEqual(sum(row["event_name"] == "Scenario Started" for row in rows), 2)
        self.assertEqual(sum(row["event_name"] == "Round Completed" for row in rows), 6)
        self.assertEqual(sum(row["event_name"] == "Scenario Stopped" for row in rows), 0)
        self.assertEqual(rows[-1]["event_name"], "Round Completed")
        self.assertEqual(rows[-1]["round"], 3)
        self.assertEqual(
            [row["event_name"] for row in rows if row["event_name"] == "Round Started"],
            ["Round Started"] * 6,
        )

    def test_observed_lifecycle_after_dedup_has_three_completed_rounds(self):
        fixture = json.loads((FIXTURES / "fitts_lifecycle_capture.json").read_text(encoding="utf-8"))
        session = QuietSession()
        seen = set()
        for row in fixture["events"]:
            key = (row["event_name"], row["round"], row["scenario_time_s"], row["lsl_offset_s"])
            if key in seen:
                continue
            seen.add(key)
            feed(session, row["event_name"], row["round"], row["scenario_time_s"])
        self.assertEqual(round_rows(session), [
            [1, 1, "completed"], [2, 1, "completed"], [3, 1, "completed"],
        ])
        self.assertTrue(session.is_active)  # This capture ends without a terminal event.

    def test_synthetic_lifecycle_contract_probes(self):
        fixture = json.loads((FIXTURES / "fitts_lifecycle_cases.json").read_text(encoding="utf-8"))
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                session = QuietSession()
                first = None
                for name, round_number, timestamp in case["events"]:
                    if name == "NEW_INSTANCE":
                        first = session
                        session = QuietSession()
                    elif name != "Stream Lost":  # Transport status is not a scenario event.
                        feed(session, name, round_number, timestamp)
                self.assertEqual(round_rows(session), case["expected_rounds"])
                self.assertEqual(session.is_active, case["expected_active"])
                if first is not None:
                    self.assertEqual(round_rows(first), case["expected_first_run_rounds"])

    def test_local_raw_capture_matches_portable_projection_when_available(self):
        capture = ROOT / "data" / "lsl_captures" / "fitts-pvt-clay.jsonl"
        if not capture.exists():
            self.skipTest("Local raw capture is optional and excluded from Git")
        observed_hash = "e8ad8469f46e73a3ed425643a1b35221de6b0c79c825b48f81d6f7b680414ccd"
        if hashlib.sha256(capture.read_bytes()).hexdigest() != observed_hash:
            self.skipTest("Local capture differs from the audited October 15 recording")
        streams, samples = read_capture(capture)
        events = custom_events(streams, samples)
        distinct, duplicates = distinct_events(events)
        report = summarize(streams, samples)
        fixture = json.loads((FIXTURES / "fitts_lifecycle_capture.json").read_text(encoding="utf-8"))
        self.assertEqual((len(events), len(distinct), duplicates), (443, 222, 221))
        self.assertEqual(report["event_counts_distinct"]["Shot Fired"], 26)
        self.assertEqual(report["observed_script_versions"], ["0.1.0"])
        self.assertEqual(
            [row["name"] for row in report["native_lifecycle_events"]],
            ["Scenario Started", "Scenario Started", "Scenario Stopped", "Scenario Stopped"],
        )
        self.assertGreater(
            report["native_lifecycle_events"][2]["capture_elapsed_s"],
            report["lifecycle_events"][-1]["capture_elapsed_s"],
        )
        self.assertFalse(any(
            row["name"] in {"Scenario Stopped", "Scenario Destroyed", "Scenario Ended"}
            for row in report["lifecycle_events"]
            if row["capture_elapsed_s"] > report["native_lifecycle_events"][0]["capture_elapsed_s"]
        ))
        self.assertEqual(lifecycle_fixture(events), fixture)


if __name__ == "__main__":
    unittest.main()
