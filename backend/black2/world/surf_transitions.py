"""Surf transition and shoreline jump point analysis for Pokémon Black 2.

Discovers legal Land <-> Water transition points (Mounting Surf and Dismounting to Land)
based on ROM terrain materials (water, water_edge), collision flags, and physical
height alignment (dy <= 8.0).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SurfJumpPoint:
    land_tile: Tuple[int, int]
    land_world_y: float
    land_kind: str
    water_tile: Tuple[int, int]
    water_world_y: float
    direction: str
    height_delta: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "surf_mount",
            "land_tile": {"x": self.land_tile[0], "z": self.land_tile[1], "world_y": self.land_world_y},
            "land_material": self.land_kind,
            "water_tile": {"x": self.water_tile[0], "z": self.water_tile[1], "world_y": self.water_world_y},
            "mount_direction": self.direction,
            "height_delta": round(self.height_delta, 2),
            "requires": "move:surf",
            "status": "legal_surf_edge",
        }


@dataclass(frozen=True)
class SurfDismountPoint:
    water_tile: Tuple[int, int]
    water_world_y: float
    land_tile: Tuple[int, int]
    land_world_y: float
    land_kind: str
    direction: str
    height_delta: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "type": "surf_dismount",
            "water_tile": {"x": self.water_tile[0], "z": self.water_tile[1], "world_y": self.water_world_y},
            "land_tile": {"x": self.land_tile[0], "z": self.land_tile[1], "world_y": self.land_world_y},
            "land_material": self.land_kind,
            "dismount_direction": self.direction,
            "height_delta": round(self.height_delta, 2),
            "status": "legal_dismount_edge",
        }


class SurfTransitionService:
    """Evaluates shoreline transitions and mounting/dismounting affordances."""

    def __init__(self, provider: Any = None) -> None:
        self.provider = provider
        self._cache: Dict[int, Dict[str, Any]] = {}

    def _resolve_provider(self) -> Any:
        if self.provider is not None:
            return self.provider
        from .static_navigation import RomStaticNavigationGraph
        self.provider = RomStaticNavigationGraph()
        return self.provider

    def analyze_zone(self, zone_id: int) -> dict[str, Any]:
        zone_id = int(zone_id)
        if zone_id in self._cache:
            return self._cache[zone_id]

        provider = self._resolve_provider()
        surfaces = provider._decode_zone_surfaces(zone_id)
        
        # Group surfaces by (x, z)
        by_coord: Dict[Tuple[int, int], List[dict[str, Any]]] = {}
        for s in surfaces:
            by_coord.setdefault((s["x"], s["z"]), []).append(s["surface"])

        mount_points: List[SurfJumpPoint] = []
        dismount_points: List[SurfDismountPoint] = []

        for (lx, lz), l_surfs in by_coord.items():
            # Check if land tile has a walkable surface or water_edge
            land_candidate = None
            for s in l_surfs:
                mat = s.get("material", {})
                col = s.get("collision", {})
                k = mat.get("kind", "")
                blocked = col.get("static_blocked")
                if k == "water_edge" or (k not in ("water", "obstacle") and not blocked):
                    land_candidate = s
                    break

            if not land_candidate:
                continue

            l_mat = land_candidate.get("material", {})
            l_col = land_candidate.get("collision", {})
            l_h = land_candidate.get("height", {})
            l_kind = l_mat.get("kind", "land")
            l_y = l_h.get("chunk_relative_world_y") or 0.0
            blocked_dirs = set(l_col.get("blocked_directions") or ())

            for dx, dz, direction in [(-1, 0, "West"), (1, 0, "East"), (0, -1, "North"), (0, 1, "South")]:
                if direction in blocked_dirs:
                    continue
                wx, wz = lx + dx, lz + dz
                w_surfs = by_coord.get((wx, wz))
                if not w_surfs:
                    continue

                # Check if neighbor has a water surface
                water_candidate = None
                for ws in w_surfs:
                    wmat = ws.get("material", {})
                    wcol = ws.get("collision", {})
                    if wmat.get("kind") == "water" or "surf" in (wcol.get("requires") or []):
                        water_candidate = ws
                        break

                if not water_candidate:
                    continue

                w_h = water_candidate.get("height", {})
                w_y = w_h.get("chunk_relative_world_y") or 0.0
                dy = abs(l_y - w_y)

                # Strict height difference guard: can only surf across smooth banks / shores (<= 8.0)
                if dy <= 8.0:
                    mount = SurfJumpPoint(
                        land_tile=(lx, lz),
                        land_world_y=l_y,
                        land_kind=l_kind,
                        water_tile=(wx, wz),
                        water_world_y=w_y,
                        direction=direction,
                        height_delta=dy,
                    )
                    dismount = SurfDismountPoint(
                        water_tile=(wx, wz),
                        water_world_y=w_y,
                        land_tile=(lx, lz),
                        land_world_y=l_y,
                        land_kind=l_kind,
                        direction={"West": "East", "East": "West", "North": "South", "South": "North"}[direction],
                        height_delta=dy,
                    )
                    mount_points.append(mount)
                    dismount_points.append(dismount)

        # Deduplicate and sort by height delta
        mount_points.sort(key=lambda item: item.height_delta)
        dismount_points.sort(key=lambda item: item.height_delta)

        res = {
            "format": "black2-surf-transitions/v1",
            "zone_id": zone_id,
            "total_mount_points": len(mount_points),
            "total_dismount_points": len(dismount_points),
            "mount_points": [m.as_dict() for m in mount_points],
            "dismount_points": [d.as_dict() for d in dismount_points],
        }
        self._cache[zone_id] = res
        return res


surf_transition_service = SurfTransitionService()
