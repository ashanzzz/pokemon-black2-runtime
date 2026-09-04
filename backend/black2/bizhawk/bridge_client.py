"""High-level Bridge Client for interacting with BizHawk."""

import asyncio
from typing import Dict, Any, List, Optional, Union
from .transport import BizHawkTransport


class BridgeClient:
    def __init__(self, transport: BizHawkTransport):
        self.transport = transport

    @property
    def is_connected(self) -> bool:
        return self.transport.is_connected()

    async def ping(self) -> Dict[str, Any]:
        return await self.transport.request("bridge.ping")

    async def get_capabilities(self) -> Dict[str, Any]:
        return await self.transport.request("bridge.capabilities")

    async def get_memory_write_trace_capabilities(self) -> Dict[str, Any]:
        """Inspect the live BizHawk event scopes/register names before tracing."""
        return await self.transport.request("bridge.trace_capabilities")

    async def get_emu_state(self) -> Dict[str, Any]:
        return await self.transport.request("emu.state")

    async def pause(self) -> Dict[str, Any]:
        return await self.transport.request("emu.pause")

    async def resume(self) -> Dict[str, Any]:
        return await self.transport.request("emu.resume")

    async def frame_advance(self, frames: int = 1) -> Dict[str, Any]:
        return await self.transport.request("emu.frame_advance", {"frames": frames})

    async def exit(self) -> Dict[str, Any]:
        return await self.transport.request("emu.exit")

    async def get_game_info(self) -> Dict[str, Any]:
        return await self.transport.request("game.info")

    async def get_memory_domains(self) -> Dict[str, Any]:
        return await self.transport.request("memory.domains")

    async def read_u8(self, addr: int, domain: str = "Main RAM") -> int:
        res = await self.transport.request("memory.read", {
            "domain": domain,
            "addr": addr,
            "size": 1,
            "format": "u8"
        })
        return res.get("value", 0)

    async def read_u16(self, addr: int, domain: str = "Main RAM") -> int:
        res = await self.transport.request("memory.read", {
            "domain": domain,
            "addr": addr,
            "size": 2,
            "format": "u16"
        })
        return res.get("value", 0)

    async def read_u32(self, addr: int, domain: str = "Main RAM") -> int:
        res = await self.transport.request("memory.read", {
            "domain": domain,
            "addr": addr,
            "size": 4,
            "format": "u32"
        })
        return res.get("value", 0)

    async def read_bytes(self, addr: int, length: int, domain: str = "Main RAM", timeout: float = 3.0) -> List[int]:
        res = await self.transport.request("memory.read", {
            "domain": domain,
            "addr": addr,
            "size": length,
            "format": "bytes"
        }, timeout=timeout)
        if "hex" in res and res["hex"] and (not res.get("bytes") or len(res.get("bytes", [])) != length):
            try:
                return list(bytes.fromhex(res["hex"]))
            except ValueError:
                pass
        return res.get("bytes", [])

    async def write_bytes(self, addr: int, bytes_data: List[int], domain: str = "Main RAM") -> Dict[str, Any]:
        return await self.transport.request("memory.write", {
            "domain": domain,
            "addr": addr,
            "bytes": bytes_data
        })

    async def read_batch(self, ranges: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Perform batch atomic read across multiple memory addresses.
        Each item in ranges: {"domain": "Main RAM", "offset": 0x1234, "length": 16, "id": "my_tag"}
        """
        res = await self.transport.request("memory.read_batch", {"ranges": ranges})
        return res.get("results", {})

    async def read_batch_payload(self, ranges: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Return the complete atomic batch payload, including the bridge frame.

        ``read_batch`` predates the evidence probes and intentionally exposes
        only the result map to its callers.  Checkpoint evidence needs the
        frame emitted by the Lua bridge as part of the same request, so it uses
        this non-breaking companion method instead of sampling the heartbeat
        clock after the read.
        """
        return await self.transport.request("memory.read_batch", {"ranges": ranges})

    async def press_buttons(self, buttons: Union[str, List[str]], frames: int = 4) -> Dict[str, Any]:
        if isinstance(buttons, str):
            buttons = [buttons]
        return await self.transport.request("input.press", {"buttons": buttons, "frames": frames})

    async def touch(self, x: int, y: int, frames: int = 4) -> Dict[str, Any]:
        return await self.transport.request("input.touch", {"x": x, "y": y, "frames": frames})

    async def get_input_state(self) -> Dict[str, Any]:
        return await self.transport.request("input.state")

    async def capture_screen(self, path: str) -> Dict[str, Any]:
        return await self.transport.request("screen.capture", {"path": path})

    async def clear_inputs(self) -> Dict[str, Any]:
        return await self.transport.request("input.clear")

    async def begin_a_edge_capture(self, button: str, sample_frames: int, ranges: List[Dict[str, Any]]) -> Dict[str, Any]:
        return await self.transport.request("probe.a_edge_begin", {
            "button": button, "sample_frames": sample_frames, "ranges": ranges,
        })

    async def get_a_edge_capture(self) -> Dict[str, Any]:
        return await self.transport.request("probe.a_edge_status")

    async def begin_memory_write_trace(
        self,
        start_addr: int,
        length: int,
        addresses: List[int],
        max_frames: int,
        max_events: int,
        button: Optional[str] = None,
        ranges: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Start a bounded, read-only ARM9 write-PC trace in the Lua bridge.

        The bridge owns the callback lifetime.  ``button`` is optional and, if
        present, is held for one emulation frame after the callback is armed.
        This lets an RE experiment correlate a controlled edge with the
        writer-PC trace without a race between two independent requests.
        """
        return await self.transport.request("probe.write_trace_begin", {
            "start_addr": start_addr,
            "length": length,
            "addresses": addresses,
            "max_frames": max_frames,
            "max_events": max_events,
            "button": button,
            "ranges": ranges or [],
        })

    async def get_memory_write_trace(self) -> Dict[str, Any]:
        """Return bounded write-trace progress or its completed raw evidence."""
        return await self.transport.request("probe.write_trace_status")

    async def cancel_memory_write_trace(self) -> Dict[str, Any]:
        """Cancel a live bounded write trace and unregister its Lua callback."""
        return await self.transport.request("probe.write_trace_cancel")

    async def save_state(self, slot: int = 1) -> Dict[str, Any]:
        return await self.transport.request("savestate.save", {"slot": slot})

    async def load_state(self, slot: int = 1) -> Dict[str, Any]:
        return await self.transport.request("savestate.load", {"slot": slot})
