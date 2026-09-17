"""
record_multicolor_gng.py — LSL listener and data recorder for VirTra Multicolor GNG.

Connects to the 'virtra_lsl' stream (VirTra Events / virtra_multicolor_gng),
displays live event metrics and target discrimination actions in real-time,
and records complete datasets into both JSONL (lossless) and CSV formats.

Usage:
    cd backend
    uv run python record_multicolor_gng.py
    uv run python record_multicolor_gng.py --output-dir ./recordings/test1
    uv run python record_multicolor_gng.py --explore-only
"""

import argparse
import csv
import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Force UTF-8 on Windows terminals to prevent charmap encoding errors
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import pylsl

STREAM_NAME = "virtra_lsl"
STREAM_TYPE = "VirTra Events"
SOURCE_ID = "virtra_multicolor_gng"

# ANSI styling
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
MAGENTA = "\033[95m"
BLUE = "\033[94m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def format_color_badge(color_name: str) -> str:
    c = str(color_name).lower()
    if c == "green":
        return f"{GREEN}[GREEN]{RESET}"
    elif c == "red":
        return f"{RED}[RED]{RESET}"
    elif c == "purple":
        return f"{MAGENTA}[PURPLE]{RESET}"
    elif c == "yellow":
        return f"{YELLOW}[YELLOW]{RESET}"
    return f"{DIM}[{color_name.upper()}]{RESET}"


