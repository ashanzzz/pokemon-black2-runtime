"""V5 original-map + runtime-join API.

Static routes read ROM only and are safe to cache.  `/current` reads the shared
runtime Field resolver and joins it to cached ROM facts; it does not repeatedly
scan all ROM archives.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from ..memory.reader import MemoryReader
from ..world.map_truth_v3 import MapTruthV3
from ..world.original_map_assets import OriginalMapAssetError, OriginalMapAssetService
from ..world.original_world import OriginalWorldService


router = APIRouter(tags=["map-v5"])
_reader: MemoryReader | None = None
_world: OriginalWorldService | None = None
_truth: MapTruthV3 | None = None
_assets: OriginalMapAssetService | None = None


def configure_map_v5_routes(reader: MemoryReader) -> None:
    global _reader
    _reader = reader


def _map_reader() -> MemoryReader:
    if _reader is None:
        raise RuntimeError("v5 map routes are not configured")
    return _reader


def _services() -> tuple[OriginalWorldService, MapTruthV3, OriginalMapAssetService]:
    global _world, _truth, _assets
    try:
        if _world is None:
            _world = OriginalWorldService()
        if _truth is None:
            _truth = MapTruthV3()
        if _assets is None:
            _assets = OriginalMapAssetService()
    except (FileNotFoundError, OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail=f"ROM unavailable: {error}") from error
    return _world, _truth, _assets


@router.get("/api/v1/map/v5/status")
async def v5_status() -> dict[str, Any]:
    try:
        world, _truth_svc, assets = _services()
        return {
            "status": "ready",
            "format": "black2-map-v5",
            "rom": world.rom.static_identity(),
            "assets": assets.cache_status(),
            "resource_policy": {
                "static_rom": "load once/lazy cache: Zone/Area/Matrix/terrain/buildings/entities/textures",
                "runtime_ram": "poll only dynamic state: player/actors/current zone/chunk/props/transitions",
            },
        }
    except HTTPException as error:
        return {"status": "rom_unavailable", "detail": error.detail}


@router.get("/api/v1/map/v5/catalog")
async def v5_catalog() -> dict[str, Any]:
    world, _truth_svc, _assets_svc = _services()
    return world.catalog()


@router.get("/api/v1/map/v5/zone/{zone_id}")
async def v5_zone(zone_id: int) -> dict[str, Any]:
    world, _truth_svc, _assets_svc = _services()
    try:
        return world.zone(zone_id)
    except (IndexError, ValueError, OSError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/current")
async def v5_current(
    include_world: bool = True,
    reader: MemoryReader = Depends(_map_reader),
) -> dict[str, Any]:
    _world_svc, truth, _assets_svc = _services()
    try:
        return await truth.current(reader, include_world=include_world)
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@router.get("/api/v1/map/v5/terrain/{zone_id}/{x}/{y}.glb")
async def v5_terrain_glb(zone_id: int, x: int, y: int):
    _world_svc, _truth_svc, assets = _services()
    try:
        path, _meta = await assets.terrain_glb(zone_id, x, y)
        return FileResponse(path, media_type="model/gltf-binary", filename=path.name)
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/terrain/{zone_id}/{x}/{y}.bmd")
async def v5_terrain_raw(zone_id: int, x: int, y: int):
    _world_svc, _truth_svc, assets = _services()
    try:
        model, _texture, _meta = assets.raw_terrain(zone_id, x, y)
        return Response(content=model, media_type="application/octet-stream")
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/building/{zone_id}/{uid}.glb")
async def v5_building_glb(zone_id: int, uid: int):
    _world_svc, _truth_svc, assets = _services()
    try:
        path, _meta = await assets.building_glb(zone_id, uid)
        return FileResponse(path, media_type="model/gltf-binary", filename=path.name)
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/api/v1/map/v5/building/{zone_id}/{uid}.bmd")
async def v5_building_raw(zone_id: int, uid: int):
    _world_svc, _truth_svc, assets = _services()
    try:
        model, _texture, _meta = assets.raw_building(zone_id, uid)
        return Response(content=model, media_type="application/octet-stream")
    except (IndexError, ValueError, OriginalMapAssetError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/original-map")
async def original_map_page():
    path = Path(__file__).resolve().parents[3] / "frontend" / "original-map.html"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="frontend/original-map.html is missing")
    return FileResponse(path)
