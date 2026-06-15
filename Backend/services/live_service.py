"""
live_service.py — Live mode handlers.
Handles live.activate / live.deactivate. No recording or disk writes.
"""

import asyncio
import os
import sys
from typing import Callable

HERE    = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from inlet_manager import InletManager


class LiveService:
    """Handles live-mode activation and deactivation."""

    def __init__(
        self,
        inlets: InletManager,
        broadcast_fn: Callable,
        send_fn: Callable,
        get_state_payload_fn: Callable,
        error_fn: Callable,
        on_live_activate_fn: Callable,
        on_live_deactivate_fn: Callable,
        loop: asyncio.AbstractEventLoop,
    ):
        self._inlets             = inlets
        self._broadcast          = broadcast_fn
        self._send               = send_fn
        self._state_payload      = get_state_payload_fn
        self._error              = error_fn
        self._on_live_activate   = on_live_activate_fn
        self._on_live_deactivate = on_live_deactivate_fn
        self._loop               = loop

    # Sample hooks

    def on_sample(self, stream_name: str, timestamp: float, data: list):
        pass

    def on_catalog_changed(self, catalog: list):
        pass

    # Live mode

    async def handle_live_activate(self, websocket):
        await self._on_live_activate()
        await self._broadcast(self._state_payload())

    async def handle_live_deactivate(self, websocket):
        await self._on_live_deactivate()
        await self._broadcast(self._state_payload())
