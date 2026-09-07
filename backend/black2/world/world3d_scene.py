"""3D world scene contract for Pokémon Black 2 runtime (v6).

This module does not invent a second map coordinate system.  Static ROM terrain,
buildings and dynamic runtime actors are all expressed in canonical Gen-5 field
world units.  A browser may subtract ``scene_origin`` for numerical/camera
convenience, but canonical coordinates remain immutable facts.

High-frequency player updates consume ``player_runtime_service.latest`` only;
they do not issue additional RAM requests.  Static world data are ROM-backed and
cached by ``OriginalWorldService``.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from copy import deepcopy
from typing import Any

from ..memory.reader import MemoryReader
from .map_truth_v3 import MapTruthV3
from .original_world import OriginalWorldService
from .exported_world_store import ExportedWorldStore
from .runtime_player_state import player_runtime_service
from .runtime_actor_overlay import runtime_actor_overlay_service
from .map_graph import ZONE_LABEL_OVERRIDES

TILE_WORLD = 16.0
TILE_HALF = 8.0


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


def canonical_player(sample: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize the cached PlayerRuntime sample into one renderer contract."""
    if not sample or sample.get("status") not in {"resolved", "candidate"}:
        return {
            "status": "unresolved",
            "confidence": "unresolved",
            "reason": (sample or {}).get("reason", "no cached PlayerRuntime sample"),
        }

    position = sample.get("position") or {}
    grid = position.get("grid") or {}
    world = position.get("world") or {}
    mapper = sample.get("mapper") or {}
    orientation = sample.get("orientation") or {}
    locomotion = sample.get("locomotion") or {}
    temporal = sample.get("temporal") or {}

    gx = grid.get("x") if isinstance(grid.get("x"), int) else None
    gy = grid.get("y") if isinstance(grid.get("y"), int) else None
    gz = grid.get("z") if isinstance(grid.get("z"), int) else None
    wx, wy, wz = _num(world.get("x")), _num(world.get("y")), _num(world.get("z"))

    expected_x = gx * TILE_WORLD + TILE_HALF if gx is not None else None
    expected_z = gz * TILE_WORLD + TILE_HALF if gz is not None else None
    dx = abs(wx - expected_x) if wx is not None and expected_x is not None else None
    dz = abs(wz - expected_z) if wz is not None and expected_z is not None else None
    # During a move the actor may interpolate between tile centres, so only a
    # stationary/turning sample is expected to sit exactly at +8.  Always keep
    # the residual visible instead of forcing the WPos onto the grid centre.
    phase = locomotion.get("phase")
    grid_centre_expected = phase in {"Idle", "Turning", "Brake"}
    grid_world_consistent = (
        dx is not None and dz is not None and dx <= 0.25 and dz <= 0.25
        if grid_centre_expected else None
    )

    chunk = mapper.get("player_chunk") or {}
    chunk_size = mapper.get("chunk_tile_size")
    chunk_consistent = bool(mapper.get("chunk_matches_gpos")) if chunk else None

    face_raw = orientation.get("face_dir_raw")
    facing = orientation.get("facing", "Unresolved")
    yaw_deg = {0: 180.0, 1: 0.0, 2: -90.0, 3: 90.0}.get(face_raw)

    return {
        "format": "black2-world3d-player/v6",
        "status": sample.get("status"),
        "confidence": sample.get("confidence"),
        "frame": sample.get("frame"),
        "zone_id": sample.get("zone_id"),
        "coordinate_space": "gen5-field-world-v1",
        "grid": {"x": gx, "y": gy, "z": gz},
        "world": {"x": wx, "y": wy, "z": wz},
        "chunk": {
            "index": chunk.get("index"),
            "x": chunk.get("x"),
            "z": chunk.get("y"),
            "tile_size": chunk_size,
        },
        "orientation": {
            "face_dir_raw": face_raw,
            "facing": facing,
            "facing_zh": orientation.get("facing_zh"),
            "yaw_degrees_if_model_forward_is_south": yaw_deg,
            "verified": bool(orientation.get("verified")),
            "rotation_angle_hex": orientation.get("rotation_angle_hex"),
        },
        "locomotion": {
            "phase": phase,
            "semantic_state": locomotion.get("semantic_state"),
            "transport_mode": locomotion.get("transport_mode"),
            "gait": locomotion.get("gait"),
        },
        "temporal": temporal,
        "validation": {
            "grid_to_world_formula": "stationary tile centre: WPos.x=GPos.x*16+8; WPos.z=GPos.z*16+8",
            "expected_world_at_grid_centre": {"x": expected_x, "z": expected_z},
            "residual_world": {"x": dx, "z": dz},
            "grid_centre_check_applicable": grid_centre_expected,
            "grid_world_consistent": grid_world_consistent,
            "chunk_matches_gpos": chunk_consistent,
            "facing_crosscheck": bool(orientation.get("sources_agree")),
        },
    }


