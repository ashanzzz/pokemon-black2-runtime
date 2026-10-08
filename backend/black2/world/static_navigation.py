"""Static, ROM-backed navigation candidates for a single Gen-5 Zone.

The observation graph is the strongest source for automatic movement, but it
is necessarily sparse until the player has walked a route.  This module
compiles the decoded terrain records into a bounded *candidate* graph.  It is
deliberately kept separate from :mod:`observed_navigation`: a flag-clear ROM
tile is useful for planning and snapping, but it is not proof that the current
game state will accept an input (actors, scripts and elevation can still
change the result).
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import heapq
import json
import math
from pathlib import Path
import re
import threading
from typing import Any, Iterable

from .gen5_rom_map import MATRIX_NONE, Gen5RomMap
from .observed_navigation import NavNode
from .tile_semantics import decode_chunk_terrain


GRID_SPACE = "gen5-field-grid-v1"
TILE_WORLD = 16.0
CHUNK_TILES = 32
ZONE_SURFACE_CACHE_CAP = 16
LAYER_CACHE_CAP = 32


@dataclass(frozen=True)
class StaticNavigationCell:
    """One static walk candidate on a concrete GPos elevation layer."""

    node: NavNode
    layer_index: int
    tile_class: int
    flags: int
    static_blocked: bool
    blocked_directions: tuple[str, ...] = ()
    ledge_direction: str | None = None
    relative_height: float | None = None
    material: dict[str, Any] | None = None
    source: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "zone_id": self.node.zone_id,
            "x": self.node.x,
            "y": self.node.y,
            "z": self.node.z,
            "layer_index": self.layer_index,
            "tile_class": self.tile_class,
            "flags": self.flags,
            "static_blocked": self.static_blocked,
            "blocked_directions": list(self.blocked_directions),
            "ledge_direction": self.ledge_direction,
            "relative_height": self.relative_height,
            "material": self.material or {},
            "source": self.source or {},
        }


def _as_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _grid_key(node: NavNode) -> tuple[int, int, int, int]:
    return node.zone_id, node.x, node.y, node.z


def _direction(dx: int, dz: int) -> str | None:
    return {
        (0, -1): "up",
        (0, 1): "down",
        (-1, 0): "left",
        (1, 0): "right",
    }.get((dx, dz))


def _opposite(direction: str | None) -> str | None:
    return {"up": "down", "down": "up", "left": "right", "right": "left"}.get(direction)


_MOVEMENT_MODES = frozenset({"walk", "run", "bike", "surf"})


def _is_unverified_material(cell: StaticNavigationCell | None) -> bool:
    """Return whether a flag-clear cell is only a terrain decode candidate."""
    if cell is None:
        return False
    material = cell.material or {}
    kind = str(material.get("kind") or "unknown")
    status = str(material.get("status") or "").lower()
    return bool(material and (kind == "unknown" or status == "unverified"))


def _terrain_policy(allow_unverified_terrain: bool) -> dict[str, Any]:
    return {
        "allow_unverified_terrain": bool(allow_unverified_terrain),
        "unknown_tiles": (
            "candidate_with_live_landing_verification"
            if allow_unverified_terrain else "excluded"
        ),
        "execution": "requires_per_step_PlayerRuntime_landing_verification",
    }


def _movement_eligibility(
    cell: StaticNavigationCell | None,
    movement_mode: str = "walk",
    *,
    allow_unverified_terrain: bool = False,
) -> tuple[bool, str | None]:
    """Apply Gen-5 transport predicates to a static terrain candidate.

    ``flags & 1 == 0`` only proves that a terrain record is not statically
    blocked.  It does *not* prove that the current locomotion can enter it.
    Keep this predicate in the ROM graph so local and Matrix-global A* use the
    same water/cycling rules.  Runtime transport activation remains outside
    this module and is checked by :class:`NavigationPlanService`.
    """
    mode = str(movement_mode or "walk").lower()
    if mode not in _MOVEMENT_MODES:
        mode = "walk"
    if cell is None:
        return False, "missing_static_surface"
    material = cell.material or {}
    kind = str(material.get("kind") or "unknown")
    material_status = str(material.get("status") or "").lower()
    # A flag-clear record with an unknown terrain class is not an open floor
    # guarantee.  Older test/third-party providers may omit material metadata
    # entirely, so preserve that compatibility while rejecting the explicit
    # decoder sentinel used for unknown ROM classes.
    if (
        material
        and (kind == "unknown" or material_status == "unverified")
        and not allow_unverified_terrain
    ):
        return False, "unknown_static_material"
    requires = material.get("requires")
    if mode == "surf":
        if kind not in {"water", "water_edge"} and requires != "surf":
            return False, "surf_requires_water"
    elif kind == "water" or requires == "surf":
        return False, "surf_required"
    if mode == "bike" and material.get("blocks_cycling"):
        return False, "cycling_blocked_by_terrain"
    return True, None


def _occupied_xy(
    values: Iterable[NavNode | dict[str, Any] | tuple[int, int]], *, zone_id: int, y: int,
) -> set[tuple[int, int]]:
    """Accept all public actor-coordinate shapes at the ROM graph boundary."""
    # Keep the canonicalizer in navigation_planning so the API and static
    # provider cannot gradually diverge.  The import is local because the
    # planner lazily constructs this provider.
    from .navigation_planning import normalize_occupancy

    result: set[tuple[int, int]] = set()
    for item in normalize_occupancy(values or (), default_zone=zone_id, default_y=y):
        if item.get("zone_id") not in (None, zone_id):
            continue
        grid = item.get("grid") or {}
        if grid.get("y") not in (None, y):
            continue
        result.add((int(grid["x"]), int(grid["z"])))
    return result


class RomStaticNavigationGraph:
    """Compile and query static terrain candidates from one ROM.

    The object is lazy and thread-safe.  A zone's decoded terrain records are
    cached independently from the selected elevation layer, so normal UI
    polling does not repeatedly parse the ROM.  ``player_sample`` is accepted
    by the public query methods as optional height alignment evidence; it is
    never read from RAM by this class.
    """

    def __init__(self, rom: Gen5RomMap | None = None, *, rom_path: str | Path | None = None) -> None:
        self.rom = rom or Gen5RomMap(rom_path)
        self._lock = threading.RLock()
        # Terrain records can be large. Bound both cache layers so repeated
        # runtime anchors and cross-Zone planning cannot retain the whole ROM.
        self._zone_surfaces_cache: OrderedDict[int, tuple[dict[str, Any], ...]] = OrderedDict()
        self._zone_surface_lookup_cache: OrderedDict[int, dict[tuple[int, int], tuple[dict[str, Any], ...]]] = OrderedDict()
        # Per-tile immutable event lookup cache. Radar polls the same window
        # repeatedly; without this cache event_overlay_at reparses every NPC,
        # furniture, and warp record for every cell on every HTTP request.
        self._event_overlay_lookup_cache: OrderedDict[tuple[int, int, int], tuple[dict[str, Any], ...]] = OrderedDict()
        self._event_overlay_cache_cap = 8192
        self._warp_doorstep_cache: dict[tuple[int, int], tuple[int, int]] = {}
        self._layer_cache: OrderedDict[tuple[int, int, str], dict[tuple[int, int], StaticNavigationCell]] = OrderedDict()
        self._zone_surface_cache_cap = ZONE_SURFACE_CACHE_CAP
        self._layer_cache_cap = LAYER_CACHE_CAP
        identity = self.rom.static_identity()
        self.revision = "rom:" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()[:16]

    def status(self) -> dict[str, Any]:
        with self._lock:
            cache = {
                "zone_surface_entries": len(self._zone_surfaces_cache),
                "zone_surface_lookup_entries": len(getattr(self, "_zone_surface_lookup_cache", {})),
                "zone_surface_cap": getattr(self, "_zone_surface_cache_cap", ZONE_SURFACE_CACHE_CAP),
                "layer_entries": len(self._layer_cache),
                "layer_cap": getattr(self, "_layer_cache_cap", LAYER_CACHE_CAP),
            }
        return {
            "format": "black2-static-navigation-status/v1",
            "available": True,
            "source": "ROM terrain records + IREJ MapTile collision predicate",
            "revision": self.revision,
            "coordinate_space": GRID_SPACE,
            "chunk_tiles": CHUNK_TILES,
            "cache": cache,
            "policy": {
                "candidate_edges": "flags_bit_0_clear_static_tiles_only",
                "movement_modes": {
                    "walk_run": "water and surf-required tiles excluded",
                    "bike": "water and blocks_cycling tiles excluded",
                    "surf": "water/water_edge tiles only",
                },
                "directed_edges": "directional barriers and ledges checked on both adjacent cells",
                "unknown_tiles": "excluded_by_default; explicit allow_unverified_terrain makes them candidates",
                "allow_unverified_terrain": "opt_in_only",
                "dynamic_occupancy": "caller supplied; never inferred from ROM spawns",
                "execution": "requires per-step PlayerRuntime landing verification",
            },
        }

    def zone_bounds(self, zone_id: int) -> dict[str, Any]:
        """Return decoded horizontal bounds for one Zone.

        Bounds are derived from decoded terrain records rather than guessed
        from the live camera.  This is intentionally a read-only ROM fact and
        does not claim that every tile in the rectangle is passable.
        """
        surfaces = self._zone_surfaces(int(zone_id))
        if not surfaces:
            return {
                "zone_id": int(zone_id), "status": "empty", "tile_count": 0,
                "bounds": None,
            }
        xs = [int(item["x"]) for item in surfaces]
        zs = [int(item["z"]) for item in surfaces]
        zone = self.rom.zone(int(zone_id))
        return {
            "zone_id": int(zone_id),
            "status": "decoded",
            "matrix_id": int(zone.matrix_id),
            "area_id": int(zone.area_id),
            "tile_count": len(surfaces),
            "bounds": {
                "min_x": min(xs), "max_x": max(xs),
                "min_z": min(zs), "max_z": max(zs),
                "width": max(xs) - min(xs) + 1,
                "height": max(zs) - min(zs) + 1,
            },
            "source": "ROM terrain records",
        }

    def matrix_bounds(self, matrix_id: int) -> dict[str, Any]:
        """Return the Matrix-global tile rectangle and owning Zones."""
        matrix = self.rom.matrix(int(matrix_id))
        active: list[dict[str, Any]] = []
        for cell in matrix.cells():
            chunk_id = _as_int(cell.get("chunk_id"))
            if chunk_id is None or chunk_id == MATRIX_NONE:
                continue
            active.append({
                "chunk_x": int(cell["x"]), "chunk_z": int(cell["y"]),
                "chunk_id": chunk_id,
                "zone_id": (None if _as_int(cell.get("zone_id")) == 0xFFFFFFFF else _as_int(cell.get("zone_id"))),
            })
        if not active:
            return {
                "matrix_id": int(matrix_id), "status": "empty",
                "width_chunks": int(matrix.width), "height_chunks": int(matrix.height),
                "tile_size": CHUNK_TILES, "bounds": None, "active_chunks": [],
                "zone_ids": [],
            }
        xs = [item["chunk_x"] for item in active]
        zs = [item["chunk_z"] for item in active]
        zone_ids = sorted({int(item["zone_id"]) for item in active if item.get("zone_id") is not None})
        return {
            "matrix_id": int(matrix_id), "status": "decoded",
            "width_chunks": int(matrix.width), "height_chunks": int(matrix.height),
            "tile_size": CHUNK_TILES,
            "bounds": {
                "min_x": min(xs) * CHUNK_TILES,
                "max_x": (max(xs) + 1) * CHUNK_TILES - 1,
                "min_z": min(zs) * CHUNK_TILES,
                "max_z": (max(zs) + 1) * CHUNK_TILES - 1,
                "width": (max(xs) - min(xs) + 1) * CHUNK_TILES,
                "height": (max(zs) - min(zs) + 1) * CHUNK_TILES,
            },
            "active_chunks": active,
            "zone_ids": zone_ids,
            "source": "ROM Matrix chunk table",
        }

    def matrix_catalog(self) -> list[dict[str, Any]]:
        """List every Matrix without decoding a full raster for each one."""
        identity = self.rom.static_identity()
        archive_path = identity.get("archives", {}).get("matrices")
        if not archive_path:
            return []
        files = self.rom.archive(archive_path).files
        result: list[dict[str, Any]] = []
        for matrix_id in range(len(files)):
            try:
                result.append(self.matrix_bounds(matrix_id))
            except (IndexError, ValueError, TypeError):
                result.append({"matrix_id": matrix_id, "status": "decode_error"})
        return result

    def resolve_door_geometry(self, zone_id: int, wx: int, wz: int, ex: int = 1, ez: int = 1) -> dict[str, Any]:
        """Resolve full geometry, doorstep candidates, and approach vector for a warp.

        Supports multi-tile doors (1-wide, 2-wide, 3-wide, 4-wide) and determines
        the approach/crossing vector (North, South, East, West).
        """
        ex = max(1, int(ex))
        ez = max(1, int(ez))

        walkable_footprint = []
        for x in range(wx, wx + ex):
            for z in range(wz, wz + ez):
                c = self.surface_at(zone_id, x, z, 0, allow_unverified_terrain=True)
                if c.get("walkable"):
                    walkable_footprint.append((x, z))

        if len(walkable_footprint) == ex * ez:
            return {
                "type": "walkable_mat",
                "width": ex,
                "height": ez,
                "width_category": f"{ex}_wide" if ex >= ez else f"{ez}_high",
                "facing": "any",
                "entry_direction": "directly",
                "entry_vector": {"dx": 0, "dz": 0},
                "doorsteps": walkable_footprint,
                "primary_doorstep": (wx, wz),
            }

        south_steps = [(x, wz + ez) for x in range(wx, wx + ex) if self.surface_at(zone_id, x, wz + ez, 0, allow_unverified_terrain=True).get("walkable")]
        north_steps = [(x, wz - 1) for x in range(wx, wx + ex) if self.surface_at(zone_id, x, wz - 1, 0, allow_unverified_terrain=True).get("walkable")]
        west_steps = [(wx - 1, z) for z in range(wz, wz + ez) if self.surface_at(zone_id, wx - 1, z, 0, allow_unverified_terrain=True).get("walkable")]
        east_steps = [(wx + ex, z) for z in range(wz, wz + ez) if self.surface_at(zone_id, wx + ex, z, 0, allow_unverified_terrain=True).get("walkable")]

        if len(south_steps) >= len(north_steps) and south_steps:
            facing, entry_dir, dx, dz, steps = "South", "North", 0, -1, south_steps
        elif north_steps:
            facing, entry_dir, dx, dz, steps = "North", "South", 0, 1, north_steps
        elif len(west_steps) >= len(east_steps) and west_steps:
            facing, entry_dir, dx, dz, steps = "West", "East", 1, 0, west_steps
        elif east_steps:
            facing, entry_dir, dx, dz, steps = "East", "West", -1, 0, east_steps
        else:
            facing, entry_dir, dx, dz, steps = "South", "North", 0, -1, [(wx, wz + ez)]

        return {
            "type": "building_portal",
            "width": ex,
            "height": ez,
            "width_category": f"{ex}_wide" if ex >= ez else f"{ez}_high",
            "facing": facing,
            "entry_direction": entry_dir,
            "entry_vector": {"dx": dx, "dz": dz},
            "doorsteps": steps,
            "primary_doorstep": steps[0] if steps else (wx, wz),
        }

    def _resolve_warp_doorstep(self, zone_id: int, wx: int, wz: int, ex: int = 1, ez: int = 1) -> tuple[int, int]:
        geom = self.resolve_door_geometry(zone_id, wx, wz, ex, ez)
        return geom["primary_doorstep"]

    def event_overlay_at(self, zone_id: int, x: int, z: int) -> list[dict[str, Any]]:
        """Return lossless event candidates at a tile.

        Static ROM event candidates are cached by (zone, x, z). Returned rows
        are copied so runtime enrichment can mutate them safely.
        """
        cache_key = (int(zone_id), int(x), int(z))
        event_cache = getattr(self, "_event_overlay_lookup_cache", None)
        if event_cache is not None:
            with self._lock:
                cached = event_cache.get(cache_key)
                if cached is not None:
                    event_cache.move_to_end(cache_key)
                    return [dict(item) for item in cached]
        zone = self.rom.zone(int(zone_id))
        entities = self.rom.entities(int(zone.entities_id))
        result: list[dict[str, Any]] = []
        tx, tz = int(x), int(z)
        for warp in entities.get("warps") or []:
            try:
                # Gen-5 warp trigger coordinates in ROM are tile centers (or top-left) in 16-world units.
                # Must use floor division (// 16) so 1688 / 16 (105.5) maps to tile 105, not rounded up to 106.
                wx = int(float(warp.get("x_raw", 0)) // TILE_WORLD)
                wz = int(float(warp.get("y_raw", 0)) // TILE_WORLD)
                ex = max(1, int(warp.get("x_extent_raw") or 1))
                ez = max(1, int(warp.get("y_extent_raw") or 1))
                # Standard Doorstep Rule: If raw door is inside a non-walkable wall,
                # resolve [D] to the adjacent walkable doorstep tile in front of the door.
                warp_key = (int(zone_id), int(warp.get("id") or 0))
                doorstep_cache = getattr(self, "_warp_doorstep_cache", None)
                doorstep = None
                if doorstep_cache is not None:
                    with self._lock:
                        doorstep = doorstep_cache.get(warp_key)
                if doorstep is None:
                    doorstep = self._resolve_warp_doorstep(int(zone_id), wx, wz, ex, ez)
                    if doorstep_cache is not None:
                        with self._lock:
                            doorstep_cache[warp_key] = doorstep
                dx, dz = doorstep
                geom = self.resolve_door_geometry(int(zone_id), wx, wz, ex, ez)
                in_footprint = (wx <= tx < wx + ex and wz <= tz < wz + ez)
                in_doorstep = (tx, tz) in geom["doorsteps"] or (dx <= tx < dx + ex and dz <= tz < dz + ez)
                is_step = (tx, tz) in geom["doorsteps"]
                if is_step or in_footprint:
                    result.append({
                        "kind": "warp", "symbol": "D",
                        "warp_id": warp.get("id"),
                        "target_zone_id_candidate": warp.get("target_zone_or_map_raw"),
                        "position": {"x": dx, "z": dz},
                        "portal_position": {"x": wx, "z": wz},
                        "extent": {"x": ex, "z": ez},
                        "door_geometry": geom,
                        "approach_direction": geom["facing"],
                        "entry_direction": geom["entry_direction"],
                        "doorstep_candidates": geom["doorsteps"],
                        "is_doorstep": is_step,
                        "is_portal_doorway": in_footprint,
                        "semantic_status": "ROM candidate; runtime transition not verified",
                    })
            except (TypeError, ValueError):
                continue
        for npc in entities.get("npcs") or []:
            if int(npc.get("x", -999999)) == tx and int(npc.get("y", -999999)) == tz:
                result.append({
                    "kind": "npc", "symbol": "N", "npc_id": npc.get("id"),
                    "record_index": npc.get("record_index"),
                    "sprite_id": npc.get("sprite_id"),
                    "movement_id": npc.get("movement_id"),
                    "movement2_raw": npc.get("movement2_raw"),
                    "flag_id": npc.get("flag_id"),
                    "script_id": npc.get("script_id"),
                    "direction_raw": npc.get("direction_raw"),
                    "facing_id": npc.get("facing_id"),
                    "sight_raw": npc.get("sight_raw"),
                    "position": {"x": tx, "z": tz},
                    "static_entity_id": f"rom-npc:z{int(zone_id)}:id{npc.get('id', '?')}:r{npc.get('record_index', '?')}",
                    "semantic_status": "ROM spawn candidate; runtime occupancy/flag may differ",
                })
        for furniture in entities.get("furniture") or []:
            try:
                fx = int(furniture.get("x", -999999))
                fz = int(furniture.get("y", -999999))
                arg3 = furniture.get("arg3_raw")
                script_id = int(furniture.get("script_id") or 0)
                zid = int(zone_id)
                # Gen-5 signposts: arg3==6, or specific registered entrance signposts (e.g. Zone 444 Ranch sign script 1)
                is_sign = bool(arg3 == 6 or (zid == 444 and script_id == 1))
                is_trash_can = bool(arg3 == 1)
                # Gen-5 type 4 with script 0 is an invisible buried hidden item (Dowsing Machine item).
                # It has no visible 3D mesh and does not block movement.
                # Gen-5 Interactibility (arg3_raw == 4) is hidden item (script_id 0 is buried dowsing item,
                # 8000..8999 is global indexed hidden item, others are contextual hidden pickups).
                # All hidden items have no 3D blocking collision mesh and do not obstruct movement.
                is_hidden_item = bool(arg3 == 4)
                fy = int(furniture.get("z", 0))
                is_center = (fx == tx and fz == tz)
                # Signposts are physically located at their exact center (fx, fz).
                # Do NOT duplicate 'S' across 3 tiles on the radar map.
                if is_center:
                    if is_sign:
                        kind, symbol = "signpost", "S"
                    elif is_trash_can:
                        kind, symbol = "trash_can", "K"
                    elif is_hidden_item:
                        kind, symbol = "hidden_item", "h"
                    else:
                        kind, symbol = "furniture", "O" 
                    result.append({
                        "kind": kind,
                        "symbol": symbol,
                        "furniture_id": furniture.get("id"),
                        "record_index": furniture.get("record_index"),
                        "script_id": furniture.get("script_id"),
                        "arg2_raw": furniture.get("arg2_raw"),
                        "arg3_raw": furniture.get("arg3_raw"),
                        "arg4_raw": furniture.get("arg4_raw"),
                        "position": {"x": fx, "z": fz},
                        "height_y": fy,
                        "is_center": is_center,
                        "is_hidden_item": is_hidden_item,
                        "wing_offset": 0,
                        "semantic_status": "ROM interaction candidate",
                    })
            except (TypeError, ValueError):
                continue
        frozen = tuple(dict(item) for item in result)
        if event_cache is not None:
            with self._lock:
                event_cache[cache_key] = frozen
                event_cache.move_to_end(cache_key)
                while len(event_cache) > getattr(self, "_event_overlay_cache_cap", 8192):
                    event_cache.popitem(last=False)
        return [dict(item) for item in frozen]

    def event_overlays_in_bounds(
        self, zone_id: int, min_x: int, max_x: int, min_z: int, max_z: int,
    ) -> list[dict[str, Any]]:
        """Return event candidates intersecting a rectangular tile range.

        This is the bulk counterpart of :meth:`event_overlay_at`. Fuzzy
        world rasters must not call the per-tile decoder hundreds of
        thousands of times just to preserve a door/NPC/object marker.
        Results are still ROM candidates; runtime flags and actor occupancy
        remain authoritative.
        """
        zone = self.rom.zone(int(zone_id))
        entities = self.rom.entities(int(zone.entities_id))
        x0, x1 = int(min_x), int(max_x)
        z0, z1 = int(min_z), int(max_z)
        result: list[dict[str, Any]] = []
        for warp in entities.get("warps") or []:
            try:
                wx = int(float(warp.get("x_raw", 0)) // TILE_WORLD)
                wz = int(float(warp.get("y_raw", 0)) // TILE_WORLD)
                ex = max(1, int(warp.get("x_extent_raw") or 1))
                ez = max(1, int(warp.get("y_extent_raw") or 1))
                if wx <= x1 and wx + ex - 1 >= x0 and wz <= z1 and wz + ez - 1 >= z0:
                    result.append({
                        "kind": "warp", "symbol": "D",
                        "warp_id": warp.get("id"),
                        "target_zone_id_candidate": warp.get("target_zone_or_map_raw"),
                        "position": {"x": wx, "z": wz},
                        "extent": {"x": ex, "z": ez},
                        "semantic_status": "ROM candidate; runtime transition not verified",
                    })
            except (TypeError, ValueError):
                continue
        for npc in entities.get("npcs") or []:
            try:
                nx, nz = int(npc.get("x", -999999)), int(npc.get("y", -999999))
            except (TypeError, ValueError):
                continue
            if x0 <= nx <= x1 and z0 <= nz <= z1:
                result.append({
                    "kind": "npc", "symbol": "N", "npc_id": npc.get("id"),
                    "sprite_id": npc.get("sprite_id"),
                    "position": {"x": nx, "z": nz},
                    "semantic_status": "ROM spawn candidate; runtime occupancy/flag may differ",
                })
        for furniture in entities.get("furniture") or []:
            try:
                fx, fz = int(furniture.get("x", -999999)), int(furniture.get("y", -999999))
                fy = int(furniture.get("z", 0))
                arg3 = furniture.get("arg3_raw")
                script_id = int(furniture.get("script_id") or 0)
                is_sign = (arg3 == 6)
                is_trash_can = (arg3 == 1)
                is_hidden_item = (arg3 == 4)
                if x0 <= fx <= x1 and z0 <= fz <= z1:
                    if is_sign:
                        kind, symbol = "signpost", "S"
                    elif is_trash_can:
                        kind, symbol = "trash_can", "K"
                    elif is_hidden_item:
                        kind, symbol = "hidden_item", "h"
                    else:
                        kind, symbol = "furniture", "O"
                    result.append({
                        "kind": kind,
                        "symbol": symbol,
                        "height_y": fy,
                        "is_hidden_item": is_hidden_item,
                        "physical_obstacle": not is_hidden_item,
                        "furniture_id": furniture.get("id"),
                        "script_id": furniture.get("script_id"),
                        "arg2_raw": furniture.get("arg2_raw"),
                        "arg3_raw": furniture.get("arg3_raw"),
                        "arg4_raw": furniture.get("arg4_raw"),
                        "position": {"x": fx, "z": fz},
                        "semantic_status": "ROM interaction candidate",
                    })
            except (TypeError, ValueError):
                continue
        return result

    def resolve_zone_for_global(
        self, matrix_id: int, x: int, z: int, *, preferred_zone: int | None = None,
    ) -> int | None:
        """Resolve a Matrix-global grid coordinate to Zone metadata.

        Matrix ownership is authoritative when present. Standalone matrices
        without a Zone table may use the live/current Zone as a preferred
        owner, but only when that Zone actually references the same Matrix.
        This keeps Zone out of the public address without inventing a shared
        origin between unrelated matrices.
        """
        matrix = self.rom.matrix(int(matrix_id))
        cell_x, cell_z = int(x) // CHUNK_TILES, int(z) // CHUNK_TILES
        if not (0 <= cell_x < matrix.width and 0 <= cell_z < matrix.height):
            return None
        cell = matrix.cell(cell_x, cell_z)
        chunk_id = _as_int(cell.get("chunk_id"))
        if chunk_id is None or chunk_id == MATRIX_NONE:
            return None
        owner = _as_int(cell.get("zone_id"))
        if owner is not None:
            try:
                header = self.rom.zone(owner)
            except (IndexError, ValueError):
                return None
            return owner if int(header.matrix_id) == int(matrix_id) else None
        if preferred_zone is not None:
            try:
                header = self.rom.zone(int(preferred_zone))
                if int(header.matrix_id) == int(matrix_id):
                    return int(preferred_zone)
            except (IndexError, ValueError):
                pass
        # Read-only fallback for a Matrix referenced by exactly one Zone.
        zone_count = _as_int(getattr(self.rom, "zone_count", None))
        if zone_count is not None:
            matches = []
            for zone_id in range(zone_count):
                try:
                    if int(self.rom.zone(zone_id).matrix_id) == int(matrix_id):
                        matches.append(zone_id)
                        if len(matches) > 1:
                            break
                except (IndexError, ValueError):
                    continue
            if len(matches) == 1:
                return matches[0]
        return None

    def global_coordinate(self, zone_id: int, x: int, y: int, z: int) -> dict[str, Any]:
        """Return the public Matrix-global address for a canonical Zone/GPos."""
        zone = self.rom.zone(int(zone_id))
        return {
            "type": "global_grid",
            "space": "gen5-matrix-grid-v1",
            "matrix_id": int(zone.matrix_id),
            "x": int(x), "y": int(y), "z": int(z),
            "resolved_zone_id": int(zone_id),
        }

    def _matrix_cells_for_layer(
        self, matrix_id: int, y: int, *, player_sample: dict[str, Any] | None = None,
    ) -> dict[tuple[int, int], StaticNavigationCell]:
        matrix = self.rom.matrix(int(matrix_id))
        if not matrix.has_zones or matrix.zone_ids is None:
            return {}
        player_zone = _as_int((player_sample or {}).get("zone_id"))
        result: dict[tuple[int, int], StaticNavigationCell] = {}
        for owner in sorted({int(v) for v in matrix.zone_ids if isinstance(v, int)}):
            try:
                header = self.rom.zone(owner)
            except (IndexError, ValueError):
                continue
            if int(header.matrix_id) != int(matrix_id):
                continue
            anchor = self._anchor_from_sample(player_sample, owner) if owner == player_zone else None
            for key, cell in self._cells_for_layer(owner, int(y), anchor=anchor).items():
                if self.resolve_zone_for_global(matrix_id, key[0], key[1]) != owner:
                    continue
                result.setdefault(key, cell)
        return result

    def find_global_path(
        self,
        start: NavNode,
        *,
        matrix_id: int,
        x: int, y: int, z: int,
        movement_mode: str = "walk",
        player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        constraint_evaluator: Any | None = None,
        allow_unverified_terrain: bool = False,
    ) -> dict[str, Any]:
        """Lazy A* across Zone boundaries inside one spatial MapMatrix.

        The graph is not materialized for the whole Matrix. Zone ownership is
        resolved per explored coordinate, and each Zone layer is decoded only
        when A* actually reaches it. This keeps long routes practical on the
        large overworld Matrix.
        """
        start_header = self.rom.zone(int(start.zone_id))
        if int(start_header.matrix_id) != int(matrix_id):
            return {"reachable": False, "reason": "start and goal belong to different Matrix coordinate domains", "path": [], "confidence": "candidate_static"}
        if int(start.y) != int(y):
            return {"reachable": False, "reason": "global planner does not infer an elevation transition", "path": [], "confidence": "candidate_static"}
        goal_zone = self.resolve_zone_for_global(int(matrix_id), int(x), int(z), preferred_zone=start.zone_id)
        if goal_zone is None:
            return {"reachable": False, "reason": "global destination has no ROM Zone ownership in this Matrix", "path": [], "confidence": "candidate_static"}
        goal = NavNode(goal_zone, int(x), int(y), int(z))
        live_zone = _as_int((player_sample or {}).get("zone_id"))
        zone_cells: dict[int, dict[tuple[int, int], StaticNavigationCell]] = {}

        def cell_at(cx: int, cz: int) -> StaticNavigationCell | None:
            owner = self.resolve_zone_for_global(int(matrix_id), int(cx), int(cz), preferred_zone=start.zone_id)
            if owner is None:
                return None
            cells = zone_cells.get(owner)
            if cells is None:
                anchor = self._anchor_from_sample(player_sample, owner) if owner == live_zone else None
                cells = self._planning_cells(
                    owner, int(y), anchor=anchor,
                    allow_unverified_terrain=allow_unverified_terrain,
                )
                zone_cells[owner] = cells
            return cells.get((int(cx), int(cz)))

        start_cell, goal_cell = cell_at(start.x, start.z), cell_at(goal.x, goal.z)
        if start_cell is None or goal_cell is None:
            missing = "start" if start_cell is None else "goal"
            return {"reachable": False, "reason": f"{missing} is not a flag-clear Matrix-global terrain candidate", "path": [], "confidence": "candidate_static"}

        start_allowed, start_reason = _movement_eligibility(
            start_cell, movement_mode,
            allow_unverified_terrain=allow_unverified_terrain,
        )
        goal_allowed, goal_reason = _movement_eligibility(
            goal_cell, movement_mode,
            allow_unverified_terrain=allow_unverified_terrain,
        )
        if not start_allowed or not goal_allowed:
            return {
                "reachable": False,
                "reason": "movement mode cannot enter the Matrix-global terrain candidate",
                "movement_mode": str(movement_mode or "walk").lower(),
                "movement_blocker": {
                    "tile": "start" if not start_allowed else "goal",
                    "reason": start_reason if not start_allowed else goal_reason,
                },
                "path": [], "confidence": "candidate_static",
            }

        from .navigation_planning import normalize_occupancy
        normalized = normalize_occupancy(occupied or (), default_zone=start.zone_id, default_y=start.y)
        occupied_xy = {
            (int((item.get("grid") or {}).get("x")), int((item.get("grid") or {}).get("z")))
            for item in normalized
            if (item.get("grid") or {}).get("y") in (None, int(y))
            and (item.get("grid") or {}).get("x") is not None
            and (item.get("grid") or {}).get("z") is not None
        }
        occupied_xy.discard((start.x, start.z))
        if (goal.x, goal.z) in occupied_xy:
            return {"reachable": False, "reason": "goal is occupied by a runtime actor", "path": [], "confidence": "candidate_static"}
        goal_evaluation = (
            constraint_evaluator.evaluate(goal, is_goal=True)
            if constraint_evaluator is not None else {"blocked": False}
        )
        if goal_evaluation.get("blocked"):
            return {
                "reachable": False, "reason": "navigation policy blocks the goal tile",
                "policy_blocked": True,
                "blocking_constraints": goal_evaluation.get("blocking_constraints") or [],
                "path": [], "confidence": "candidate_static",
            }

        def neighbors(current: StaticNavigationCell) -> Iterable[StaticNavigationCell]:
            for dx, dz in ((0, -1), (-1, 0), (1, 0), (0, 1)):
                direction = _direction(dx, dz)
                if direction in current.blocked_directions:
                    continue
                if current.ledge_direction and direction != current.ledge_direction:
                    continue
                nx, nz = current.node.x + dx, current.node.z + dz
                if (nx, nz) in occupied_xy:
                    continue
                candidate = cell_at(nx, nz)
                if candidate is None:
                    continue
                allowed_candidate, _reason = _movement_eligibility(
                    candidate, movement_mode,
                    allow_unverified_terrain=allow_unverified_terrain,
                )
                if not allowed_candidate:
                    continue
                if _opposite(direction) in candidate.blocked_directions:
                    continue
                yield candidate

        start_state = (start.x, start.z, 0, 0)
        queue: list[tuple[float, int, float, int, int, int, int]] = [
            (abs(start.x - goal.x) + abs(start.z - goal.z), 0, 0.0, 0, start.x, start.z, 0)
        ]
        costs: dict[tuple[int, int, int, int], tuple[float, int]] = {start_state: (0, 0)}
        constraint_costs: dict[tuple[int, int, int, int], float] = {start_state: 0.0}
        parent: dict[tuple[int, int, int, int], tuple[int, int, int, int]] = {}
        encountered: dict[str, dict[str, Any]] = {}
        blocked_constraints: dict[str, dict[str, Any]] = {}
        goal_state: tuple[int, int, int, int] | None = None
        while queue:
            _f_cost, turns, g_cost, steps, cx, cz, packed_dir = heapq.heappop(queue)
            dx = ((packed_dir >> 8) & 0xFF) - 128 if packed_dir else 0
            dz = (packed_dir & 0xFF) - 128 if packed_dir else 0
            state = (cx, cz, dx, dz)
            if (g_cost, turns) != costs.get(state):
                continue
            if (cx, cz) == (goal.x, goal.z):
                goal_state = state
                break
            current = cell_at(cx, cz)
            if current is None:
                continue
            for neighbor in neighbors(current):
                ndx, ndz = neighbor.node.x - cx, neighbor.node.z - cz
                evaluation = (
                    constraint_evaluator.evaluate(neighbor.node, is_goal=neighbor.node == goal)
                    if constraint_evaluator is not None
                    else {"blocked": False, "extra_cost": 0.0, "constraints": []}
                )
                for item in evaluation.get("constraints") or ():
                    encountered[str(item.get("constraint_id"))] = item
                if evaluation.get("blocked"):
                    for item in evaluation.get("blocking_constraints") or ():
                        blocked_constraints[str(item.get("constraint_id"))] = item
                    continue
                nsteps = steps + 1
                nturns = turns + (1 if (dx or dz) and (ndx, ndz) != (dx, dz) else 0)
                nstate = (neighbor.node.x, neighbor.node.z, ndx, ndz)
                extra_cost = float(evaluation.get("extra_cost", 0.0) or 0.0)
                is_grass = bool(
                    neighbor.tile_class in (4, 5)
                    or (neighbor.material or {}).get("kind") in ("tall_grass", "dark_grass")
                )
                if is_grass:
                    eff_policy = (
                        constraint_evaluator.policy.get("encounter_grass", "soft_avoid")
                        if constraint_evaluator is not None
                        else "soft_avoid"
                    )
                    if eff_policy == "hard_avoid" and (neighbor.node.x, neighbor.node.z) != (goal.x, goal.z):
                        continue
                    elif eff_policy in ("soft_avoid", "soft_cost"):
                        grass_extra = 25.0 if neighbor.tile_class == 5 else 15.0
                        extra_cost += grass_extra
                ncost = (g_cost + 1.0 + extra_cost, nturns)
                if ncost >= costs.get(nstate, (10**9, 10**9)):
                    continue
                costs[nstate] = ncost
                constraint_costs[nstate] = constraint_costs.get(state, 0.0) + extra_cost
                parent[nstate] = state
                heuristic = abs(neighbor.node.x - goal.x) + abs(neighbor.node.z - goal.z)
                packed = ((ndx + 128) << 8) | (ndz + 128)
                heapq.heappush(queue, (ncost[0] + heuristic, nturns, ncost[0], nsteps, neighbor.node.x, neighbor.node.z, packed))
        if goal_state is None:
            return {
                "reachable": False,
                "reason": "navigation policy blocked all Matrix-global paths" if blocked_constraints else "no connected Matrix-global static path",
                "policy_blocked": bool(blocked_constraints),
                "blocking_constraints": list(blocked_constraints.values()),
                "constraints_encountered": list(encountered.values()),
                "path": [], "confidence": "candidate_static",
            }
        states = [goal_state]
        while states[-1] != start_state:
            states.append(parent[states[-1]])
        states.reverse()
        path = []
        for state in states:
            cell = cell_at(state[0], state[1])
            if cell is None:
                return {"reachable": False, "reason": "global path reconstruction lost a terrain cell", "path": [], "confidence": "candidate_static"}
            path.append(cell.node.public())
        zone_transitions = []
        for a, b in zip(path, path[1:]):
            if a["zone_id"] != b["zone_id"]:
                zone_transitions.append({"from_zone_id": a["zone_id"], "to_zone_id": b["zone_id"], "at": {"x": b["x"], "y": b["y"], "z": b["z"]}})
        unverified_count = sum(
            1 for item in path
            if _is_unverified_material(cell_at(int(item["x"]), int(item["z"])))
        )
        return {
            "reachable": True,
            "movement_mode": str(movement_mode or "walk").lower(),
            "path": path,
            "steps": len(path) - 1,
            "turns": costs[goal_state][1],
            "cost": float(costs[goal_state][0]),
            "movement_cost": float(len(path) - 1),
            "constraint_cost": constraint_costs.get(goal_state, 0.0),
            "confidence": "candidate_static",
            "source": "rom_matrix_global_collision_candidate",
            "optimization": "shortest_steps_then_fewest_turns",
            "matrix_id": int(matrix_id),
            "zone_transitions": zone_transitions,
            "constraints_encountered": list(encountered.values()),
            "decoded_zone_count": len(zone_cells),
            "decoded_zone_ids": sorted(zone_cells),
            "terrain_policy": _terrain_policy(allow_unverified_terrain),
            "unverified_tile_count": unverified_count,
            "world_revision": self.revision,
        }

    def movement_rules(
        self, zone_id: int, *, path: Iterable[NavNode] = (),
        player_sample: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return transport restrictions across every Zone touched by a path."""
        path_nodes = list(path or ())
        zone_ids = sorted({int(node.zone_id) for node in path_nodes} or {int(zone_id)})
        headers = [self.rom.zone(zid) for zid in zone_ids]
        areas = [self.rom.area(int(header.area_id)) for header in headers]
        enable_running = all(bool(header.enable_running) for header in headers)
        enable_cycling = all(bool(header.enable_cycling) for header in headers)
        bike_blockers: list[dict[str, Any]] = []
        if path_nodes:
            by_zone: dict[int, list[NavNode]] = {}
            for node in path_nodes:
                by_zone.setdefault(int(node.zone_id), []).append(node)
            live_zone = _as_int((player_sample or {}).get("zone_id"))
            for zid, nodes in by_zone.items():
                anchor = self._anchor_from_sample(player_sample, zid) if zid == live_zone else None
                cells_by_layer: dict[int, dict[tuple[int, int], Any]] = {}
                for node in nodes:
                    layer_y = int(node.y or 0)
                    if layer_y not in cells_by_layer:
                        cells_by_layer[layer_y] = self._cells_for_layer(zid, layer_y, anchor=anchor)
                    cell = cells_by_layer[layer_y].get((node.x, node.z))
                    material = (cell.material if cell else None) or {}
                    if material.get("blocks_cycling"):
                        bike_blockers.append({
                            "zone_id": node.zone_id, "x": node.x, "y": node.y, "z": node.z,
                            "kind": material.get("kind"), "label": material.get("label"),
                        })
        environments = {"exterior" if bool(area.is_exterior) else "interior" for area in areas}
        return {
            "zone_id": int(zone_id),
            "zone_ids": zone_ids,
            "environment": environments.pop() if len(environments) == 1 else "mixed",
            "enable_running": enable_running,
            "enable_cycling": enable_cycling,
            "bike_blockers": bike_blockers,
            "source": "ROM ZoneHeader + AreaHeader + decoded TileClass semantics across route Zones",
        }

    def warp_display_center(
        self, zone_id: int, *, picked_id: Any = None,
        x: int | None = None, z: int | None = None,
    ) -> dict[str, Any] | None:
        """Return the canonical center of a rendered multi-tile warp.

        The entity decoder exposes the raw warp anchor in world units.  The
        browser deliberately draws a wide warp marker at the center of its
        footprint, however.  Keeping this conversion here means a click on
        the marker can use that display center without mutating the raw ROM
        record or making the renderer's presentation offset part of the
        navigation coordinate contract.
        """
        zone = self.rom.zone(int(zone_id))
        records = (self.rom.entities(zone.entities_id) or {}).get("warps") or []
        selected = None
        numeric_id = None
        if picked_id is not None:
            match = re.search(r"(\d+)$", str(picked_id))
            if match:
                numeric_id = int(match.group(1))
        if numeric_id is not None:
            selected = next((row for row in records if _as_int(row.get("id")) == numeric_id), None)
        if selected is None and x is not None and z is not None:
            # Fallback for clients that omit the overlay id.  The hit point
            # may be anywhere inside the marker footprint, so use the raw
            # anchor plus its declared extent for the match radius.
            ranked = []
            for row in records:
                raw_x, raw_z = _finite(row.get("x_raw")), _finite(row.get("y_raw"))
                if raw_x is None or raw_z is None:
                    continue
                width = max(1, _as_int(row.get("x_extent_raw")) or 1)
                height = max(1, _as_int(row.get("y_extent_raw")) or 1)
                center_x = raw_x + (width - 1) * TILE_WORLD * 0.5
                center_z = raw_z + (height - 1) * TILE_WORLD * 0.5
                distance = abs(float(x) - center_x) + abs(float(z) - center_z)
                ranked.append((distance, row))
            if ranked:
                distance, candidate = min(ranked, key=lambda item: (item[0], _as_int(item[1].get("id")) or 0))
                if distance <= 2 * TILE_WORLD:
                    selected = candidate
        if selected is None:
            return None
        raw_x, raw_z = _finite(selected.get("x_raw")), _finite(selected.get("y_raw"))
        if raw_x is None or raw_z is None:
            return None
        width = max(1, _as_int(selected.get("x_extent_raw")) or 1)
        height = max(1, _as_int(selected.get("y_extent_raw")) or 1)
        center = {
            "x": raw_x + (width - 1) * TILE_WORLD * 0.5,
            "y": None,
            "z": raw_z + (height - 1) * TILE_WORLD * 0.5,
        }
        return {
            "warp_id": _as_int(selected.get("id")),
            "raw_world": {"x": raw_x, "y": center["y"], "z": raw_z},
            "display_world_center": center,
            "semantic_entry_grid": {
                "x": math.floor(center["x"] / TILE_WORLD),
                "y": 0,
                "z": math.floor(center["z"] / TILE_WORLD),
            },
            "footprint": {"width": width, "height": height},
            "source": "ROM warp anchor + declared footprint center",
        }

    @staticmethod
    def _anchor_from_sample(sample: dict[str, Any] | None, zone_id: int) -> dict[str, Any] | None:
        if not isinstance(sample, dict) or sample.get("zone_id") != zone_id:
            return None
        position = sample.get("position") or {}
        grid = position.get("grid") or sample.get("grid") or {}
        world = position.get("world") or sample.get("world") or {}
        gx, gy, gz = (_as_int(grid.get(k)) for k in ("x", "y", "z"))
        wx, wy, wz = (_finite(world.get(k)) for k in ("x", "y", "z"))
        if None in (gx, gy, gz, wx, wy, wz):
            return None
        live_tile = position.get("tile_under") or sample.get("environment", {}).get("tile_under") or sample.get("tile_under") or {}
        return {
            "grid": {"x": gx, "y": gy, "z": gz},
            "world": {"x": wx, "y": wy, "z": wz},
            "chunk": {"x": gx // CHUNK_TILES, "z": gz // CHUNK_TILES},
            "tile_under": live_tile,
        }

    def _zone_surfaces(self, zone_id: int) -> tuple[dict[str, Any], ...]:
        """Return all decoded surfaces in active matrix cells for ``zone_id``."""
        zone_id = int(zone_id)
        with self._lock:
            cached = self._zone_surfaces_cache.get(zone_id)
            if cached is not None:
                self._zone_surfaces_cache.move_to_end(zone_id)
                return cached

        frozen = self._decode_zone_surfaces(zone_id)
        with self._lock:
            # Another caller may have completed the same lazy decode first.
            cached = self._zone_surfaces_cache.get(zone_id)
            if cached is not None:
                self._zone_surfaces_cache.move_to_end(zone_id)
                return cached
            self._zone_surfaces_cache[zone_id] = frozen
            self._zone_surfaces_cache.move_to_end(zone_id)
            while len(self._zone_surfaces_cache) > getattr(self, "_zone_surface_cache_cap", ZONE_SURFACE_CACHE_CAP):
                self._zone_surfaces_cache.popitem(last=False)
            return frozen

    def _decode_zone_surfaces(self, zone_id: int) -> tuple[dict[str, Any], ...]:
        """Decode one Zone outside the cache lock; callers publish via LRU."""
        zone = self.rom.zone(int(zone_id))
        matrix = self.rom.matrix(zone.matrix_id)
        decoded_chunks: dict[int, tuple[Any, ...]] = {}
        result: list[dict[str, Any]] = []
        for matrix_cell in matrix.cells():
            chunk_id = _as_int(matrix_cell.get("chunk_id"))
            if chunk_id is None or chunk_id == MATRIX_NONE:
                continue
            owner = matrix_cell.get("zone_id")
            if owner is not None and owner != int(zone_id):
                continue
            try:
                layers = decoded_chunks.setdefault(chunk_id, decode_chunk_terrain(self.rom.chunk(chunk_id)))
            except (IndexError, ValueError, TypeError):
                # An undecodable cell is unknown, never an open fallback.
                continue
            for layer in layers:
                for local_z in range(layer.height):
                    for local_x in range(layer.width):
                        try:
                            surface = layer.tile(local_x, local_z)
                        except (IndexError, ValueError):
                            continue
                        result.append({
                            "zone_id": int(zone_id),
                            "matrix_cell": {"x": int(matrix_cell["x"]), "z": int(matrix_cell["y"])},
                            "chunk_id": chunk_id,
                            "x": int(matrix_cell["x"]) * CHUNK_TILES + local_x,
                            "z": int(matrix_cell["y"]) * CHUNK_TILES + local_z,
                            "local_x": local_x,
                            "local_z": local_z,
                            "layer_index": int(layer.layer_index),
                            "surface": surface,
                        })
        return tuple(result)

    @staticmethod
    def _anchor_relative_height(
        surfaces: Iterable[dict[str, Any]], anchor: dict[str, Any] | None,
    ) -> float | None:
        if not anchor:
            return None
        grid = anchor.get("grid") or {}
        ax, az = _as_int(grid.get("x")), _as_int(grid.get("z"))
        if ax is None or az is None:
            return None
        live_tile = anchor.get("tile_under") or {}
        candidates = []
        for item in surfaces:
            if item.get("x") != ax or item.get("z") != az:
                continue
            surface = item.get("surface") or {}
            sampled = surface.get("sampled_tile_type") or {}
            height = (surface.get("height") or {}).get("chunk_relative_world_y")
            if _finite(height) is None:
                continue
            if (
                isinstance(live_tile, dict)
                and _as_int(live_tile.get("class")) is not None
                and _as_int(live_tile.get("flags")) is not None
                and _as_int(sampled.get("class")) == _as_int(live_tile.get("class"))
                and _as_int(sampled.get("flags")) == _as_int(live_tile.get("flags"))
            ):
                return float(height)
            candidates.append(float(height))
        if candidates and all(abs(c - candidates[0]) < 0.1 for c in candidates):
            return candidates[0]
        return candidates[0] if len(candidates) == 1 else None

    def _height_signature(self, anchor: dict[str, Any] | None) -> str:
        if not anchor:
            return "none"
        grid, world = anchor.get("grid") or {}, anchor.get("world") or {}
        return ":".join(str(grid.get(k)) for k in ("x", "y", "z")) + ":" + str(world.get("y"))

    def _cells_for_layer(
        self, zone_id: int, y: int, *, anchor: dict[str, Any] | None = None,
    ) -> dict[tuple[int, int], StaticNavigationCell]:
        signature = (int(zone_id), int(y), self._height_signature(anchor))
        with self._lock:
            cached = self._layer_cache.get(signature)
            if cached is not None:
                self._layer_cache.move_to_end(signature)
                return cached

        surfaces = self._zone_surfaces(int(zone_id))
        anchor_grid = (anchor or {}).get("grid") or {}
        anchor_world = (anchor or {}).get("world") or {}
        anchor_relative = self._anchor_relative_height(surfaces, anchor)
        anchor_y = _as_int(anchor_grid.get("y"))
        anchor_world_y = _finite(anchor_world.get("y"))
        cells: dict[tuple[int, int], StaticNavigationCell] = {}
        for item in surfaces:
            surface = item.get("surface") or {}
            collision = surface.get("collision") or {}
            # Ledges and directional barriers are encoded with the same
            # collision flag as a hard obstacle, but they are not ordinary
            # dead tiles: they are traversable only through a directed edge.
            # Keep them in the graph so the neighbor predicate can enforce
            # the one-way rule instead of deleting the edge entirely.
            directional = bool(
                collision.get("ledge_direction")
                or collision.get("blocked_directions")
            )
            if collision.get("static_blocked") is not False and not directional:
                continue
            relative = _finite((surface.get("height") or {}).get("chunk_relative_world_y"))
            resolved_y = int(y)
            if anchor_relative is not None and anchor_y is not None and anchor_world_y is not None and relative is not None:
                aligned_world_y = anchor_world_y + (relative - anchor_relative)
                candidate_y = anchor_y + int(round((aligned_world_y - anchor_world_y) / TILE_WORLD))
                if candidate_y != int(y):
                    continue
            elif relative is not None:
                candidate_y = int(round(relative / TILE_WORLD))
                if candidate_y != int(y):
                    continue
            node = NavNode(int(zone_id), int(item["x"]), resolved_y, int(item["z"]))
            material = surface.get("material") if isinstance(surface.get("material"), dict) else {}
            source = {
                "chunk_id": item.get("chunk_id"),
                "matrix_cell": item.get("matrix_cell"),
                "local_tile": {"x": item.get("local_x"), "z": item.get("local_z")},
                "record_offset": surface.get("record_offset"),
            }
            cell = StaticNavigationCell(
                node=node,
                layer_index=int(item.get("layer_index") or 0),
                tile_class=int((surface.get("raw") or {}).get("tile_class", 0)),
                flags=int((surface.get("raw") or {}).get("flags", 0)),
                static_blocked=False,
                blocked_directions=tuple(str(v) for v in collision.get("blocked_directions") or ()),
                ledge_direction=collision.get("ledge_direction"),
                relative_height=relative,
                material=dict(material),
                source=source,
            )
            # If two terrain layers collapse onto one GPos layer, keep the
            # first deterministic candidate.  The ambiguity remains visible
            # through ``surface_at`` and does not create duplicate graph nodes.
            cells.setdefault((node.x, node.z), cell)

        with self._lock:
            cached = self._layer_cache.get(signature)
            if cached is not None:
                self._layer_cache.move_to_end(signature)
                return cached
            self._layer_cache[signature] = cells
            self._layer_cache.move_to_end(signature)
            while len(self._layer_cache) > getattr(self, "_layer_cache_cap", LAYER_CACHE_CAP):
                self._layer_cache.popitem(last=False)
            # 自动注入阶梯护栏物理阻断：东西向阶梯阻断南北(up/down)，南北向阶梯阻断东西(left/right)
        # Handrails are already naturally bounded by non-walkable side terrain and ROM collision.
        pass
        return cells

    def _planning_cells(
        self, zone_id: int, y: int, *, anchor: dict[str, Any] | None = None,
        allow_unverified_terrain: bool = False,
    ) -> dict[tuple[int, int], StaticNavigationCell]:
        """Resolve safe planning cells, with an explicit seam fallback.

        A live height anchor remains authoritative for verified terrain. At a
        Matrix seam the decoder can expose flag-clear unknown records without
        a usable relative-height sample, so the opt-in policy may merge only
        those unknown records from the unanchored candidate layer. Execution
        still proves every landing through PlayerRuntime.
        """
        cells = self._cells_for_layer(zone_id, y, anchor=anchor)
        fallback = self._cells_for_layer(zone_id, y, anchor=None)
        if anchor is not None and fallback:
            if len(cells) < len(fallback):
                merged = dict(fallback)
                merged.update(cells)
                cells = merged
        if not allow_unverified_terrain or anchor is None:
            return cells
        if not fallback:
            return cells
        merged = dict(cells)
        for key, candidate in fallback.items():
            if key not in merged and _is_unverified_material(candidate):
                merged[key] = candidate
        return merged

    def _surface_records(self, zone_id: int, x: int, z: int) -> list[dict[str, Any]]:
        """Return records at one tile through a bounded O(1) lookup cache."""
        zone_id = int(zone_id)
        key = (int(x), int(z))
        cache = getattr(self, "_zone_surface_lookup_cache", None)
        if cache is None:
            cache = OrderedDict()
            self._zone_surface_lookup_cache = cache
        with self._lock:
            lookup = cache.get(zone_id)
            if lookup is not None:
                cache.move_to_end(zone_id)
                return list(lookup.get(key, ()))
        lookup_builder: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for item in self._zone_surfaces(zone_id):
            lookup_builder.setdefault((int(item["x"]), int(item["z"])), []).append(item)
        lookup = {item_key: tuple(items) for item_key, items in lookup_builder.items()}
        with self._lock:
            cache[zone_id] = lookup
            cache.move_to_end(zone_id)
            while len(cache) > getattr(self, "_zone_surface_cache_cap", ZONE_SURFACE_CACHE_CAP):
                cache.popitem(last=False)
        return list(lookup.get(key, ()))

    def surface_at(
        self, zone_id: int, x: int, z: int, y: int, *, anchor: dict[str, Any] | None = None,
        movement_mode: str = "walk", allow_unverified_terrain: bool = False,
    ) -> dict[str, Any]:
        records = self._surface_records(zone_id, x, z)
        cells = self._cells_for_layer(zone_id, y, anchor=anchor)
        cell = cells.get((int(x), int(z)))
        surfaces: list[dict[str, Any]] = []
        for item in records:
            raw_surface = item.get("surface") or {}
            collision = raw_surface.get("collision") or {}
            material = raw_surface.get("material") or {}
            surfaces.append({
                "layer_index": item.get("layer_index"),
                "tile_class": (raw_surface.get("raw") or {}).get("tile_class"),
                "flags": (raw_surface.get("raw") or {}).get("flags"),
                "static_blocked": collision.get("static_blocked"),
                "material": material,
                "height": raw_surface.get("height"),
                "source": {
                    "chunk_id": item.get("chunk_id"),
                    "record_offset": raw_surface.get("record_offset"),
                },
            })
        mode_allowed, mode_reason = _movement_eligibility(
            cell, movement_mode,
            allow_unverified_terrain=allow_unverified_terrain,
        )
        return {
            "zone_id": int(zone_id),
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "status": "walkable_candidate" if cell else "blocked_or_unknown",
            "walkable": cell is not None,
            "movement_mode": str(movement_mode or "walk").lower(),
            "movement_allowed": bool(mode_allowed),
            "movement_blocker": mode_reason,
            "terrain_policy": _terrain_policy(allow_unverified_terrain),
            "cell": cell.public() if cell else None,
            "surfaces": surfaces,
        }

    def preview_surface_at(self, zone_id: int, x: int, z: int, y: int) -> dict[str, Any]:
        """Cheap terrain preview for coarse/fuzzy rasterization.

        Unlike ``surface_at`` this does not compile the complete navigation
        layer for the Zone. It reads the already decoded tile record and is
        therefore safe to call at many Matrix-block sample points. The
        returned value intentionally remains a candidate; movement execution
        still uses the full layer and runtime landing verification.
        """
        records = self._surface_records(int(zone_id), int(x), int(z))
        if not records:
            return {"walkable": False, "movement_allowed": False, "cell": None, "surfaces": []}
        target_world_y = float(y) * 16.0
        candidates: list[tuple[float, dict[str, Any]]] = []
        for candidate in records:
            raw_surface_candidate = candidate.get("surface") or {}
            raw_candidate = raw_surface_candidate.get("raw") or {}
            if raw_candidate.get("tile_class") == 0xFE:
                continue
            rel_candidate = (raw_surface_candidate.get("height") or {}).get("chunk_relative_world_y")
            distance = abs(float(rel_candidate) - target_world_y) if rel_candidate is not None else 1e12
            candidates.append((distance, candidate))
        item = min(candidates, key=lambda pair: pair[0])[1] if candidates else records[-1]
        raw_surface = item.get("surface") or {}
        raw = raw_surface.get("raw") or {}
        collision = raw_surface.get("collision") or {}
        material = raw_surface.get("material") or {}
        static_blocked = bool(collision.get("static_blocked"))
        blocked_directions = list(collision.get("blocked_directions") or ())
        ledge_direction = collision.get("ledge_direction")
        directional = bool(ledge_direction or blocked_directions)
        walkable = bool(not static_blocked or directional)
        return {
            "zone_id": int(zone_id), "x": int(x), "y": int(y), "z": int(z),
            "status": "walkable_candidate" if walkable else "blocked_or_unknown",
            "walkable": walkable,
            "movement_allowed": walkable,
            "cell": {
                "tile_class": raw.get("tile_class"), "flags": raw.get("flags"),
                "static_blocked": static_blocked,
                "blocked_directions": blocked_directions,
                "ledge_direction": ledge_direction, "material": material,
            },
            "surfaces": [{
                "tile_class": raw.get("tile_class"), "flags": raw.get("flags"),
                "static_blocked": static_blocked, "material": material,
                "height": raw_surface.get("height") or {},
            }],
        }

    @staticmethod
    def _neighbors(
        cells: dict[tuple[int, int], StaticNavigationCell],
        current: StaticNavigationCell,
        occupied: set[tuple[int, int]],
        allowed: set[tuple[int, int]] | None = None,
    ) -> Iterable[StaticNavigationCell]:
        # Stable order makes plans reproducible and is convenient for UI tests.
        for dx, dz in ((0, -1), (-1, 0), (1, 0), (0, 1)):
            direction = _direction(dx, dz)
            if direction in current.blocked_directions:
                continue
            if current.ledge_direction and direction != current.ledge_direction:
                continue
            candidate = cells.get((current.node.x + dx, current.node.z + dz))
            if candidate is None or (candidate.node.x, candidate.node.z) in occupied:
                continue
            if allowed is not None and (candidate.node.x, candidate.node.z) not in allowed:
                continue
            if _opposite(direction) in candidate.blocked_directions:
                continue
            yield candidate

    def _corridor_lane(self, c: Any, from_portal: dict[str, Any], to_portal: dict[str, Any]) -> list[tuple[int, int]]:
        step_set = {(st.x, st.z) for st in c.steps}
        entries = [cand for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)) if (cand := (from_portal["x"] + dx, from_portal["z"] + dz)) in step_set]
        exits = {cand for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)) if (cand := (to_portal["x"] + dx, to_portal["z"] + dz)) in step_set}
        if not entries or not exits:
            return [(st.x, st.z) for st in c.steps]
        queue = [[e] for e in entries]
        visited = set(entries)
        while queue:
            p = queue.pop(0)
            curr = p[-1]
            if curr in exits:
                return p
            for dx, dz in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nxt = (curr[0] + dx, curr[1] + dz)
                if nxt in step_set and nxt not in visited:
                    visited.add(nxt)
                    queue.append(p + [nxt])
        return [(st.x, st.z) for st in c.steps]

    def find_cross_layer_path(
        self,
        start: NavNode,
        goal: NavNode,
        *,
        movement_mode: str = "walk",
        player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        allowed: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        constraint_evaluator: Any | None = None,
        allow_unverified_terrain: bool = False,
    ) -> dict[str, Any]:
        """Find multi-layer path bridging elevation differences via discovered staircase corridors."""
        from .staircase_corridors import StaircaseCorridorService
        scs = StaircaseCorridorService(self)
        corridors = scs.analyze_zone(int(start.zone_id))
        candidates = []
        for c in corridors:
            lp, up = c.lower_portal, c.upper_portal
            if not lp or not up:
                continue
            if lp.get("floor_y") == start.y and up.get("floor_y") == goal.y:
                candidates.append((c, lp, up, False))
            elif up.get("floor_y") == start.y and lp.get("floor_y") == goal.y:
                candidates.append((c, up, lp, True))

        if not candidates:
            fallback_3d = self._find_3d_multilayer_path(
                start, goal, movement_mode=movement_mode,
                player_sample=player_sample, occupied=occupied,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
            if fallback_3d.get("reachable"):
                return fallback_3d
            return {
                "reachable": False,
                "reason": f"No staircase corridor connects layer Y={start.y} to Y={goal.y} in Zone {start.zone_id}",
                "path": [],
                "confidence": "candidate_static",
            }

        best_route = None
        min_total_cost = float("inf")

        for c, entry_p, exit_p, desc in candidates:
            entry_node = NavNode(start.zone_id, entry_p["x"], start.y, entry_p["z"])
            p1 = self.find_path(
                start, entry_node,
                movement_mode=movement_mode, player_sample=player_sample,
                occupied=occupied, allowed=allowed,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
            if not p1.get("reachable"):
                continue

            exit_node = NavNode(goal.zone_id, exit_p["x"], goal.y, exit_p["z"])
            p3 = self.find_path(
                exit_node, goal,
                movement_mode=movement_mode, player_sample=player_sample,
                occupied=occupied, allowed=allowed,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
            if not p3.get("reachable"):
                continue

            stair_steps = []
            steps_ordered = list(c.steps)
            if desc:
                steps_ordered.reverse()
            for s in steps_ordered:
                stair_steps.append({
                    "zone_id": int(start.zone_id),
                    "x": int(s.x),
                    "y": int(c.flattened_slice_y),
                    "z": int(s.z),
                })

            p1_path = p1.get("path", [])
            p3_path = p3.get("path", [])
            full_path = list(p1_path) + stair_steps + list(p3_path)
            total_steps = max(0, len(full_path) - 1)

            if total_steps < min_total_cost:
                min_total_cost = total_steps
                best_route = {
                    "reachable": True,
                    "path": full_path,
                    "cost": total_steps,
                    "corridor": c.as_dict(),
                    "staircase_detected": True,
                    "confidence": "candidate_static",
                    "source": "staircase_corridor_stitched",
                }

        if best_route is not None:
            return best_route

        # Multi-corridor search across intermediate elevation layers
        import heapq
        queue = [(0, start, [start.public()])]
        visited_nodes = {}
        best_multi_route = None

        while queue and len(visited_nodes) < 150:
            cost, curr, path = heapq.heappop(queue)
            state_key = (curr.x, curr.z, curr.y)
            if state_key in visited_nodes and visited_nodes[state_key] <= cost:
                continue
            visited_nodes[state_key] = cost

            if curr.y == goal.y:
                p_goal = self.find_path(
                    curr, goal,
                    movement_mode=movement_mode, player_sample=player_sample,
                    occupied=occupied, allowed=allowed,
                    constraint_evaluator=constraint_evaluator,
                    allow_unverified_terrain=allow_unverified_terrain,
                )
                if p_goal.get("reachable"):
                    full_p = list(path[:-1]) + list(p_goal.get("path") or [])
                    total_steps = max(0, len(full_p) - 1)
                    best_multi_route = {
                        "reachable": True,
                        "path": full_p,
                        "cost": total_steps,
                        "staircase_detected": True,
                        "confidence": "candidate_static",
                        "source": "multi_staircase_corridor_stitched",
                    }
                    break

            for c in corridors:
                lp, up = c.lower_portal, c.upper_portal
                if not lp or not up:
                    continue
                # Traverse LP -> UP
                if lp.get("floor_y") == curr.y:
                    lp_node = NavNode(start.zone_id, lp["x"], curr.y, lp["z"])
                    p_lp = self.find_path(
                        curr, lp_node,
                        movement_mode=movement_mode, player_sample=player_sample,
                        occupied=occupied, allowed=allowed,
                        constraint_evaluator=constraint_evaluator,
                        allow_unverified_terrain=allow_unverified_terrain,
                    )
                    if p_lp.get("reachable"):
                        lane = self._corridor_lane(c, lp, up)
                        c_steps = [
                            {"zone_id": int(start.zone_id), "x": int(x), "y": int(lp["floor_y"]), "z": int(z)}
                            for x, z in lane
                        ]
                        up_node = NavNode(start.zone_id, up["x"], up["floor_y"], up["z"])
                        new_p = list(path[:-1]) + list(p_lp.get("path") or []) + c_steps + [up_node.public()]
                        ncost = cost + len(p_lp.get("path") or []) + len(c_steps) + 1
                        heapq.heappush(queue, (ncost, up_node, new_p))
                # Traverse UP -> LP
                if up.get("floor_y") == curr.y:
                    up_node = NavNode(start.zone_id, up["x"], curr.y, up["z"])
                    p_up = self.find_path(
                        curr, up_node,
                        movement_mode=movement_mode, player_sample=player_sample,
                        occupied=occupied, allowed=allowed,
                        constraint_evaluator=constraint_evaluator,
                        allow_unverified_terrain=allow_unverified_terrain,
                    )
                    if p_up.get("reachable"):
                        lane = self._corridor_lane(c, up, lp)
                        c_steps = [
                            {"zone_id": int(start.zone_id), "x": int(x), "y": int(up["floor_y"]), "z": int(z)}
                            for x, z in lane
                        ]
                        lp_node = NavNode(start.zone_id, lp["x"], lp["floor_y"], lp["z"])
                        new_p = list(path[:-1]) + list(p_up.get("path") or []) + c_steps + [lp_node.public()]
                        ncost = cost + len(p_up.get("path") or []) + len(c_steps) + 1
                        heapq.heappush(queue, (ncost, lp_node, new_p))

        if best_multi_route is not None:
            return best_multi_route

        # 3D multi-layer A* fallback for zones with natural elevation slopes (e.g. Route 20)
        fallback_3d = self._find_3d_multilayer_path(
            start, goal, movement_mode=movement_mode,
            player_sample=player_sample, occupied=occupied,
            constraint_evaluator=constraint_evaluator,
            allow_unverified_terrain=allow_unverified_terrain,
        )
        if fallback_3d.get("reachable"):
            return fallback_3d

        return {
            "reachable": False,
            "reason": "No connected path through available staircase corridors",
            "path": [],
            "confidence": "candidate_static",
        }


    def _find_3d_multilayer_path(
        self, start: NavNode, goal: NavNode, *,
        movement_mode: str = "walk",
        player_sample: dict[str, Any] | None = None,
        occupied: Iterable[Any] = (),
        constraint_evaluator: Any | None = None,
        allow_unverified_terrain: bool = False,
        max_nodes: int = 5000,
    ) -> dict[str, Any]:
        """3D A* multi-layer search bridging natural elevation slopes across layers."""
        import heapq
        from .navigation_planning import normalize_occupancy
        zid = int(start.zone_id)
        norm_occ = normalize_occupancy(occupied or (), default_zone=zid, default_y=start.y)
        occupied_set = {(int((item.get("grid") or {}).get("x", item.get("x", getattr(item, "x", 0)))), int((item.get("grid") or {}).get("z", item.get("z", getattr(item, "z", 0))))) for item in norm_occ}

        start_tuple = (start.x, start.z, start.y)
        goal_tuple = (goal.x, goal.z, goal.y)

        def heuristic(x: int, z: int, y: int) -> float:
            return abs(x - goal.x) + abs(z - goal.z) + abs(y - goal.y) * 2.0

        queue: list[tuple[float, float, int, int, int]] = [(heuristic(*start_tuple), 0.0, *start_tuple)]
        costs: dict[tuple[int, int, int], float] = {start_tuple: 0.0}
        parent: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        nodes_evaluated = 0

        while queue and nodes_evaluated < max_nodes:
            f, g, x, z, y = heapq.heappop(queue)
            nodes_evaluated += 1
            if (x, z, y) == goal_tuple:
                break
            if g > costs.get((x, z, y), 1e9):
                continue
            for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, nz = x + dx, z + dz
                if (nx, nz) in occupied_set:
                    continue
                cur_surf = self.surface_at(zid, x, z, y, allow_unverified_terrain=allow_unverified_terrain)
                cur_blocked = (cur_surf.get("cell") or {}).get("blocked_directions") or ()
                direction = _direction(dx, dz)
                if direction in cur_blocked:
                    continue
                for dy in (-1, 0, 1):
                    ny = y + dy
                    surf = self.surface_at(zid, nx, nz, ny, allow_unverified_terrain=allow_unverified_terrain)
                    if not surf.get("walkable"):
                        continue
                    cand_blocked = (surf.get("cell") or {}).get("blocked_directions") or ()
                    if _opposite(direction) in cand_blocked:
                        continue
                    if movement_mode == "bike" and (surf.get("material") or {}).get("blocks_cycling"):
                        continue
                    ng = g + 1.0 + (0.5 if dy != 0 else 0.0)
                    state = (nx, nz, ny)
                    if ng < costs.get(state, 1e9):
                        costs[state] = ng
                        h = heuristic(nx, nz, ny)
                        heapq.heappush(queue, (ng + h, ng, nx, nz, ny))
                        parent[state] = (x, z, y)

        if goal_tuple not in parent and start_tuple != goal_tuple:
            return {"reachable": False, "reason": f"3D multi-layer A* found no path to layer Y={goal.y}", "path": []}

        # Reconstruct path
        curr = goal_tuple
        raw_path = [curr]
        while curr in parent:
            curr = parent[curr]
            raw_path.append(curr)
        raw_path.reverse()

        node_path = [{"zone_id": zid, "x": px, "y": py, "z": pz} for px, pz, py in raw_path]
        return {
            "reachable": True,
            "path": node_path,
            "cost": len(node_path) - 1,
            "staircase_detected": True,
            "confidence": "candidate_static",
            "source": "3d_multilayer_astar",
        }


    def find_path(
        self,
        start: NavNode,
        goal: NavNode,
        *,
        movement_mode: str = "walk",
        player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        allowed: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        constraint_evaluator: Any | None = None,
        allow_unverified_terrain: bool = False,
    ) -> dict[str, Any]:
        if start.zone_id != goal.zone_id:
            try:
                start_matrix = int(self.rom.zone(start.zone_id).matrix_id)
                goal_matrix = int(self.rom.zone(goal.zone_id).matrix_id)
            except (IndexError, ValueError):
                return {"reachable": False, "reason": "Zone Matrix metadata unavailable", "path": [], "confidence": "candidate_static"}
            if start_matrix != goal_matrix:
                return {"reachable": False, "reason": "cross-Matrix path requires a verified connector", "path": [], "confidence": "candidate_static"}
            return self.find_global_path(
                start, matrix_id=start_matrix, x=goal.x, y=goal.y, z=goal.z,
                movement_mode=movement_mode,
                player_sample=player_sample, occupied=occupied,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
        if start.y != goal.y:
            return self.find_cross_layer_path(
                start, goal,
                movement_mode=movement_mode,
                player_sample=player_sample,
                occupied=occupied,
                allowed=allowed,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
        anchor = self._anchor_from_sample(player_sample, start.zone_id)
        cells = self._planning_cells(
            start.zone_id, start.y, anchor=anchor,
            allow_unverified_terrain=allow_unverified_terrain,
        )
        start_cell, goal_cell = cells.get((start.x, start.z)), cells.get((goal.x, goal.z))
        if start_cell is None or goal_cell is None:
            missing = "start" if start_cell is None else "goal"
            return {"reachable": False, "reason": f"{missing} is not a flag-clear static terrain candidate", "path": [], "confidence": "candidate_static"}
        start_allowed, start_reason = _movement_eligibility(
            start_cell, movement_mode,
            allow_unverified_terrain=allow_unverified_terrain,
        )
        goal_allowed, goal_reason = _movement_eligibility(
            goal_cell, movement_mode,
            allow_unverified_terrain=allow_unverified_terrain,
        )
        if not start_allowed or not goal_allowed:
            return {
                "reachable": False,
                "reason": "movement mode cannot enter the static terrain candidate",
                "movement_mode": str(movement_mode or "walk").lower(),
                "movement_blocker": {
                    "tile": "start" if not start_allowed else "goal",
                    "reason": start_reason if not start_allowed else goal_reason,
                },
                "path": [], "confidence": "candidate_static",
            }
        occupied_xy = _occupied_xy(occupied, zone_id=start.zone_id, y=start.y)
        occupied_xy.discard((start.x, start.z))
        if (goal.x, goal.z) in occupied_xy:
            return {"reachable": False, "reason": "goal is occupied by a runtime actor", "path": [], "confidence": "candidate_static"}
        allowed_xy = _occupied_xy(allowed, zone_id=start.zone_id, y=start.y) if allowed else None
        if allowed_xy is not None:
            if (start.x, start.z) not in allowed_xy or (goal.x, goal.z) not in allowed_xy:
                return {
                    "reachable": False,
                    "reason": "start or goal is outside the allowed navigation subgraph",
                    "path": [],
                    "confidence": "candidate_static",
                }

        goal_evaluation = (
            constraint_evaluator.evaluate(goal, is_goal=True)
            if constraint_evaluator is not None else {"blocked": False}
        )
        if goal_evaluation.get("blocked"):
            return {
                "reachable": False, "reason": "navigation policy blocks the goal tile",
                "policy_blocked": True,
                "blocking_constraints": goal_evaluation.get("blocking_constraints") or [],
                "path": [], "confidence": "candidate_static",
            }

        start_key, goal_key = (start.x, start.z), (goal.x, goal.z)
        # Lexicographic A*: minimize tile count first, then turns among all
        # shortest routes.  Fewer direction changes make the executor visibly
        # smoother without ever taking a longer path merely for aesthetics.
        # State includes the incoming direction because turn count depends on
        # how a cell was reached.
        start_state = (start.x, start.z, 0, 0)
        queue: list[tuple[float, int, float, int, int, int, int]] = [
            (abs(start.x - goal.x) + abs(start.z - goal.z), 0, 0.0, 0, start.x, start.z, 0)
        ]
        costs: dict[tuple[int, int, int, int], tuple[float, int]] = {start_state: (0, 0)}
        constraint_costs: dict[tuple[int, int, int, int], float] = {start_state: 0.0}
        parent: dict[tuple[int, int, int, int], tuple[int, int, int, int]] = {}
        encountered: dict[str, dict[str, Any]] = {}
        blocked_constraints: dict[str, dict[str, Any]] = {}
        goal_state: tuple[int, int, int, int] | None = None
        while queue:
            _f_cost, turns, g_cost, steps, x, z, packed_dir = heapq.heappop(queue)
            dx = ((packed_dir >> 8) & 0xFF) - 128 if packed_dir else 0
            dz = (packed_dir & 0xFF) - 128 if packed_dir else 0
            state = (x, z, dx, dz)
            if (g_cost, turns) != costs.get(state):
                continue
            if (x, z) == goal_key:
                goal_state = state
                break
            current = cells[(x, z)]
            for neighbor in self._neighbors(cells, current, occupied_xy, allowed_xy):
                allowed_neighbor, _reason = _movement_eligibility(
                    neighbor, movement_mode,
                    allow_unverified_terrain=allow_unverified_terrain,
                )
                if not allowed_neighbor:
                    continue
                ndx = neighbor.node.x - x
                ndz = neighbor.node.z - z
                evaluation = (
                    constraint_evaluator.evaluate(neighbor.node, is_goal=neighbor.node == goal)
                    if constraint_evaluator is not None
                    else {"blocked": False, "extra_cost": 0.0, "constraints": []}
                )
                for item in evaluation.get("constraints") or ():
                    encountered[str(item.get("constraint_id"))] = item
                if evaluation.get("blocked"):
                    for item in evaluation.get("blocking_constraints") or ():
                        blocked_constraints[str(item.get("constraint_id"))] = item
                    continue
                nsteps = steps + 1
                nturns = turns + (1 if (dx or dz) and (ndx, ndz) != (dx, dz) else 0)
                nstate = (neighbor.node.x, neighbor.node.z, ndx, ndz)
                extra_cost = float(evaluation.get("extra_cost", 0.0) or 0.0)
                is_grass = bool(
                    neighbor.tile_class in (4, 5)
                    or (neighbor.material or {}).get("kind") in ("tall_grass", "dark_grass")
                )
                if is_grass:
                    eff_policy = (
                        constraint_evaluator.policy.get("encounter_grass", "soft_avoid")
                        if constraint_evaluator is not None
                        else "soft_avoid"
                    )
                    if eff_policy == "hard_avoid" and (neighbor.node.x, neighbor.node.z) != (goal.x, goal.z):
                        continue
                    elif eff_policy in ("soft_avoid", "soft_cost"):
                        grass_extra = 25.0 if neighbor.tile_class == 5 else 15.0
                        extra_cost += grass_extra
                ncost = (g_cost + 1.0 + extra_cost, nturns)
                if ncost >= costs.get(nstate, (10**9, 10**9)):
                    continue
                costs[nstate] = ncost
                constraint_costs[nstate] = constraint_costs.get(state, 0.0) + extra_cost
                parent[nstate] = state
                heuristic = abs(neighbor.node.x - goal.x) + abs(neighbor.node.z - goal.z)
                packed = ((ndx + 128) << 8) | (ndz + 128)
                heapq.heappush(
                    queue,
                    (ncost[0] + heuristic, nturns, ncost[0], nsteps, neighbor.node.x, neighbor.node.z, packed),
                )
        if goal_state is None:
            return {
                "reachable": False,
                "reason": "navigation policy blocked all static paths" if blocked_constraints else "no connected flag-clear static path on this layer",
                "policy_blocked": bool(blocked_constraints),
                "blocking_constraints": list(blocked_constraints.values()),
                "constraints_encountered": list(encountered.values()),
                "path": [], "confidence": "candidate_static",
            }
        states = [goal_state]
        while states[-1] != start_state:
            states.append(parent[states[-1]])
        states.reverse()
        keys = [(state[0], state[1]) for state in states]
        path = [cells[key].node.public() for key in keys]
        turns = costs[goal_state][1]
        unverified_count = sum(
            1 for key in keys
            if _is_unverified_material(cells.get(key))
        )
        return {
            "reachable": True,
            "movement_mode": str(movement_mode or "walk").lower(),
            "path": path,
            "steps": len(path) - 1,
            "turns": turns,
            "cost": float(costs[goal_state][0]),
            "movement_cost": float(len(path) - 1),
            "constraint_cost": constraint_costs.get(goal_state, 0.0),
            "optimization": "shortest_steps_then_fewest_turns",
            "confidence": "candidate_static",
            "reason": "route uses ROM flag-clear terrain candidates; shortest steps are preferred, then fewer turns; each landing requires live verification",
            "source": "rom_static_collision_candidate",
            "constraints_encountered": list(encountered.values()),
            "terrain_policy": _terrain_policy(allow_unverified_terrain),
            "unverified_tile_count": unverified_count,
            "world_revision": self.revision,
        }

    def has_candidate_edge(
        self, start: NavNode, goal: NavNode, *, player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        movement_mode: str = "walk",
        constraint_evaluator: Any | None = None,
        allow_unverified_terrain: bool = False,
    ) -> bool:
        if abs(start.x - goal.x) + abs(start.z - goal.z) != 1:
            return False
        # 1. 优先校验阶梯过渡边 (无论 start.y 与 goal.y 是否相等，只要属于阶梯过渡边即合法)
        try:
            from .staircase_corridors import StaircaseCorridorService
            scs = StaircaseCorridorService(self)
            for c in scs.analyze_zone(int(start.zone_id)):
                lp, up, steps = c.lower_portal, c.upper_portal, c.steps
                if not lp or not up or not steps:
                    continue
                step_coords = {(s.x, s.z) for s in steps}
                # 检查台阶之间过渡 (支持 2~5 格任意宽度的楼梯与斜坡)
                if (start.x, start.z) in step_coords and (goal.x, goal.z) in step_coords:
                    return True
                # 检查下层入口 ⇄ 台阶
                if ((start.x, start.z) == (lp["x"], lp["z"]) and (goal.x, goal.z) in step_coords) or \
                   ((goal.x, goal.z) == (lp["x"], lp["z"]) and (start.x, start.z) in step_coords):
                    return True
                # 检查台阶 ⇄ 上层出口
                if ((start.x, start.z) in step_coords and (goal.x, goal.z) == (up["x"], up["z"])) or \
                   ((goal.x, goal.z) in step_coords and (start.x, start.z) == (up["x"], up["z"])):
                    return True
        except Exception:
            pass

        # 2. 普通单层平面边校验
        if start.y == goal.y:
            result = self.find_path(
                start, goal, player_sample=player_sample, occupied=occupied,
                movement_mode=movement_mode,
                constraint_evaluator=constraint_evaluator,
                allow_unverified_terrain=allow_unverified_terrain,
            )
            return bool(result.get("reachable") and len(result.get("path") or []) == 2)

        # 3. 3D多层/自然斜坡过渡边校验 (|start.y - goal.y| <= 1)
        if abs(start.y - goal.y) <= 1:
            try:
                from .navigation_planning import normalize_occupancy
                norm_occ = normalize_occupancy(occupied or (), default_zone=int(goal.zone_id), default_y=goal.y)
                occupied_set = {(int((item.get("grid") or {}).get("x", item.get("x", getattr(item, "x", 0)))), int((item.get("grid") or {}).get("z", item.get("z", getattr(item, "z", 0))))) for item in norm_occ}
                if (goal.x, goal.z) in occupied_set:
                    return False
            except Exception:
                pass
            surf_start = self.surface_at(int(start.zone_id), start.x, start.z, start.y, allow_unverified_terrain=allow_unverified_terrain)
            surf_goal = self.surface_at(int(goal.zone_id), goal.x, goal.z, goal.y, allow_unverified_terrain=allow_unverified_terrain)
            if surf_start.get("walkable") and surf_goal.get("walkable"):
                if movement_mode == "bike":
                    start_block = (surf_start.get("material") or {}).get("blocks_cycling")
                    goal_block = (surf_goal.get("material") or {}).get("blocks_cycling")
                    if start_block or goal_block:
                        return False
                if constraint_evaluator is not None:
                    eval_res = constraint_evaluator.evaluate(goal)
                    if eval_res.get("blocked"):
                        return False
                return True

        return False

    def snap(
        self,
        zone_id: int,
        x: int,
        z: int,
        y: int,
        *,
        movement_mode: str = "walk",
        player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
        force_adjacent: bool = False,
        max_radius: int = 12,
    ) -> dict[str, Any]:
        """Snap a clicked grid point to the nearest connected walk candidate."""
        max_radius = max(0, min(int(max_radius), 32))
        anchor = self._anchor_from_sample(player_sample, int(zone_id))
        cells = self._cells_for_layer(int(zone_id), int(y), anchor=anchor)
        occupied_xy = _occupied_xy(occupied, zone_id=int(zone_id), y=int(y))
        requested = (int(x), int(z))
        exact = cells.get(requested)
        exact_allowed, _exact_reason = _movement_eligibility(exact, movement_mode)

        # Static walkability does not imply reachability. A flag-clear tile can
        # still sit behind walls, trees, directional collision or another
        # disconnected component. If a live same-layer player tile is known,
        # require connectivity before accepting the exact clicked tile.
        live_start = NavNode.from_player(player_sample)
        if live_start is not None and (
            live_start.zone_id != int(zone_id)
            or live_start.y != int(y)
        ):
            live_start = None

        if exact is not None and exact_allowed and requested not in occupied_xy and not force_adjacent:
            exact_reachable = True
            if live_start is not None:
                exact_route = self.find_path(
                    live_start,
                    exact.node,
                    player_sample=player_sample,
                    occupied=occupied_xy,
                    movement_mode=movement_mode,
                )
                exact_reachable = bool(exact_route.get("reachable"))

            if exact_reachable:
                return {
                    "ok": True,
                    "target": exact.node.public(),
                    "distance_tiles": 0,
                    "snapped": False,
                    "reason": "clicked surface is a reachable flag-clear static terrain candidate",
                    "confidence": "candidate_static",
                    "cell": exact.public(),
                }

        candidates = [
            cell for (cx, cz), cell in cells.items()
            if (cx, cz) not in occupied_xy
            and abs(cx - x) + abs(cz - z) <= max_radius
            and (not force_adjacent or (cx, cz) != requested)
            and _movement_eligibility(cell, movement_mode)[0]
        ]
        candidates.sort(key=lambda cell: (abs(cell.node.x - x) + abs(cell.node.z - z), cell.node.z, cell.node.x))
        if not candidates:
            return {"ok": False, "reason": "no flag-clear static surface is within snap radius", "confidence": "unresolved"}

        for candidate in candidates:
            if live_start is not None:
                route = self.find_path(
                    live_start, candidate.node, player_sample=player_sample,
                    occupied=occupied_xy, movement_mode=movement_mode,
                )
                if not route.get("reachable"):
                    continue
            distance = abs(candidate.node.x - x) + abs(candidate.node.z - z)
            return {
                "ok": True,
                "target": candidate.node.public(),
                "distance_tiles": distance,
                "snapped": True,
                "reason": "clicked surface is blocked, occupied, or a renderable object; selected nearest connected walk candidate",
                "confidence": "candidate_static",
                "cell": candidate.public(),
            }
        return {"ok": False, "reason": "no connected static surface is within snap radius", "confidence": "candidate_static"}