class MulticolorGngRecorder:
    def __init__(
        self,
        output_dir: Optional[str] = None,
        verbose: bool = True,
        stream_name: str = STREAM_NAME,
        record_files: bool = True,
    ):
        self.stream_name = stream_name
        self.verbose = verbose
        self.record_files = record_files
        self.output_dir_arg = output_dir

        self.output_dir: Optional[Path] = None
        self.jsonl_path: Optional[Path] = None
        self.csv_path: Optional[Path] = None
        self.summary_path: Optional[Path] = None
        self.jsonl_file = None
        self.csv_file = None
        self.csv_writer = None

        self.total_samples = 0
        self.event_counts: Dict[str, int] = {}
        self.latest_metrics: Dict[str, Any] = {}
        self.first_timestamp: Optional[float] = None
        self.last_timestamp: Optional[float] = None
        self.start_time_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.active_inlet: Optional[pylsl.StreamInlet] = None
        self._is_closed = False

    def _setup_files(self, stream_name: str = "GNG_LSL"):
        if not self.record_files:
            return

        now_str = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in stream_name)
        if self.output_dir_arg:
            self.output_dir = Path(self.output_dir_arg)
        else:
            self.output_dir = Path(__file__).resolve().parent.parent / "data" / "MulticolorGNG_Recordings" / f"{safe_name}_{now_str}"

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.output_dir / "events.jsonl"
        self.csv_path = self.output_dir / "events.csv"
        self.summary_path = self.output_dir / "session_summary.json"

        self.jsonl_file = open(self.jsonl_path, "w", encoding="utf-8", buffering=1)
        self.csv_file = open(self.csv_path, "w", newline="", encoding="utf-8", buffering=1)
        self.csv_writer = csv.writer(self.csv_file)

        self.csv_writer.writerow([
            "sample_index",
            "lsl_timestamp",
            "arrival_utc",
            "scenario_time_s",
            "round",
            "state",
            "event_code",
            "event_name",
            "event_description",
            "drill_type",
            "scenario_name",
            "reason",
            "payload_json",
        ])
        self.csv_file.flush()

    def discover_stream(self, wait_continuous: bool = True) -> pylsl.StreamInfo:
        """Locates the Multicolor GNG stream or continuously waits until hardware starts broadcasting."""
        print("\n" + "=" * 80)
        print(f"{CYAN}{BOLD} VirTra Multicolor GNG — LSL Stream Explorer & Recorder{RESET}")
        print("=" * 80)
        print(f"Targeting: Name='{self.stream_name}', Type='{STREAM_TYPE}', SourceID='{SOURCE_ID}'")
        if self.record_files:
            dest_hint = self.output_dir_arg or "data/MulticolorGNG_Recordings/<timestamp>/"
            print(f"Destination: {dest_hint}")
        else:
            print("Mode: Explore-only (no files written)")
        print("-" * 80)

        attempt = 0
        last_announced_streams: set = set()

        while True:
            attempt += 1
            # 1. Direct query by exact stream name
            streams = pylsl.resolve_byprop("name", self.stream_name, timeout=1.0)
            if streams:
                print(f"\n{GREEN}[OK] Stream found by name '{self.stream_name}'!{RESET}")
                return streams[0]

            # 2. Query by source_id
            streams = pylsl.resolve_byprop("source_id", SOURCE_ID, timeout=0.5)
            if streams:
                print(f"\n{GREEN}[OK] Stream found by source_id '{SOURCE_ID}'!{RESET}")
                return streams[0]

            # 3. Query by type
            streams = pylsl.resolve_byprop("type", STREAM_TYPE, timeout=0.5)
            if streams:
                print(f"\n{GREEN}[OK] Stream found by type '{STREAM_TYPE}'!{RESET}")
                return streams[0]

            # Check general network streams
            all_streams = pylsl.resolve_streams(wait_time=0.5)
            current_stream_names = {s.name() for s in all_streams}

            # Check if any stream matches fuzzy keywords
            for s in all_streams:
                s_name = s.name()
                s_id = s.source_id()
                if (
                    self.stream_name.lower() in s_name.lower()
                    or "virtra" in s_name.lower()
                    or "gng" in s_name.lower()
                    or s_id == SOURCE_ID
                ):
                    print(f"\n{GREEN}[OK] Auto-matched candidate stream: '{s_name}' (type: '{s.type()}'){RESET}")
                    return s

            if not wait_continuous:
                raise RuntimeError(f"Stream '{self.stream_name}' not detected on network.")

            # Show active streams if they changed
            if current_stream_names != last_announced_streams and current_stream_names:
                last_announced_streams = current_stream_names
                print(f"\n[Notice] Other streams currently visible on network: {', '.join(current_stream_names)}")

            # Display polling spinner
            spinner = ["|", "/", "-", "\\"][attempt % 4]
            sys.stdout.write(f"\r  {spinner} Waiting for hardware to broadcast '{self.stream_name}'... (Press Ctrl+C to stop) ")
            sys.stdout.flush()
            time.sleep(0.5)

    def print_stream_info(self, info: pylsl.StreamInfo):
        print(f"\n{BOLD}Stream Information:{RESET}")
        print(f"  • Name:           {info.name()}")
        print(f"  • Type:           {info.type()}")
        print(f"  • Channel Count:  {info.channel_count()}")
        print(f"  • Channel Format: string (cf_string)")
        print(f"  • Nominal Srate:  {info.nominal_srate():.1f} Hz (event-driven)")
        print(f"  • Source ID:      {info.source_id() or '(none)'}")
        print(f"  • Hostname:       {info.hostname() or '(local)'}")
        print(f"  • Protocol:       LSL v{info.version() / 100:.1f}")
        if self.record_files and self.output_dir:
            print(f"  • Saving to:      {self.output_dir.resolve()}")
            print(f"    ├─ events.jsonl (Complete lossless records)")
            print(f"    └─ events.csv   (Tabular index)\n")
        print("=" * 80)

    def record_sample(self, sample: List[str], lsl_timestamp: float):
        arrival_utc = datetime.datetime.now(datetime.timezone.utc).isoformat()
        self.total_samples += 1
        if self.first_timestamp is None:
            self.first_timestamp = lsl_timestamp
        self.last_timestamp = lsl_timestamp

        # Channels per spec:
        # 0: event_name
        # 1: event_description
        # 2: event_code
        # 3: event_fields_json
        event_name = sample[0] if len(sample) > 0 else ""
        event_description = sample[1] if len(sample) > 1 else ""
        event_code = sample[2] if len(sample) > 2 else ""
        raw_json_str = sample[3] if len(sample) > 3 else "{}"

        self.event_counts[event_code] = self.event_counts.get(event_code, 0) + 1

        parsed_payload: Dict[str, Any] = {}
        try:
            parsed_payload = json.loads(raw_json_str)
        except Exception:
            parsed_payload = {"raw": raw_json_str}

        scenario_time = parsed_payload.get("scenario_time_seconds", "")
        round_num = parsed_payload.get("round", "")
        state = parsed_payload.get("state", "")
        drill_type = parsed_payload.get("drill_type", "")
        scenario_name = parsed_payload.get("scenario_name", "")
        reason = parsed_payload.get("reason", "")
        inner_data = parsed_payload.get("data", {})

        # Track metrics
        if "metrics" in inner_data and isinstance(inner_data["metrics"], dict):
            self.latest_metrics.update(inner_data["metrics"])
        elif event_code == "METRICS" and isinstance(inner_data, dict):
            self.latest_metrics.update(inner_data)

        # Write files if recording
        if self.record_files and self.jsonl_file and self.csv_file:
            # 1. JSONL lossless
            jsonl_record = {
                "sample_index": self.total_samples,
                "lsl_timestamp": lsl_timestamp,
                "arrival_utc": arrival_utc,
                "event_name": event_name,
                "event_description": event_description,
                "event_code": event_code,
                "payload": parsed_payload,
            }
            self.jsonl_file.write(json.dumps(jsonl_record, ensure_ascii=False) + "\n")
            self.jsonl_file.flush()

            # 2. CSV tabular
            self.csv_writer.writerow([
                self.total_samples,
                f"{lsl_timestamp:.6f}",
                arrival_utc,
                scenario_time,
                round_num,
                state,
                event_code,
                event_name,
                event_description,
                drill_type,
                scenario_name,
                reason,
                raw_json_str,
            ])
            self.csv_file.flush()

        # Live terminal exploration
        if self.verbose:
            self._print_live_event(
                sample_idx=self.total_samples,
                event_name=event_name,
                event_code=event_code,
                event_desc=event_description,
                payload=parsed_payload,
                inner_data=inner_data,
            )

    def _print_live_event(
        self,
        sample_idx: int,
        event_name: str,
        event_code: str,
        event_desc: str,
        payload: Dict[str, Any],
        inner_data: Dict[str, Any],
    ):
        sc_time = payload.get("scenario_time_seconds", 0.0)
        round_val = payload.get("round", 1)
        time_str = f"T+{sc_time:06.2f}s" if isinstance(sc_time, (int, float)) else "T+--.--s"

        # Code highlighting
        code_badge = f"{BOLD}{CYAN}[{event_code:^7}]{RESET}"
        if event_code in ("SH", "SF"):
            code_badge = f"{BOLD}{GREEN}[{event_code:^7}]{RESET}"
        elif event_code in ("SM", "TT"):
            code_badge = f"{BOLD}{YELLOW}[{event_code:^7}]{RESET}"
        elif event_code in ("TF", "RR"):
            code_badge = f"{BOLD}{RED}[{event_code:^7}]{RESET}"
        elif event_code in ("RS", "RC", "SS", "ST"):
            code_badge = f"{BOLD}{MAGENTA}[{event_code:^7}]{RESET}"

        print(f"#{sample_idx:04d} | {time_str} | R{round_val} | {code_badge} {BOLD}{event_name:<24}{RESET} | {event_desc}")

        def get_val(key: str, default: Any = ""):
            if isinstance(inner_data, dict) and key in inner_data and inner_data[key] != "":
                return inner_data[key]
            return payload.get(key, default)

        # Detail highlights
        if event_code == "TA":  # Target Appeared
            t_color = get_val("assigned_color", "unknown")
            is_focus = get_val("is_focus_target", False)
            exp_action = get_val("expected_action", "")
            t_name = get_val("target_name", "")
            badge = format_color_badge(t_color)
            focus_str = f"{BOLD}{'SHOOT' if exp_action == 'shoot' else 'WITHHOLD'}{RESET}"
            print(f"       +-- Target: {t_name} | Color: {badge} | Focus: {is_focus} | Action: {focus_str}")

        elif event_code == "SF":  # Shot Fired
            rt = get_val("reaction_time_seconds", "")
            first = get_val("first_shot_after_target_appearance", False)
            rt_str = f"{rt:.3f}s" if isinstance(rt, (int, float)) else str(rt)
            print(f"       +-- Shot Fired | RT: {rt_str} | First Shot: {first}")

        elif event_code == "SH":  # Shot Hit
            classification = get_val("gng_classification", "")
            res = get_val("result", "")
            dist = get_val("target_distance", "")
            score = get_val("score", "")
            zones = get_val("hit_hitzones", [])
            class_color = GREEN if classification == "true_positive" else RED
            print(f"       +-- Hit: {class_color}{classification}{RESET} | Result: {res} | Dist: {dist}m | Zones: {zones} | Score: {score}")

        elif event_code == "TT":  # Target Timed Out
            classification = get_val("classification", "")
            class_color = GREEN if classification == "true_negative" else RED
            print(f"       +-- Timeout: {class_color}{classification}{RESET}")

        elif event_code == "METRICS" or "inhibition_failure_rate" in inner_data or "inhibition_failure_rate" in payload:
            tp = get_val("true_positives", 0)
            fp = get_val("false_positives", 0)
            tn = get_val("true_negatives", 0)
            fn = get_val("false_negatives", 0)
            acc = get_val("cognitive_accuracy", 0.0)
            m_acc = get_val("marksmanship_accuracy", 0.0)
            rt = get_val("mean_cognitive_reaction_time_seconds", 0.0)
            print(f"       +-- Metrics -> TP:{tp} FP:{fp} TN:{tn} FN:{fn} | CogAcc: {acc*100:.1f}% | MarkAcc: {m_acc*100:.1f}% | MeanRT: {rt:.3f}s")

        elif event_code in ("RS", "RC"):
            print(f"       +-- Round transition -> {event_name}")

        elif event_code == "IE":
            trig = inner_data.get("triggered_event", "")
            fb = inner_data.get("feedback", "")
            print(f"       +-- Instructor: '{trig}' - {fb}")

    def listen_and_record(self, info: pylsl.StreamInfo):
        self.connected_stream_name = info.name()
        self.connected_stream_type = info.type()
        self.connected_stream_source_id = info.source_id()
        self._setup_files(stream_name=info.name())
        self.active_inlet = pylsl.StreamInlet(info, max_buflen=360, max_chunklen=1)
        self.print_stream_info(info)

        print(f"{BOLD}Listening for live events from hardware... (Press Ctrl+C when finished){RESET}\n")
        print("-" * 80)

        try:
            while True:
                sample, timestamp = self.active_inlet.pull_sample(timeout=1.0)
                if sample is None:
                    continue
                self.record_sample(sample, timestamp)

        except KeyboardInterrupt:
            print(f"\n{YELLOW}Stopping listener and saving session...{RESET}")
        finally:
            self.close()

    def close(self):
        if self._is_closed:
            return
        self._is_closed = True

        if self.active_inlet:
            try:
                self.active_inlet.close_stream()
            except Exception:
                pass

        if self.record_files:
            try:
                if self.jsonl_file:
                    self.jsonl_file.close()
                if self.csv_file:
                    self.csv_file.close()
            except Exception:
                pass

            duration = 0.0
            if self.first_timestamp is not None and self.last_timestamp is not None:
                duration = max(0.0, self.last_timestamp - self.first_timestamp)

            summary = {
                "stream_name": getattr(self, "connected_stream_name", self.stream_name),
                "stream_type": getattr(self, "connected_stream_type", STREAM_TYPE),
                "stream_source_id": getattr(self, "connected_stream_source_id", SOURCE_ID),
                "session_start_utc": self.start_time_iso,
                "session_end_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "duration_seconds": round(duration, 3),
                "total_samples": self.total_samples,
                "event_counts": self.event_counts,
                "final_metrics": self.latest_metrics,
                "files": {
                    "jsonl": str(self.jsonl_path.resolve()) if self.jsonl_path else "",
                    "csv": str(self.csv_path.resolve()) if self.csv_path else "",
                },
            }

            if self.summary_path:
                try:
                    with open(self.summary_path, "w", encoding="utf-8") as f:
                        json.dump(summary, f, indent=2, ensure_ascii=False)
                except Exception as e:
                    print(f"Error saving summary: {e}")

        print(f"\n{GREEN}{BOLD}================================================================================{RESET}")
        print(f"{GREEN}{BOLD} Session Finished{RESET}")
        print(f"{GREEN}{BOLD}================================================================================{RESET}")
        print(f"  • Total Events Captured: {self.total_samples}")
        print(f"  • Event Breakdown:       {dict(self.event_counts)}")
        if self.latest_metrics:
            cog_acc = self.latest_metrics.get("cognitive_accuracy")
            mean_rt = self.latest_metrics.get("mean_cognitive_reaction_time_seconds")
            if cog_acc is not None:
                print(f"  • Cognitive Accuracy:    {cog_acc*100:.1f}%")
            if mean_rt is not None:
                print(f"  • Cognitive Reaction:    {mean_rt:.3f}s")

        if self.record_files and self.output_dir:
            print(f"\n  Files saved to: {self.output_dir.resolve()}")
            print(f"    +-- {self.jsonl_path.name} (Lossless JSON lines)")
            print(f"    +-- {self.csv_path.name}   (Spreadsheet/analysis ready)")
            print(f"    +-- {self.summary_path.name} (Run summary)\n")


def main():
    parser = argparse.ArgumentParser(description="VirTra Multicolor GNG LSL Stream Explorer and Recorder")
    parser.add_argument("--name", default=STREAM_NAME, help=f"Stream name to listen for (default: {STREAM_NAME})")
    parser.add_argument("--output-dir", default=None, help="Custom directory path to save recordings")
    parser.add_argument("--explore-only", action="store_true", help="Only view and explore the stream live without saving to disk")
    parser.add_argument("--no-wait", action="store_true", help="Exit immediately if stream is not currently present")
    args = parser.parse_args()

    recorder = MulticolorGngRecorder(
        output_dir=args.output_dir,
        verbose=True,
        stream_name=args.name,
        record_files=not args.explore_only,
    )

    try:
        info = recorder.discover_stream(wait_continuous=not args.no_wait)
        recorder.listen_and_record(info)
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Aborted by user.{RESET}")
        recorder.close()
    except RuntimeError as e:
        print(f"\n{RED}Error: {e}{RESET}")
        recorder.close()
        sys.exit(1)


if __name__ == "__main__":
    main()
