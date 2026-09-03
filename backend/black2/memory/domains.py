"""Memory domain representations and address translation."""

from typing import Dict, Any, List
from pydantic import BaseModel


class MemoryDomain(BaseModel):
    name: str
    size: int
    readable: bool = True
    writable: bool = True


# NDS MelonDS standard domains
MAIN_RAM = "Main RAM"          # 0x00000000..0x003FFFFF (4MB / ARM9 RAM 0x02000000..0x023FFFFF)
ARM9_BIOS = "ARM9 BIOS"
ARM7_BIOS = "ARM7 BIOS"
ARM9_SYS_BUS = "ARM9 System Bus"
SYSTEM_BUS = "System Bus"


def arm9_to_main_ram_offset(arm9_addr: int) -> int:
    """Convert ARM9 bus address (0x02000000..0x023FFFFF) to Main RAM domain offset (0..0x3FFFFF)."""
    if 0x02000000 <= arm9_addr < 0x02400000:
        return arm9_addr - 0x02000000
    return arm9_addr


def main_ram_offset_to_arm9(offset: int) -> int:
    """Convert Main RAM domain offset to ARM9 address."""
    return 0x02000000 + offset
