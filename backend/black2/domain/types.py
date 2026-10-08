"""Domain types and strongly-typed value objects for Pokémon Black 2."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import math
from typing import Any


class ZoneId(int):
    """Represents a Pokémon Gen 5 Zone identifier (0-65535)."""

    def __new__(cls, value: int | str) -> "ZoneId":
        val = int(value)
        if val < 0 or val > 65535:
            raise ValueError(f"ZoneId must be between 0 and 65535, got {val}")
        return super().__new__(cls, val)

    def __repr__(self) -> str:
        return f"ZoneId({int(self)})"


class MatrixId(int):
    """Represents a map Matrix identifier (0-65535)."""

    def __new__(cls, value: int | str) -> "MatrixId":
        val = int(value)
        if val < 0 or val > 65535:
            raise ValueError(f"MatrixId must be between 0 and 65535, got {val}")
        return super().__new__(cls, val)

    def __repr__(self) -> str:
        return f"MatrixId({int(self)})"


class ScriptId(int):
    """Represents a ROM Script file/event identifier."""

    def __new__(cls, value: int | str) -> "ScriptId":
        val = int(value)
        if val < 0:
            raise ValueError(f"ScriptId must be non-negative, got {val}")
        return super().__new__(cls, val)

    def __repr__(self) -> str:
        return f"ScriptId({int(self)})"


class EntityId(int):
    """Represents an entity/actor slot identifier."""

    def __new__(cls, value: int | str) -> "EntityId":
        val = int(value)
        if val < 0:
            raise ValueError(f"EntityId must be non-negative, got {val}")
        return super().__new__(cls, val)

    def __repr__(self) -> str:
        return f"EntityId({int(self)})"


class FlagId(int):
    """Represents an in-game storyline progress flag identifier."""

    def __new__(cls, value: int | str) -> "FlagId":
        val = int(value)
        if val < 0:
            raise ValueError(f"FlagId must be non-negative, got {val}")
        return super().__new__(cls, val)

    def __repr__(self) -> str:
        return f"FlagId({int(self)})"


class SpeciesId(int):
    """Represents a National Pokédex species ID (1-649 for Gen 5)."""

    def __new__(cls, value: int | str) -> "SpeciesId":
        val = int(value)
        if val < 0 or val > 1025:
            raise ValueError(f"SpeciesId out of range: {val}")
        return super().__new__(cls, val)

    def __repr__(self) -> str:
        return f"SpeciesId({int(self)})"


class MoveId(int):
    """Represents a Pokémon Move ID."""

    def __new__(cls, value: int | str) -> "MoveId":
        val = int(value)
        if val < 0 or val > 1000:
            raise ValueError(f"MoveId out of range: {val}")
        return super().__new__(cls, val)

    def __repr__(self) -> str:
        return f"MoveId({int(self)})"


class ItemId(int):
    """Represents an item ID."""

    def __new__(cls, value: int | str) -> "ItemId":
        val = int(value)
        if val < 0:
            raise ValueError(f"ItemId must be non-negative, got {val}")
        return super().__new__(cls, val)

    def __repr__(self) -> str:
        return f"ItemId({int(self)})"


class Direction(str, Enum):
    """Cardinal directions matching Black 2 overworld orientation."""

    NORTH = "North"
    SOUTH = "South"
    EAST = "East"
    WEST = "West"

    @classmethod
    def from_str(cls, value: str) -> "Direction":
        normalized = value.strip().capitalize()
        for member in cls:
            if member.value == normalized or member.name == value.upper():
                return member
        raise ValueError(f"Invalid direction: {value}")

    @property
    def dx(self) -> int:
        if self == Direction.EAST:
            return 1
        if self == Direction.WEST:
            return -1
        return 0

    @property
    def dz(self) -> int:
        # In Gen 5 grid coordinates: North is -Z, South is +Z
        if self == Direction.SOUTH:
            return 1
        if self == Direction.NORTH:
            return -1
        return 0

    @property
    def opposite(self) -> "Direction":
        mapping = {
            Direction.NORTH: Direction.SOUTH,
            Direction.SOUTH: Direction.NORTH,
            Direction.EAST: Direction.WEST,
            Direction.WEST: Direction.EAST,
        }
        return mapping[self]


class GameMode(str, Enum):
    """High-level semantic mode of the game runtime."""

    OVERWORLD = "OVERWORLD"
    BATTLE = "BATTLE"
    DIALOGUE = "DIALOGUE"
    MENU = "MENU"
    TITLE = "TITLE"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_str(cls, value: str) -> "GameMode":
        val = value.strip().upper()
        for member in cls:
            if member.value == val:
                return member
        return cls.UNKNOWN


class Confidence(str, Enum):
    """Evidence confidence tier for reverse-engineering facts."""

    UNVERIFIED = "unverified"
    CANDIDATE = "candidate"
    PROBABLE = "probable"
    VERIFIED = "verified"

    @property
    def is_verified(self) -> bool:
        return self == Confidence.VERIFIED


class CommandStatus(str, Enum):
    """Unified command execution lifecycle status."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Severity(str, Enum):
    """Event / Log severity levels."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class GridCoord:
    """Discrete tile coordinates in a map matrix or zone."""

    x: int
    y: int
    z: int

    def distance_manhattan(self, other: "GridCoord") -> int:
        return abs(self.x - other.x) + abs(self.z - other.z)

    def as_tuple(self) -> tuple[int, int, int]:
        return (self.x, self.y, self.z)

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "z": self.z}


@dataclass(frozen=True, slots=True)
class WorldCoord:
    """Continuous 3D world coordinates."""

    x: float
    y: float
    z: float

    def distance(self, other: "WorldCoord") -> float:
        return math.sqrt(
            (self.x - other.x) ** 2 + (self.y - other.y) ** 2 + (self.z - other.z) ** 2
        )

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    def as_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "z": self.z}
