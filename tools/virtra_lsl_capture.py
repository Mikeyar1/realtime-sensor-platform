"""Capture and replay raw LSL samples without passing them through LabReview scoring.

The JSONL file retains every channel value, stream identity, LSL timestamp, and
sample arrival offset. Replay publishes the same channel values on new outlets
using the recorded arrival gaps and the replay machine's LSL clock.
"""

from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "labreview.raw_lsl.v1"
EVENT_TYPES = {"VirTraEvents", "VirTra Events"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_selected(info, all_streams: bool) -> bool:
    return all_streams or info.name() == "V_300" or info.type() in EVENT_TYPES


def _channels_from_xml(xml: str) -> list[dict[str, str]]:
    try:
        nodes = ET.fromstring(xml).findall(".//desc/channels/channel")
    except ET.ParseError:
        return []
    return [{key: node.findtext(key, "") for key in ("label", "unit", "type")}
            for node in nodes]


def stream_metadata(info) -> dict:
    xml = info.as_xml()
    return {
        "name": info.name(), "type": info.type(), "source_id": info.source_id(),
        "channel_count": info.channel_count(), "nominal_srate": info.nominal_srate(),
        "channel_format": info.channel_format(), "channels": _channels_from_xml(xml),
        "info_xml": xml,
    }


def write_row(handle, row: dict) -> None:
    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    handle.flush()


class InletReader(threading.Thread):
    def __init__(self, info, stream_id: str, rows: queue.Queue, stopped: threading.Event):
        super().__init__(daemon=True)
        self.stream_id, self.rows, self.stopped = stream_id, rows, stopped
        self.info = info
        self.inlet = None

    def run(self) -> None:
        from pylsl import StreamInlet

        try:
            self.inlet = StreamInlet(self.info)
            while not self.stopped.is_set():
                sample, timestamp = self.inlet.pull_sample(timeout=0.2)
                if sample is not None:
                    self.rows.put((time.monotonic_ns(), self.stream_id, timestamp, sample))
        except Exception as exc:
            print(f"Inlet {self.stream_id} failed: {exc}", file=sys.stderr)
        finally:
            if self.inlet is not None:
                self.inlet.close_stream()


def record(path: Path, duration: float, all_streams: bool) -> int:
    from pylsl import resolve_streams

    path.parent.mkdir(parents=True, exist_ok=True)
    rows: queue.Queue = queue.Queue()
    stopped = threading.Event()
    readers: dict[str, InletReader] = {}
    start_ns = time.monotonic_ns()
    deadline = time.monotonic() + duration if duration > 0 else None
    next_discovery = 0.0
    samples = 0
    last_elapsed = 0.0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        write_row(handle, {"format": FORMAT, "recorded_at_utc": utc_now()})
        print(f"Recording to {path}. Press Ctrl+C to stop.")
        try:
            while deadline is None or time.monotonic() < deadline:
                if time.monotonic() >= next_discovery:
                    for info in resolve_streams(wait_time=0.25):
                        if not is_selected(info, all_streams):
                            continue
                        stream_id = info.uid() or f"{info.name()}|{info.type()}|{info.source_id()}"
                        if stream_id in readers:
                            continue
                        write_row(handle, {"kind": "stream", "stream_id": stream_id,
                                           "info": stream_metadata(info)})
                        reader = InletReader(info, stream_id, rows, stopped)
                        readers[stream_id] = reader
                        reader.start()
                        print(f"  + {info.name()} ({info.type()})")
                    next_discovery = time.monotonic() + 2.0
                try:
                    arrival_ns, stream_id, timestamp, sample = rows.get(timeout=0.05)
                except queue.Empty:
                    continue
                last_elapsed = max(last_elapsed, (arrival_ns - start_ns) / 1e9)
                write_row(handle, {"kind": "sample", "stream_id": stream_id,
                                   "elapsed_s": round(last_elapsed, 9),
                                   "lsl_timestamp": timestamp, "channels": list(sample)})
                samples += 1
        except KeyboardInterrupt:
            pass
        finally:
            stopped.set()
            for reader in readers.values():
                reader.join(timeout=1.0)
            while not rows.empty():
                arrival_ns, stream_id, timestamp, sample = rows.get_nowait()
                last_elapsed = max(last_elapsed, (arrival_ns - start_ns) / 1e9)
                write_row(handle, {"kind": "sample", "stream_id": stream_id,
                                   "elapsed_s": round(last_elapsed, 9),
                                   "lsl_timestamp": timestamp, "channels": list(sample)})
                samples += 1
    print(f"Saved {samples} raw samples from {len(readers)} stream(s).")
    return 0 if samples else 1


def load_capture(path: Path) -> tuple[dict[str, dict], list[dict]]:
    streams: dict[str, dict] = {}
    samples: list[dict] = []
    with path.open("r", encoding="utf-8-sig") as handle:
        first = json.loads(next(handle))
        if first.get("format") != FORMAT:
            raise ValueError(f"Expected {FORMAT} capture")
        for line_number, line in enumerate(handle, 2):
            if not line.strip():
                continue
            row = json.loads(line)
            stream_id = row.get("stream_id")
            if row.get("kind") == "stream":
                if stream_id in streams:
                    raise ValueError(f"Duplicate stream_id on line {line_number}")
                info = row.get("info")
                if not isinstance(info, dict) or not all(info.get(k) is not None for k in
                                                      ("name", "type", "channel_count", "channel_format")):
                    raise ValueError(f"Invalid stream on line {line_number}")
                streams[stream_id] = info
            elif row.get("kind") == "sample":
                info = streams.get(stream_id)
                values = row.get("channels")
                elapsed = row.get("elapsed_s")
                if (info is None or not isinstance(values, list) or
                        len(values) != info["channel_count"] or
                        isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or
                        not math.isfinite(elapsed) or elapsed < 0):
                    raise ValueError(f"Invalid sample on line {line_number}")
                samples.append(row)
            else:
                raise ValueError(f"Unknown row on line {line_number}")
    if not samples:
        raise ValueError("Capture contains no samples")
    if any(b["elapsed_s"] < a["elapsed_s"] for a, b in zip(samples, samples[1:])):
        raise ValueError("Sample arrival offsets are not ordered")
    return streams, samples


def _outlet(info: dict):
    from pylsl import StreamInfo, StreamOutlet

    stream = StreamInfo(info["name"], info["type"], info["channel_count"],
                        info["nominal_srate"], info["channel_format"],
                        info.get("source_id", ""))
    if info.get("channels"):
        channel_nodes = stream.desc().append_child("channels")
        for channel in info["channels"]:
            node = channel_nodes.append_child("channel")
            for key in ("label", "unit", "type"):
                node.append_child_value(key, channel.get(key, ""))
    return StreamOutlet(stream, chunk_size=1)


def _replay_outlets(streams: dict[str, dict]) -> dict[str, object]:
    """Coalesce restarted outlets because LabReview subscribes by stream name."""
    by_name: dict[str, tuple[dict, object]] = {}
    by_id: dict[str, object] = {}
    identity_fields = ("type", "source_id", "channel_count", "nominal_srate",
                       "channel_format")
    for stream_id, info in streams.items():
        name = info["name"]
        if name in by_name:
            original, outlet = by_name[name]
            if any(original.get(field) != info.get(field) for field in identity_fields):
                raise ValueError(f"Captured stream name {name!r} has incompatible outlet metadata")
        else:
            outlet = _outlet(info)
            by_name[name] = (info, outlet)
        by_id[stream_id] = outlet
    return by_id


def _drop_mirrored_samples(streams: dict[str, dict], samples: list[dict]) -> tuple[list[dict], int]:
    """Ignore copies emitted by separate instances of the same named outlet."""
    last_seen: dict[tuple[str, str, str], tuple[str, float]] = {}
    unique = []
    duplicate_count = 0
    for row in samples:
        info = streams[row["stream_id"]]
        key = (info["name"], info.get("source_id", ""),
               json.dumps(row["channels"], ensure_ascii=False, separators=(",", ":")))
        previous = last_seen.get(key)
        if (previous and previous[0] != row["stream_id"] and
                row["elapsed_s"] - previous[1] <= 0.01):
            duplicate_count += 1
            continue
        last_seen[key] = (row["stream_id"], row["elapsed_s"])
        unique.append(row)
    return unique, duplicate_count


def replay(path: Path, speed: float, start_delay: float, from_seconds: float = 0) -> int:
    from pylsl import local_clock

    streams, samples = load_capture(path)
    samples, mirrored = _drop_mirrored_samples(streams, samples)
    samples = [row for row in samples if row["elapsed_s"] >= from_seconds]
    if not samples:
        raise ValueError("No samples remain after --from-seconds")
    outlets = _replay_outlets(streams)
    print(f"Published {len({id(outlet) for outlet in outlets.values()})} outlet(s) "
          f"for {len(streams)} captured stream instance(s); "
          f"suppressed {mirrored} mirrored sample(s); starting in {start_delay:g} s.")
    time.sleep(start_delay)
    start = time.monotonic()
    first_offset = samples[0]["elapsed_s"]
    try:
        for row in samples:
            due = start + (row["elapsed_s"] - first_offset) / speed
            remaining = due - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            outlets[row["stream_id"]].push_sample(row["channels"], timestamp=local_clock())
    except KeyboardInterrupt:
        print("Replay stopped.")
        return 130
    print(f"Replayed {len(samples)} samples.")
    return 0


def export_custom(source: Path, output: Path, stream_id: str | None) -> int:
    """Write a single custom stream in the existing generator's editable JSONL format."""
    streams, samples = load_capture(source)
    eligible = [key for key, info in streams.items()
                if info["type"] == "VirTra Events" and info["channel_count"] == 4]
    if stream_id is None:
        if len(eligible) != 1:
            raise ValueError(f"Expected one custom stream, found {len(eligible)}; use --stream-id")
        stream_id = eligible[0]
    if stream_id not in eligible:
        raise ValueError("Selected stream is not a four-channel VirTra Events stream")
    info = streams[stream_id]
    if not info.get("source_id"):
        raise ValueError("The generator requires a source_id for the custom stream")
    selected = [row for row in samples if row["stream_id"] == stream_id]
    if not selected:
        raise ValueError("Selected stream has no samples")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as handle:
        write_row(handle, {"stream": {"name": info["name"], "type": info["type"],
                                      "source_id": info.get("source_id", "")}})
        first_offset = selected[0]["elapsed_s"]
        for row in selected:
            name, description, code, raw_json = row["channels"]
            write_row(handle, {"relative_time": round(row["elapsed_s"] - first_offset, 6),
                               "event_name": name, "event_description": description,
                               "event_code": code, "fields": json.loads(raw_json)})
    print(f"Exported {len(selected)} custom samples to {output}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("record", help="Record raw LSL samples")
    capture.add_argument("output", type=Path)
    capture.add_argument("--duration", type=float, default=0,
                         help="Seconds to capture; 0 runs until Ctrl+C")
    capture.add_argument("--all-streams", action="store_true",
                         help="Include all discovered LSL streams, including physiology")
    playback = commands.add_parser("replay", help="Republish recorded streams")
    playback.add_argument("capture", type=Path)
    playback.add_argument("--speed", type=float, default=1)
    playback.add_argument("--start-delay", type=float, default=3)
    playback.add_argument("--from-seconds", type=float, default=0,
                          help="Skip capture time before this offset in seconds")
    export = commands.add_parser("export-custom", help="Export for the existing generator GUI")
    export.add_argument("capture", type=Path)
    export.add_argument("output", type=Path)
    export.add_argument("--stream-id")
    args = parser.parse_args(argv)
    try:
        if args.command == "record":
            if args.duration < 0:
                raise ValueError("--duration must be nonnegative")
            return record(args.output, args.duration, args.all_streams)
        if args.command == "replay":
            if args.speed <= 0 or args.start_delay < 0 or args.from_seconds < 0:
                raise ValueError("--speed must be positive; delay and offset nonnegative")
            return replay(args.capture, args.speed, args.start_delay, args.from_seconds)
        return export_custom(args.capture, args.output, args.stream_id)
    except (OSError, ValueError, ImportError, StopIteration, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
