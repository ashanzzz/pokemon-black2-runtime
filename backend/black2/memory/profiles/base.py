"""Base memory profile specifications for Pokémon Gen 5 games."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryRange:
    name: str
    base_address: int
    size: int
    description: str = ""

    @property
    def end_address(self) -> int:
        return self.base_address + self.size

    def contains(self, address: int) -> bool:
        return self.base_address <= address < self.end_address


@dataclass(frozen=True)
class MemoryProfile:
    """Immutable memory layout specification for a specific ROM code and revision."""

    rom_code: str
    rom_title: str
    revision: int
    arm9_base: int = 0x02000000
    main_ram_size: int = 0x00400000  # 4 MB
    ranges: dict[str, MemoryRange] = field(default_factory=dict)
    known_pointers: dict[str, int] = field(default_factory=dict)

    @property
    def arm9_end(self) -> int:
        return self.arm9_base + self.main_ram_size

    def to_offset(self, arm9_addr: int) -> int:
        """Translate ARM9 virtual bus address to Main RAM linear 0-indexed offset."""
        if not (self.arm9_base <= arm9_addr < self.arm9_end):
            raise ValueError(f"Address 0x{arm9_addr:08X} outside Main RAM range (0x{self.arm9_base:08X}..0x{self.arm9_end:08X})")
        return arm9_addr - self.arm9_base

    def to_arm9(self, offset: int) -> int:
        """Translate Main RAM 0-indexed offset to ARM9 virtual address."""
        if not (0 <= offset < self.main_ram_size):
            raise ValueError(f"Offset 0x{offset:X} outside 4MB Main RAM size")
        return self.arm9_base + offset
