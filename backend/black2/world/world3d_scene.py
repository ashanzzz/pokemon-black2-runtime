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
from typing import Any

from ..memory.reader import MemoryReader
from .map_truth_v3 import MapTruthV3
from .original_world import OriginalWorldService
from .exported_world_store import ExportedWorldStore
from .runtime_player_state import player_runtime_service

TILE_WORLD = 16.0
TILE_HALF = 8.0


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


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

    def static_scene(self, zone_id: int, *, live_span: float | None = None) -> dict[str, Any]:
        world = self.exported.zone(zone_id) if self.exported is not None else self.original.zone(zone_id)
        if isinstance(live_span, (int, float)) and live_span > 0:
            world = self.truth._apply_live_chunk_span(world, float(live_span))
        span = float((world.get("render_coordinate_system") or {}).get("chunk_span_world") or 512.0)
        terrains = []
        for cell in world.get("cells", []):
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
                "asset_url": f"/api/v1/map/v5/terrain/{zone_id}/{cell['x']}/{cell['y']}.glb",
            })
        buildings = []
        for item in world.get("buildings", []):
            if item.get("belongs_to_zone") is False:
                continue
            p = item.get("world_position") or item.get("world_position_candidate") or {}
            buildings.append({
                "id": item.get("instance_id"),
                "uid": item.get("model_uid"),
                "world": {"x": _num(p.get("x")), "y": _num(p.get("y")), "z": _num(p.get("z"))},
                "rotation_degrees": item.get("rotation_degrees"),
                "door_uid": (item.get("resource") or {}).get("door_uid"),
                "has_door_metadata": bool((item.get("resource") or {}).get("has_door_metadata")),
                "asset_url": f"/api/v1/map/v5/building/{zone_id}/{item.get('model_uid')}.glb",
            })
        return {
            "format": "black2-world3d-static/v6",
            "zone_id": zone_id,
            "environment": "exterior" if (world.get("area") or {}).get("is_exterior") else "interior",
            "coordinate_space": "gen5-field-world-v1",
            "chunk_span_world": span,
            "zone": world.get("zone"),
            "area": world.get("area"),
            "matrix": world.get("matrix"),
            "terrains": terrains,
            "buildings": buildings,
            "entities": world.get("entities"),
            "source_policy": {
                "terrain": "ROM only / cached",
                "buildings": "ROM only / cached",
                "player": "not embedded in static scene",
            },
        }

    async def current_scene(self, reader: MemoryReader, *, force_identity: bool = False) -> dict[str, Any]:
        player = self.player_live()
        identity = await self._refresh_identity(reader, force=force_identity)
        zone_id = player.get("zone_id") if isinstance(player.get("zone_id"), int) else None
        verified_zone = ((identity.get("zone_identity") or {}).get("value") if identity else None)
        if zone_id is None and isinstance(verified_zone, int):
            zone_id = verified_zone
        if zone_id is None:
            return {
                "format": "black2-world3d-scene/v6",
                "status": "unresolved",
                "player": player,
                "identity": identity,
                "reason": "no runtime ZoneID is available",
            }
        span = None
        runtime_mapper = (identity.get("runtime") or {}).get("mapper") if identity else None
        if runtime_mapper:
            span = runtime_mapper.get("chunk_span_world")
        static = self.static_scene(zone_id, live_span=span)
        return {
            "format": "black2-world3d-scene/v6",
            "status": "resolved" if player.get("status") in {"resolved", "candidate"} else "candidate",
            "confidence": identity.get("confidence") if identity else player.get("confidence"),
            "scene_key": f"zone:{zone_id}:matrix:{(static.get('matrix') or {}).get('matrix_id')}",
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
        identity = await self._refresh_identity(reader, force=force)
        runtime = identity.get("runtime") or {}
        actors = ((runtime.get("actors") or {}).get("actors") or [])
        result = []
        for actor in actors:
            pos = actor.get("world_position") or {}
            if not all(isinstance(pos.get(k), (int, float)) for k in ("x", "y", "z")):
                continue
            result.append({
                "slot": actor.get("slot"),
                "actor_uid": actor.get("actor_uid"),
                "model_id": actor.get("model_id"),
                "zone_id": actor.get("zone_id"),
                "is_player": bool(actor.get("is_player")),
                "world": {"x": pos.get("x"), "y": pos.get("y"), "z": pos.get("z")},
                "grid": actor.get("grid_position"),
                "facing": actor.get("facing"),
                "face_dir_raw": actor.get("face_direction_raw"),
                "movement_flags_raw": actor.get("movement_flags"),
            })
        return {
            "format": "black2-world3d-runtime-actors/v6",
            "status": "resolved" if result else "candidate",
            "refresh_policy": "low-frequency identity/actor overlay; player uses /player/live instead",
            "actors": result,
        }
