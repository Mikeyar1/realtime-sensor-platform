"""Read-only inventory of a LabReview raw LSL capture.

Prints event/clock evidence without copying participant names, target IDs, or
raw payloads into the report. A compact lifecycle fixture can be exported for
portable regression tests; it deliberately omits raw identifiers and shots.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path


LIFECYCLE = {
    "Scenario Started", "Scenario Stopped", "Scenario Destroyed", "Scenario Ended",
    "Round Started", "Round Completed", "Round Reset", "Preset Changed",
}
SETTING_KEYS = (
    "active_distance_meters", "active_visibility_duration_seconds",
    "active_spawn_interval_seconds", "active_targets_per_round",
    "active_concurrent_targets",
)


def read_capture(path: Path) -> tuple[dict, list[dict]]:
    streams: dict[str, dict] = {}
    samples: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if number == 1 and row.get("format") != "labreview.raw_lsl.v1":
                raise ValueError("Expected labreview.raw_lsl.v1 capture")
            if row.get("kind") == "stream":
                streams[row["stream_id"]] = row["info"]
            elif row.get("kind") == "sample":
                samples.append(row)
    return streams, samples


def custom_events(streams: dict, samples: list[dict]) -> list[dict]:
    events = []
    for row in samples:
        info = streams.get(row.get("stream_id"), {})
        if info.get("type") != "VirTra Events" or len(row.get("channels", [])) < 4:
            continue
        try:
            payload = json.loads(row["channels"][3])
        except (ValueError, TypeError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        events.append({
            "name": row["channels"][0],
            "payload": payload,
            "lsl": row["lsl_timestamp"],
            "elapsed": row["elapsed_s"],
            "stream_id": row["stream_id"],
            "channels": row["channels"],
        })
    return events


def distinct_events(events: list[dict]) -> tuple[list[dict], int]:
    """Collapse byte-identical samples at one LSL timestamp.

    Distinct outlets can carry the same event. This diagnostic groups those
    copies, but is not a production deduplication policy.
    """
    distinct = []
    duplicates = 0
    seen = set()
    for event in events:
        fingerprint = (event["lsl"], tuple(event["channels"]))
        if fingerprint in seen:
            duplicates += 1
        else:
            distinct.append(event)
            seen.add(fingerprint)
    return distinct, duplicates


def summarize(streams: dict, samples: list[dict]) -> dict:
    events = custom_events(streams, samples)
    distinct, duplicates = distinct_events(events)
    counts = collections.Counter(event["name"] for event in distinct)
    clock_offsets = []
    payload_keys = collections.Counter()
    shot_keys = collections.Counter()
    shot_ids_by_event: dict[str, set[str]] = collections.defaultdict(set)
    shot_missing_id = collections.Counter()
    preset_names = set()
    script_versions = set()
    settings_examples = []
    boundaries = []
    for event in distinct:
        payload = event["payload"]
        payload_keys.update(payload.keys())
        if payload.get("scenario_version"):
            script_versions.add(str(payload["scenario_version"]))
        scenario_time = payload.get("scenario_time_seconds")
        if isinstance(scenario_time, (float, int)):
            clock_offsets.append(event["lsl"] - scenario_time)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        shot = data.get("shot") if isinstance(data.get("shot"), dict) else {}
        if event["name"].startswith("Shot"):
            shot_keys.update(shot.keys())
            shot_id = shot.get("shot_id") or data.get("shot_id")
            if shot_id:
                shot_ids_by_event[event["name"]].add(str(shot_id))
            else:
                shot_missing_id[event["name"]] += 1
        settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
        preset = data.get("preset") or settings.get("preset")
        if isinstance(preset, dict):
            preset = preset.get("name")
        if isinstance(preset, str) and preset:
            preset_names.add(preset)
        active = {key: settings[key] for key in SETTING_KEYS if key in settings}
        if active and active not in settings_examples and len(settings_examples) < 8:
            settings_examples.append(active)
        if event["name"] in LIFECYCLE:
            boundaries.append({
                "name": event["name"],
                "round": payload.get("round"),
                "scenario_time_s": scenario_time,
                "capture_elapsed_s": event["elapsed"],
                "state": payload.get("state"),
            })
    stream_counts = collections.Counter(
        streams.get(row.get("stream_id"), {}).get("type", "unknown") for row in samples
    )
    outlet_groups = collections.Counter(
        (info.get("name"), info.get("type"), info.get("source_id"))
        for info in streams.values()
    )
    native_lifecycle = []
    for row in samples:
        if streams.get(row.get("stream_id"), {}).get("type") != "VirTraEvents":
            continue
        channels = row.get("channels", [])
        if not channels or channels[0] not in {"Scenario Started", "Scenario Stopped"}:
            continue
        try:
            metadata = json.loads(channels[3]) if len(channels) > 3 else []
        except (ValueError, TypeError):
            metadata = []
        metadata = dict(item for item in metadata
                        if isinstance(item, list) and len(item) == 2) if isinstance(metadata, list) else {}
        native_lifecycle.append({
            "name": channels[0], "capture_elapsed_s": row["elapsed_s"],
            "scenario": metadata.get("Scenario"),
            "session_id_present": bool(metadata.get("SessionID")),
        })
    return {
        "streams": dict(sorted(stream_counts.items())),
        "outlet_groups": [
            {"name": name, "type": kind, "outlets": count}
            for (name, kind, _source_id), count in sorted(outlet_groups.items())
        ],
        "custom_raw_samples": len(events),
        "custom_cross_outlet_identical_duplicates": duplicates,
        "custom_distinct_samples": len(distinct),
        "event_counts_distinct": dict(sorted(counts.items())),
        "payload_keys_observed": sorted(payload_keys),
        "shot_keys_observed": sorted(shot_keys),
        "unique_shot_ids_by_event": {
            key: len(value) for key, value in sorted(shot_ids_by_event.items())
        },
        "shot_result_ids_seen_in_fired": {
            key: len(value & shot_ids_by_event.get("Shot Fired", set()))
            for key, value in sorted(shot_ids_by_event.items()) if key != "Shot Fired"
        },
        "shot_events_without_id": dict(sorted(shot_missing_id.items())),
        "observed_preset_names": sorted(preset_names),
        "observed_script_versions": sorted(script_versions),
        "settings_examples": settings_examples,
        "lsl_minus_scenario_time_s": {
            "min": round(min(clock_offsets), 4) if clock_offsets else None,
            "max": round(max(clock_offsets), 4) if clock_offsets else None,
            "median": round(statistics.median(clock_offsets), 4) if clock_offsets else None,
        },
        "lifecycle_events": boundaries,
        "native_lifecycle_events": native_lifecycle,
    }


def lifecycle_fixture(events: list[dict]) -> dict:
    """Sanitized lifecycle projection of the observed sequence, duplicates kept."""
    first_lsl = events[0]["lsl"] if events else 0
    rows = []
    for event in events:
        if event["name"] not in LIFECYCLE:
            continue
        payload = event["payload"]
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        settings = data.get("settings") if isinstance(data.get("settings"), dict) else {}
        preset = data.get("preset") or settings.get("preset")
        if isinstance(preset, dict):
            preset = preset.get("name")
        rows.append({
            "event_name": event["name"],
            "round": payload.get("round", 0),
            "scenario_time_s": payload.get("scenario_time_seconds"),
            "lsl_offset_s": round(event["lsl"] - first_lsl, 6),
            "state": payload.get("state", ""),
            "settings": {key: settings[key] for key in SETTING_KEYS if key in settings},
            "preset": preset if isinstance(preset, str) else None,
        })
    return {
        "format": "labreview.sanitized_lifecycle.v1",
        "provenance": "data/lsl_captures/fitts-pvt-clay.jsonl; lifecycle projection",
        "note": "Captured order and timestamps; no raw participant, target or scenario IDs. Includes duplicate samples.",
        "events": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--fixture", type=Path, help="Write a sanitized lifecycle fixture")
    args = parser.parse_args()
    streams, samples = read_capture(args.capture)
    events = custom_events(streams, samples)
    print(json.dumps(summarize(streams, samples), indent=2))
    if args.fixture:
        args.fixture.parent.mkdir(parents=True, exist_ok=True)
        args.fixture.write_text(json.dumps(lifecycle_fixture(events), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
