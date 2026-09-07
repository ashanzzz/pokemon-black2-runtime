"""Encounter Region and safe same-Zone patrol API.

The API exposes physical regions now, while explicitly retaining research
status for wild species tables and battle identity.  It never invents species
probabilities or promotes an OVERWORLD interruption to a confirmed battle.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..world.encounter_regions import EncounterRegionService
from ..world.encounter_tasks import EncounterTaskError, EncounterTaskService
from ..world.player_coordinates import canonical_grid_player
from ..world.runtime_player_state import player_runtime_service
from .navigation_routes import (
    navigation_occupancy_snapshot,
    navigation_planner_service,
    navigation_static_provider,
    navigation_task_service,
)


router = APIRouter(prefix="/api/v1/encounters", tags=["encounters-v1"])
_region_service: EncounterRegionService | None = None
_task_service: EncounterTaskService | None = None


def _error(status: int, code: str, message: str, *, details: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details or {}}})


def _player() -> dict[str, Any] | None:
    value = player_runtime_service.latest
    return value if isinstance(value, dict) else None


def _services() -> tuple[EncounterRegionService, EncounterTaskService | None]:
    global _region_service, _task_service
    provider = navigation_static_provider()
    if provider is None:
        raise EncounterTaskError(
            "ENCOUNTER_ROM_UNAVAILABLE",
            "Encounter Region decoding requires the same ROM-backed terrain provider used by navigation.",
            status_code=503,
        )
    if _region_service is None or _region_service.provider is not provider:
        _region_service = EncounterRegionService(provider, _player)
        _task_service = None
    if _task_service is None:
        try:
            navigation = navigation_task_service()
        except Exception:
            navigation = None
        if navigation is not None:
            _task_service = EncounterTaskService(
                _region_service, navigation_planner_service(), navigation, _player,
                occupancy_sample=navigation_occupancy_snapshot,
            )
    return _region_service, _task_service


def _live_zone_y() -> tuple[int, int, dict[str, Any]]:
    sample = _player()
    player = canonical_grid_player(sample, require_resolved=False)
    position = player.get("position") if isinstance(player, dict) and isinstance(player.get("position"), dict) else player
    grid = position.get("grid") if isinstance(position, dict) and isinstance(position.get("grid"), dict) else (player.get("grid") if isinstance(player, dict) else None)
    try:
        return int(player["zone_id"]), int(grid["y"]), sample or {}
    except (KeyError, TypeError, ValueError):
        raise EncounterTaskError("ENCOUNTER_PLAYER_UNRESOLVED", "PlayerRuntime does not contain a resolved Zone/GPos.")


@router.get("/capabilities")
async def encounter_capabilities():
    try:
        regions, tasks = _services()
        payload = regions.capabilities()
        payload["execution"]["available"] = tasks is not None
        return payload
    except EncounterTaskError as exc:
        return _error(exc.status_code, exc.code, exc.message, details=exc.details)


@router.get("/regions")
async def encounter_regions(
    zone_id: int = Query(..., ge=0),
    y: int = Query(0),
    include_water_candidates: bool = True,
):
    try:
        regions, _tasks = _services()
        return regions.zone_regions(zone_id, y, include_water_candidates=include_water_candidates)
    except EncounterTaskError as exc:
        return _error(exc.status_code, exc.code, exc.message, details=exc.details)
    except (IndexError, OSError, RuntimeError, ValueError) as exc:
        return _error(503, "ENCOUNTER_REGION_DECODE_FAILED", str(exc), details={"zone_id": zone_id, "y": y})


@router.get("/regions/current")
async def encounter_regions_current(
    connected: bool = True,
    include_water_candidates: bool = True,
    max_zones: int = Query(24, ge=1, le=64),
):
    try:
        zone_id, y, sample = _live_zone_y()
        regions, _tasks = _services()
        if connected:
            payload = regions.connected_regions(zone_id, y, player_sample=sample, max_zones=max_zones)
            if not include_water_candidates:
                payload["regions"] = [r for r in payload["regions"] if r.get("encounter_method") != "surf_candidate"]
                payload["region_count"] = len(payload["regions"])
            return payload
        return regions.zone_regions(zone_id, y, player_sample=sample, include_water_candidates=include_water_candidates)
    except EncounterTaskError as exc:
        return _error(exc.status_code, exc.code, exc.message, details=exc.details)
    except (IndexError, OSError, RuntimeError, ValueError) as exc:
        return _error(503, "ENCOUNTER_REGION_DECODE_FAILED", str(exc))


@router.get("/regions/{region_id}")
async def encounter_region_detail(region_id: str, zone_id: int, y: int = 0):
    try:
        regions, _tasks = _services()
        region = regions.find_region(region_id, zone_id, y)
        if region is None:
            return _error(404, "ENCOUNTER_REGION_NOT_FOUND", "Encounter Region was not found.")
        return {"format": "black2-encounter-region/v1", "status": "resolved", "region": region}
    except EncounterTaskError as exc:
        return _error(exc.status_code, exc.code, exc.message, details=exc.details)


@router.get("/profiles")
async def encounter_profiles(zone_id: int | None = None):
    return {
        "format": "black2-encounter-profiles/v1",
        "status": "research",
        "zone_id": zone_id,
        "profiles": [],
        "verified": False,
        "reason": "Wild Encounter ROM resource mapping and active subentry selection are not yet verified in this repository.",
    }


@router.get("/search")
async def encounter_search(pokemon_id: int = Query(..., ge=1)):
    return {
        "format": "black2-encounter-search/v1",
        "status": "research",
        "pokemon_id": pokemon_id,
        "candidates": [],
        "verified": False,
        "reason": "Species-to-EncounterProfile search remains unavailable until the Wild Encounter ROM decoder is verified.",
    }


class EncounterTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    region_id: str
    zone_id: int
    y: int = 0
    strategy: Literal["auto", "ping_pong", "loop", "line_shuttle"] = "auto"
    movement_mode: Literal["auto", "walk", "run", "bike", "surf"] = "auto"
    until: Literal["overworld_interrupted"] = "overworld_interrupted"
    max_cycles: int = Field(default=1000, ge=1, le=100000)
    max_steps_per_leg: int = Field(default=2000, ge=1, le=2000)


@router.post("/tasks", status_code=202)
async def create_encounter_task(body: EncounterTaskRequest):
    try:
        _regions, tasks = _services()
        if tasks is None:
            return _error(503, "ENCOUNTER_EXECUTION_UNAVAILABLE", "BizHawk navigation execution is unavailable.")
        task = tasks.start(
            region_id=body.region_id,
            zone_id=body.zone_id,
            y=body.y,
            strategy=body.strategy,
            movement_mode=body.movement_mode,
            max_cycles=body.max_cycles,
            max_steps_per_leg=body.max_steps_per_leg,
        )
        task["status_url"] = f"/api/v1/encounters/tasks/{task['task_id']}"
        task["cancel_url"] = f"/api/v1/encounters/tasks/{task['task_id']}/cancel"
        return task
    except EncounterTaskError as exc:
        return _error(exc.status_code, exc.code, exc.message, details=exc.details)
    except Exception as exc:
        return _error(503, "ENCOUNTER_EXECUTION_UNAVAILABLE", f"{type(exc).__name__}: {exc}")


@router.get("/tasks/{task_id}")
async def encounter_task_status(task_id: str):
    try:
        _regions, tasks = _services()
        if tasks is None:
            return _error(503, "ENCOUNTER_EXECUTION_UNAVAILABLE", "BizHawk navigation execution is unavailable.")
        return tasks.get(task_id)
    except EncounterTaskError as exc:
        return _error(exc.status_code, exc.code, exc.message, details=exc.details)


@router.post("/tasks/{task_id}/cancel")
async def cancel_encounter_task(task_id: str):
    try:
        _regions, tasks = _services()
        if tasks is None:
            return _error(503, "ENCOUNTER_EXECUTION_UNAVAILABLE", "BizHawk navigation execution is unavailable.")
        return await tasks.cancel(task_id)
    except EncounterTaskError as exc:
        return _error(exc.status_code, exc.code, exc.message, details=exc.details)
