"""V5 original-map API + v6 unified 3D scene endpoints.

V5 routes remain backward compatible.  V6 adds a 3D-only scene contract where
static ROM assets are loaded/cached separately from high-frequency PlayerRuntime
updates.  `/api/v1/map/v6/player/live` performs no extra RAM request.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from ..memory.reader import MemoryReader
from ..runtime.versions import WORLD3D_SCENE_VERSION
from ..world.map_truth_v3 import MapTruthV3
from ..world.original_actor_assets import (
    ActorAssetError,
    OriginalActorAssetService,
    PLAYER_OBJCODE_FEMALE,
    PLAYER_OBJCODE_MALE,
)
from ..world.original_map_assets import OriginalMapAssetError, OriginalMapAssetService
from ..world.original_world import OriginalWorldService
from ..world.world3d_scene import World3DSceneService
from ..world.runtime_player_state import player_runtime_service
from ..world.world3d_scene import canonical_player

router = APIRouter(tags=["map-v5-v6"])
_reader: MemoryReader | None = None
_world: OriginalWorldService | None = None
_truth: MapTruthV3 | None = None
_assets: OriginalMapAssetService | None = None
_actors: OriginalActorAssetService | None = None
_scene: World3DSceneService | None = None


def configure_map_v5_routes(reader: MemoryReader) -> None:
    global _reader
    _reader = reader


def _map_reader() -> MemoryReader:
    if _reader is None:
        raise RuntimeError("map routes are not configured")
    return _reader


def _services() -> tuple[OriginalWorldService, MapTruthV3, OriginalMapAssetService, OriginalActorAssetService, World3DSceneService]:
    global _world, _truth, _assets, _actors, _scene
    try:
        if _world is None:
            _world = OriginalWorldService()
        if _truth is None:
            _truth = MapTruthV3()
        if _assets is None:
            _assets = OriginalMapAssetService()
        if _actors is None:
            _actors = OriginalActorAssetService()
        if _scene is None:
            _scene = World3DSceneService(original=_world, truth=_truth)
    except (FileNotFoundError, OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail=f"ROM unavailable: {error}") from error
    return _world, _truth, _assets, _actors, _scene


# ---------------- v5 compatibility ----------------

@router.get("/api/v1/map/v5/status")
async def v5_status() -> dict[str, Any]:
    try:
        world, _truth_svc, assets, actors, _scene_svc = _services()
        return {
            "status": "ready",
            "format": "black2-map-v5+v6",
            "rom": world.rom.static_identity(),
            "assets": assets.cache_status(),
            "actor_assets": actors.cache_status(),
            "resource_policy": {
                "static_rom": "load once/lazy cache: Zone/Area/Matrix/terrain/buildings/entities/textures/actor resources",
                "runtime_ram": "PlayerRuntime cache at high frequency; scene identity/actor set at low frequency",
            },
        }
    except HTTPException as error:
        return {"status": "rom_unavailable", "detail": error.detail}


@router.get("/api/v1/map/v5/catalog")
async def v5_catalog() -> dict[str, Any]:
    world, *_ = _services()
    return world.catalog()


@router.get("/api/v1/map/v5/zone/{zone_id}")
async def v5_zone(zone_id: int) -> dict[str, Any]:
    world, *_ = _services()
    try:
        return world.zone(zone_id)
    except (IndexError, ValueError, OSError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/current")
async def v5_current(include_world: bool = True, reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    _world_svc, truth, *_ = _services()
    try:
        return await truth.current(reader, include_world=include_world)
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/api/v1/map/v5/terrain/{zone_id}/{x}/{y}.glb")
async def v5_terrain_glb(zone_id: int, x: int, y: int):
    _world_svc, _truth_svc, assets, *_ = _services()
    try:
        path, _meta = await assets.terrain_glb(zone_id, x, y)
        return FileResponse(path, media_type="model/gltf-binary", filename=path.name)
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/terrain/{zone_id}/{x}/{y}/model.glb")
async def v6_terrain_glb(zone_id: int, x: int, y: int):
    """Serve a GLB from a directory-shaped URL so relative PNG URIs resolve."""
    return await v5_terrain_glb(zone_id, x, y)


@router.get("/api/v1/map/v5/terrain/{zone_id}/{x}/{y}/{texture_name}")
async def v6_terrain_texture(zone_id: int, x: int, y: int, texture_name: str):
    if Path(texture_name).name != texture_name or not texture_name.lower().endswith(".png"):
        raise HTTPException(status_code=404, detail="invalid terrain texture name")
    _world_svc, _truth_svc, assets, *_ = _services()
    try:
        model_path, _meta = await assets.terrain_glb(zone_id, x, y)
        texture_path = model_path.parent / texture_name
        if not texture_path.is_file():
            raise OriginalMapAssetError(f"terrain texture is missing: {texture_name}")
        return FileResponse(texture_path, media_type="image/png", filename=texture_path.name)
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/terrain/{zone_id}/{x}/{y}.bmd")
async def v5_terrain_raw(zone_id: int, x: int, y: int):
    _world_svc, _truth_svc, assets, *_ = _services()
    try:
        model, _texture, _meta = assets.raw_terrain(zone_id, x, y)
        return Response(content=model, media_type="application/octet-stream")
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/building/{zone_id}/{uid}.glb")
async def v5_building_glb(zone_id: int, uid: int):
    _world_svc, _truth_svc, assets, *_ = _services()
    try:
        path, _meta = await assets.building_glb(zone_id, uid)
        return FileResponse(path, media_type="model/gltf-binary", filename=path.name)
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/building/{zone_id}/{uid}/model.glb")
async def v6_building_glb(zone_id: int, uid: int):
    """Directory-shaped variant used by the 3D scene for relative textures."""
    return await v5_building_glb(zone_id, uid)


@router.get("/api/v1/map/v5/building/{zone_id}/{uid}/{texture_name}")
async def v6_building_texture(zone_id: int, uid: int, texture_name: str):
    if Path(texture_name).name != texture_name or not texture_name.lower().endswith(".png"):
        raise HTTPException(status_code=404, detail="invalid building texture name")
    _world_svc, _truth_svc, assets, *_ = _services()
    try:
        model_path, _meta = await assets.building_glb(zone_id, uid)
        texture_path = model_path.parent / texture_name
        if not texture_path.is_file():
            raise OriginalMapAssetError(f"building texture is missing: {texture_name}")
        return FileResponse(texture_path, media_type="image/png", filename=texture_path.name)
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/building/{zone_id}/{uid}.bmd")
async def v5_building_raw(zone_id: int, uid: int):
    _world_svc, _truth_svc, assets, *_ = _services()
    try:
        model, _texture, _meta = assets.raw_building(zone_id, uid)
        return Response(content=model, media_type="application/octet-stream")
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


# ---------------- v6 3D-only world ----------------

@router.get("/api/v1/map/v6/status")
async def v6_status() -> dict[str, Any]:
    try:
        world, _truth_svc, assets, actors, _scene_svc = _services()
        return {
            "status": "ready",
            "format": "black2-world3d/v6",
            "component": {
                "name": "world3d-scene",
                "version": WORLD3D_SCENE_VERSION,
                "capabilities": {
                    "fast_unresolved_scene": True,
                },
            },
            "ui_policy": "3D only; 2D projection is not a product surface",
            "coordinate_space": "gen5-field-world-v1",
            "rom": world.rom.static_identity(),
            "map_assets": assets.cache_status(),
            "actor_assets": actors.cache_status(),
            "poll_policy": {
                "player_live": "high frequency, cache only, zero additional RAM requests",
                "scene_identity": "low frequency / transition-sensitive",
                "terrain_buildings": "ROM lazy cache; reload only when scene_key changes",
            },
        }
    except HTTPException as error:
        return {"status": "rom_unavailable", "detail": error.detail}


@router.get("/api/v1/map/v6/player/live")
async def v6_player_live() -> dict[str, Any]:
    """High-frequency player transform. Reads no RAM; consumes RuntimeHub cache."""
    # PlayerRuntime is a RAM cache and must remain observable even when static
    # ROM configuration is missing.  Static-world construction is a separate
    # pipeline stage and may fail without obscuring live transport/player state.
    return canonical_player(player_runtime_service.latest)


@router.get("/api/v1/map/v6/scene/current")
async def v6_scene_current(
    force_identity: bool = False,
    refresh_visual: bool = False,
    reader: MemoryReader = Depends(_map_reader),
) -> dict[str, Any]:
    *_prefix, scene = _services()
    try:
        loaded_visual = None
        if refresh_visual:
            # Header discovery is an explicitly requested scene refresh.  It
            # validates the actual BMD0/BTX0 pair resident in ARM9 so the 3D
            # viewport does not silently apply an unrelated area's default
            # terrain texture.  Never enable this on background polling.
            from .map_routes import _native_maps
            loaded_visual = await _native_maps().build_live(reader)
        # Identity discovery reads all of Main RAM and is deliberately opt-in.
        # Bound it here so an explicit probe cannot monopolize the HTTP route.
        if force_identity:
            import asyncio
            return await asyncio.wait_for(
                scene.current_scene(reader, force_identity=True, loaded_visual=loaded_visual), timeout=3.0,
            )
        return await scene.current_scene(reader, force_identity=False, loaded_visual=loaded_visual)
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/api/v1/map/v6/scene/zone/{zone_id}")
async def v6_scene_zone(zone_id: int) -> dict[str, Any]:
    *_prefix, scene = _services()
    try:
        return scene.static_scene(zone_id)
    except (IndexError, ValueError, OSError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v6/actors/live")
async def v6_actors_live(force: bool = False, reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    *_prefix, scene = _services()
    try:
        return await scene.runtime_actors(reader, force=force)
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def _player_objcode(gender: str) -> int:
    return PLAYER_OBJCODE_FEMALE if gender.strip().lower() in {"female", "f", "girl", "1"} else PLAYER_OBJCODE_MALE


@router.get("/api/v1/map/v6/player/asset/meta")
async def v6_player_asset_meta(gender: str = "male") -> dict[str, Any]:
    *_p, actors, _scene_svc = _services()
    try:
        return actors.descriptor(_player_objcode(gender))
    except (ActorAssetError, IndexError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v6/player/asset/model.glb")
async def v6_player_asset_glb(gender: str = "male"):
    *_p, actors, _scene_svc = _services()
    try:
        path, _meta = await actors.actor_glb(_player_objcode(gender))
        return FileResponse(path, media_type="model/gltf-binary", filename=path.name)
    except (ActorAssetError, IndexError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v6/player/asset/sprite/{frame}.png")
async def v6_player_asset_sprite(frame: int, gender: str = "male"):
    *_p, actors, _scene_svc = _services()
    try:
        paths, _meta = await actors.billboard_pngs(_player_objcode(gender))
        if not paths:
            raise ActorAssetError("no original sprite PNG was extracted")
        index = max(0, min(int(frame), len(paths) - 1))
        return FileResponse(paths[index], media_type="image/png", filename=paths[index].name)
    except (ActorAssetError, IndexError, ValueError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/original-map")
async def original_map_page():
    path = Path(__file__).resolve().parents[3] / "frontend" / "original-map.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frontend/original-map.html is missing")
    return FileResponse(path)


# ---------------- v6 3D World Evidence Bundle Endpoints ----------------

V6_EVIDENCE_DIR = Path(__file__).resolve().parents[3] / "reverse_engineering" / "v6_evidence"


@router.post("/api/v1/map/v6/evidence/capture")
async def v6_evidence_capture(label: str = "general"):
    """Atomically run verify_v6_runtime, capture screenshot, bundle JSONs and zip package."""
    import asyncio
    from tools.capture_v6_evidence import capture_v6_evidence
    loop = asyncio.get_running_loop()
    res = await loop.run_in_executor(None, capture_v6_evidence, label)
    return res


@router.get("/api/v1/map/v6/evidence/list")
async def v6_evidence_list():
    """List all saved v6 3D world evidence ZIP packages."""
    if not V6_EVIDENCE_DIR.is_dir():
        return {"count": 0, "bundles": []}
    bundles = []
    for z in sorted(V6_EVIDENCE_DIR.glob("*.zip"), reverse=True):
        stat = z.stat()
        manifest_file = V6_EVIDENCE_DIR / z.stem / "manifest.json"
        manifest = {}
        if manifest_file.is_file():
            try:
                import json
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        bundles.append({
            "zip_name": z.name,
            "size_kb": round(stat.st_size / 1024, 1),
            "created_time": stat.st_mtime,
            "download_url": f"/api/v1/map/v6/evidence/download/{z.name}",
            "label": manifest.get("label", z.stem),
            "scene_key": manifest.get("scene_key"),
            "environment": manifest.get("environment"),
            "zone_id": manifest.get("zone_id"),
            "player_world": manifest.get("player_world"),
            "player_facing": manifest.get("player_facing"),
            "player_asset_mode": manifest.get("player_asset_mode"),
        })
    return {"count": len(bundles), "bundles": bundles}


@router.get("/api/v1/map/v6/evidence/download/{zip_name}")
async def v6_evidence_download(zip_name: str):
    """Download a verified v6 evidence ZIP archive."""
    import re
    if not re.fullmatch(r"evidence_[A-Za-z0-9_\-]+\.zip", zip_name):
        raise HTTPException(status_code=400, detail="Invalid zip filename")
    zip_path = V6_EVIDENCE_DIR / zip_name
    if not zip_path.is_file():
        raise HTTPException(status_code=404, detail="Evidence ZIP not found")
    return FileResponse(zip_path, media_type="application/zip", filename=zip_name)
