"""Unified World Lab diagnostics, layered navigation and calibration API."""
from __future__ import annotations

import math
from pathlib import Path
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..memory.reader import MemoryReader
from ..world.map_truth_v3 import MapTruthV3
from ..world.observed_navigation import NavNode, observed_navigation_graph
from ..world.original_actor_assets import ActorAssetError, OriginalActorAssetService
from ..world.original_world import OriginalWorldService
from ..world.runtime_actor_overlay import runtime_actor_overlay_service
from ..world.spatial_calibration import spatial_calibration_service
from ..world.world3d_scene import World3DSceneService

router = APIRouter(prefix="/api/v1/lab", tags=["world-lab"])
_reader: MemoryReader | None = None
_scene: World3DSceneService | None = None
_actor_assets: OriginalActorAssetService | None = None


def configure_world_lab_routes(reader: MemoryReader) -> None:
    global _reader
    _reader = reader


def _get_reader() -> MemoryReader:
    if _reader is None:
        raise RuntimeError("World Lab routes are not configured")
    return _reader


def _services() -> tuple[World3DSceneService, OriginalActorAssetService]:
    global _scene, _actor_assets
    try:
        if _scene is None:
            original = OriginalWorldService()
            _scene = World3DSceneService(original=original, truth=MapTruthV3())
        if _actor_assets is None:
            _actor_assets = OriginalActorAssetService()
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=f"ROM unavailable: {exc}") from exc
    return _scene, _actor_assets


def _near_buildings(scene: dict[str, Any], limit: int = 20) -> list[dict[str, Any]]:
    player = scene.get("player") or {}
    p = player.get("world") or {}
    rows = []
    for b in ((scene.get("static") or {}).get("buildings") or []):
        q = b.get("world") or {}
        try:
            dx, dy, dz = float(q["x"])-float(p["x"]), float(q["y"])-float(p["y"]), float(q["z"])-float(p["z"])
        except (KeyError, TypeError, ValueError):
            continue
        rows.append({**b, "distance_xz_world": round(math.hypot(dx, dz), 4), "distance_3d_world": round(math.sqrt(dx*dx+dy*dy+dz*dz), 4)})
    rows.sort(key=lambda row: row["distance_xz_world"])
    return rows[:max(1, min(limit, 100))]


class CalibrationStart(BaseModel):
    label: str = "calibration"
    scenario: str = "general"


class CalibrationFinish(BaseModel):
    renderer_diagnostics: dict[str, Any] | None = None
    notes: str | None = None


class LayeredPathRequest(BaseModel):
    goal_x: int
    goal_z: int
    goal_y: int | None = None
    max_snap_radius: int = 2


@router.get("/status")
async def lab_status() -> dict[str, Any]:
    scene, actor_assets = _services()
    return {
        "format": "black2-world-lab/v1",
        "status": "ready",
        "player": scene.player_live(),
        "navigation": observed_navigation_graph.status(),
        "calibration": spatial_calibration_service.status(),
        "actor_assets": actor_assets.cache_status(),
        "performance_policy": {
            "player": "cached PlayerRuntime only",
            "actors": "bounded ActorSystem+heap, opt-in UI",
            "scene": "event-driven; no periodic ARM9 visual scan",
            "legacy_native_map_cache": "off by default",
        },
    }


@router.get("/inspect")
async def lab_inspect(include_actors: bool = False, reader: MemoryReader = Depends(_get_reader)) -> dict[str, Any]:
    scene_service, actor_assets = _services()
    scene = await scene_service.current_scene(reader, force_identity=False)
    actors = await runtime_actor_overlay_service.sample(reader) if include_actors else {"status": "disabled", "actors": []}
    return {
        "format": "black2-world-lab-inspect/v1",
        "scene": scene,
        "nearby_buildings": _near_buildings(scene),
        "actors": actors,
        "navigation": observed_navigation_graph.status(),
        "calibration": spatial_calibration_service.status(),
        "actor_asset_policy": {
            "runtime_model_id_to_obj_code": "candidate until visually cross-validated",
            "registry": actor_assets.cache_status(),
        },
    }


