"""
main.py — LabReview Backend application entry point.

Initializes the LabStreamingLayer ingestion layer, session lifecycle coordinator,
and WebSocket server transport, and manages graceful startup and shutdown.

Run:
    uv run python main.py
"""

import asyncio
import os
import signal
import sys

# Ensure Backend package root is in sys.path
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from config import HOST, PORT, LSL_DISCOVERY_INTERVAL, LSL_PULL_TIMEOUT
from lsl.lsl_stream_inlet_manager import LslStreamInletManager
from transport.websocket_server_transport import WebSocketServerTransport
from services.session_lifecycle_coordinator import SessionLifecycleCoordinator


class LabReviewBackendServer:
    """
    Root server application that orchestrates ingestion, transport, and session logic.
    """

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self._loop: asyncio.AbstractEventLoop | None = None

        # 1. Initialize WebSocket transport
        self.transport = WebSocketServerTransport(host=self.host, port=self.port)

        # 2. Initialize LSL hardware inlet manager
        self.inlet_manager = LslStreamInletManager(
            on_sample=self.transport.on_sample,
            on_catalog_changed=self.transport.on_catalog_changed,
            discovery_interval=LSL_DISCOVERY_INTERVAL,
            pull_timeout=LSL_PULL_TIMEOUT,
        )

        self.coordinator: SessionLifecycleCoordinator | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self):
        """Asynchronous server startup."""
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        # 3. Initialize session coordinator with transport callbacks
        self.coordinator = SessionLifecycleCoordinator(
            inlet_manager=self.inlet_manager,
            broadcast_fn=self.transport.broadcast,
            send_fn=self.transport.send,
            loop=self._loop,
        )
        self.transport.set_coordinator(self.coordinator)

        # 4. Start LSL stream discovery thread
        self.inlet_manager.start()

        # 5. Start WebSocket listener
        await self.transport.start()

        # Setup OS signal handlers for graceful shutdown (Unix only)
        if sys.platform != "win32":
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    self._loop.add_signal_handler(sig, self._request_shutdown)
                except NotImplementedError:
                    pass

        print(f"[LabReviewBackendServer] Running on ws://{self.host}:{self.port} — Press Ctrl+C to stop.")
        await self._stop_event.wait()

    def _request_shutdown(self):
        print("\n[LabReviewBackendServer] Shutdown signal received...")
        if self._stop_event and not self._stop_event.is_set():
            self._stop_event.set()

    async def stop(self):
        """Clean shutdown of all components."""
        print("[LabReviewBackendServer] Stopping services...")
        self.inlet_manager.stop()
        await self.transport.stop()
        print("[LabReviewBackendServer] Shutdown complete.")

    def run(self):
        """Synchronous wrapper to run the event loop."""
        try:
            asyncio.run(self._run_async())
        except KeyboardInterrupt:
            print("\n[LabReviewBackendServer] Stopped by user.")

    async def _run_async(self):
        try:
            await self.start()
        finally:
            await self.stop()


def main():
    server = LabReviewBackendServer()
    server.run()


if __name__ == "__main__":
    main()
