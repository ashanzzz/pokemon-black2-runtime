"""Read-only current-perception profiles backed by cached runtime state.

This module intentionally does not share the semantic map-window route.  That
route can enrich a view with connectors and other map-oriented data; a current
perception request must never turn into a broad ROM/catalog query.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query

from ..runtime.hub import RuntimeHub
from ..world.gen5_rom_map import Gen5RomMap
from ..world.semantic_world import SemanticWorldService


router = APIRouter(prefix="/api/v1", tags=["ai-current-view"])

PROFILE_STRICT = "strict"
PROFILE_ASSISTED_LOCAL = "assisted_local"
PROFILE_LOCAL_7X7 = "local_7x7"
PROFILE_GLOBAL_STATIC = "global_static"
LOCAL_WIDTH = 9
LOCAL_HEIGHT = 7
_hub: RuntimeHub | Any | None = None
_static_world: SemanticWorldService | Any | None = None


def configure_current_view_routes(hub: RuntimeHub) -> None:
    """Configure the cache provider without sampling it during a request."""
    global _hub
    _hub = hub


def _snapshot() -> dict[str, Any]:
    """Return the hub's already-cached snapshot; never ask the bridge for data."""
    if _hub is None:
        return {"runtime": {"status": "unavailable"}}
    value = _hub.snapshot()
    return value if isinstance(value, dict) else {"runtime": {"status": "unavailable"}}


def _projection_world() -> SemanticWorldService:
    """Lazily create only the bounded static terrain reader for assisted mode."""
    global _static_world
    if _static_world is None:
        _static_world = SemanticWorldService(Gen5RomMap())
    return _static_world


def _integer(value: Any) -> int | None:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else None


def _player_cache(snapshot: dict[str, Any]) -> dict[str, Any]:
    player = snapshot.get("player") if isinstance(snapshot.get("player"), dict) else {}
    position = player.get("position") if isinstance(player.get("position"), dict) else {}
    grid = position.get("grid") if isinstance(position.get("grid"), dict) else {}
    if not grid and isinstance(player.get("grid"), dict):
        grid = player["grid"]
    player_matrix = _integer(player.get("matrix_id"))
    global_position = player.get("global") if isinstance(player.get("global"), dict) else {}
    global_matrix = _integer(global_position.get("matrix_id"))
    matrix = player_matrix if player_matrix is not None else global_matrix
    matrix_provenance = {
        "status": "unresolved",
        "selected_matrix_id": matrix,
        "sources": {
            "runtime_player.matrix_id": player_matrix,
            "runtime_player.global.matrix_id": global_matrix,
        },
        "reason": "no cached runtime matrix identifier is available",
    }
    if player_matrix is not None and global_matrix is not None and player_matrix != global_matrix:
        matrix = None
        matrix_provenance.update(
            status="conflict",
            selected_matrix_id=None,
            reason="cached runtime matrix identifiers disagree; no precedence is assumed",
        )
    elif matrix is not None:
        matrix_provenance.update(
            status="candidate",
            selected_matrix_id=matrix,
            reason="cached runtime matrix identifier; ROM ownership was not substituted",
        )
    return {
        "status": player.get("status") if isinstance(player.get("status"), str) else "unresolved",
        "source_kind": "runtime_cache",
        "source_frame": _integer(player.get("frame")),
        "runtime_zone_id": _integer(player.get("zone_id")),
        "matrix_id": matrix,
        "matrix_provenance": matrix_provenance,
        "gpos": {key: _integer(grid.get(key)) for key in ("x", "y", "z")},
        "locomotion": player.get("locomotion") if isinstance(player.get("locomotion"), dict) else {},
        "environment": player.get("environment") if isinstance(player.get("environment"), dict) else {},
        "props": player.get("props") if isinstance(player.get("props"), dict) else {},
    }


