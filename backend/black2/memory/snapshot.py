"""Memory Snapshot, Scanning, and Research tools."""

import time
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel
from .reader import MemoryReader
from .domains import MAIN_RAM


class MemorySnapshot(BaseModel):
    id: str
    label: str
    timestamp: float
    frame: int
    domain: str
    offset: int
    size: int
    data: List[int]  # raw byte list


class MemoryDiffEntry(BaseModel):
    offset: int
    old_value: int
    new_value: int
    delta: int


class MemoryResearchLab:
    def __init__(self, reader: MemoryReader):
        self.reader = reader
        self.snapshots: Dict[str, MemorySnapshot] = {}

    async def take_snapshot(self, label: str = "snapshot", offset: int = 0, size: int = 65536, domain: str = MAIN_RAM) -> MemorySnapshot:
        """Capture a slice of memory for research/diff analysis."""
        bytes_data = await self.reader.read_bytes(offset, size, domain)
        emu_state = await self.reader.client.get_emu_state()
        frame = emu_state.get("frame", 0)
        snap_id = f"snap_{int(time.time()*1000)}"

        snap = MemorySnapshot(
            id=snap_id,
            label=label,
            timestamp=time.time(),
            frame=frame,
            domain=domain,
            offset=offset,
            size=size,
            data=bytes_data
        )
        self.snapshots[snap_id] = snap
        return snap

    def diff_snapshots(self, snap_a_id: str, snap_b_id: str) -> List[MemoryDiffEntry]:
        """Compute byte-level differences between two snapshots."""
        snap_a = self.snapshots.get(snap_a_id)
        snap_b = self.snapshots.get(snap_b_id)
        if not snap_a or not snap_b:
            raise ValueError("Snapshot not found")

        min_len = min(len(snap_a.data), len(snap_b.data))
        diffs = []
        for i in range(min_len):
            v1 = snap_a.data[i]
            v2 = snap_b.data[i]
            if v1 != v2:
                diffs.append(MemoryDiffEntry(
                    offset=snap_a.offset + i,
                    old_value=v1,
                    new_value=v2,
                    delta=v2 - v1
                ))
        return diffs

    def search_pattern(self, snap_id: str, pattern: List[int]) -> List[int]:
        """Search byte pattern in snapshot."""
        snap = self.snapshots.get(snap_id)
        if not snap or not pattern:
            return []
        matches = []
        data = snap.data
        plen = len(pattern)
        for i in range(len(data) - plen + 1):
            if data[i:i+plen] == pattern:
                matches.append(snap.offset + i)
        return matches
