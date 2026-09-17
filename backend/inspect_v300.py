"""
inspect_v300.py — LSL stream inspector for V_300 / VirTraEvents.

Discovers the V_300 stream on the network, prints its full metadata (including raw XML),
and visualizes live data channel-by-channel. Also includes a --demo flag to immediately
visualize real recorded V_300 events without needing the simulator to be active.

Usage:
    cd backend
    uv run python inspect_v300.py          # Listen to live V_300 LSL stream
    uv run python inspect_v300.py --demo   # Show real recorded events channel-by-channel
"""

import sys
import os
import csv
import time
import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
from pathlib import Path
import pylsl

FORMAT_MAP = {
    1: "float32",
    2: "double64",
    3: "string",
    4: "int32",
    5: "int16",
    6: "int8",
    7: "int64",
    pylsl.cf_float32: "float32",
    pylsl.cf_double64: "double64",
    pylsl.cf_string: "string",
    pylsl.cf_int32: "int32",
    pylsl.cf_int16: "int16",
    pylsl.cf_int8: "int8",
}

CHANNEL_MEANINGS = [
    ("Channel 0", "event_type",  "Primary event title (e.g. Scenario Started, Shot Fired, Target Activated)"),
    ("Channel 1", "description", "Detailed human-readable message (e.g. Playing Scenario..., Target Ivan Hit)"),
    ("Channel 2", "category",    "State/action classification code (e.g. 'SS - Scenario Started', 'TFW')"),
    ("Channel 3", "metadata",    "JSON key-value payload (e.g. ShotID, Weapon, Score, HitZones, SessionID)"),
]


def pretty_xml(xml_str: str) -> str:
    try:
        parsed = minidom.parseString(xml_str)
        return parsed.toprettyxml(indent="  ")
    except Exception:
        return xml_str


def parse_metadata_json(raw_str: str):
    """Attempt to parse VirTra's JSON list format: [["Key", "Val"], ...]."""
    if not raw_str:
        return None
    try:
        parsed = json.loads(raw_str)
        if isinstance(parsed, list):
            if all(isinstance(item, list) and len(item) == 2 for item in parsed):
                return dict(parsed)
        return parsed
    except Exception:
        return None


def print_sample_breakdown(sample_idx: int, arrival_str: str, timestamp: float, sample: list):
    """Prints a single sample with clear channel labels and parsed metadata."""
    print(f"\n[Event Sample #{sample_idx:04d}]  Arrival: {arrival_str} | LSL Timestamp: {timestamp:.4f}")
    
    for i, val in enumerate(sample):
        ch_name = CHANNEL_MEANINGS[i][1] if i < len(CHANNEL_MEANINGS) else f"channel_{i}"
        val_str = str(val).strip()

        if i == 3 or (isinstance(val_str, str) and val_str.startswith("[[")):
            parsed_dict = parse_metadata_json(val_str)
            if parsed_dict and isinstance(parsed_dict, dict):
                print(f"  ch[{i}] [{ch_name}]:")
                for k, v in parsed_dict.items():
                    print(f"         ├─ {k}: {v}")
                continue

        print(f"  ch[{i}] [{ch_name}]: {val_str if val_str else '(empty)'}")

    print("─" * 75)


def show_demo_data():
    """Shows real sample events from actual session data files."""
    csv_path = Path(__file__).parent.parent / "data" / "L2GoNoGo" / "V_300_VirTraEvents_at_IVR-MASTER_auto_2026_03_25-12_12_03.csv"
    
    print("\n" + "═" * 75)
    print(" DEMO MODE: Real Recorded V_300 Event Samples")
    print("═" * 75)
    print("  V_300 broadcasts 4 channels per sample packet:")
    for ch_num, label, desc in CHANNEL_MEANINGS:
        print(f"    • {ch_num} ({label:<12}): {desc}")
    print("═" * 75)

    if not csv_path.exists():
        print(f"Could not find sample CSV at {csv_path}")
        return

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        count = 0
        interesting_types = {
            "Scenario Started", "New Target", "Target Activated",
            "Shot Fired", "Shot Hit", "Shot Miss", "Scenario Stopped"
        }
        seen_types = set()

        for row in reader:
            if len(row) < 8:
                continue
            # Row structure in recorded CSV:
            # row[0]: lsl_timestamp
            # row[1]: local_time
            # row[2]: utc_time
            # row[3]: elapsed_time
            # row[4]: event_type (Channel 0)
            # row[5]: description (Channel 1)
            # row[6]: category (Channel 2)
            # row[7]: metadata (Channel 3)
            ev_type = row[4].strip()
            if ev_type in interesting_types and ev_type not in seen_types:
                seen_types.add(ev_type)
                count += 1
                sample_data = [row[4], row[5], row[6], row[7]]
                print_sample_breakdown(count, row[1].split()[-1], float(row[0]), sample_data)

            if len(seen_types) >= len(interesting_types):
                break

    print("\n✓ End of sample preview. Run without '--demo' to stream live events from hardware.")