def _address(matrix_id: int | None, gpos: dict[str, int | None], *, reason: str | None = None) -> dict[str, Any]:
    complete = matrix_id is not None and all(gpos.get(key) is not None for key in ("x", "y", "z"))
    return {
        "space": "gen5-matrix-grid-v1",
        "spatial_key": (
            f"matrix:{matrix_id}:gpos:{gpos['x']}:{gpos['y']}:{gpos['z']}" if complete else None
        ),
        "matrix_id": matrix_id,
        "gpos": {key: gpos.get(key) for key in ("x", "y", "z")},
        "status": "resolved" if complete else "unresolved",
        "reason": None if complete else (reason or "cached PlayerRuntime location is incomplete"),
    }


def _capture_reference(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Expose an existing reference without ever treating it as same-frame proof."""
    cached = snapshot.get("capture")
    if not isinstance(cached, dict):
        cached = snapshot.get("screen_capture")
    if not isinstance(cached, dict):
        runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
        cached = runtime.get("capture")
    if not isinstance(cached, dict):
        return {
            "status": "unavailable",
            "reference": None,
            "frame": None,
            "alignment": "not_frame_verified",
        }
    return {
        "status": "cached",
        "reference": cached.get("reference") or cached.get("image_url") or cached.get("url"),
        "frame": _integer(cached.get("frame")),
        "sha256": cached.get("sha256") if isinstance(cached.get("sha256"), str) else None,
        "alignment": "not_frame_verified",
    }


def _base_response(snapshot: dict[str, Any], profile: str) -> tuple[dict[str, Any], dict[str, Any]]:
    player = _player_cache(snapshot)
    matrix_reason = player["matrix_provenance"]["reason"] if player["matrix_id"] is None else None
    address = _address(player["matrix_id"], player["gpos"], reason=matrix_reason)
    return {
        "format": "black2-ai-current-view/v1",
        "profile": profile,
        "read_policy": "cached_only_no_bridge_capture_or_persistence",
        "observation": {
            "sampled_at": snapshot.get("sampled_at"),
            "age_seconds": snapshot.get("age_seconds"),
            "runtime_status": (snapshot.get("runtime") or {}).get("status"),
            "source_frame": player["source_frame"],
        },
        "address": address,
        "context": {
            "runtime_zone_id": player["runtime_zone_id"],
            "rom_owner_zone_id": None,
            "zone_policy": "runtime and ROM owner zones are context only; they are not spatial identity",
        },
        "resources": {
            "npcs": "/api/v1/ai/npcs",
            "trainers": "/api/v1/ai/trainers",
            "navigation_hazards": "/api/v1/navigation/hazards",
            "environment": "/api/v1/game/environment",
        },
        "runtime_player": player,
        "capture": _capture_reference(snapshot),
        "tiles": [],
        "entities": [],
        "coverage": {
            "observed_tiles": 0,
            "static_candidate_tiles": 0,
            "unknown_tiles": 0,
            "screen_visibility": "not_verified",
        },
    }, player


def _static_tile(tile: Any, runtime_zone_id: int, fallback_matrix_id: int) -> dict[str, Any]:
    if not isinstance(tile, dict):
        return {
            "address": _address(fallback_matrix_id, {"x": None, "y": None, "z": None}, reason="static projection returned a malformed tile"),
            "context": {"runtime_zone_id": runtime_zone_id, "rom_owner_zone_id": None},
            "knowledge_state": "unresolved",
            "source_kind": "rom_static",
            "screen_visibility": "not_applicable",
            "observed_frame": None,
            "status": "unresolved",
            "surface_candidates": [],
        }
    coordinate = tile.get("coordinate") if isinstance(tile.get("coordinate"), dict) else {}
    gpos = {key: _integer(coordinate.get(key)) for key in ("x", "y", "z")}
    rom = tile.get("rom") if isinstance(tile.get("rom"), dict) else {}
    matrix_id = _integer(rom.get("matrix_id"))
    matrix_id = matrix_id if matrix_id is not None else fallback_matrix_id
    surfaces = tile.get("surfaces") if isinstance(tile.get("surfaces"), list) else []
    candidates = []
    for surface in surfaces:
        if not isinstance(surface, dict):
            continue
        collision = surface.get("collision") if isinstance(surface.get("collision"), dict) else {}
        candidates.append({
            "layer_index": _integer(surface.get("layer_index")),
            "terrain": surface.get("material") if isinstance(surface.get("material"), dict) else {},
            "walkability": {
                "static_blocked": collision.get("static_blocked"),
                "can_walk": collision.get("can_walk"),
                "status": collision.get("status", "static_candidate"),
            },
            "height": surface.get("height") if isinstance(surface.get("height"), dict) else {"status": "unresolved"},
        })
    return {
        "address": _address(matrix_id, gpos, reason="static projection coordinate is incomplete"),
        "context": {
            "runtime_zone_id": runtime_zone_id,
            "rom_owner_zone_id": _integer(rom.get("owner_zone_id")),
        },
        "knowledge_state": "static_candidate",
        "source_kind": "rom_static",
        "screen_visibility": "not_applicable",
        "observed_frame": None,
        "status": tile.get("status", "unresolved"),
        "surface_candidates": candidates,
    }


def _unknown_static_tile(tile: Any) -> bool:
    """Whether a projected tile lacks usable static terrain evidence."""
    if not isinstance(tile, dict):
        return True
    status = str(tile.get("status") or "unresolved").lower()
    if status in {"unresolved", "unavailable", "error", "failed", "rejected_candidate"}:
        return True
    return not isinstance(tile.get("surfaces"), list) or not bool(tile.get("surfaces"))


def _assisted_local(response: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
    zone_id = player["runtime_zone_id"]
    gpos = player["gpos"]
    if zone_id is None or any(gpos.get(key) is None for key in ("x", "y", "z")):
        response["mode"] = "assisted_local_unresolved"
        response["coverage"]["unknown_tiles"] = LOCAL_WIDTH * LOCAL_HEIGHT
        response["blockers"] = ["cached PlayerRuntime Zone/GPos is unavailable"]
        return response

    # This is deliberately direct tile access rather than /ai/map/window:
    # no connector lookup, runtime overlay, screenshot, or catalog is involved.
    try:
        world = _projection_world()
        raw_tiles = [
            world.tile(zone_id, x, gpos["y"], z, include_raw=False)
            for z in range(gpos["z"] - LOCAL_HEIGHT // 2, gpos["z"] + LOCAL_HEIGHT // 2 + 1)
            for x in range(gpos["x"] - LOCAL_WIDTH // 2, gpos["x"] + LOCAL_WIDTH // 2 + 1)
        ]
    except (AttributeError, FileNotFoundError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        response["mode"] = "assisted_local_unavailable"
        response["coverage"]["unknown_tiles"] = LOCAL_WIDTH * LOCAL_HEIGHT
        response["blockers"] = [f"bounded static projection unavailable: {type(error).__name__}"]
        return response

    center = raw_tiles[(LOCAL_HEIGHT // 2) * LOCAL_WIDTH + LOCAL_WIDTH // 2]
    if not isinstance(center, dict):
        response["mode"] = "assisted_local_unresolved"
        response["coverage"]["unknown_tiles"] = LOCAL_WIDTH * LOCAL_HEIGHT
        response["blockers"] = ["static projection returned a malformed center tile"]
        return response
    center_rom = center.get("rom") if isinstance(center.get("rom"), dict) else {}
    matrix_id = _integer(center_rom.get("matrix_id"))
    if matrix_id is None:
        response["mode"] = "assisted_local_unresolved"
        response["coverage"]["unknown_tiles"] = LOCAL_WIDTH * LOCAL_HEIGHT
        response["blockers"] = ["static projection did not resolve a matrix_id"]
        return response

    response["mode"] = "assisted_local_static_projection"
    response["address"] = _address(matrix_id, gpos)
    response["context"]["rom_owner_zone_id"] = _integer(center_rom.get("owner_zone_id"))
    response["tiles"] = [_static_tile(tile, zone_id, matrix_id) for tile in raw_tiles]
    unknown_tiles = sum(1 for tile in raw_tiles if _unknown_static_tile(tile))
    response["coverage"] = {
        "observed_tiles": 0,
        "static_candidate_tiles": len(response["tiles"]) - unknown_tiles,
        "unknown_tiles": unknown_tiles,
        "screen_visibility": "not_applicable",
        "bounds": {"width": LOCAL_WIDTH, "height": LOCAL_HEIGHT, "max_tiles": LOCAL_WIDTH * LOCAL_HEIGHT},
        "order": "row-major, x increases east, z increases south",
    }
    return response


def _local_window(response: dict[str, Any], player: dict[str, Any], *, width: int, height: int) -> dict[str, Any]:
    """Build a small matrix-aligned static window from the cache location.

    The 7x7 projection is intentionally labelled a candidate: the NDS
    camera/occlusion rectangle is not decoded yet.  It gives an AI 49 stable
    grid cells without pretending that every cell is currently visible on the
    physical dual-screen viewport.
    """
    zone_id = player["runtime_zone_id"]
    gpos = player["gpos"]
    if zone_id is None or any(gpos.get(key) is None for key in ("x", "y", "z")):
        response["mode"] = "local_window_unresolved"
        response["coverage"]["unknown_tiles"] = width * height
        response["blockers"] = ["cached PlayerRuntime Zone/GPos is unavailable"]
        return response
    try:
        world = _projection_world()
        raw_tiles = [
            world.tile(zone_id, tx, gpos["y"], tz, include_raw=False)
            for tz in range(gpos["z"] - height // 2, gpos["z"] + height // 2 + 1)
            for tx in range(gpos["x"] - width // 2, gpos["x"] + width // 2 + 1)
        ]
    except (AttributeError, FileNotFoundError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        response["mode"] = "local_window_unavailable"
        response["coverage"]["unknown_tiles"] = width * height
        response["blockers"] = ["bounded static projection unavailable"]
        return response
    center = raw_tiles[(height // 2) * width + width // 2]
    if not isinstance(center, dict):
        response["mode"] = "local_window_unresolved"
        response["coverage"]["unknown_tiles"] = width * height
        response["blockers"] = ["static projection returned a malformed center tile"]
        return response
    center_rom = center.get("rom") if isinstance(center.get("rom"), dict) else {}
    matrix_id = _integer(center_rom.get("matrix_id"))
    if matrix_id is None:
        response["mode"] = "local_window_unresolved"
        response["coverage"]["unknown_tiles"] = width * height
        response["blockers"] = ["static projection did not resolve a matrix_id"]
        return response
    response["mode"] = "local_static_window_candidate"
    response["address"] = _address(matrix_id, gpos)
    response["context"]["rom_owner_zone_id"] = _integer(center_rom.get("owner_zone_id"))
    response["tiles"] = [_static_tile(tile, zone_id, matrix_id) for tile in raw_tiles]
    unknown_tiles = sum(1 for tile in raw_tiles if _unknown_static_tile(tile))
    response["coverage"] = {
        "observed_tiles": 0,
        "static_candidate_tiles": len(response["tiles"]) - unknown_tiles,
        "unknown_tiles": unknown_tiles,
        "screen_visibility": "candidate_centered_not_verified",
        "bounds": {"width": width, "height": height, "max_tiles": width * height},
        "order": "row-major, x increases east, z increases south",
    }
    response["view_policy"] = {
        "is_current_nds_view": False,
        "screen_verified": False,
        "memory_backed": False,
        "reason": "NDS camera bounds and occlusion are not decoded; this is a stable grid memory/projection window.",
    }
    return response


def _global_static(response: dict[str, Any], player: dict[str, Any]) -> dict[str, Any]:
    """Return a ROM-backed Matrix manifest in the same global coordinate space."""
    zone_id = player.get("runtime_zone_id")
    if zone_id is None:
        response["mode"] = "global_static_unresolved"
        response["blockers"] = ["current runtime Zone is unavailable"]
        return response
    try:
        world = _projection_world()
        rom = getattr(world, "rom")
        header = rom.zone(int(zone_id))
        matrix = rom.matrix(int(header.matrix_id))
        matrix_cells = list(matrix.cells())
        owners = sorted({int(cell["zone_id"]) for cell in matrix_cells if cell.get("zone_id") is not None})
        if int(zone_id) not in owners:
            owners.append(int(zone_id))
        owners.sort()
        response["mode"] = "global_matrix_static_manifest"
        response["address"] = {
            "space": "gen5-matrix-grid-v1",
            "matrix_id": int(header.matrix_id),
            "status": "resolved",
            "spatial_key": None,
            "gpos": player.get("gpos"),
        }
        response["global"] = {
            "matrix_id": int(header.matrix_id),
            "dimensions": {"width": int(matrix.width), "height": int(matrix.height)},
            "zone_ids": owners,
            "coordinate_policy": "all same-Matrix Zones share gen5-matrix-grid-v1; Zone is ownership metadata",
            "scene_endpoint": "/api/v1/map/v6/world/cluster/" + str(int(zone_id)),
            "tile_endpoint": "/api/v1/ai/map/tile?zone_id={zone_id}&x={x}&y={y}&z={z}",
        }
        active_cell_count = sum(1 for cell in matrix_cells if cell.get("chunk_id") not in (None, 0xFFFFFFFF))
        candidate_tiles = active_cell_count * 32 * 32
        response["coverage"] = {
            "observed_tiles": 0,
            "static_candidate_tiles": candidate_tiles,
            "unknown_tiles": candidate_tiles,
            "terrain_decoded": False,
            "screen_visibility": "not_applicable_global_static",
            "bounds": {"width": int(matrix.width), "height": int(matrix.height), "active_cells": active_cell_count, "max_tiles": candidate_tiles},
        }
        response["view_policy"] = {
            "is_current_nds_view": False,
            "screen_verified": False,
            "memory_backed": False,
            "reason": "Global mode is a ROM manifest/coordinate index, not a human-visible camera view.",
        }
        return response
    except (AttributeError, FileNotFoundError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
        response["mode"] = "global_static_unavailable"
        response["blockers"] = ["ROM Matrix manifest is unavailable"]
        return response


def build_current_view(snapshot: dict[str, Any], profile: Literal["strict", "assisted_local", "local_7x7", "global_static"]) -> dict[str, Any]:
    """Build a response from supplied cache data; useful for offline contract tests."""
    response, player = _base_response(snapshot, profile)
    if profile == PROFILE_STRICT:
        response["mode"] = "strict_runtime_cache"
        response["coverage"]["unknown_tiles"] = 0
        return response
    if profile == PROFILE_LOCAL_7X7:
        return _local_window(response, player, width=7, height=7)
    if profile == PROFILE_GLOBAL_STATIC:
        return _global_static(response, player)
    return _assisted_local(response, player)


@router.get("/ai/view/current")
def ai_current_view(
    profile: Literal["strict", "assisted_local", "local_7x7", "global_static"] = Query(PROFILE_STRICT),
    capture: Literal["latest"] = Query("latest"),
) -> dict[str, Any]:
    # `capture` is deliberately a selector for an already-cached reference.
    # It never initiates a capture operation.
    del capture
    return build_current_view(_snapshot(), profile)


@router.get("/ai/view/global")
def ai_global_view() -> dict[str, Any]:
    """Explicit alias for the global Matrix coordinate manifest."""
    return build_current_view(_snapshot(), PROFILE_GLOBAL_STATIC)
