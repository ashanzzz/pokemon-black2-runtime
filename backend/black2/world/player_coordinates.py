"""Small, dependency-free projection of PlayerRuntime navigation coordinates."""
from __future__ import annotations

import math
from typing import Any


def canonical_grid_player(
    sample: dict[str, Any] | None, *, require_resolved: bool = False
) -> dict[str, Any]:
    """Expose only the canonical Zone/GPos/WPos facts needed by navigation."""
    allowed = {"resolved"} if require_resolved else {"resolved", "candidate"}
    if not sample or sample.get("status") not in allowed:
        return {
            "status": "unresolved",
            "confidence": "unresolved",
            "reason": (sample or {}).get("reason", "no cached PlayerRuntime sample"),
        }
    position = sample.get("position") or {}
    grid = position.get("grid") or {}
    world = position.get("world") or {}

    def grid_value(name: str) -> int | None:
        value = grid.get(name)
        return int(value) if isinstance(value, int) and not isinstance(value, bool) else None

    def world_value(name: str) -> float | None:
        value = world.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            return float(value)
        return None

    return {
        "status": sample.get("status"),
        "confidence": sample.get("confidence"),
        "frame": sample.get("frame"),
        "zone_id": sample.get("zone_id"),
        "grid": {name: grid_value(name) for name in ("x", "y", "z")},
        "world": {name: world_value(name) for name in ("x", "y", "z")},
    }
