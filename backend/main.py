"""
main.py — LabReview Backend application entry point.

Initializes the LabStreamingLayer ingestion layer, session lifecycle coordinator,
and WebSocket server transport, and manages graceful startup and shutdown.

Also starts an embedded HTTP server for the frontend and opens the browser
automatically on launch (skipped gracefully if the port is already in use).

Run:
    uv run python main.py
"""

import asyncio
import http.server
import os
import signal
import socket
import sys
import threading
import webbrowser
from pathlib import Path

# Ensure Backend package root is in sys.path
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from config import HOST, PORT, LSL_DISCOVERY_INTERVAL, LSL_PULL_TIMEOUT, DATABASE_PATH, FRONTEND_PORT, OPEN_BROWSER
from lsl.lsl_stream_inlet_manager import LslStreamInletManager
from audit.event_store import EventStore
from transport.websocket_server_transport import WebSocketServerTransport
from services.session_lifecycle_coordinator import SessionLifecycleCoordinator
from services.database import init_db


# ---------------------------------------------------------------------------
# Frontend HTTP server helpers
# ---------------------------------------------------------------------------

def _find_frontend_dir() -> Path:
    """Locate the frontend/ folder in both dev and frozen (PyInstaller) contexts."""
    if getattr(sys, "frozen", False):
        # PyInstaller one-folder bundle: everything lives next to the .exe
        return Path(sys._MEIPASS) / "frontend"
    # Dev mode: backend/ is one level below the project root
    return Path(__file__).parent.parent / "frontend"


def _start_frontend_server(port: int) -> bool:
    """
    Start a threaded HTTP server serving the frontend/ directory on *port*.

    Returns True if the server was started by this process, False if the port
    was already occupied (i.e. an external dev server is already running).
    """
    frontend_dir = _find_frontend_dir()
    if not frontend_dir.exists():
        print(f"[FrontendServer] Directory not found: {frontend_dir} — skipping.")
        return False

    # Check whether the port is already bound by something else
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex(("localhost", port)) == 0:
            print(
                f"[FrontendServer] Port {port} already in use — "
                "skipping embedded server (using existing one)."
            )
            return False

    _dir = str(frontend_dir)  # capture for closure

    class _Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=_dir, **kwargs)

        def log_message(self, fmt, *args):  # silence per-request logs
            pass

    httpd = http.server.ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True, name="FrontendHTTP")
    thread.start()
    print(f"[FrontendServer] Serving {frontend_dir} on http://localhost:{port}")
    return True



class LabReviewBackendServer:
    """
    Root server application that orchestrates ingestion, transport, and session logic.
    """

    def __init__(self, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self._loop: asyncio.AbstractEventLoop | None = None

        # 0. Initialize SQLite database (idempotent — creates tables if needed)
        init_db(DATABASE_PATH)
        self.event_store = EventStore(
            DATABASE_PATH,
            provenance=os.environ.get("LABREVIEW_CAPTURE_PROVENANCE", "lsl_ingest"))

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

        # 3-pre. Start embedded frontend HTTP server (daemon thread).
        # Returns False if an external server is already on the port — in that
        # case we skip auto-opening the browser so we don't fight with the
        # existing dev server.
        self._owns_frontend_server: bool = _start_frontend_server(FRONTEND_PORT)

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
            event_store=self.event_store,
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

        # Open the browser once the WebSocket server is confirmed ready.
        # - Frozen (installed exe): always open — the user expects the dashboard to appear.
        # - Dev mode: only open if we started the embedded server ourselves, to avoid
        #   double-opening when an external dev server is already running.
        is_frozen = getattr(sys, "frozen", False)
        if OPEN_BROWSER and (is_frozen or self._owns_frontend_server):
            url = f"http://localhost:{FRONTEND_PORT}"
            print(f"[LabReviewBackendServer] Opening browser at {url}")
            webbrowser.open(url)

        await self._stop_event.wait()

    def _request_shutdown(self):
        print("\n[LabReviewBackendServer] Shutdown signal received...")
        if self._stop_event and not self._stop_event.is_set():
            self._stop_event.set()

    async def stop(self):
        """Clean shutdown of all components."""
        print("[LabReviewBackendServer] Stopping services...")
        try:
            self.inlet_manager.stop()
            await self.transport.stop()
        finally:
            self.event_store.close()
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
