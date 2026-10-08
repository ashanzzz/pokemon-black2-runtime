"""Memory profiles package."""
from .base import MemoryProfile, MemoryRange
from .irej1 import IREJ1_PROFILE

PROFILES: dict[str, MemoryProfile] = {
    "IREJ": IREJ1_PROFILE,
    "IREJ1": IREJ1_PROFILE,
}


def get_profile(rom_code: str = "IREJ") -> MemoryProfile:
    normalized = rom_code.strip().upper()
    if normalized in PROFILES:
        return PROFILES[normalized]
    # Default to IREJ1 as canonical base
    return IREJ1_PROFILE


__all__ = ["MemoryProfile", "MemoryRange", "IREJ1_PROFILE", "PROFILES", "get_profile"]
