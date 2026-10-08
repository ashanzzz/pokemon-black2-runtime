"""ROM connector semantics without mistaking archive records for live travel.

Warp endpoints are joined by ZoneData.entitiesID (+0x16) and entity record
index. A target record is a useful destination candidate, but is not the
player's observed landing position or proof that a transition can execute.
"""
from __future__ import annotations

from collections import Counter, OrderedDict, defaultdict
from copy import deepcopy
import math
import threading
from typing import Any

from .gen5_rom_map import MATRIX_NONE
from .map_graph import RomMapGraphService
from .warp_transition_evidence import runtime_warp_evidence


GRID_SPACE = "gen5-field-grid-v1"
WORLD_SPACE = "gen5-field-world-v1"
TILE_SIZE_WORLD = 16
CHUNK_SIZE_TILES = 32
SENTINEL_IDS = frozenset({0xFFFE, 0xFFFF})
COORDINATE_CACHE_CAP = 256


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
        return result if math.isfinite(result) else None
    return None


def _endpoint(edge: dict[str, Any], prefix: str) -> tuple[int, int] | None:
    zone = _integer(edge.get("source_zone_id" if prefix == "source" else "target_zone_id_candidate"))
    # The source record index is stable within the decoded archive.  Warp
    # arg2 is deliberately not treated as a target record index until a live
    # transition registry verifies that relationship.
    warp = _integer(edge.get("source_warp_id")) if prefix == "source" else (
        _integer(edge.get("target_warp_id"))
        if edge.get("target_warp_semantics_verified") is True else None
    )
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
        self._coordinate_lock = threading.RLock()
        self._coordinate_cache: OrderedDict[tuple[int, int], dict[str, Any]] = OrderedDict()

    def _coordinates(self, edge: dict[str, Any]) -> dict[str, Any]:
        key = _endpoint(edge, "source")
        if key is not None:
            with self._coordinate_lock:
                cached = self._coordinate_cache.get(key)
                if cached is not None:
                    self._coordinate_cache.move_to_end(key)
                    return cached
        record = edge.get("source_record") or {}
        units = str(record.get("coordinate_units") or "unknown")
        zone_id = _integer(edge.get("source_zone_id"))
        x_world = _number(record.get("x_raw"))
        z_world = _number(record.get("y_raw"))
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
                "raw": record.get("arg11_raw"),
                "grid_y": None,
                "status": "unverified",
                "reason": "Warp +0x12 is an unverified raw parameter, not a calibrated elevation field.",
            },
            "transform": "x=floor(raw.x_raw/16), z=floor(raw.y_raw/16); no live chunk offset",
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
            with self._coordinate_lock:
                cached = self._coordinate_cache.get(key)
                if cached is not None:
                    self._coordinate_cache.move_to_end(key)
                    return cached
                self._coordinate_cache[key] = result
                self._coordinate_cache.move_to_end(key)
                while len(self._coordinate_cache) > COORDINATE_CACHE_CAP:
                    self._coordinate_cache.popitem(last=False)
        return result

    @staticmethod
    def _window_source_tile(record: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        x_world = _number(record.get("x_raw"))
        z_world = _number(record.get("y_raw"))
        return (
            {"x": x_world, "y": None, "z": z_world},
            {
                "x": (x_world / TILE_SIZE_WORLD) if x_world is not None else None,
                "z": (z_world / TILE_SIZE_WORLD) if z_world is not None else None,
                "x_extent_raw": record.get("x_extent_raw"),
                "y_extent_raw": record.get("y_extent_raw"),
            },
        )

    def window_warps(self, zone_id: int) -> list[dict[str, Any]]:
        """Return only source trigger footprints needed by a static map window.

        This deliberately does not call ``RomMapGraphService.build()``.  A
        window needs local markers, not a complete ROM-wide portal catalog.
        """
        if self.rom is None:
            return []
        if _integer(zone_id) is None or zone_id < 0:
            raise IndexError("zone_id must be a non-negative integer")
        zone = self.rom.zone(int(zone_id))
        entities = self.rom.entities(zone.entities_id)
        zone_count = _integer(getattr(self.rom, "zone_count", None))
        records = entities.get("warps") if isinstance(entities, dict) else []
        result: list[dict[str, Any]] = []
        for index, raw in enumerate(records or []):
            if not isinstance(raw, dict):
                continue
            warp_id = _integer(raw.get("id"))
            if warp_id is None:
                warp_id = index
            edge = {
                "source_zone_id": int(zone_id),
                "source_warp_id": warp_id,
                "source_record": raw,
            }
            coordinates = self._coordinates(edge)
            source_world, source_tile = self._window_source_tile(raw)
            raw_target = _integer(raw.get("target_zone_or_map_raw"))
            target_zone = (
                raw_target
                if (
                    zone_count is not None
                    and raw_target is not None
                    and raw_target not in SENTINEL_IDS
                    and 0 <= raw_target < zone_count
                )
                else None
            )
            if raw_target in SENTINEL_IDS:
                resolution = "dynamic_or_sentinel"
                reason = "Reserved target value may depend on return-location or script state."
            elif target_zone is None:
                resolution = "invalid_or_unresolved_zone"
                reason = "The raw destination does not resolve to an in-range ZoneData record."
            else:
                resolution = "not_expanded_for_static_window"
                reason = "Static window projection retains only the local trigger; use the warp catalog for global candidate detail."
            result_item = {
                "id": f"zone:{zone_id}:warp:{warp_id}",
                "kind": "warp",
                "source": {
                    "zone_id": int(zone_id),
                    "warp_id": warp_id,
                    "entities_id": zone.entities_id,
                    "tile": source_tile,
                    "world": source_world,
                    "grid_candidate": coordinates.get("grid_candidate"),
                    "coordinate_resolution": coordinates,
                },
                "destination": {
                    "zone_id": target_zone,
                    "warp_id": None,
                    "target_zone_or_map_raw": raw_target,
                    "status": "unresolved",
                    "resolution": resolution,
                    "landing_tile": None,
                    "landing_status": "not_observed",
                    "reason": reason,
                },
                "role": {
                    "kind": "unknown",
                    "status": "unverified",
                    "basis": "The local window intentionally does not expand target Zone metadata.",
                },
                "traversal": {
                    "can_traverse": None,
                    "status": "unverified",
                    "reason": "A ROM trigger footprint does not prove collision, direction, script gates, or transition execution.",
                },
                "verification": {
                    "status": "candidate" if coordinates.get("status") == "candidate" else "unverified",
                    "source": "ROM ZoneData entitiesID source trigger only",
                    "runtime_observed": False,
                },
            }
            self._apply_runtime_evidence(result_item)
            result.append(result_item)
        return result

    @staticmethod
    def _grid_candidate(warp: dict[str, Any]) -> dict[str, Any]:
        source = warp.get("source") if isinstance(warp.get("source"), dict) else {}
        candidate = source.get("grid_candidate")
        if isinstance(candidate, dict):
            return candidate
        resolution = source.get("coordinate_resolution")
        return resolution.get("grid_candidate") if isinstance(resolution, dict) and isinstance(resolution.get("grid_candidate"), dict) else {}

    def _apply_runtime_evidence(self, warp: dict[str, Any]) -> dict[str, Any]:
        """Decorate a static connector with observed transition evidence.

        This promotes only the fact that the source footprint led to a live
        destination/landing.  It deliberately does not set
        ``target_warp_semantics_verified`` or invent a target record index.
        """
        source = warp.get("source") if isinstance(warp.get("source"), dict) else {}
        destination = warp.get("destination") if isinstance(warp.get("destination"), dict) else {}
        source_zone = _integer(source.get("zone_id"))
        source_grid = self._grid_candidate(warp)
        target_zone = _integer(destination.get("zone_id"))
        if target_zone is None:
            target_zone = _integer(destination.get("target_zone_or_map_raw"))
        if source_zone is None or target_zone is None:
            return warp
        matches = runtime_warp_evidence.match(
            source_zone=source_zone,
            source_x=_integer(source_grid.get("x")),
            source_z=_integer(source_grid.get("z")),
            destination_zone=target_zone,
        )
        if not matches:
            return warp
        latest = matches[-1]
        landing = latest.get("landing_grid") or {}
        if isinstance(destination, dict):
            destination["landing_tile"] = dict(landing)
            destination["landing_status"] = "verified" if len(matches) >= 2 else "observed_candidate"
            destination["status"] = "candidate"
            destination["reason"] = (
                "Live Zone transition and landing observed twice; target Warp record identity remains unresolved."
                if len(matches) >= 2 else
                "Live Zone transition and landing observed once; repeat traversal before upgrading evidence."
            )
        verification = warp.setdefault("verification", {})
        verification.update({
            "status": "verified_transition" if len(matches) >= 2 else "observed_transition",
            "runtime_observed": True,
            "evidence_count": len(matches),
            "evidence": matches[-8:],
            "target_warp_semantics_verified": False,
        })
        traversal = warp.setdefault("traversal", {})
        traversal.update({
            "can_traverse": True,
            "status": "observed",
            "requirements": ["fresh PlayerRuntime", "revalidate source tile and story gates"],
            "reason": "A live transition was observed from this source footprint; future execution remains session/state gated.",
        })
        return warp

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
        target_verified = edge.get("target_warp_semantics_verified") is True
        target_warp = _integer(edge.get("target_warp_id")) if target_verified else None
        raw_arg2 = edge.get("target_warp_arg2_raw")
        raw_zone = edge.get("target_zone_or_map_raw")
        if raw_zone is None:
            raw_zone = (edge.get("source_record") or {}).get("target_zone_or_map_raw")
        target_records = index.get((target_zone, target_warp), []) if target_verified else []
        destination: dict[str, Any] = {
            "zone_id": target_zone,
            "warp_id": target_warp,
            "arg2_raw": raw_arg2,
            "target_zone_or_map_raw": raw_zone,
            "label": edge.get("target_label_candidate"),
            "status": "unresolved",
            "resolution": "unresolved",
            "target_connector_id": None,
            "target_tile_candidate": None,
            "target_world_candidate": None,
            "target_coordinate_resolution": None,
            "landing_tile": None,
            "landing_status": "not_observed",
            "reason": None,
        }
        if raw_zone in SENTINEL_IDS or raw_arg2 in SENTINEL_IDS:
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
        elif not target_verified:
            destination.update(
                resolution="target_warp_index_unresolved",
                reason="Warp arg2 is retained as raw data; target record identity is not live-transition verified.",
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
                status="candidate",
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
            # pairs must match, including each side's verified Warp index.
            reverse = [
                target.get("edge_id") for target in target_records
                if edge.get("target_warp_semantics_verified") is True
                and source_key is not None and _endpoint(target, "target") == source_key
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
                    "reason": "Reciprocal edges require verified target endpoint identity and live traversal evidence.",
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
        for warp in page:
            self._apply_runtime_evidence(warp)
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
            "coverage": {
                **deepcopy(self._coverage),
                "runtime_verified_count": sum(
                    1 for warp in self._warps or ()
                    if self._apply_runtime_evidence(deepcopy(warp)).get("verification", {}).get("runtime_observed") is True
                ),
            },
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

    def cache_status(self) -> dict[str, Any]:
        with self._coordinate_lock:
            coordinates = {
                "entries": len(self._coordinate_cache),
                "cap": COORDINATE_CACHE_CAP,
            }
        return {
            "format": "black2-semantic-connectors-cache-status/v1",
            "coordinates": coordinates,
            "global_catalog": {
                "built": self._warps is not None,
                "warp_entries": len(self._warps or []),
                "zone_indexes": len(self._by_zone),
            },
        }
