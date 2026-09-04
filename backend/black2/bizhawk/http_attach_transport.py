"""HTTP Attach Transport for BizHawk Bridge.

Allows already-running BizHawk instances to attach by running black2_bridge.lua in Lua Console.
"""

import asyncio
import time
import uuid
from typing import Dict, Any, Optional, List
from .transport import BizHawkTransport


class HttpAttachTransport(BizHawkTransport):
    def __init__(self):
        self.session_id: Optional[str] = None
        self.last_heartbeat: float = 0.0
        self.last_frame: int = 0
        self.hello_data: Dict[str, Any] = {}
        self.pending_commands: asyncio.Queue = asyncio.Queue()
        self.pending_futures: Dict[str, asyncio.Future] = {}
        self.running: bool = False
        self._connected: bool = False

    async def connect(self) -> bool:
        self.running = True
        return True

    async def disconnect(self) -> None:
        self.running = False
        self._connected = False
        self.session_id = None
        for f in self.pending_futures.values():
            if not f.done():
                f.cancel()
        self.pending_futures.clear()

    def is_connected(self) -> bool:
        # Connected if last heartbeat was within last 3 seconds
        return self._connected and (time.time() - self.last_heartbeat < 4.0)

    def get_transport_type(self) -> str:
        return "http_attach"

    async def handle_hello(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Called when Lua bridge sends initial hello."""
        self.session_id = str(uuid.uuid4())[:8]
        self.last_heartbeat = time.time()
        self.last_frame = data.get("frame", 0)
        self.hello_data = data
        self._connected = True
        return {
            "status": "ok",
            "session_id": self.session_id,
            "message": "Attached to Semantic Runtime backend"
        }

    async def handle_poll(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Called by Lua bridge on each frame poll."""
        self.last_heartbeat = time.time()
        self.last_frame = data.get("frame", self.last_frame)
        self._connected = True

        commands_to_send = []
        # Pop all currently pending commands
        while not self.pending_commands.empty():
            try:
                cmd = self.pending_commands.get_nowait()
                commands_to_send.append(cmd)
            except asyncio.QueueEmpty:
                break

        return {
            "session_id": self.session_id,
            "commands": commands_to_send
        }

    async def handle_results(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Called by Lua bridge when returning command results."""
        self.last_heartbeat = time.time()
        responses = data.get("responses", [])
        for resp in responses:
            req_id = resp.get("id")
            if req_id and req_id in self.pending_futures:
                fut = self.pending_futures.pop(req_id)
                if not fut.done():
                    fut.set_result(resp)
        return {"status": "ok", "received": len(responses)}

    async def request(self, op: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 3.0) -> Dict[str, Any]:
        """Send RPC request to BizHawk bridge and await response."""
        if not self.is_connected():
            raise ConnectionError("BizHawk Bridge is not connected via HTTP Attach")

        req_id = f"req_{uuid.uuid4().hex[:8]}"
        cmd = {
            "v": 1,
            "id": req_id,
            "type": "request",
            "op": op,
            "payload": payload or {}
        }

        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self.pending_futures[req_id] = fut

        await self.pending_commands.put(cmd)

        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
            if not resp.get("ok", False):
                raise RuntimeError(f"Bridge error: {resp.get('error', 'unknown error')}")
            return resp.get("payload", {})
        except asyncio.TimeoutError:
            self.pending_futures.pop(req_id, None)
            raise TimeoutError(f"Bridge command '{op}' timed out after {timeout}s")
