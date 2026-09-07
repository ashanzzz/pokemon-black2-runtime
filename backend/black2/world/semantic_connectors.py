"""ROM connector semantics without mistaking archive records for live travel.

Warp endpoints are joined by ZoneData.entitiesID (+0x16) and entity record
index. A target record is a useful destination candidate, but is not the
player's observed landing position or proof that a transition can execute.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
import math
from typing import Any

from .gen5_rom_map import MATRIX_NONE
from .map_graph import RomMapGraphService


GRID_SPACE = "gen5-field-grid-v1"
WORLD_SPACE = "gen5-field-world-v1"
TILE_SIZE_WORLD = 16
CHUNK_SIZE_TILES = 32
SENTINEL_IDS = frozenset({0xFFFE, 0xFFFF})


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) else None
    return None


def _endpoint(edge: dict[str, Any], prefix: str) -> tuple[int, int] | None:
    zone = _integer(edge.get("source_zone_id" if prefix == "source" else "target_zone_id_candidate"))
    warp = _integer(edge.get("source_warp_id" if prefix == "source" else "target_warp_id"))
    return (zone, warp) if zone is not None and warp is not None else None


class SemanticConnectorService:
    """Enrich the existing static graph and paginate its immutable snapshot."""

    def __init__(self, graph: RomMapGraphService) -> None:
        self.graph = graph
        self.rom = getattr(graph, "rom", None)
        self._warps: list[dict[str, Any]] | None = None
        self._by_zone: dict[int, list[dict[str, Any]]] = {}
        self._coverage: dict[str, Any] = {}
        self._nodes: dict[int, dict[str, Any]] = {}
        self._coordinate_cache: dict[tuple[int, int], dict[str, Any]] = {}

    def _coordinates(self, edge: dict[str, Any]) -> dict[str, Any]:
        key = _endpoint(edge, "source")
        if key in self._coordinate_cache:
            return self._coordinate_cache[key]
        record = edge.get("source_record") or {}
        units = str(record.get("coordinate_units") or "unknown")
        zone_id = _integer(edge.get("source_zone_id"))
        x_world = _number(record.get("x_world"))
        z_world = _number(record.get("y_world"))
        # The established Warp decoder emits matrix-world X/Z. Adding the
        # player's live chunk origin would translate Zone 439 twice.
        supported_units = units == "map_world_units_16_per_tile_candidate"
        tile_x = math.floor(x_world / TILE_SIZE_WORLD) if supported_units and x_world is not None else None
        tile_z = math.floor(z_world / TILE_SIZE_WORLD) if supported_units and z_world is not None else None
        grid = {"space": GRID_SPACE, "zone_id": zone_id, "x": tile_x, "y": None, "z": tile_z}
        result: dict[str, Any] = {
            "status": "unresolved",
            "raw_units": units,
            "horizontal_reference": "unresolved",
            "grid_candidate": grid if tile_x is not None and tile_z is not None else None,
            "matrix_id": self._nodes.get(zone_id, {}).get("matrix_id"),
            "matrix_cell_candidate": None,
            "chunk_local_tile_candidate": None,
            "height": {
                "raw": record.get("z"),
                "grid_y": None,
                "status": "unverified",
                "reason": "The event height field has not been calibrated to PlayerRuntime GPos.y.",
            },
            "transform": "x=floor(raw.x_world/16), z=floor(raw.y_world/16); no live chunk offset",
            "reason": "Coordinate reference requires a decoded matrix and a matching cell.",
        }
        if tile_x is not None and tile_z is not None and zone_id is not None and self.rom is not None:
            try:
                zone = self.rom.zone(zone_id)
                matrix = self.rom.matrix(zone.matrix_id)
                chunk_x, chunk_z = tile_x // CHUNK_SIZE_TILES, tile_z // CHUNK_SIZE_TILES
                result["matrix_id"] = zone.matrix_id
                if 0 <= chunk_x < matrix.width and 0 <= chunk_z < matrix.height:
                    cell = matrix.cell(chunk_x, chunk_z)
                    result["matrix_cell_candidate"] = {
                        "x": chunk_x, "z": chunk_z,
                        "chunk_id": cell.get("chunk_id"),
                        "zone_id": cell.get("zone_id"),
                    }
                    result["chunk_local_tile_candidate"] = {
                        "x": tile_x % CHUNK_SIZE_TILES, "z": tile_z % CHUNK_SIZE_TILES,
                    }
                    occupied = cell.get("chunk_id") not in (None, MATRIX_NONE)
                    if occupied and matrix.has_zones and cell.get("zone_id") == zone_id:
                        result.update(
                            status="candidate",
                            horizontal_reference="matrix_absolute_candidate",
                            reason="ROM world X/Z select a populated matrix cell owned by the source Zone.",
                        )
                    elif occupied and not matrix.has_zones:
                        result.update(
                            status="candidate",
                            horizontal_reference="standalone_matrix_local_candidate",
                            reason="ROM world X/Z are in the Zone's standalone matrix; runtime alignment is unverified.",
                        )
                    else:
                        result["reason"] = "ROM position selects an empty matrix cell or a different Zone."
                else:
                    result["reason"] = "ROM position lies outside the source Zone's matrix."
            except (IndexError, ValueError, OSError, RuntimeError) as error:
                result["reason"] = f"Matrix coordinate resolution failed: {error}"
        if key is not None:
            self._coordinate_cache[key] = result
        return result

    def _role(self, source_zone: int | None, target_zone: int | None) -> dict[str, Any]:
        source = self._nodes.get(source_zone, {})
        target = self._nodes.get(target_zone, {})
        src_ext, dst_ext = source.get("is_exterior"), target.get("is_exterior")
        if source_zone is not None and source_zone == target_zone:
            kind = "same_zone_link"
        elif isinstance(src_ext, bool) and isinstance(dst_ext, bool):
            kind = {
                (True, False): "entrance",
                (False, True): "exit",
                (False, False): "interior_link",
                (True, True): "exterior_link",
            }[(src_ext, dst_ext)]
        else:
            kind = "unknown"
        return {
            "kind": kind,
            "status": "candidate" if kind != "unknown" else "unverified",
            "source_environment": source.get("environment"),
            "target_environment": target.get("environment"),
            "source_parent_zone_id": source.get("parent_zone_id"),
            "target_parent_zone_id": target.get("parent_zone_id"),
            "basis": "Source and target AreaData.is_exterior; a role does not imply an unlocked or traversable door.",
        }

    def _destination(
        self, edge: dict[str, Any], index: dict[tuple[int, int], list[dict[str, Any]]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        target_zone = _integer(edge.get("target_zone_id_candidate"))
        target_warp = _integer(edge.get("target_warp_id"))
        raw_zone = edge.get("target_zone_or_map_raw")
        if raw_zone is None:
            raw_zone = (edge.get("source_record") or {}).get("target_zone_or_map_raw")
        target_records = index.get((target_zone, target_warp), [])
        destination: dict[str, Any] = {
            "zone_id": target_zone,
            "warp_id": target_warp,
            "target_zone_or_map_raw": raw_zone,
            "label": edge.get("target_label_candidate"),
            "status": "candidate" if target_zone is not None else "unresolved",
            "resolution": "unresolved",
            "target_connector_id": None,
            "target_tile_candidate": None,
            "target_world_candidate": None,
            "target_coordinate_resolution": None,
            "landing_tile": None,
            "landing_status": "not_observed",
            "reason": None,
        }
        if raw_zone in SENTINEL_IDS or target_warp in SENTINEL_IDS:
            destination.update(
                resolution="dynamic_or_sentinel",
                status="unresolved",
                reason="A reserved target value may depend on return-location or script state; no concrete destination is assumed.",
            )
            target_records = []
        elif target_zone is None:
            destination.update(
                resolution="invalid_or_unresolved_zone",
                reason="The raw destination does not resolve to an in-range ZoneData record.",
            )
        elif target_warp is None or target_warp < 0:
            destination.update(resolution="invalid_target_warp", reason="Target Warp ID is not a non-negative integer.")
        elif not target_records:
            destination.update(
                resolution="missing_target_warp",
                reason="No decoded Warp record exists at the target Zone and Warp ID.",
            )
        elif len(target_records) != 1:
            destination.update(
                resolution="ambiguous_target_warp",
                reason="Multiple records share the target Zone and Warp ID.",
            )
        else:
            target = target_records[0]
            coordinates = self._coordinates(target)
            destination.update(
                resolution="rom_target_warp_resolved",
                target_connector_id=target.get("edge_id"),
                target_tile_candidate=coordinates.get("grid_candidate"),
                target_world_candidate=target.get("source_position"),
                target_coordinate_resolution=coordinates,
                reason="Target Warp trigger coordinates are known from ROM; actual spawn offset and floor require a live transition.",
            )
        return destination, target_records

    def _audit_coverage(self, graph: dict[str, Any], edges: list[dict[str, Any]]) -> dict[str, Any]:
        expected = getattr(self.rom, "zone_count", None)
        errors: list[dict[str, Any]] = []
        entities_decoded = 0
        expected_warps = 0
        if isinstance(expected, int):
            for zone_id in range(expected):
                try:
                    zone = self.rom.zone(zone_id)
                    entities = self.rom.entities(zone.entities_id)
                    entities_decoded += 1
                    expected_warps += len(entities.get("warps") or [])
                except (IndexError, ValueError, OSError, RuntimeError) as error:
                    errors.append({"zone_id": zone_id, "stage": "zone_entities", "reason": str(error)})
                if zone_id not in self._nodes:
                    errors.append({"zone_id": zone_id, "stage": "graph_node", "reason": "Zone node is absent from the decoded graph."})
        counts = Counter((warp["destination"]["resolution"]) for warp in self._warps or [])
        complete = expected is not None and entities_decoded == expected and len(self._nodes) == expected and expected_warps == len(edges)
        return {
            "status": "complete_rom_catalog" if complete else "partial_or_unverified",
            "zone_count_expected": expected,
            "zone_count_decoded": len(self._nodes),
            "entities_zone_count_decoded": entities_decoded if expected is not None else None,
            "warp_count_expected": expected_warps if expected is not None else None,
            "warp_count_decoded": len(edges),
            "destination_resolutions": dict(sorted(counts.items())),
            "reciprocal_connector_count": sum(bool(warp["reverse_edges"]) for warp in self._warps or []),
            "runtime_verified_count": 0,
            "decode_errors": errors,
            "scope": "All decoded ROM Warp records; script teleports, lifts, and seamless matrix boundaries may have no Warp record.",
        }

    def _build(self) -> None:
        if self._warps is not None:
            return
        graph = self.graph.build()
        self._nodes = {node["zone_id"]: node for node in graph.get("nodes", []) if _integer(node.get("zone_id")) is not None}
        edges = graph.get("edges") or []
        index: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            key = _endpoint(edge, "source")
            if key is not None:
                index[key].append(edge)
        warps = []
        by_zone: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for edge in edges:
            source_key = _endpoint(edge, "source")
            source_zone = _integer(edge.get("source_zone_id"))
            destination, target_records = self._destination(edge, index)
            coordinates = self._coordinates(edge)
            # An arbitrary incoming edge is not a return path. Both endpoint
            # pairs must match, including each side's Warp index.
            reverse = [
                target.get("edge_id") for target in target_records
                if source_key is not None and _endpoint(target, "target") == source_key
                and target.get("edge_id") != edge.get("edge_id")
            ]
            warp = {
                "id": edge.get("edge_id"),
                "kind": "warp",
                "role": self._role(source_zone, destination.get("zone_id")),
                "source": {
                    "zone_id": source_zone,
                    "warp_id": edge.get("source_warp_id"),
                    "entities_id": self._nodes.get(source_zone, {}).get("entities_id"),
                    "tile": edge.get("source_tile_candidate"),
                    "world": edge.get("source_position"),
                    "grid_candidate": coordinates.get("grid_candidate"),
                    "coordinate_resolution": coordinates,
                },
                "destination": destination,
                "reverse_edges": reverse,
                "return_path": {
                    "status": "reciprocal_rom_candidate" if reverse else "unverified",
                    "can_return": None,
                    "reason": "Reciprocal ROM records require live traversal evidence; absent reciprocity does not prove a one-way transition.",
                },
                "traversal": {
                    "can_traverse": None,
                    "status": "unverified",
                    "requirements": None,
                    "reason": "Collision, event flags, approach direction, and scripts can gate a ROM Warp.",
                },
                "verification": {
                    "status": "candidate" if destination.get("status") == "candidate" else "unverified",
                    "source": "rom:/a/0/1/2 entitiesID at +0x16 -> rom:/a/1/2/6 Warp record",
                    "runtime_observed": False,
                },
                "raw": edge.get("source_record"),
            }
            warps.append(warp)
            if source_zone is not None:
                by_zone[source_zone].append(warp)
        self._warps = warps
        self._by_zone = dict(by_zone)
        self._coverage = self._audit_coverage(graph, edges)

    def query(
        self, zone_id: int | None = None, *, offset: int = 0, limit: int = 2048,
        include_raw: bool = False,
    ) -> dict[str, Any]:
        """Return a bounded Zone or global catalog with explicit coverage.

        ``IndexError`` means an invalid Zone. ``ValueError`` means invalid
        pagination. The source ``tile`` field preserves the original graph's
        fractional candidates; use ``grid_candidate`` for integer tile cells.
        """
        if _integer(offset) is None or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if _integer(limit) is None or not 1 <= limit <= 2048:
            raise ValueError("limit must be an integer between 1 and 2048")
        if zone_id is not None:
            if _integer(zone_id) is None or zone_id < 0:
                raise IndexError("zone_id must be a non-negative integer")
            if self.rom is not None:
                self.rom.zone(zone_id)
        self._build()
        if zone_id is not None and self.rom is None and zone_id not in self._nodes and zone_id not in self._by_zone:
            raise IndexError(f"Zone {zone_id} is absent from the decoded graph")
        selected = self._warps if zone_id is None else self._by_zone.get(zone_id, [])
        selected = selected or []
        page = deepcopy(selected[offset:offset + limit])
        if not include_raw:
            for warp in page:
                warp.pop("raw", None)
        return {
            "format": "black2-ai-warps/v1",
            "coordinate_space": WORLD_SPACE,
            "grid_coordinate_space": GRID_SPACE,
            "zone_filter": zone_id,
            "count": len(page),
            "total_count": len(selected),
            "offset": offset,
            "limit": limit,
            "next_offset": offset + limit if offset + limit < len(selected) else None,
            "warps": page,
            "coverage": deepcopy(self._coverage),
            "semantic_policy": {
                "destination": "A target Warp record is a candidate trigger position; landing_tile stays null without live evidence.",
                "reverse": "Only exact source and destination Zone/Warp pairs form a reciprocal ROM candidate.",
                "coordinates": "Horizontal integer cells use floor(world/16), with explicit matrix reference and unknown grid height.",
                "role": "Entrance/exit roles follow source and target AreaData environment and remain candidates.",
                "execution": "ROM metadata alone never grants traversal or return-path permission.",
            },
        }

    def coverage(self) -> dict[str, Any]:
        self._build()
        return deepcopy(self._coverage)