@router.get("/buildings/nearby")
async def nearby_buildings(limit: int = 20, reader: MemoryReader = Depends(_get_reader)) -> dict[str, Any]:
    scene_service, _actors = _services()
    scene = await scene_service.current_scene(reader, force_identity=False)
    return {"zone_id": scene.get("zone_id"), "count": len(_near_buildings(scene, limit)), "buildings": _near_buildings(scene, limit)}


@router.get("/actors/live")
async def lab_actors_live(reader: MemoryReader = Depends(_get_reader)) -> dict[str, Any]:
    return await runtime_actor_overlay_service.sample(reader)


@router.get("/actors/{obj_code}/asset")
async def lab_actor_asset(obj_code: int) -> dict[str, Any]:
    _scene_service, actors = _services()
    try:
        value = actors.descriptor(obj_code)
        value["runtime_mapping_status"] = "candidate"
        return value
    except (ActorAssetError, IndexError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/actors/{obj_code}/model.glb")
async def lab_actor_model(obj_code: int):
    _scene_service, actors = _services()
    try:
        path, _meta = await actors.actor_glb(obj_code)
        return FileResponse(path, media_type="model/gltf-binary", filename=path.name)
    except (ActorAssetError, IndexError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/actors/{obj_code}/sprite/{frame}.png")
async def lab_actor_sprite(obj_code: int, frame: int):
    _scene_service, actors = _services()
    try:
        paths, _meta = await actors.billboard_pngs(obj_code)
        if not paths:
            raise ActorAssetError("no sprite frames extracted")
        index = max(0, min(int(frame), len(paths)-1))
        return FileResponse(paths[index], media_type="image/png", filename=paths[index].name)
    except (ActorAssetError, IndexError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/calibration/start")
async def calibration_start(request: CalibrationStart) -> dict[str, Any]:
    return spatial_calibration_service.start(request.label, request.scenario)


@router.get("/calibration/status")
async def calibration_status() -> dict[str, Any]:
    return spatial_calibration_service.status()


@router.post("/calibration/sample")
async def calibration_sample(reader: MemoryReader = Depends(_get_reader)) -> dict[str, Any]:
    scene_service, _actors = _services()
    player = scene_service.player_live()
    scene = await scene_service.current_scene(reader, force_identity=False)
    result = spatial_calibration_service.sample(player, scene)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.post("/calibration/finish")
async def calibration_finish(request: CalibrationFinish) -> dict[str, Any]:
    result = spatial_calibration_service.finish(renderer_diagnostics=request.renderer_diagnostics, notes=request.notes)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.get("/calibration/reports")
async def calibration_reports() -> dict[str, Any]:
    return spatial_calibration_service.list_reports()


@router.get("/calibration/download/{zip_name}")
async def calibration_download(zip_name: str):
    if not re.fullmatch(r"calibration_[A-Za-z0-9_\-\u4e00-\u9fff]+\.zip", zip_name):
        raise HTTPException(status_code=400, detail="invalid report filename")
    path = spatial_calibration_service.out_dir / zip_name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="report not found")
    return FileResponse(path, media_type="application/zip", filename=zip_name)


@router.get("/navigation/status")
async def navigation_status() -> dict[str, Any]:
    return observed_navigation_graph.status()


@router.post("/navigation/path")
async def layered_path(request: LayeredPathRequest) -> dict[str, Any]:
    scene_service, _actors = _services()
    player = scene_service.player_live()
    start = NavNode.from_player(player)
    if start is None:
        raise HTTPException(status_code=409, detail="player runtime grid/zone unresolved")
    goal = observed_navigation_graph.nearest_known_node(
        start.zone_id, request.goal_x, request.goal_z, request.goal_y, max_radius=max(0, min(request.max_snap_radius, 8))
    )
    if goal is None:
        return {
            "reachable": False,
            "confidence": "unresolved",
            "reason": "goal has not been observed on any nearby elevation layer; record a calibration walk through this area first",
            "start": start.public(),
            "requested_goal": {"x": request.goal_x, "y": request.goal_y, "z": request.goal_z},
            "path": [],
        }
    result = observed_navigation_graph.find_path(start, goal)
    result.update({"start": start.public(), "goal": goal.public(), "requested_goal": {"x": request.goal_x, "y": request.goal_y, "z": request.goal_z}})
    return result
