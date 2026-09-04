"""Memory Reader & Batch Sampling engine."""

import asyncio
from typing import Dict, Any, List, Optional
from ..bizhawk.bridge_client import BridgeClient
from .domains import MAIN_RAM, arm9_to_main_ram_offset


class MemoryReader:
    def __init__(self, client: BridgeClient):
        self.client = client
        self.cached_domains: Dict[str, Any] = {}

    async def read_u8(self, addr: int, domain: str = MAIN_RAM) -> int:
        offset = arm9_to_main_ram_offset(addr) if domain == MAIN_RAM else addr
        return await self.client.read_u8(offset, domain)

    async def read_u16(self, addr: int, domain: str = MAIN_RAM) -> int:
        offset = arm9_to_main_ram_offset(addr) if domain == MAIN_RAM else addr
        return await self.client.read_u16(offset, domain)

    async def read_u32(self, addr: int, domain: str = MAIN_RAM) -> int:
        offset = arm9_to_main_ram_offset(addr) if domain == MAIN_RAM else addr
        return await self.client.read_u32(offset, domain)

    async def read_bytes(self, addr: int, length: int, domain: str = MAIN_RAM) -> List[int]:
        offset = arm9_to_main_ram_offset(addr) if domain == MAIN_RAM else addr
        return await self.client.read_bytes(offset, length, domain)

    async def read_batch_ranges(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Perform batch atomic read across multiple memory addresses."""
        formatted_ranges = []
        for it in items:
            dom = it.get("domain", MAIN_RAM)
            raw_addr = it.get("addr", it.get("offset", 0))
            offset = arm9_to_main_ram_offset(raw_addr) if dom == MAIN_RAM else raw_addr
            formatted_ranges.append({
                "id": it.get("id", str(raw_addr)),
                "domain": dom,
                "offset": offset,
                "length": it.get("length", it.get("size", 1))
            })
        return await self.client.read_batch(formatted_ranges)

    async def read_batch_snapshot(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Read ranges atomically and preserve the bridge's exact frame number."""
        formatted_ranges = []
        for it in items:
            dom = it.get("domain", MAIN_RAM)
            raw_addr = it.get("addr", it.get("offset", 0))
            offset = arm9_to_main_ram_offset(raw_addr) if dom == MAIN_RAM else raw_addr
            formatted_ranges.append({
                "id": it.get("id", str(raw_addr)),
                "domain": dom,
                "offset": offset,
                "length": it.get("length", it.get("size", 1)),
            })
        return await self.client.read_batch_payload(formatted_ranges)

    async def scan_headers(self, patterns: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Scan ARM9 RAM inside BizHawk and return only matching headers."""
        payload = await self.client.transport.request(
            "memory.scan_headers",
            {"domain": MAIN_RAM, "size": 0x400000, "patterns": patterns or ["BMD0", "BTX0"]},
            timeout=15.0,
        )
        return list(payload.get("matches", []))

    async def scan_pattern(
        self,
        pattern: List[int],
        start: int = 0,
        size: int = 0x400000,
        limit: int = 64,
        domain: str = MAIN_RAM,
    ) -> List[int]:
        """Find a byte pattern in a bounded memory window and return its offsets."""
        payload = await self.client.transport.request(
            "memory.scan_pattern",
            {
                "domain": domain,
                "bytes": pattern,
                "start": start,
                "size": size,
                "limit": limit,
            },
        )
        return list(payload.get("matches", []))

    async def scan_pattern_snapshot(
        self,
        pattern: List[int],
        start: int = 0,
        size: int = 0x400000,
        limit: int = 64,
        domain: str = MAIN_RAM,
    ) -> Dict[str, Any]:
        """Return a bounded bridge-owned scan response including its frame."""
        return await self.client.transport.request(
            "memory.scan_pattern",
            {
                "domain": domain,
                "bytes": pattern,
                "start": start,
                "size": size,
                "limit": limit,
            },
            timeout=15.0,
        )