def scene_origin(player: dict[str, Any], world: dict[str, Any] | None) -> dict[str, float]:
    """Pick a display-only origin.  Never alter canonical object coordinates."""
    p = player.get("world") or {}
    if all(isinstance(p.get(k), (int, float)) for k in ("x", "z")):
        return {"x": float(p["x"]), "y": 0.0, "z": float(p["z"]), "source": "live_player_wpos"}
    matrix = (world or {}).get("matrix") or {}
    span = ((world or {}).get("render_coordinate_system") or {}).get("chunk_span_world") or 512.0
    width = matrix.get("width") or 1
    height = matrix.get("height") or 1
    return {
        "x": float(width) * float(span) * 0.5,
        "y": 0.0,
        "z": float(height) * float(span) * 0.5,
        "source": "matrix_center_fallback",
    }


@dataclass
class World3DSceneService:
    original: OriginalWorldService = field(default_factory=OriginalWorldService)
    truth: MapTruthV3 = field(default_factory=MapTruthV3)
    identity_ttl_seconds: float = 1.0
    exported: ExportedWorldStore | None = None
    _identity_cache: dict[str, Any] | None = None
    _identity_time: float = 0.0
    _identity_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # A loaded BMD0/BTX0 visual binding is expensive to establish because it
    # verifies headers against the current ARM9 map window.  It is therefore
    # filled only by an explicit scene refresh and reused only while the same
    # Zone/Matrix remains current.  This is display data, never a substitute
    # for live player/actor state.
    _loaded_visual_cache: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.exported is None:
            self.exported = ExportedWorldStore(self.original)

    def player_live(self) -> dict[str, Any]:
        """Zero-RAM-request high-frequency endpoint source."""
        return canonical_player(player_runtime_service.latest)

    async def _refresh_identity(self, reader: MemoryReader, *, force: bool = False) -> dict[str, Any]:
        now = time.monotonic()
        cached_player = self.player_live()
        cached_zone = cached_player.get("zone_id")
        cached_identity_zone = ((self._identity_cache or {}).get("zone_identity") or {}).get("value")
        fresh = self._identity_cache is not None and (now - self._identity_time) < self.identity_ttl_seconds
        zone_same = cached_zone is None or cached_identity_zone is None or cached_zone == cached_identity_zone
        if not force and fresh and zone_same:
            return self._identity_cache
        async with self._identity_lock:
            now = time.monotonic()
            fresh = self._identity_cache is not None and (now - self._identity_time) < self.identity_ttl_seconds
            if not force and fresh:
                return self._identity_cache
            result = await self.truth.current(reader, include_world=False)
            self._identity_cache = result
            self._identity_time = now
            return result

    def static_scene(
        self,
        zone_id: int,
        *,
        live_span: float | None = None,
        runtime_matrix_id: int | None = None,
    ) -> dict[str, Any]:
        world = self.exported.zone(zone_id) if self.exported is not None else self.original.zone(zone_id)
        if isinstance(live_span, (int, float)) and live_span > 0:
            world = self.truth._apply_live_chunk_span(world, float(live_span))
        span = float((world.get("render_coordinate_system") or {}).get("chunk_span_world") or 512.0)
        terrains = []
        matrix_meta = world.get("matrix") or {}
        source_cells = world.get("cells", [])
        runtime_matrix_bound = (
            isinstance(runtime_matrix_id, int)
            and runtime_matrix_id != matrix_meta.get("matrix_id")
            and self.original is not None
        )
        runtime_cells_excluded = 0
        if runtime_matrix_bound:
            # Runtime FieldG3DMapper's exact ROM-matched matrix is stronger
            # evidence than a ZoneHeader's static matrix field.  This matters
            # for one-cell maps where the header's default matrix describes a
            # different shared world.  The matching ROM chunk is still served
            # through the normal lazy BMD0+BTX0 converter below.
            matrix = self.original.rom.matrix(runtime_matrix_id)
            # Runtime FieldG3DMapper exposes the complete loaded matrix, which
            # can include neighbouring zones.  The browser scene is scoped to
            # the current ZoneID, so retain only coordinates that the ROM
            # ZoneData assigns to this zone.  Without this guard, unrelated
            # terrain cells (including the blue platform seen around
            # 18/19,10/11 in Sangi Town) leak into the current map even though
            # the NDS renderer never draws them for this zone.
            # Some lightweight callers provide no exported cell inventory
            # (for example a metadata-only test fixture). In that case there
            # is no evidence with which to exclude neighboring cells, so keep
            # the matched runtime matrix intact. A populated inventory still
            # enables the strict current-Zone filter used by the browser.
            has_zone_inventory = any(
                isinstance(cell, dict) and cell.get("present")
                for cell in source_cells
            )
            zone_cells = {
                (int(cell.get("x")), int(cell.get("y")))
                for cell in source_cells
                if cell.get("present") and cell.get("belongs_to_zone") is not False
            } if has_zone_inventory else None
            runtime_cells = []
            for cell in matrix.cells():
                key = (int(cell["x"]), int(cell["y"]))
                if zone_cells is not None and key not in zone_cells:
                    if cell.get("chunk_id") != 0xFFFFFFFF:
                        runtime_cells_excluded += 1
                    continue
                runtime_cells.append({
                    "x": cell["x"], "y": cell["y"], "chunk_id": cell["chunk_id"],
                    "present": cell["chunk_id"] != 0xFFFFFFFF,
                    "belongs_to_zone": True,
                    "runtime_matrix_bound": True,
                })
            source_cells = [
                cell for cell in runtime_cells if cell["present"]
            ]
            matrix_meta = {
                "matrix_id": matrix.matrix_id,
                "has_zones": matrix.has_zones,
                "width": matrix.width,
                "height": matrix.height,
                "cell_count": matrix.cell_count,
                "trailing_bytes": matrix.trailing_bytes,
            }
        for cell in source_cells:
            if not cell.get("present") or cell.get("belongs_to_zone") is False:
                continue
            terrains.append({
                "id": f"terrain-{zone_id}-{cell['x']}-{cell['y']}",
                "cell": {"x": cell["x"], "z": cell["y"]},
                "chunk_id": cell.get("chunk_id"),
                "world": {
                    "x": (float(cell["x"]) + 0.5) * span,
                    "y": 0.0,
                    "z": (float(cell["y"]) + 0.5) * span,
                },
                # The runtime mapper has already been matched to the ROM
                # matrix.  Keep the original ROM converter URL available even
                # before the optional ARM9 visual probe completes; returning
                # null here used to make the browser render only a coordinate
                # grid, so a valid map looked empty.
                "asset_url": f"/api/v1/map/v5/terrain/{zone_id}/{cell['x']}/{cell['y']}/model.glb",
            })
        buildings = []
        for item in world.get("buildings", []):
            if item.get("belongs_to_zone") is False:
                continue
            if not item.get("resource"):
                continue
            p = item.get("world_position") or item.get("world_position_candidate") or {}
            buildings.append({
                "id": item.get("instance_id"),
                "uid": item.get("model_uid"),
                "world": {"x": _num(p.get("x")), "y": _num(p.get("y")), "z": _num(p.get("z"))},
                "rotation_degrees": item.get("rotation_degrees"),
                "door_uid": (item.get("resource") or {}).get("door_uid"),
                "door_offset": (item.get("resource") or {}).get("door_offset"),
                "has_door_metadata": bool((item.get("resource") or {}).get("has_door_metadata")),
                "asset_url": f"/api/v1/map/v5/building/{zone_id}/{item.get('model_uid')}/model.glb",
            })
        return {
            "format": "black2-world3d-static/v6",
            "zone_id": zone_id,
            "environment": "exterior" if (world.get("area") or {}).get("is_exterior") else "interior",
            "coordinate_space": "gen5-field-world-v1",
            "chunk_span_world": span,
            "zone": world.get("zone"),
            "area": world.get("area"),
            "matrix": matrix_meta,
            "terrains": terrains,
            "buildings": buildings,
            "entities": world.get("entities"),
            "source_policy": {
                "terrain": "ROM matrix coordinates + original BMD0/BTX0 conversion (cached)",
                "buildings": "ROM only / cached",
                "player": "not embedded in static scene",
            },
            "runtime_matrix_binding": {
                "status": "probable" if runtime_matrix_bound else "not_needed",
                "matrix_id": runtime_matrix_id if runtime_matrix_bound else matrix_meta.get("matrix_id"),
                "excluded_neighbour_cells": runtime_cells_excluded,
                "reason": (
                    "exact runtime mapper chunk table matched this ROM matrix; current ZoneID filter applied"
                    if runtime_matrix_bound else "ZoneHeader matrix agrees with static world"
                ),
            },
        }

    def connected_zone_cluster(self, zone_id: int, *, matrix_id: int | None = None, max_zones: int = 24) -> dict[str, Any]:
        """Return the exact spatial component around an exterior Zone.

        Only Zones that own cardinally adjacent cells in the *same* ROM
        MapMatrix are spatially stitched.  Warp-linked Zones on another Matrix
        are intentionally not overlaid because they do not share a proven
        canonical transform.
        """
        zone_id = int(zone_id)
        max_zones = max(1, min(64, int(max_zones)))
        anchor_header = self.original.rom.zone(zone_id)
        anchor_area = self.original.rom.area(anchor_header.area_id)
        selected_matrix_id = int(matrix_id if isinstance(matrix_id, int) else anchor_header.matrix_id)
        matrix = self.original.rom.matrix(selected_matrix_id)
        base = {
            "format": "black2-connected-zone-cluster/v1",
            "anchor_zone_id": zone_id,
            "matrix_id": selected_matrix_id,
            "matrix": {
                "width": matrix.width, "height": matrix.height,
                "has_zones": matrix.has_zones, "cell_count": matrix.cell_count,
            },
            "alignment": "shared_matrix_exact",
            "cross_matrix_policy": "cross_matrix_connector_graph_only_until_runtime_landing_transform_is_verified",
        }
        if not anchor_area.is_exterior:
            return {**base, "environment": "interior", "zone_ids": [zone_id], "zone_count": 1,
                    "adjacency": [], "cells": [], "reason": "interior_zone_not_spatially_stitched"}
        if not matrix.has_zones or matrix.zone_ids is None:
            return {**base, "environment": "exterior", "zone_ids": [zone_id], "zone_count": 1,
                    "adjacency": [], "cells": [], "reason": "matrix_has_no_zone_ownership_table"}

        owners: dict[tuple[int, int], int] = {}
        cells: list[dict[str, Any]] = []
        for cell in matrix.cells():
            chunk_id = int(cell.get("chunk_id", 0xFFFFFFFF))
            owner = _int(cell.get("zone_id"))
            if chunk_id == 0xFFFFFFFF or owner is None:
                continue
            try:
                header = self.original.rom.zone(owner)
                area = self.original.rom.area(header.area_id)
            except (IndexError, ValueError):
                continue
            if not area.is_exterior or int(header.matrix_id) != selected_matrix_id:
                continue
            key = (int(cell["x"]), int(cell["y"]))
            owners[key] = owner
            cells.append({"x": key[0], "z": key[1], "chunk_id": chunk_id, "zone_id": owner})

        adjacency_pairs: set[tuple[int, int]] = set()
        graph: dict[int, set[int]] = {}
        for (x, z), owner in owners.items():
            graph.setdefault(owner, set())
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                other = owners.get((x + dx, z + dz))
                if other is None or other == owner:
                    continue
                a, b = sorted((owner, other))
                adjacency_pairs.add((a, b))
                graph.setdefault(owner, set()).add(other)
                graph.setdefault(other, set()).add(owner)

        selected: list[int] = []
        pending = [zone_id]
        seen: set[int] = set()
        while pending and len(selected) < max_zones:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            selected.append(current)
            pending.extend(sorted(graph.get(current, set()) - seen))
        selected_set = set(selected)
        selected_cells = [cell for cell in cells if int(cell["zone_id"]) in selected_set]
        return {
            **base,
            "environment": "exterior",
            "zone_ids": selected,
            "zone_count": len(selected),
            "adjacency": [{"zone_a": a, "zone_b": b} for a, b in sorted(adjacency_pairs)
                          if a in selected_set and b in selected_set],
            "cells": selected_cells,
            "truncated": bool(pending),
            "reason": "cardinally_adjacent_zone_ownership_cells_in_same_matrix",
        }

    def connected_static_scene(
        self,
        zone_id: int,
        *,
        live_span: float | None = None,
        matrix_id: int | None = None,
        anchor_static: dict[str, Any] | None = None,
        max_zones: int = 24,
    ) -> dict[str, Any]:
        """Combine an exterior same-Matrix Zone component into one static scene."""
        anchor = deepcopy(anchor_static) if isinstance(anchor_static, dict) else self.static_scene(
            int(zone_id), live_span=live_span, runtime_matrix_id=matrix_id,
        )
        if anchor.get("environment") != "exterior":
            anchor["connected_world"] = self.connected_zone_cluster(
                int(zone_id), matrix_id=(anchor.get("matrix") or {}).get("matrix_id"), max_zones=max_zones,
            )
            return anchor
        selected_matrix_id = _int((anchor.get("matrix") or {}).get("matrix_id"))
        cluster = self.connected_zone_cluster(int(zone_id), matrix_id=selected_matrix_id, max_zones=max_zones)
        zone_ids = [int(v) for v in cluster.get("zone_ids") or [int(zone_id)]]
        span = float(anchor.get("chunk_span_world") or live_span or 512.0)

        terrains: list[dict[str, Any]] = []
        buildings: list[dict[str, Any]] = []
        entities: dict[str, Any] = {"zones": {}}
        terrain_seen: set[tuple[int, int, int]] = set()
        building_seen: set[tuple[Any, ...]] = set()
        zone_summaries: list[dict[str, Any]] = []
        for current_zone in zone_ids:
            st = anchor if current_zone == int(zone_id) else self.static_scene(current_zone, live_span=span)
            if _int((st.get("matrix") or {}).get("matrix_id")) != selected_matrix_id:
                continue
            if current_zone != int(zone_id):
                st = deepcopy(st)
                self._attach_warp_world_positions(st, {})
            label = ZONE_LABEL_OVERRIDES.get(current_zone) or {}
            zone_meta = st.get("zone") or {}
            zone_summaries.append({
                "zone_id": current_zone,
                "name_zh": label.get("name_zh") or f"Zone {current_zone}",
                "name_en": label.get("name_en") or f"Zone {current_zone}",
                "location_name_id": zone_meta.get("location_name_id"),
                "parent_zone_id": zone_meta.get("parent_zone_id"),
            })
            for item in st.get("terrains") or []:
                cell = item.get("cell") or {}
                key = (int(cell.get("x", -1)), int(cell.get("z", -1)), int(item.get("chunk_id", -1)))
                if key in terrain_seen:
                    continue
                terrain_seen.add(key)
                terrains.append({**deepcopy(item), "source_zone_id": current_zone})
            for item in st.get("buildings") or []:
                world = item.get("world") or {}
                key = (item.get("uid"), round(float(world.get("x") or 0), 4),
                       round(float(world.get("y") or 0), 4), round(float(world.get("z") or 0), 4))
                if key in building_seen:
                    continue
                building_seen.add(key)
                buildings.append({**deepcopy(item), "source_zone_id": current_zone})
            raw_entities = deepcopy(st.get("entities") or {})
            entities["zones"][str(current_zone)] = raw_entities
            for key, value in raw_entities.items():
                if not isinstance(value, list):
                    continue
                merged = entities.setdefault(key, [])
                for item in value:
                    merged.append({**deepcopy(item), "source_zone_id": current_zone} if isinstance(item, dict) else item)

        cluster_cells = cluster.get("cells") or []
        if cluster_cells:
            min_x = min(int(cell["x"]) for cell in cluster_cells) * span
            max_x = (max(int(cell["x"]) for cell in cluster_cells) + 1) * span
            min_z = min(int(cell["z"]) for cell in cluster_cells) * span
            max_z = (max(int(cell["z"]) for cell in cluster_cells) + 1) * span
        else:
            positions = [item.get("world") or {} for item in terrains]
            xs = [float(p["x"]) for p in positions if isinstance(p.get("x"), (int, float))]
            zs = [float(p["z"]) for p in positions if isinstance(p.get("z"), (int, float))]
            min_x = min(xs, default=0.0) - span / 2.0; max_x = max(xs, default=0.0) + span / 2.0
            min_z = min(zs, default=0.0) - span / 2.0; max_z = max(zs, default=0.0) + span / 2.0
        origin = {"x": (min_x + max_x) / 2.0, "y": 0.0, "z": (min_z + max_z) / 2.0,
                  "source": "shared_matrix_cluster_center"}
        cluster.update({
            "bounds_world": {"min_x": min_x, "max_x": max_x, "min_z": min_z, "max_z": max_z},
            "scene_origin": origin,
            "zones": zone_summaries,
        })
        anchor["terrains"] = terrains
        anchor["buildings"] = buildings
        anchor["entities"] = entities
        anchor["connected_world"] = cluster
        anchor["source_policy"] = {
            **(anchor.get("source_policy") or {}),
            "connected_world": "same-Matrix exterior Zone ownership cells; no cross-Matrix transform invented",
        }
        return anchor

    def connected_static_preview_scene(self, zone_id: int, *, max_zones: int = 24) -> dict[str, Any]:
        base = self.static_preview_scene(int(zone_id))
        connected = self.connected_static_scene(int(zone_id), anchor_static=base.get("static"), max_zones=max_zones)
        cluster = connected.get("connected_world") or {}
        origin = cluster.get("scene_origin") or base.get("scene_origin")
        zone_ids = cluster.get("zone_ids") or [int(zone_id)]
        key = f"cluster:matrix:{cluster.get('matrix_id')}:zones:{','.join(map(str, zone_ids))}:static-preview"
        base.update({"static": connected, "scene_origin": origin, "scene_key": key, "render_key": key,
                     "connected_world": cluster})
        return base

    async def connected_current_scene(
        self,
        reader: MemoryReader,
        *,
        force_identity: bool = False,
        loaded_visual: dict[str, Any] | None = None,
        max_zones: int = 24,
    ) -> dict[str, Any]:
        base = await self.current_scene(reader, force_identity=force_identity, loaded_visual=loaded_visual)
        if base.get("status") == "unresolved" or not isinstance(base.get("zone_id"), int):
            return base
        connected = self.connected_static_scene(
            int(base["zone_id"]), anchor_static=base.get("static"), max_zones=max_zones,
        )
        cluster = connected.get("connected_world") or {}
        if connected.get("environment") != "exterior" or len(cluster.get("zone_ids") or []) <= 1:
            return {**base, "static": connected, "connected_world": cluster}
        zone_ids = cluster.get("zone_ids") or [int(base["zone_id"])]
        key = f"cluster:matrix:{cluster.get('matrix_id')}:zones:{','.join(map(str, zone_ids))}"
        return {
            **base,
            "scene_key": key,
            "render_key": key,
            "scene_origin": cluster.get("scene_origin") or base.get("scene_origin"),
            "static": connected,
            "connected_world": cluster,
            "render_contract": {
                **(base.get("render_contract") or {}),
                "connected_world": "same-Matrix exterior Zones are spatially stitched; cross-Matrix links remain graph metadata",
            },
        }

    def static_preview_scene(self, zone_id: int) -> dict[str, Any]:
        """Build a self-contained, read-only scene envelope for a Zone.

        The normal ``scene/current`` contract is intentionally anchored to a
        live PlayerRuntime sample.  That is the right contract for execution,
        but it leaves the map workbench blank when BizHawk is not connected.
        This preview contract uses only ROM data and supplies a clearly marked
        synthetic camera anchor.  It must never be used as proof of the
        player's current location or as authorization to execute inputs.
        """
        static = self.static_scene(int(zone_id))
        # ``static_scene`` reuses the decoded entity lists from the world
        # store.  Copy before promoting warp coordinates so a browser preview
        # cannot mutate the cached ROM projection used by other endpoints.
        static = deepcopy(static)
        self._attach_warp_world_positions(static, {})
        zone_meta = static.get("zone") or {}
        label = ZONE_LABEL_OVERRIDES.get(int(zone_id))
        static["location"] = {
            "name_zh": (label or {}).get("name_zh") or f"Zone {zone_id}",
            "name_en": (label or {}).get("name_en") or f"Zone {zone_id}",
            "location_name_id": zone_meta.get("location_name_id"),
            "parent_zone_id": zone_meta.get("parent_zone_id"),
            "name_source": "operator_confirmed" if label else "zone_id_fallback",
        }

        positions: list[tuple[float, float, float]] = []
        for item in static.get("terrains") or []:
            world = item.get("world") if isinstance(item, dict) else None
            if isinstance(world, dict):
                x, y, z = _num(world.get("x")), _num(world.get("y")), _num(world.get("z"))
                if x is not None and y is not None and z is not None:
                    positions.append((x, y, z))
        for item in static.get("buildings") or []:
            world = item.get("world") if isinstance(item, dict) else None
            if isinstance(world, dict):
                x, y, z = _num(world.get("x")), _num(world.get("y")), _num(world.get("z"))
                if x is not None and y is not None and z is not None:
                    positions.append((x, y, z))
        for item in (static.get("entities") or {}).get("warps") or []:
            world = item.get("world") if isinstance(item, dict) else None
            if isinstance(world, dict):
                x, y, z = _num(world.get("x")), _num(world.get("y")), _num(world.get("z"))
                if x is not None and y is not None and z is not None:
                    positions.append((x, y, z))

        if positions:
            origin = {
                "x": (min(p[0] for p in positions) + max(p[0] for p in positions)) / 2.0,
                "y": 0.0,
                "z": (min(p[2] for p in positions) + max(p[2] for p in positions)) / 2.0,
                "source": "static_preview_bounds",
            }
        else:
            # A malformed/empty map remains inspectable and keeps the reason
            # explicit rather than inventing a world coordinate.
            origin = {"x": 0.0, "y": 0.0, "z": 0.0, "source": "static_preview_empty_fallback"}

        gx = math.floor(origin["x"] / TILE_WORLD)
        gz = math.floor(origin["z"] / TILE_WORLD)
        player = {
            "format": "black2-world3d-player/v6",
            "status": "candidate",
            "confidence": "candidate",
            "source": "static_rom_preview",
            "zone_id": int(zone_id),
            "frame": None,
            "coordinate_space": "gen5-field-world-v1",
            "grid": {"x": gx, "y": 0, "z": gz},
            "world": {"x": origin["x"], "y": origin["y"], "z": origin["z"]},
            "orientation": {"face_dir_raw": None, "facing": "Unresolved", "verified": False},
            "locomotion": {"phase": "StaticPreview", "semantic_state": "PreviewOnly", "transport_mode": None, "gait": None},
            "validation": {"grid_to_world_formula": "preview anchor only", "grid_world_consistent": None},
        }
        matrix_id = (static.get("matrix") or {}).get("matrix_id")
        key = f"zone:{int(zone_id)}:matrix:{matrix_id}:static-preview"
        return {
            "format": "black2-world3d-scene/v6",
            "status": "candidate",
            "confidence": "candidate",
            "preview_only": True,
            "zone_id": int(zone_id),
            "environment": static.get("environment"),
            "coordinate_space": "gen5-field-world-v1",
            "scene_key": key,
            "render_key": key,
            "scene_origin": origin,
            "player": player,
            "identity": {"confidence": "candidate", "source": "static_rom_preview"},
            "static": static,
            "render_contract": {
                "canonical_position": "ROM terrain/buildings/entities use Gen-5 field world units",
                "browser_display_position": "canonical - scene_origin (x/z only)",
                "player_position_source": "synthetic static preview anchor; no runtime claim",
                "execution": "disabled until a live PlayerRuntime sample replaces this preview",
            },
        }

    def _bind_loaded_visual(
        self,
        static: dict[str, Any],
        visual: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Replace only terrain GLBs proven loaded with their BTX-matched URLs.

        ``OriginalMapAssetService`` can render a ROM terrain model with the
        area's default texture archive.  That is useful as a static fallback,
        but not sufficient proof that it is the archive selected by the game.
        ``/api/v1/map/visual`` establishes the BMD0+BTX0 pairing from the live
        ARM9 window; retain static matrix placement and only replace matching
        cell asset URLs with those verified GLBs.
        """
        if not visual:
            static["visual_binding"] = {
                "status": "unresolved",
                "reason": "no explicit ARM9 BMD0/BTX0 visual refresh has completed",
            }
            return static

        matrix = static.get("matrix") or {}
        zone_id = static.get("zone_id")
        aligned = bool((visual.get("player_alignment") or {}).get("verified"))
        exact_zone = visual.get("map_definition_id") == zone_id
        exact_matrix = visual.get("matrix_id") == matrix.get("matrix_id")
        if not (visual.get("verified") and aligned and exact_zone and exact_matrix):
            static["visual_binding"] = {
                "status": "rejected",
                "reason": "loaded visual does not match the current Zone/Matrix/player chunk",
                "visual_zone_id": visual.get("map_definition_id"),
                "visual_matrix_id": visual.get("matrix_id"),
            }
            return static

        loaded = {}
        rejected_models: list[int] = []
        for model in visual.get("models") or []:
            cell = model.get("cell") or {}
            x, z = cell.get("x"), cell.get("y")
            url = model.get("asset_url")
            if not isinstance(x, int) or not isinstance(z, int) or not isinstance(url, str):
                continue
            if model.get("texture_candidate"):
                rejected_models.append(model.get("model_id"))
                continue
            loaded[(x, z)] = model

        bound_cells: list[dict[str, int]] = []
        for terrain in static.get("terrains") or []:
            cell = terrain.get("cell") or {}
            key = (cell.get("x"), cell.get("z"))
            model = loaded.get(key)
            if model is None:
                continue
            terrain["asset_url"] = model["asset_url"]
            terrain["texture_binding"] = {
                "status": "verified",
                "source": "ARM9 loaded BMD0 + ROM material-matched BTX0",
                "model_id": model.get("model_id"),
                "texture_id": model.get("texture_id"),
                "texture_match": model.get("texture_match"),
            }
            bound_cells.append({"x": key[0], "z": key[1]})

        cache_key = visual.get("cache_key") or (visual.get("cache") or {}).get("key")
        static["visual_binding"] = {
            "status": "verified" if bound_cells else "unresolved",
            "source": "ARM9 loaded BMD0 + ROM material-matched BTX0",
            "cache_key": cache_key,
            "texture_id": visual.get("texture_id"),
            "bound_cells": bound_cells,
            "rejected_model_ids": rejected_models,
        }
        return static

    @staticmethod
    def _attach_warp_world_positions(static: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
        """Promote ROM warp-local coordinates into canonical world X/Z.

        Entity archives store warp X/Y in map-local 16-unit coordinates.  For
        exterior maps those coordinates are relative to the currently loaded
        mapper chunk; interiors use the single chunk at (0, 0).  Keeping the
        raw fields and adding ``world`` lets the browser place an entrance
        without guessing a second coordinate system.
        """
        entities = static.get("entities")
        if not isinstance(entities, dict):
            return static
        span = float(static.get("chunk_span_world") or 512.0)
        chunk = (player.get("chunk") or {}) if isinstance(player, dict) else {}
        chunk_x = chunk.get("x") if isinstance(chunk.get("x"), (int, float)) else 0
        chunk_z = chunk.get("z") if isinstance(chunk.get("z"), (int, float)) else 0
        # Entity coordinates are already absolute field WPos in the v6
        # scene assembled from the ROM matrix.  The old code added the live
        # chunk origin a second time, placing every warp hundreds of tiles
        # away (for example Zone 439 warp X=1688 became X=3224).  Preserve
        # the raw values and only apply a chunk transform for legacy local
        # coordinates explicitly marked as such.
        warps = entities.get("warps") or []
        for warp in warps:
            if not isinstance(warp, dict):
                continue
            x = warp.get("x_world")
            z = warp.get("y_world")
            if not isinstance(x, (int, float)) or not isinstance(z, (int, float)):
                continue
            # The scene builder receives coordinates from the decoded Zone
            # entity archive.  These values are field WPos already (the same
            # 16-unit grid used by buildings and FieldActor.WPos), so adding
            # the live chunk origin here would double-translate the marker.
            # Keep a legacy fallback only for archives explicitly tagged as
            # map-local tile coordinates.
            units = str(warp.get("coordinate_units") or "")
            # The decoder labels the archive fields as map-world units.  They
            # are already absolute WPos in this renderer's matrix contract.
            # Only a future explicitly local-tile source should receive the
            # chunk-origin transform.
            if "local_tile" in units or "map_local" in units:
                wx = float(chunk_x) * span + float(x)
                wz = float(chunk_z) * span + float(z)
            else:
                wx, wz = float(x), float(z)
            width = max(1, _int(warp.get("width")) or 1)
            height = max(1, _int(warp.get("height")) or 1)
            # The ROM record is the footprint's first tile, while the
            # renderer's orange marker represents the middle tile.  Export
            # both explicitly so a client never has to repeat (or accidentally
            # double-apply) this presentation offset.  A one-tile exterior
            # warp therefore remains exactly on its ROM coordinate.
            center_x = wx + (width - 1) * TILE_WORLD * 0.5
            center_z = wz + (height - 1) * TILE_WORLD * 0.5
            world_y = float(warp.get("z") or 0.0)
            warp["world"] = {
                "x": wx,
                "y": world_y,
                "z": wz,
            }
            warp["rom_anchor_world"] = {"x": wx, "y": world_y, "z": wz}
            warp["display_world_center"] = {"x": center_x, "y": world_y, "z": center_z}
            warp["semantic_entry_world"] = {"x": center_x, "y": world_y, "z": center_z}
            warp["semantic_entry_grid"] = {
                "x": math.floor(center_x / TILE_WORLD),
                "y": 0,
                "z": math.floor(center_z / TILE_WORLD),
            }
            warp["display_footprint"] = {"width": width, "height": height}
            warp["world_position_confidence"] = "runtime_chunk_aligned"
        return static

    async def current_scene(
        self,
        reader: MemoryReader,
        *,
        force_identity: bool = False,
        loaded_visual: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        player = self.player_live()
        zone_id = player.get("zone_id") if isinstance(player.get("zone_id"), int) else None
        # A browser refresh must never start a fresh 4 MiB discovery pass just
        # because the player cache has not been resolved yet.  That work is an
        # explicit reverse-engineering probe, not a normal render-loop task.
        # It previously made ``/scene/current`` wait behind dozens of bridge
        # reads and left the 3D UI on its loading screen indefinitely.
        cached_identity = self._identity_cache
        verified_zone = ((cached_identity.get("zone_identity") or {}).get("value") if cached_identity else None)
        if zone_id is None and isinstance(verified_zone, int):
            zone_id = verified_zone
        if zone_id is None:
            return {
                "format": "black2-world3d-scene/v6",
                "status": "unresolved",
                "player": player,
                "identity": cached_identity,
                "reason": "no runtime ZoneID is available",
            }

        # Full identity validation remains available only to an explicit
        # caller.  Normal UI polling consumes the latest RAM-derived Player
        # cache and any already-completed identity result.
        identity = cached_identity
        # Identity and mapper data are scene-specific.  Keeping an exterior
        # identity cache while the player has entered an interior zone causes
        # ``runtime_matrix_id`` from the previous scene to replace the
        # interior's one-cell matrix, placing its terrain thousands of world
        # units away from the camera.  Treat a zone mismatch as a cache miss.
        if identity is not None:
            identity_zone = (
                ((identity.get("consistency") or {}).get("runtime_zone"))
                or ((identity.get("runtime") or {}).get("zone_id"))
                or ((identity.get("zone_identity") or {}).get("value"))
            )
            if isinstance(identity_zone, int) and identity_zone != zone_id:
                identity = None
                self._identity_cache = None
        if force_identity or identity is None:
            # A successful PlayerRuntime discovery already contains the full
            # mapper chunk table.  Reuse it before considering a new RAM scan.
            discovered = player_runtime_service.locator.last_discovery_result
            if (
                discovered
                and discovered.get("status") in {"resolved", "candidate"}
                and discovered.get("zone_id") == zone_id
            ):
                identity = self.truth.from_runtime(discovered, include_world=False)
                self._identity_cache = identity
                self._identity_time = time.monotonic()
            elif force_identity:
                identity = await self._refresh_identity(reader, force=True)
        span = None
        runtime_mapper = (identity.get("runtime") or {}).get("mapper") if identity else None
        if runtime_mapper:
            span = runtime_mapper.get("chunk_span_world")
        runtime_matrix_id = ((identity.get("matrix_match") or {}).get("selected_matrix_id") if identity else None)
        static = self.static_scene(zone_id, live_span=span, runtime_matrix_id=runtime_matrix_id)
        # Promote the lossless Zone/parent/location fields into the scene
        # payload so the UI and future route planner do not need a second ROM
        # lookup.  Names are registry/operator labels; raw IDs remain present.
        zone_meta = static.get("zone") or {}
        label = ZONE_LABEL_OVERRIDES.get(zone_id)
        static["location"] = {
            "name_zh": (label or {}).get("name_zh") or f"Zone {zone_id}",
            "name_en": (label or {}).get("name_en") or f"Zone {zone_id}",
            "location_name_id": zone_meta.get("location_name_id"),
            "parent_zone_id": zone_meta.get("parent_zone_id"),
            "name_source": "operator_confirmed" if label else "zone_id_fallback",
        }
        static_matrix = (static.get("matrix") or {}).get("matrix_id")
        if loaded_visual is not None:
            self._loaded_visual_cache = {
                "zone_id": zone_id,
                "matrix_id": static_matrix,
                "visual": loaded_visual,
            }
        cached_visual = self._loaded_visual_cache or {}
        matching_visual = (
            cached_visual.get("visual")
            if cached_visual.get("zone_id") == zone_id and cached_visual.get("matrix_id") == static_matrix
            else None
        )
        static = self._bind_loaded_visual(static, matching_visual)
        static = self._attach_warp_world_positions(static, player)
        visual_key = (static.get("visual_binding") or {}).get("cache_key") or "rom-fallback"
        return {
            "format": "black2-world3d-scene/v6",
            "status": "resolved" if player.get("status") in {"resolved", "candidate"} else "candidate",
            "confidence": identity.get("confidence") if identity else player.get("confidence"),
            "scene_key": f"zone:{zone_id}:matrix:{(static.get('matrix') or {}).get('matrix_id')}",
            "render_key": f"zone:{zone_id}:matrix:{static_matrix}:visual:{visual_key}",
            "zone_id": zone_id,
            "environment": static.get("environment"),
            "coordinate_space": "gen5-field-world-v1",
            "scene_origin": scene_origin(player, static),
            "player": player,
            "identity": {
                "confidence": identity.get("confidence") if identity else None,
                "matrix_match": identity.get("matrix_match") if identity else None,
                "zone_identity": identity.get("zone_identity") if identity else None,
                "consistency": identity.get("consistency") if identity else None,
            },
            "static": static,
            "render_contract": {
                "canonical_position": "all ROM terrain/buildings and RAM actors use Gen5 field world units",
                "browser_display_position": "canonical - scene_origin (x/z only)",
                "player_position_source": "FieldActor.WPos",
                "player_grid_source": "FieldActor.GPos",
                "player_facing_source": "FieldActor.FaceDir cross-checked by PlayerState.RotationAngle",
                "zone_transition": "replace StaticWorld; keep RuntimeOverlay contract unchanged",
            },
        }

    async def runtime_actors(self, reader: MemoryReader, *, force: bool = False) -> dict[str, Any]:
        """Bounded actor overlay; never trigger a full Main-RAM identity scan."""
        return await runtime_actor_overlay_service.sample(reader)
