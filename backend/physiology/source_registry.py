"""Validated stream match rules and reusable numeric channel decoder."""

from __future__ import annotations

import math
import os
import re
import sys
import time
import tomllib
from pathlib import Path

from .observation import SignalObservation


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def default_path() -> Path:
    override = os.environ.get("LABREVIEW_PHYSIOLOGY_SOURCES_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "physiology" / "sources.toml"
    return Path(__file__).with_name("sources.toml")


class SignalSourceRegistry:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_path()
        with self.path.open("rb") as handle:
            config = tomllib.load(handle)
        if config.get("schema_version") != 1:
            raise ValueError("physiology sources require schema_version = 1")
        self.sources = config.get("sources", [])
        ids: set[str] = set()
        for spec in self.sources:
            name = spec.get("id")
            if not isinstance(name, str) or not name or name in ids:
                raise ValueError("physiology source IDs must be unique")
            ids.add(name)
            if spec.get("decoder") != "numeric_channels":
                raise ValueError(f"unknown physiology decoder for {name}")
            if spec.get("signal") not in {"hr", "ppi", "gaze"}:
                raise ValueError(f"unsupported physiology signal for {name}")
            channels = spec.get("channels", [])
            n = len(channels)
            if n != (2 if spec["signal"] == "gaze" else 1) or any(
                    not isinstance(index, int) or isinstance(index, bool) or index < 0
                    for index in channels):
                raise ValueError(f"invalid channel layout for {name}")
            if any(len(spec.get(key, [])) != n for key in ("units", "minimums", "maximums")):
                raise ValueError(f"incomplete unit or range declaration for {name}")
            if any(not isinstance(low, (int, float)) or not isinstance(high, (int, float))
                   or low >= high for low, high in zip(spec["minimums"], spec["maximums"])):
                raise ValueError(f"invalid range for {name}")
            if not any(spec.get(key) for key in ("types", "names", "name_suffixes", "name_contains")):
                raise ValueError(f"source {name} has no stream match rule")
            for key in ("types", "names", "name_suffixes", "name_contains", "source_ids"):
                if key in spec and (not isinstance(spec[key], list) or
                                    any(not isinstance(v, str) or not v for v in spec[key])):
                    raise ValueError(f"invalid {key} rule for {name}")
        self._match_order = sorted(self.sources, key=lambda spec: bool(spec.get("source_ids")),
                                   reverse=True)

    def match(self, stream_name: str, stream_type: str, source_id: str = "") -> dict | None:
        name, kind = _normal(stream_name), _normal(stream_type)
        # A source-specific layout overrides a generic stream-type layout.
        for spec in self._match_order:
            if spec.get("source_ids") and source_id not in spec["source_ids"]:
                continue
            if (kind in {_normal(item) for item in spec.get("types", [])} or
                    name in {_normal(item) for item in spec.get("names", [])} or
                    any(name.endswith(_normal(item)) for item in spec.get("name_suffixes", [])) or
                    any(_normal(item) in name for item in spec.get("name_contains", []))):
                return spec
        return None

    def decode(self, spec: dict, stream_name: str, stream_type: str,
               timestamp: float, data: list, *, source_id: str = "",
               outlet_uid: str = "", received_at: float | None = None) -> SignalObservation | None:
        try:
            lsl_timestamp = float(timestamp)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(lsl_timestamp):
            return None
        values: list[float | None] = []
        for index, lower, upper in zip(spec["channels"], spec["minimums"], spec["maximums"]):
            try:
                value = float(data[index])
                values.append(value if math.isfinite(value) and lower < value < upper else None)
            except (IndexError, TypeError, ValueError):
                values.append(None)
        if all(value is None for value in values):
            return None
        source_key = source_id or outlet_uid or f"{stream_name}:{stream_type}"
        return SignalObservation(
            signal=spec["signal"], source_key=source_key,
            lsl_timestamp=lsl_timestamp, received_at=time.time() if received_at is None else received_at,
            value=values[0], value_2=values[1] if len(values) > 1 else None,
            unit=spec["units"][0])
