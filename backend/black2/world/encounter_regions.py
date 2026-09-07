"""ROM-backed physical encounter-region aggregation.

This module deliberately stops at facts that the current project can support:

* verified grass TileClass predicates become physical encounter regions;
* probable water TileClass records become Surf *candidate* regions;
* exact tile membership remains the navigation truth;
* wild species tables and battle runtime state remain unresolved until their
  own decoders are verified.

Region geometry is four-neighbour connected because ordinary Gen-5 grid
movement is cardinal.  A diagonal touch does not merge two patrol regions.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

from .static_navigation import RomStaticNavigationGraph


REGION_FORMAT = "black2-encounter-regions/v1"
GRID_SPACE = "gen5-field-grid-v1"
_CARDINAL = ((0, -1), (-1, 0), (1, 0), (0, 1))


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _tile(zone_id: int, y: int, point: tuple[int, int]) -> dict[str, int]:
    return {"zone_id": int(zone_id), "x": int(point[0]), "y": int(y), "z": int(point[1])}


def _component_key(point: tuple[int, int]) -> tuple[int, int]:
    return point[1], point[0]


@dataclass(frozen=True)
class EncounterClassification:
    terrain_kind: str
    method: str
    evidence: str
    encounter_eligible: bool | None
    requirements: tuple[str, ...] = ()
    note: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "terrain_kind": self.terrain_kind,
            "encounter_method": self.method,
            "encounter_eligible": self.encounter_eligible,
            "requirements": list(self.requirements),
            "evidence": self.evidence,
            "note": self.note,
        }


def classify_material(material: dict[str, Any] | None) -> EncounterClassification | None:
    """Map verified terrain semantics to conservative encounter-region kinds."""
    material = material or {}
    kind = str(material.get("kind") or "unknown")
    encounter = material.get("encounter")
    status = str(material.get("status") or "unverified")
    if encounter == "single" and kind in {"tall_grass", "very_tall_grass"}:
        return EncounterClassification(
            terrain_kind=kind,
            method="walk_regular",
            evidence="verified" if status == "verified" else status,
            encounter_eligible=True,
        )
    if encounter == "double" and kind in {"dark_grass", "very_tall_grass"}:
        return EncounterClassification(
            terrain_kind=kind,
            method="walk_double_grass",
            evidence="verified" if status == "verified" else status,
            encounter_eligible=True,
        )
    if kind == "water" and material.get("requires") == "surf":
        return EncounterClassification(
            terrain_kind=kind,
            method="surf_candidate",
            evidence="probable" if status == "probable" else status,
            encounter_eligible=None,
            requirements=("surf",),
            note="Water TileClass is a Surf navigation candidate; wild encounter eligibility is not yet verified.",
        )
    return None


class EncounterRegionService:
    """Aggregate static terrain into exact physical encounter regions."""

    def __init__(
        self,
        provider: RomStaticNavigationGraph,
        player_sample: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self.provider = provider
        self.player_sample = player_sample or (lambda: None)

    def capabilities(self) -> dict[str, Any]:
        return {
            "format": "black2-encounter-capabilities/v1",
            "regions": {
                "available": True,
                "grass": "verified",
                "water": "probable_navigation_candidate",
                "connected_component": "four_neighbour_same_zone_same_y_same_kind",
                "exact_tile_set": True,
                "outline_segments": True,
                "entry_tiles": True,
                "interior_tiles": True,
                "patrol_recommendation": True,
            },
            "wild_encounter_profiles": {
                "available": False,
                "status": "research",
                "reason": "Zone-to-wild-encounter resource mapping and active variant selection are not verified in this project.",
            },
            "battle_observer": {
                "available": False,
                "status": "research",
                "safe_stop": "navigation stops when the player is no longer controllable in OVERWORLD",
                "limitation": "An overworld interruption is not promoted to a confirmed wild battle.",
            },
            "execution": {
                "same_zone_patrol": True,
                "cross_zone_patrol": False,
                "strategies": ["auto", "ping_pong", "loop", "line_shuttle"],
            },
        }

    @staticmethod
    def _sample_for_zone(sample: dict[str, Any] | None, zone_id: int) -> dict[str, Any] | None:
        return sample if isinstance(sample, dict) and _int(sample.get("zone_id")) == int(zone_id) else None

    def _cells(self, zone_id: int, y: int, sample: dict[str, Any] | None) -> dict[tuple[int, int], Any]:
        anchor = self.provider._anchor_from_sample(self._sample_for_zone(sample, zone_id), int(zone_id))
        return self.provider._cells_for_layer(int(zone_id), int(y), anchor=anchor)

    @staticmethod
    def _components(points: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
        remaining = set(points)
        components: list[set[tuple[int, int]]] = []
        while remaining:
            start = min(remaining, key=_component_key)
            queue = deque([start])
            remaining.remove(start)
            component = {start}
            while queue:
                x, z = queue.popleft()
                for dx, dz in _CARDINAL:
                    nxt = (x + dx, z + dz)
                    if nxt not in remaining:
                        continue
                    remaining.remove(nxt)
                    component.add(nxt)
                    queue.append(nxt)
            components.append(component)
        return sorted(components, key=lambda comp: min((_component_key(p) for p in comp)))

    @staticmethod
    def _outline_segments(points: set[tuple[int, int]]) -> list[dict[str, int]]:
        segments: list[dict[str, int]] = []
        for x, z in sorted(points, key=_component_key):
            # Grid vertices, not world-unit centres.  Consumers multiply by 16.
            if (x, z - 1) not in points:
                segments.append({"x1": x, "z1": z, "x2": x + 1, "z2": z})
            if (x + 1, z) not in points:
                segments.append({"x1": x + 1, "z1": z, "x2": x + 1, "z2": z + 1})
            if (x, z + 1) not in points:
                segments.append({"x1": x + 1, "z1": z + 1, "x2": x, "z2": z + 1})
            if (x - 1, z) not in points:
                segments.append({"x1": x, "z1": z + 1, "x2": x, "z2": z})
        return segments

    @staticmethod
    def _shortest_path(points: set[tuple[int, int]], start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]]:
        if start == goal:
            return [start]
        queue = deque([start])
        parent: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
        while queue:
            current = queue.popleft()
            x, z = current
            for dx, dz in _CARDINAL:
                nxt = (x + dx, z + dz)
                if nxt not in points or nxt in parent:
                    continue
                parent[nxt] = current
                if nxt == goal:
                    path = [goal]
                    while path[-1] != start:
                        path.append(parent[path[-1]])  # type: ignore[arg-type]
                    path.reverse()
                    return path
                queue.append(nxt)
        return []

    @staticmethod
    def _farthest(points: set[tuple[int, int]], start: tuple[int, int]) -> tuple[int, int]:
        queue = deque([(start, 0)])
        seen = {start}
        best = (0, _component_key(start), start)
        while queue:
            current, distance = queue.popleft()
            candidate = (distance, _component_key(current), current)
            if candidate > best:
                best = candidate
            x, z = current
            for dx, dz in _CARDINAL:
                nxt = (x + dx, z + dz)
                if nxt in points and nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, distance + 1))
        return best[2]

    @classmethod
    def _patrol(cls, zone_id: int, y: int, points: set[tuple[int, int]]) -> dict[str, Any]:
        ordered = sorted(points, key=_component_key)
        if len(points) < 2:
            return {
                "possible": False,
                "recommended_strategy": "none",
                "route": [_tile(zone_id, y, ordered[0])] if ordered else [],
                "reason": "A patrol requires at least two connected tiles; turning in place is not assumed to trigger encounters.",
            }
        if len(points) == 2:
            return {
                "possible": True,
                "recommended_strategy": "ping_pong",
                "route": [_tile(zone_id, y, point) for point in ordered],
                "route_policy": "two_adjacent_tiles",
            }
        # Prefer an exact 2x2 cycle.  It produces continuous movement with
        # predictable four-turn loops and does not leave the region.
        for x, z in ordered:
            square = [(x, z), (x + 1, z), (x + 1, z + 1), (x, z + 1)]
            if all(point in points for point in square):
                return {
                    "possible": True,
                    "recommended_strategy": "loop",
                    "route": [_tile(zone_id, y, point) for point in square],
                    "route_policy": "smallest_deterministic_2x2_cycle",
                }
        # A thin/irregular region without a simple 2x2 cycle gets a stable
        # graph-diameter shuttle.  Every waypoint is still an exact region tile.
        a = cls._farthest(points, ordered[0])
        b = cls._farthest(points, a)
        path = cls._shortest_path(points, a, b)
        return {
            "possible": len(path) >= 2,
            "recommended_strategy": "line_shuttle",
            "route": [_tile(zone_id, y, point) for point in path],
            "route_policy": "deterministic_region_graph_diameter",
        }

    def zone_regions(
        self,
        zone_id: int,
        y: int,
        *,
        player_sample: dict[str, Any] | None = None,
        include_water_candidates: bool = True,
    ) -> dict[str, Any]:
        sample = player_sample if player_sample is not None else self.player_sample()
        cells = self._cells(int(zone_id), int(y), sample)
        classified: dict[tuple[str, str, str], set[tuple[int, int]]] = {}
        cell_meta: dict[tuple[int, int], tuple[Any, EncounterClassification]] = {}
        for point, cell in cells.items():
            classification = classify_material(cell.material)
            if classification is None:
                continue
            if classification.method == "surf_candidate" and not include_water_candidates:
                continue
            key = (classification.method, classification.terrain_kind, classification.evidence)
            classified.setdefault(key, set()).add(point)
            cell_meta[point] = (cell, classification)

        regions: list[dict[str, Any]] = []
        serial = 0
        for key in sorted(classified):
            method, terrain_kind, _evidence = key
            for component in self._components(classified[key]):
                serial += 1
                sample_point = min(component, key=_component_key)
                _sample_cell, classification = cell_meta[sample_point]
                boundary = [
                    point for point in component
                    if any((point[0] + dx, point[1] + dz) not in component for dx, dz in _CARDINAL)
                ]
                interior = [
                    point for point in component
                    if all((point[0] + dx, point[1] + dz) in component for dx, dz in _CARDINAL)
                ]
                entry = [
                    point for point in boundary
                    if any((point[0] + dx, point[1] + dz) in cells and (point[0] + dx, point[1] + dz) not in component
                           for dx, dz in _CARDINAL)
                ]
                min_x = min(point[0] for point in component); max_x = max(point[0] for point in component)
                min_z = min(point[1] for point in component); max_z = max(point[1] for point in component)
                tile_classes = sorted({int(cell_meta[p][0].tile_class) for p in component})
                material_statuses = sorted({str((cell_meta[p][0].material or {}).get("status") or "unverified") for p in component})
                region_id = f"z{int(zone_id)}-y{int(y)}-{method}-{terrain_kind}-cc{serial}"
                regions.append({
                    "region_id": region_id,
                    "zone_id": int(zone_id),
                    "y": int(y),
                    "coordinate_space": GRID_SPACE,
                    **classification.public(),
                    "tile_count": len(component),
                    "tiles": [_tile(zone_id, y, p) for p in sorted(component, key=_component_key)],
                    "bounds": {"min_x": min_x, "max_x": max_x, "min_z": min_z, "max_z": max_z},
                    "boundary_tiles": [_tile(zone_id, y, p) for p in sorted(boundary, key=_component_key)],
                    "interior_tiles": [_tile(zone_id, y, p) for p in sorted(interior, key=_component_key)],
                    "entry_tiles": [_tile(zone_id, y, p) for p in sorted(entry, key=_component_key)],
                    "outline_segments": self._outline_segments(component),
                    "patrol": self._patrol(zone_id, y, component),
                    "source": {
                        "kind": "rom_terrain_tileclass",
                        "tile_classes": tile_classes,
                        "material_statuses": material_statuses,
                        "static_navigation_revision": self.provider.revision,
                    },
                })
        return {
            "format": REGION_FORMAT,
            "status": "resolved",
            "zone_id": int(zone_id),
            "y": int(y),
            "coordinate_space": GRID_SPACE,
            "region_count": len(regions),
            "regions": regions,
            "limitations": [
                "Grass regions are physical TileClass regions, not species encounter tables.",
                "Surf regions are navigation candidates until water encounter eligibility is independently verified.",
                "Dynamic actors/scripts may still block a static candidate tile.",
            ],
        }

    def find_region(self, region_id: str, zone_id: int, y: int, *, player_sample: dict[str, Any] | None = None) -> dict[str, Any] | None:
        payload = self.zone_regions(zone_id, y, player_sample=player_sample)
        return next((region for region in payload["regions"] if region["region_id"] == region_id), None)

    def connected_zone_ids(self, anchor_zone_id: int, *, max_zones: int = 24) -> dict[str, Any]:
        """Return cardinally adjacent exterior Zone ownership in one Matrix."""
        anchor_zone_id = int(anchor_zone_id)
        zone = self.provider.rom.zone(anchor_zone_id)
        matrix = self.provider.rom.matrix(zone.matrix_id)
        area = self.provider.rom.area(zone.area_id)
        base = {
            "matrix_id": int(matrix.matrix_id),
            "anchor_zone_id": anchor_zone_id,
            "environment": "exterior" if bool(area.is_exterior) else "interior",
        }
        if not bool(area.is_exterior):
            return {**base, "zone_ids": [anchor_zone_id], "adjacency": [], "reason": "interior_not_spatially_stitched"}
        ownership: dict[tuple[int, int], int] = {}
        for cell in matrix.cells():
            owner = _int(cell.get("zone_id"))
            chunk = _int(cell.get("chunk_id"))
            if owner is None or chunk is None or chunk == 0xFFFF:
                continue
            ownership[(int(cell["x"]), int(cell["y"]))] = owner
        graph: dict[int, set[int]] = {}
        adjacency: set[tuple[int, int]] = set()
        for (x, z), owner in ownership.items():
            for dx, dz in _CARDINAL:
                other = ownership.get((x + dx, z + dz))
                if other is None or other == owner:
                    continue
                pair = tuple(sorted((owner, other)))
                adjacency.add(pair)
                graph.setdefault(owner, set()).add(other)
                graph.setdefault(other, set()).add(owner)
        selected: list[int] = []
        seen: set[int] = set()
        pending = [anchor_zone_id]
        while pending and len(selected) < max(1, int(max_zones)):
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            selected.append(current)
            pending.extend(sorted(graph.get(current, set()) - seen))
        selected_set = set(selected)
        return {
            **base,
            "zone_ids": selected,
            "zone_count": len(selected),
            "adjacency": [
                {"zone_a": a, "zone_b": b}
                for a, b in sorted(adjacency)
                if a in selected_set and b in selected_set
            ],
            "truncated": bool(pending),
            "reason": "cardinally_adjacent_zone_ownership_cells_in_same_matrix",
        }

    def connected_regions(
        self,
        anchor_zone_id: int,
        y: int,
        *,
        player_sample: dict[str, Any] | None = None,
        max_zones: int = 24,
    ) -> dict[str, Any]:
        cluster = self.connected_zone_ids(anchor_zone_id, max_zones=max_zones)
        regions: list[dict[str, Any]] = []
        per_zone: list[dict[str, Any]] = []
        for zone_id in cluster.get("zone_ids") or [int(anchor_zone_id)]:
            payload = self.zone_regions(int(zone_id), int(y), player_sample=player_sample)
            regions.extend(payload["regions"])
            per_zone.append({"zone_id": int(zone_id), "region_count": payload["region_count"]})
        return {
            "format": "black2-connected-encounter-regions/v1",
            "status": "resolved",
            "anchor_zone_id": int(anchor_zone_id),
            "y": int(y),
            "connected_world": cluster,
            "region_count": len(regions),
            "zone_summary": per_zone,
            "regions": regions,
            "execution_policy": "Regions outside the live player Zone are read-only until cross-Zone navigation is verified.",
        }
