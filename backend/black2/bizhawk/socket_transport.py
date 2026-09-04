"""TCP Socket Transport for BizHawk Bridge.

Supports bidirectional line-delimited or length-prefixed JSON streaming.
"""

import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
import uuid
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from .transport import BizHawkTransport


def _make_bridge_logger() -> logging.Logger:
    """Create a small persistent transport log without changing root logging."""
    logger = logging.getLogger("black2.bridge.transport")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        log_dir = Path(__file__).resolve().parents[3] / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "bridge_transport.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


_bridge_log = _make_bridge_logger()


class SocketTransport(BizHawkTransport):
    def __init__(self, host: str = "127.0.0.1", port: int = 8766):
        self.host = host
        self.port = port
        self.servers: List[asyncio.Server] = []
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.pending_futures: Dict[str, asyncio.Future] = {}
        self.running: bool = False
        self._connected: bool = False
        self.last_heartbeat: float = 0.0
        self.last_frame: int = 0
        self.session_id: Optional[str] = None
        self.bridge_version: str = "unknown"
        self.hello_data: Dict[str, Any] = {}
        self._last_heartbeat_log: float = 0.0
        self._last_connection_state: Optional[bool] = None
        # The Lua bridge handles requests in its emulator-frame loop.  Keep
        # one in flight so dashboard polling cannot interleave a map scan and
        # produce partial position samples.
        self._request_lock = asyncio.Lock()

    async def connect(self) -> bool:
        self.running = True
        try:
            srv = await asyncio.start_server(self._handle_client, self.host, self.port)
            self.servers.append(srv)
            print(f"[SocketTransport] BizHawk TCP bridge listening on {self.host}:{self.port}")
            _bridge_log.info("listen host=%s port=%s role=bizhawk_bridge", self.host, self.port)
        except Exception as e:
            _bridge_log.warning("listen_failed host=%s port=%s error=%r", self.host, self.port, e)
        return len(self.servers) > 0

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        buffer = ""
        is_bizhawk = False
        peer = writer.get_extra_info("peername")
        _bridge_log.info("accepted peer=%s", peer)
        try:
            while self.running:
                data = await reader.read(8192)
                if not data:
                    _bridge_log.info("peer_eof peer=%s bizhawk=%s", peer, is_bizhawk)
                    break
                
                buffer += data.decode("utf-8", errors="ignore")

                # If browser HTTP request, reply gracefully and close
                if buffer.startswith("GET ") or buffer.startswith("POST ") or buffer.startswith("HEAD "):
                    _bridge_log.info("http_probe_on_bridge_port peer=%s", peer)
                    resp = "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nBizHawk Socket Bridge Port (Use Port 8765 for Web API)\r\n"
                    writer.write(resp.encode("utf-8"))
                    await writer.drain()
                    writer.close()
                    return

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    # Strip BizHawk prefix "<digits> " if present
                    if " " in line:
                        prefix, rest = line.split(" ", 1)
                        if prefix.isdigit() and (rest.startswith("{") or rest.startswith("[")):
                            line = rest

                    # Find JSON object boundaries
                    if "{" in line and "}" in line:
                        start_idx = line.find("{")
                        end_idx = line.rfind("}")
                        json_candidate = line[start_idx : end_idx + 1]
                    else:
                        json_candidate = line

                    try:
                        msg = json.loads(json_candidate)
                        if not is_bizhawk:
                            self.reader = reader
                            self.writer = writer
                            self._connected = True
                            self.session_id = str(uuid.uuid4())[:8]
                            is_bizhawk = True
                            print(f"[SocketTransport] BizHawk Bridge connected!")
                            _bridge_log.info("bizhawk_connected peer=%s session=%s", peer, self.session_id)

                        self.last_heartbeat = time.time()
                        if self.last_heartbeat - self._last_heartbeat_log >= 5.0:
                            _bridge_log.info("heartbeat peer=%s frame=%s", peer, msg.get("frame"))
                            self._last_heartbeat_log = self.last_heartbeat
                        msg_type = msg.get("type")
                        if msg_type == "hello":
                            self.hello_data = msg
                            self.bridge_version = msg.get("bridge_version", "1.1.0")
                            self.last_frame = msg.get("frame", 0)
                            print(f"[SocketTransport] Hello received! Bridge v{self.bridge_version}, ROM: {msg.get('game', {}).get('rom_name')}")
                            _bridge_log.info("hello peer=%s bridge_version=%s frame=%s rom=%s", peer, self.bridge_version, self.last_frame, msg.get("game", {}).get("rom_name"))
                            resp = json.dumps({"type": "hello_ack", "session_id": self.session_id}) + "\n"
                            writer.write(resp.encode("utf-8"))
                            await writer.drain()
                            continue
                        elif msg_type == "heartbeat":
                            self.last_frame = msg.get("frame", self.last_frame)
                            if "version" in msg:
                                self.bridge_version = msg["version"]
                            continue

                        req_id = msg.get("id")
                        if req_id and req_id in self.pending_futures:
                            fut = self.pending_futures.pop(req_id)
                            if not fut.done():
                                fut.set_result(msg)
                    except Exception as e:
                        _bridge_log.warning("message_parse_or_dispatch_error peer=%s error=%r", peer, e)
        except Exception as e:
            _bridge_log.warning("client_handler_error peer=%s error=%r", peer, e)
        finally:
            if is_bizhawk:
                print("[SocketTransport] BizHawk Bridge disconnected")
                # A reconnect can arrive before the old handler reaches this
                # finally block.  Only the handler owning the active writer
                # may clear connection state; otherwise a healthy bridge is
                # reported as disconnected while its heartbeat still moves.
                if self.writer is writer:
                    self._connected = False
                    self.reader = None
                    self.writer = None
                    _bridge_log.info("bizhawk_disconnected peer=%s active_owner=true", peer)
                else:
                    _bridge_log.info("bizhawk_disconnected peer=%s active_owner=false state_preserved=true", peer)
            else:
                _bridge_log.info("client_closed peer=%s bizhawk=false", peer)

    async def disconnect(self) -> None:
        self.running = False
        self._connected = False
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        for srv in self.servers:
            srv.close()
            await srv.wait_closed()
        self.servers.clear()

    def is_connected(self) -> bool:
        connected = self._connected and (self.writer is not None) and (time.time() - self.last_heartbeat < 6.0)
        if connected != self._last_connection_state:
            _bridge_log.info(
                "connection_state connected=%s flag=%s writer=%s heartbeat_age=%.3f frame=%s",
                connected,
                self._connected,
                self.writer is not None,
                max(0.0, time.time() - self.last_heartbeat) if self.last_heartbeat else -1.0,
                self.last_frame,
            )
            self._last_connection_state = connected
        return connected

    def diagnostics(self) -> Dict[str, Any]:
        """Expose non-sensitive connection facts for the local diagnostics UI."""
        now = time.time()
        return {
            "connected": self.is_connected(),
            "connected_flag": self._connected,
            "writer_present": self.writer is not None,
            "heartbeat_age_sec": round(now - self.last_heartbeat, 3) if self.last_heartbeat else None,
            "last_frame": self.last_frame,
            "bridge_version": self.bridge_version,
            "session_id": self.session_id,
            "pending_requests": len(self.pending_futures),
        }

    def get_transport_type(self) -> str:
        return "socket"

    async def request(self, op: str, payload: Optional[Dict[str, Any]] = None, timeout: float = 3.0) -> Dict[str, Any]:
        async with self._request_lock:
            if not self.is_connected() or not self.writer:
                _bridge_log.warning("request_rejected op=%s connected=%s", op, self.is_connected())
                raise ConnectionError("BizHawk Bridge is not connected via Socket")

            req_id = f"req_{uuid.uuid4().hex[:8]}"
            cmd = {
                "v": 1,
                "id": req_id,
                "type": "request",
                "op": op,
                "payload": payload or {}
            }
            json_str = json.dumps(cmd)
            framed = f"{json_str}\n"

            loop = asyncio.get_running_loop()
            future = loop.create_future()
            self.pending_futures[req_id] = future

            self.writer.write(framed.encode("utf-8"))
            await self.writer.drain()
            _bridge_log.info("request_sent op=%s id=%s frame=%s", op, req_id, self.last_frame)

            try:
                response = await asyncio.wait_for(future, timeout=timeout)
                if not response.get("ok", False):
                    _bridge_log.warning("request_failed op=%s id=%s error=%s", op, req_id, response.get("error", "unknown error"))
                    raise RuntimeError(f"Bridge error: {response.get('error', 'unknown error')}")
                _bridge_log.info("request_completed op=%s id=%s", op, req_id)
                return response.get("payload", {})
            except asyncio.TimeoutError:
                self.pending_futures.pop(req_id, None)
                _bridge_log.warning("request_timeout op=%s id=%s timeout=%s", op, req_id, timeout)
                raise TimeoutError(f"Bridge socket command '{op}' timed out after {timeout}s")
