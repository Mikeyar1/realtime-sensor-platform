"""Single entry point for stream decoding and physiology projections."""

from __future__ import annotations

from .session import PhysiologySession
from .source_registry import SignalSourceRegistry
from .windows import RoundPhysiologyCapture


class PhysiologyService:
    def __init__(self, broadcast_fn, loop, registry: SignalSourceRegistry | None = None):
        self.registry = registry or SignalSourceRegistry()
        self.session = PhysiologySession(broadcast_fn, loop)
        self.rounds = RoundPhysiologyCapture()

    def ingest(self, stream_name: str, stream_type: str, timestamp: float, data: list,
               *, source_id: str = "", outlet_uid: str = "") -> tuple[bool, dict | None]:
        spec = self.registry.match(stream_name, stream_type, source_id)
        if spec is None:
            return False, None
        observation = self.registry.decode(spec, stream_name, stream_type, timestamp, data,
                                           source_id=source_id, outlet_uid=outlet_uid)
        if observation is None:
            return True, None
        self.session.ingest(observation)
        return True, self.rounds.ingest(observation)
