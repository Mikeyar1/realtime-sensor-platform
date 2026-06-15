"""
websocket_bridge.py — Transport layer only.

Responsibilities:
  - Accept / close WebSocket connections
  - Read raw JSON → call session_service.handle_message()
  - Write raw JSON back to individual clients or broadcast to all
  - Route stream.sample messages to subscribed clients (via BatchRouter)
"""

import asyncio
import json
import os
import sys
import threading
import websockets

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from config import HOST, PORT, DB_SCAN_DIR, TOTAL_RECALL_DIR
from inlet_manager import InletManager
from replay_engine import ReplayEngine
from session_service import SessionService
from batch_router import BatchRouter

print(f"[WsBridge] DB scan dir: {DB_SCAN_DIR}")
print(f"[WsBridge] Total Recall dir: {TOTAL_RECALL_DIR}")


class WsBridge:
    """
    Pure WebSocket transport.
    Owns: connections dict, subscription sets, raw send/broadcast.
    Delegates: business logic to SessionService, batching to BatchRouter.
    """

    def __init__(self):
        self._clients: dict = {}          # websocket → set of subscribed stream names
        self._lock    = threading.Lock()
        self._loop:   asyncio.AbstractEventLoop | None = None

        # Engine + inlets
        self._engine = ReplayEngine(
            db_scan_dir      = DB_SCAN_DIR,
            total_recall_dir = TOTAL_RECALL_DIR,
        )
        self._inlets = InletManager(
            on_sample          = self._on_sample,
            on_catalog_changed = self._on_catalog_changed,
        )

        # Batch router
        self._router = BatchRouter(
            clients_lock    = self._lock,
            clients         = self._clients,
            get_service_fn  = lambda: self._service,
        )

        # SessionService wired in _main() once the event loop is running
        self._service: SessionService | None = None

    # Lifecycle
    def run(self):
        asyncio.run(self._main())

    async def _main(self):
        self._loop = asyncio.get_running_loop()

        self._service = SessionService(
            replay_engine = self._engine,
            inlet_manager = self._inlets,
            broadcast_fn  = self._broadcast,
            send_fn       = self._send,
            loop          = self._loop,
            db_scan_dir   = DB_SCAN_DIR,
        )

        self._inlets.start()
        self._router.start(self._loop)

        print(f"[WsBridge] Listening on ws://{HOST}:{PORT}")
        async with websockets.serve(self._handle_client, HOST, PORT):
            await asyncio.Future()

    # Client connection
    async def _handle_client(self, websocket):
        print(f"[WsBridge] Connected: {websocket.remote_address}")
        with self._lock:
            self._clients[websocket] = set()

        await self._service.on_client_connect(websocket)

        try:
            async for raw in websocket:
                await self._handle_message(websocket, raw)
        except websockets.exceptions.ConnectionClosedError:
            pass
        finally:
            with self._lock:
                self._clients.pop(websocket, None)
            print(f"[WsBridge] Disconnected: {websocket.remote_address}")

    # Message dispatch
    async def _handle_message(self, websocket, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        t = msg.get("type", "")

        # Subscription management stays in the bridge (owns the client dict)
        if t == "streams.subscribe":
            stream_ids = set(msg.get("stream_ids", []))
            with self._lock:
                self._clients[websocket].update(stream_ids)
            print(f"[WsBridge] Subscribed {websocket.remote_address}: {stream_ids}")
            await self._send(websocket, {
                "type":       "streams.subscribed",
                "stream_ids": list(stream_ids),
            })
            return

        if t == "streams.unsubscribe":
            stream_ids = set(msg.get("stream_ids", []))
            with self._lock:
                self._clients[websocket].difference_update(stream_ids)
            return

        # Everything else → service
        await self._service.handle_message(websocket, msg)

    # Sample routing
    def _on_sample(self, stream_name: str, timestamp: float, data: list):
        """Called from InletManager thread."""
        if self._loop is None:
            return

        # Routing: high-Hz → buffer, low-Hz → immediate
        self._loop.call_soon_threadsafe(
            self._router.route_sample, stream_name, timestamp, data
        )

    def _on_catalog_changed(self, catalog: list):
        """Called from InletManager thread."""
        if self._loop is None or self._service is None:
            return
        self._router.update_stream_rates(catalog)
        self._service.on_catalog_changed(catalog)

    # Transport helpers
    async def _send(self, websocket, data: dict):
        try:
            await websocket.send(json.dumps(data))
        except Exception:
            pass

    async def _broadcast(self, data: dict):
        payload = json.dumps(data)
        with self._lock:
            clients = list(self._clients.keys())
        for ws in clients:
            try:
                await ws.send(payload)
            except Exception:
                pass


# Entry point
if __name__ == "__main__":
    bridge = WsBridge()
    try:
        bridge.run()
    except KeyboardInterrupt:
        print("\n[WsBridge] Stopped.")
