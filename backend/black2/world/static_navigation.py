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
        self._zone_surfaces_cache: dict[int, tuple[dict[str, Any], ...]] = {}
        self._layer_cache: dict[tuple[int, int, str], dict[tuple[int, int], StaticNavigationCell]] = {}
        identity = self.rom.static_identity()
        self.revision = "rom:" + hashlib.sha256(
            json.dumps(identity, sort_keys=True, ensure_ascii=True).encode("utf-8")
        ).hexdigest()[:16]

    def status(self) -> dict[str, Any]:
        return {
            "format": "black2-static-navigation-status/v1",
            "available": True,
            "source": "ROM terrain records + IREJ MapTile collision predicate",
            "revision": self.revision,
            "coordinate_space": GRID_SPACE,
            "chunk_tiles": CHUNK_TILES,
            "policy": {
                "candidate_edges": "flags_bit_0_clear_static_tiles_only",
                "unknown_tiles": "excluded",
                "dynamic_occupancy": "caller supplied; never inferred from ROM spawns",
                "execution": "requires per-step PlayerRuntime landing verification",
            },
        }

    def movement_rules(
        self, zone_id: int, *, path: Iterable[NavNode] = (),
        player_sample: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return ROM-backed transport restrictions for a planned corridor."""
        zone = self.rom.zone(int(zone_id))
        area = self.rom.area(int(zone.area_id))
        anchor = self._anchor_from_sample(player_sample, int(zone_id))
        bike_blockers: list[dict[str, Any]] = []
        path_nodes = list(path or ())
        if path_nodes:
            cells = self._cells_for_layer(path_nodes[0].zone_id, path_nodes[0].y, anchor=anchor)
            for node in path_nodes:
                cell = cells.get((node.x, node.z))
                material = (cell.material if cell else None) or {}
                if material.get("blocks_cycling"):
                    bike_blockers.append({
                        "zone_id": node.zone_id, "x": node.x, "y": node.y, "z": node.z,
                        "kind": material.get("kind"), "label": material.get("label"),
                    })
        return {
            "zone_id": int(zone_id),
            "environment": "exterior" if bool(area.is_exterior) else "interior",
            "enable_running": bool(zone.enable_running),
            "enable_cycling": bool(zone.enable_cycling),
            "bike_blockers": bike_blockers,
            "source": "ROM ZoneHeader + AreaHeader + decoded TileClass semantics",
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
                raw_x, raw_z = _finite(row.get("x_world")), _finite(row.get("y_world"))
                if raw_x is None or raw_z is None:
                    continue
                width = max(1, _as_int(row.get("width")) or 1)
                height = max(1, _as_int(row.get("height")) or 1)
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
        raw_x, raw_z = _finite(selected.get("x_world")), _finite(selected.get("y_world"))
        if raw_x is None or raw_z is None:
            return None
        width = max(1, _as_int(selected.get("width")) or 1)
        height = max(1, _as_int(selected.get("height")) or 1)
        center = {
            "x": raw_x + (width - 1) * TILE_WORLD * 0.5,
            "y": _finite(selected.get("z")) or 0.0,
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
        return {
            "grid": {"x": gx, "y": gy, "z": gz},
            "world": {"x": wx, "y": wy, "z": wz},
            "chunk": {"x": gx // CHUNK_TILES, "z": gz // CHUNK_TILES},
        }

    def _zone_surfaces(self, zone_id: int) -> tuple[dict[str, Any], ...]:
        """Return all decoded surfaces in active matrix cells for ``zone_id``."""
        with self._lock:
            cached = self._zone_surfaces_cache.get(int(zone_id))
            if cached is not None:
                return cached

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
        frozen = tuple(result)
        with self._lock:
            self._zone_surfaces_cache.setdefault(int(zone_id), frozen)
            return self._zone_surfaces_cache[int(zone_id)]

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
            if collision.get("static_blocked") is not False:
                continue
            relative = _finite((surface.get("height") or {}).get("chunk_relative_world_y"))
            resolved_y = int(y)
            if anchor_relative is not None and anchor_y is not None and anchor_world_y is not None and relative is not None:
                aligned_world_y = anchor_world_y + (relative - anchor_relative)
                candidate_y = anchor_y + int(round((aligned_world_y - anchor_world_y) / TILE_WORLD))
                if candidate_y != int(y):
                    continue
            elif len({(x["x"], x["z"]) for x in surfaces}) and anchor is None:
                # With no height anchor, a multi-layer map cannot safely map
                # terrain layer indices to GPos.y.  A single flat surface per
                # tile remains usable for a read-only/static preview.
                pass
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
            self._layer_cache.setdefault(signature, cells)
            return self._layer_cache[signature]

    def _surface_records(self, zone_id: int, x: int, z: int) -> list[dict[str, Any]]:
        return [item for item in self._zone_surfaces(int(zone_id)) if item["x"] == int(x) and item["z"] == int(z)]

    def surface_at(
        self, zone_id: int, x: int, z: int, y: int, *, anchor: dict[str, Any] | None = None,
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
        return {
            "zone_id": int(zone_id),
            "x": int(x),
            "y": int(y),
            "z": int(z),
            "status": "walkable_candidate" if cell else "blocked_or_unknown",
            "walkable": cell is not None,
            "cell": cell.public() if cell else None,
            "surfaces": surfaces,
        }

    @staticmethod
    def _neighbors(
        cells: dict[tuple[int, int], StaticNavigationCell],
        current: StaticNavigationCell,
        occupied: set[tuple[int, int]],
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
            if _opposite(direction) in candidate.blocked_directions:
                continue
            yield candidate

    def find_path(
        self,
        start: NavNode,
        goal: NavNode,
        *,
        player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
    ) -> dict[str, Any]:
        if start.zone_id != goal.zone_id:
            return {"reachable": False, "reason": "static candidate graph is same-Zone only", "path": [], "confidence": "candidate_static"}
        if start.y != goal.y:
            return {"reachable": False, "reason": "static candidate graph does not infer an elevation transition", "path": [], "confidence": "candidate_static"}
        anchor = self._anchor_from_sample(player_sample, start.zone_id)
        cells = self._cells_for_layer(start.zone_id, start.y, anchor=anchor)
        start_cell, goal_cell = cells.get((start.x, start.z)), cells.get((goal.x, goal.z))
        if start_cell is None or goal_cell is None:
            missing = "start" if start_cell is None else "goal"
            return {"reachable": False, "reason": f"{missing} is not a flag-clear static terrain candidate", "path": [], "confidence": "candidate_static"}
        occupied_xy = _occupied_xy(occupied, zone_id=start.zone_id, y=start.y)
        occupied_xy.discard((start.x, start.z))
        if (goal.x, goal.z) in occupied_xy:
            return {"reachable": False, "reason": "goal is occupied by a runtime actor", "path": [], "confidence": "candidate_static"}

        start_key, goal_key = (start.x, start.z), (goal.x, goal.z)
        # Lexicographic A*: minimize tile count first, then turns among all
        # shortest routes.  Fewer direction changes make the executor visibly
        # smoother without ever taking a longer path merely for aesthetics.
        # State includes the incoming direction because turn count depends on
        # how a cell was reached.
        start_state = (start.x, start.z, 0, 0)
        queue: list[tuple[int, int, int, int, int, int]] = [
            (abs(start.x - goal.x) + abs(start.z - goal.z), 0, 0, start.x, start.z, 0)
        ]
        costs: dict[tuple[int, int, int, int], tuple[int, int]] = {start_state: (0, 0)}
        parent: dict[tuple[int, int, int, int], tuple[int, int, int, int]] = {}
        goal_state: tuple[int, int, int, int] | None = None
        while queue:
            _f_steps, turns, steps, x, z, packed_dir = heapq.heappop(queue)
            dx = ((packed_dir >> 8) & 0xFF) - 128 if packed_dir else 0
            dz = (packed_dir & 0xFF) - 128 if packed_dir else 0
            state = (x, z, dx, dz)
            if (steps, turns) != costs.get(state):
                continue
            if (x, z) == goal_key:
                goal_state = state
                break
            current = cells[(x, z)]
            for neighbor in self._neighbors(cells, current, occupied_xy):
                ndx = neighbor.node.x - x
                ndz = neighbor.node.z - z
                nsteps = steps + 1
                nturns = turns + (1 if (dx or dz) and (ndx, ndz) != (dx, dz) else 0)
                nstate = (neighbor.node.x, neighbor.node.z, ndx, ndz)
                if (nsteps, nturns) >= costs.get(nstate, (10**9, 10**9)):
                    continue
                costs[nstate] = (nsteps, nturns)
                parent[nstate] = state
                heuristic = abs(neighbor.node.x - goal.x) + abs(neighbor.node.z - goal.z)
                packed = ((ndx + 128) << 8) | (ndz + 128)
                heapq.heappush(
                    queue,
                    (nsteps + heuristic, nturns, nsteps, neighbor.node.x, neighbor.node.z, packed),
                )
        if goal_state is None:
            return {"reachable": False, "reason": "no connected flag-clear static path on this layer", "path": [], "confidence": "candidate_static"}
        states = [goal_state]
        while states[-1] != start_state:
            states.append(parent[states[-1]])
        states.reverse()
        keys = [(state[0], state[1]) for state in states]
        path = [cells[key].node.public() for key in keys]
        turns = costs[goal_state][1]
        return {
            "reachable": True,
            "path": path,
            "steps": len(path) - 1,
            "turns": turns,
            "cost": float(len(path) - 1),
            "optimization": "shortest_steps_then_fewest_turns",
            "confidence": "candidate_static",
            "reason": "route uses ROM flag-clear terrain candidates; shortest steps are preferred, then fewer turns; each landing requires live verification",
            "source": "rom_static_collision_candidate",
            "world_revision": self.revision,
        }

    def has_candidate_edge(
        self, start: NavNode, goal: NavNode, *, player_sample: dict[str, Any] | None = None,
        occupied: Iterable[NavNode | dict[str, Any] | tuple[int, int]] = (),
    ) -> bool:
        if abs(start.x - goal.x) + abs(start.z - goal.z) != 1 or start.y != goal.y:
            return False
        result = self.find_path(start, goal, player_sample=player_sample, occupied=occupied)
        return bool(result.get("reachable") and len(result.get("path") or []) == 2)

    def snap(
        self,
        zone_id: int,
        x: int,
        z: int,
        y: int,
        *,
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
        if exact is not None and requested not in occupied_xy and not force_adjacent:
            return {
                "ok": True,
                "target": exact.node.public(),
                "distance_tiles": 0,
                "snapped": False,
                "reason": "clicked surface is a flag-clear static terrain candidate",
                "confidence": "candidate_static",
                "cell": exact.public(),
            }

        candidates = [
            cell for (cx, cz), cell in cells.items()
            if (cx, cz) not in occupied_xy
            and abs(cx - x) + abs(cz - z) <= max_radius
            and (not force_adjacent or (cx, cz) != requested)
        ]
        candidates.sort(key=lambda cell: (abs(cell.node.x - x) + abs(cell.node.z - z), cell.node.z, cell.node.x))
        if not candidates:
            return {"ok": False, "reason": "no flag-clear static surface is within snap radius", "confidence": "unresolved"}

        # If a live start exists, reject isolated/static candidates that the
        # player could not reach.  This also makes a click on a wall select the
        # nearest *useful* floor, not merely a disconnected decorative patch.
        live = self._anchor_from_sample(player_sample, int(zone_id))
        live_grid = (live or {}).get("grid") or {}
        live_start = None
        if all(_as_int(live_grid.get(k)) is not None for k in ("x", "y", "z")):
            live_start = NavNode(int(zone_id), int(live_grid["x"]), int(live_grid["y"]), int(live_grid["z"]))
        for candidate in candidates:
            if live_start is not None:
                route = self.find_path(live_start, candidate.node, player_sample=player_sample, occupied=occupied_xy)
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