def extract_channels(info: pylsl.StreamInfo) -> list[dict]:
    channels = []
    try:
        xml_str = info.as_xml()
        root = ET.fromstring(xml_str)
        nodes = root.findall(".//desc/channels/channel")
        for i, ch in enumerate(nodes):
            channels.append({
                "index": i,
                "label": ch.findtext("label") or (CHANNEL_MEANINGS[i][1] if i < len(CHANNEL_MEANINGS) else f"channel_{i}"),
                "unit": ch.findtext("unit") or "",
                "type": ch.findtext("type") or "",
            })
    except Exception:
        pass

    if not channels:
        count = info.channel_count()
        channels = [
            {
                "index": i,
                "label": CHANNEL_MEANINGS[i][1] if i < len(CHANNEL_MEANINGS) else f"channel_{i}",
                "unit": "",
                "type": "string"
            }
            for i in range(count)
        ]

    return channels


def find_target_stream(target_name: str = "V_300", timeout: float = 3.0) -> pylsl.StreamInfo | None:
    print(f"\n[1/3] Searching for LSL stream '{target_name}' (timeout: {timeout}s)...")
    
    streams = pylsl.resolve_byprop("name", target_name, timeout=timeout)
    if streams:
        return streams[0]

    print("      Not found by name. Checking for type='VirTraEvents'...")
    streams = pylsl.resolve_byprop("type", "VirTraEvents", timeout=timeout)
    if streams:
        return streams[0]

    print("      Resolving all active LSL streams on the network...")
    all_streams = pylsl.resolve_streams(wait_time=2.0)
    if not all_streams:
        print("\n  ❌ No LSL streams found on the network.")
        return None

    print(f"\n  Found {len(all_streams)} active stream(s) on the network:")
    print("  " + "─" * 70)
    matched = None
    for s in all_streams:
        s_name = s.name()
        s_type = s.type()
        s_rate = f"{s.nominal_srate():.0f} Hz" if s.nominal_srate() > 0 else "irregular (0 Hz)"
        print(f"  • Name: {s_name:<28} | Type: {s_type:<18} | Channels: {s.channel_count()} | {s_rate}")
        if target_name.lower() in s_name.lower() or "virtra" in s_name.lower() or "virtra" in s_type.lower():
            matched = s

    print("  " + "─" * 70)
    if matched:
        print(f"  ✓ Matched stream: '{matched.name()}' (type: '{matched.type()}')")
        return matched

    return None


def print_metadata(info: pylsl.StreamInfo):
    fmt = FORMAT_MAP.get(info.channel_format(), str(info.channel_format()))
    rate = info.nominal_srate()
    rate_str = f"{rate:.1f} Hz" if rate > 0 else "0.0 Hz (irregular / event-driven)"
    channels = extract_channels(info)

    print("\n" + "═" * 75)
    print(f" METADATA: {info.name()} (Type: {info.type()})")
    print("═" * 75)
    print(f"  Stream Name:        {info.name()}")
    print(f"  Stream Type:        {info.type()}")
    print(f"  Channel Count:      {info.channel_count()}")
    print(f"  Channel Format:     {fmt}")
    print(f"  Nominal Rate:       {rate_str}")
    print(f"  Source ID:          {info.source_id() or '(none)'}")
    print(f"  Hostname:           {info.hostname() or '(local)'}")
    print(f"  Session ID:         {info.session_id() or '(none)'}")
    print(f"  LSL Protocol Ver:   {info.version() / 100:.1f}")

    print("\n  ── Channel Definitions & Roles ──")
    for ch in channels:
        role = CHANNEL_MEANINGS[ch['index']][2] if ch['index'] < len(CHANNEL_MEANINGS) else ""
        print(f"    Channel [{ch['index']}]: '{ch['label']}'  —  {role}")

    print("\n  ── Raw XML Tree (<info>) ──")
    raw_xml = info.as_xml()
    indented = "\n".join("    " + line for line in pretty_xml(raw_xml).splitlines())
    print(indented)
    print("═" * 75)


def stream_live_data(info: pylsl.StreamInfo):
    inlet = pylsl.StreamInlet(info, max_buflen=360, max_chunklen=1)
    
    print("\n[3/3] Connected to stream inlet. Listening for live samples...")
    print("      ⚠️  NOTE: V_300 is an EVENT-DRIVEN stream (nominal rate 0 Hz).")
    print("          It only broadcasts when an action occurs in VirTra")
    print("          (e.g. Scenario Started, Shot Fired, Target Spawned).")
    print("          (Press Ctrl+C to stop at any time)\n")
    print("─" * 75)

    sample_count = 0
    try:
        while True:
            sample, timestamp = inlet.pull_sample(timeout=1.0)
            if sample is None:
                continue

            sample_count += 1
            now_str = time.strftime("%H:%M:%S")
            print_sample_breakdown(sample_count, now_str, timestamp, sample)

    except KeyboardInterrupt:
        print(f"\n\n[Stopped] Captured {sample_count} sample(s).")
    finally:
        inlet.close_stream()


def main():
    if "--demo" in sys.argv or "-d" in sys.argv:
        show_demo_data()
        return

    target_name = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "V_300"
    
    info = find_target_stream(target_name=target_name, timeout=4.0)
    if not info:
        print("\nTip: Run with '--demo' to preview sample events from recorded sessions:")
        print("  uv run python inspect_v300.py --demo\n")
        sys.exit(1)

    print_metadata(info)
    stream_live_data(info)


if __name__ == "__main__":
    main()
