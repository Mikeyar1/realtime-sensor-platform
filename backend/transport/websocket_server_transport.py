"""
websocket_server_transport.py — WebSocket server and asynchronous client transport.

Responsibilities:
  - Manage WebSocket client connections and per-client stream subscriptions.
  - Deserialize incoming JSON messages and route them to SessionLifecycleCoordinator.
  - Serialize and broadcast JSON messages to single clients or all connected clients.
  - Fan out high-throughput LSL stream samples to subscribed clients thread-safely.
"""

import asyncio
import json
import threading
from typing import Callable, Optional, Set
import websockets

try:
    from config import HOST, PORT
except ImportError:
    HOST = "0.0.0.0"
    PORT = 8500


class WebSocketServerTransport:
    """
    WebSocket server transport layer.
    Owns the active connection pool, per-client subscription sets, and network fan-out.
    Delegates high-level message handling and session state to the coordinator service.
    """

    def __init__(
        self,
        host: str = HOST,
        port: int = PORT,
        session_coordinator = None,
    ):
        self.host: str = host
        self.port: int = port
        self._coordinator = session_coordinator
        self._clients: dict = {}  # websocket -> set of subscribed stream IDs
        self._lock: threading.Lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._server = None

    def set_coordinator(self, session_coordinator):
        """Bind or update the session coordinator service."""
        self._coordinator = session_coordinator

    async def start(self):
        """Start the WebSocket server listening loop."""
        self._loop = asyncio.get_running_loop()
        print(f"[WebSocketServerTransport] Listening on ws://{self.host}:{self.port}")
        self._server = await websockets.serve(self._handle_client, self.host, self.port)

    async def stop(self):
        """Close all connections and stop the server."""
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        with self._lock:
            clients = list(self._clients.keys())
        for ws in clients:
            try:
                await ws.close()
            except Exception:
                pass
        print("[WebSocketServerTransport] Stopped.")

    # ── Client connection lifecycle ──────────────────────────────────────────

    async def _handle_client(self, websocket):
        addr = websocket.remote_address
        print(f"[WebSocketServerTransport] Client connected: {addr}")
        with self._lock:
            self._clients[websocket] = set()

        if self._coordinator:
            await self._coordinator.on_client_connect(websocket)

        try:
            async for raw in websocket:
                await self._handle_message(websocket, raw)
        except websockets.exceptions.ConnectionClosedError:
            pass
        finally:
            with self._lock:
                self._clients.pop(websocket, None)
            print(f"[WebSocketServerTransport] Client disconnected: {addr}")

    # ── Message dispatch ──────────────────────────────────────────────────────

    async def _handle_message(self, websocket, raw: str):
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type", "")

        # Subscription management stays in the transport layer
        if msg_type == "streams.subscribe":
            stream_ids = set(msg.get("stream_ids", []))
            with self._lock:
                if websocket in self._clients:
                    self._clients[websocket].update(stream_ids)
            print(f"[WebSocketServerTransport] {websocket.remote_address} subscribed to {len(stream_ids)} stream(s): {stream_ids}")
            await self.send(websocket, {
                "type": "streams.subscribed",
                "stream_ids": list(stream_ids),
            })
            return

        if msg_type == "streams.unsubscribe":
            stream_ids = set(msg.get("stream_ids", []))
            with self._lock:
                if websocket in self._clients:
                    self._clients[websocket].difference_update(stream_ids)
            return

        # Forward all application and session commands to the coordinator
        if self._coordinator:
            await self._coordinator.handle_message(websocket, msg)

    # ── LSL sample routing ────────────────────────────────────────────────────

    def on_sample(self, stream_name: str, stream_type: str, timestamp: float, data: list,
                  *, outlet_uid: str = "", source_id: str = ""):
        """
        Called thread-safely from the LSL inlet worker thread.
        Notifies coordinator for real-time processing and fans out samples to subscribers.
        """
        # Forward to coordinator first (for scenario & biosignal processing)
        outcome = None
        if self._coordinator is not None:
            outcome = self._coordinator.on_sample(
                stream_name, stream_type, timestamp, data,
                outlet_uid=outlet_uid, source_id=source_id)

        if self._loop is None:
            return outcome

        # Fan out immediately to subscribed clients
        elapsed_s = self._coordinator.get_elapsed_s(timestamp) if self._coordinator else None
        self._loop.call_soon_threadsafe(
            asyncio.ensure_future,
            self._fanout_sample(stream_name, timestamp, elapsed_s, data),
        )
        return outcome

    async def _fanout_sample(
        self,
        stream_name: str,
        timestamp: float,
        elapsed_s,
        data: list,
    ):
        """Send a single LSL sample to every client subscribed to stream_name."""
        payload = json.dumps({
            "type":      "stream.sample",
            "stream_id": stream_name,
            "timestamp": timestamp,
            "elapsed_s": elapsed_s,
            "data":      data,
        })
        with self._lock:
            targets = [ws for ws, subs in self._clients.items() if stream_name in subs]
        for ws in targets:
            try:
                await ws.send(payload)
            except Exception:
                pass

    def on_catalog_changed(self, catalog: list):
        """Called thread-safely from the LSL inlet worker thread when streams appear/disappear."""
        if self._loop is None or self._coordinator is None:
            return
        self._coordinator.on_catalog_changed(catalog)

    # ── Transport send/broadcast primitives ───────────────────────────────────

    async def send(self, websocket, data: dict):
        """Send a JSON payload to a single WebSocket client."""
        try:
            await websocket.send(json.dumps(data))
        except Exception:
            pass

    async def broadcast(self, data: dict):
        """Broadcast a JSON payload to all connected WebSocket clients."""
        payload = json.dumps(data)
        with self._lock:
            clients = list(self._clients.keys())
        for ws in clients:
            try:
                await ws.send(payload)
            except Exception:
                pass

