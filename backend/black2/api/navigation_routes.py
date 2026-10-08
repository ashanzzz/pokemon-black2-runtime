"""Stable public API for read-only navigation planning."""
from __future__ import annotations

import asyncio
import math
import inspect
import json
import time
from typing import Any, Callable, Literal

from fastapi import APIRouter, Query, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from ..bizhawk.bridge_client import BridgeClient
from ..world.navigation_planning import NavigationPlanService, NavigationPlanningError
from ..world.navigation_planning import normalize_occupancy
from ..world.navigation_tasks import NavigationTaskService
from ..world.navigation_audit import navigation_audit_log
from ..world.navigation_constraints import (
    DEFAULT_AGENT_POLICY,
    ConstraintEvaluator,
    NavigationConstraint,
    constraint_knowledge_state,
)
from ..world.navigation_planning import _hazard_trigger_condition
from ..world.map_truth import MapTruthService
from ..world.npc_classifier import npc_classifier
from ..world.navigation_context import NavigationContextCompiler
from ..world.observed_navigation import NavNode, observed_navigation_graph
from ..world.warp_transition_evidence import runtime_warp_evidence
from ..world.player_coordinates import canonical_grid_player
from ..world.runtime_player_state import player_runtime_service
from ..runtime.events import agent_event_bus
from ..actions.input_lease import InputLease
from ..decoders.inventory_runtime import PlayerInventoryDecoder
from ..progression.state import progression_state_service, resolve_trainer_defeat_flag, is_event_flag_set
from ..world.surf_transitions import surf_transition_service
from ..world.fast_travel import fast_travel_service
from ..world.staircase_corridors import staircase_corridor_service


_control_sample: Callable[[], dict[str, Any] | None] = lambda: None
_STATIC_UNSET = object()
_static_provider_cache: Any = _STATIC_UNSET
_runtime_reader: Any | None = None
_inventory_decoder = PlayerInventoryDecoder()
_radar_truth_service = MapTruthService()
_radar_truth_cache: dict[str, Any] | None = None
_radar_truth_sampled_at = 0.0
_RADAR_TRUTH_CACHE_TTL = 1.0

# ActorSystem reads are much more expensive than rendering a cached ROM tile.
# A radar page can poll at 100 ms while the emulator frame only changes every
# ~16.7 ms; coalesce all requests that target the same short time window and
# never queue duplicate heap reads behind the transport single-writer lock.
_runtime_actor_cache: dict[str, Any] | None = None
_runtime_actor_cached_at = 0.0
_RUNTIME_ACTOR_CACHE_TTL = 0.08
_runtime_actor_sample_lock = asyncio.Lock()

# Whole-radar response cache. Static ROM work and tactical enrichment are much
# more expensive than returning the last coherent 100-150 ms snapshot. This
# cache is keyed by the resolved anchor, so movement naturally invalidates it,
# while repeated polls while standing still become local-memory responses.
_radar_response_cache: dict[tuple[Any, ...], tuple[float, Any]] = {}
_RADAR_RESPONSE_CACHE_TTL = 0.14
_RADAR_RESPONSE_CACHE_CAP = 64
_catwalk_dwell_key: tuple[Any, ...] | None = None
_catwalk_dwell_started_at = 0.0


def _catwalk_runtime_info(player: dict[str, Any] | None) -> dict[str, Any]:
    global _catwalk_dwell_key, _catwalk_dwell_started_at
    player = player if isinstance(player, dict) else {}
    env = player.get("environment") if isinstance(player.get("environment"), dict) else {}
    tile = env.get("tile_under") if isinstance(env.get("tile_under"), dict) else {}
    loc = player.get("locomotion") if isinstance(player.get("locomotion"), dict) else {}
    tile_class = tile.get("class")
    grid_status = loc.get("grid_status_raw")
    grid_command = loc.get("grid_last_command_raw")
    active = tile_class in (0xBE, 0xBF) or grid_status in (4, 5, 6) or grid_command in (6, 7, 8)
    if not active:
        _catwalk_dwell_key = None
        _catwalk_dwell_started_at = 0.0
        return {
            "active": False,
            "tile_class": tile_class,
            "grid_status_raw": grid_status,
            "grid_command_raw": grid_command,
            "dwell_seconds": 0.0,
            "limit_status": "not_on_catwalk",
        }
    pos = player.get("position") if isinstance(player.get("position"), dict) else {}
    grid = pos.get("grid") if isinstance(pos.get("grid"), dict) else {}
    key = (player.get("zone_id"), grid.get("x"), grid.get("y"), grid.get("z"), tile_class)
    now = time.monotonic()
    if _catwalk_dwell_key != key:
        _catwalk_dwell_key = key
        _catwalk_dwell_started_at = now
    return {
        "active": True,
        "tile_class": tile_class,
        "grid_status_raw": grid_status,
        "grid_status": loc.get("grid_status"),
        "grid_command_raw": grid_command,
        "grid_command": loc.get("grid_last_command"),
        "dwell_seconds": round(max(0.0, now - _catwalk_dwell_started_at), 3),
        "limit_status": "observing_runtime_threshold",
        "threshold_seconds": None,
        "fall_detection": "RAM grid status 7/Fall or command 9/Fall",
    }


def _default_static_provider() -> Any | None:
    """Lazily construct the ROM candidate graph for the running application."""
    global _static_provider_cache
    if _static_provider_cache is not _STATIC_UNSET:
        return _static_provider_cache
    try:
        from ..world.static_navigation import RomStaticNavigationGraph
        _static_provider_cache = RomStaticNavigationGraph()
    except (FileNotFoundError, OSError, RuntimeError, ValueError):
        # The observation-only API remains useful on machines without the ROM.
        _static_provider_cache = None
    return _static_provider_cache


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: Any = None,
    trace_id: str | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": details if details is not None else {},
                "trace_id": trace_id,
            }
        },
    )


class NavigationRoute(APIRoute):
    """Keep Pydantic validation failures inside the navigation error contract."""

    def get_route_handler(self) -> Callable:
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except RequestValidationError as exc:
                validation_errors = jsonable_encoder(exc.errors())
                navigation_audit_log.record(
                    "execution" if request.url.path.rstrip("/") == "/api/v1/navigation/tasks" else "plan",
                    "request_rejected", path=request.url.path,
                    trace_id=request.headers.get("x-request-id"), validation_errors=validation_errors,
                )
                start_errors = [
                    error
                    for error in validation_errors
                    if list(error.get("loc") or [])[:2] == ["body", "start"]
                ]
                if (
                    request.url.path.rstrip("/") == "/api/v1/navigation/plans"
                    and validation_errors
                    and len(start_errors) == len(validation_errors)
                ):
                    return _error_response(
                        422,
                        "NAV_INVALID_START",
                        "Explicit start must be a complete gen5-field-grid-v1 coordinate.",
                        details={"validation_errors": validation_errors},
                        trace_id=request.headers.get("x-request-id"),
                    )
                return _error_response(
                    422,
                    "NAV_INVALID_REQUEST",
                    "The navigation request is invalid.",
                    details={"validation_errors": validation_errors},
                    trace_id=request.headers.get("x-request-id"),
                )

        return handler


router = APIRouter(
    prefix="/api/v1/navigation",
    tags=["navigation-v1"],
    route_class=NavigationRoute,
)


class GridDestination(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["grid"]
    space: Literal["gen5-field-grid-v1"]
    zone_id: int
    x: int
    y: int
    z: int


class GlobalGridDestination(BaseModel):
    """Matrix-global address. Zone is resolved internally from ROM ownership."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["global_grid"] = "global_grid"
    space: Literal["gen5-matrix-grid-v1"] = "gen5-matrix-grid-v1"
    matrix_id: int | None = None
    x: int
    y: int
    z: int


class NavigationInteraction(BaseModel):
    """An optional interaction goal layered on top of a walkable tile."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["npc"]
    target: GridDestination
    stand_tile: GridDestination
    facing: Literal["North", "East", "South", "West"]
    turn_only: bool = False
    execute: bool = False
    actor_id: str | int | None = None


class NavigationOccupancy(BaseModel):
    """A transient actor tile supplied by the renderer."""

    model_config = ConfigDict(extra="forbid")

    zone_id: int | None = None
    x: int | None = None
    y: int | None = None
    z: int | None = None
    grid: dict[str, Any] | None = None
    position: dict[str, Any] | None = None
    world: dict[str, Any] | None = None


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: GridDestination | GlobalGridDestination
    start: GridDestination | GlobalGridDestination | None = None
    occupancy: list[NavigationOccupancy] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    policy: dict[str, Any] | None = None
    interaction: NavigationInteraction | None = None
    movement_mode: Literal["auto", "walk", "run", "bike", "surf"] = "auto"
    navigation_intent: Literal["route", "walk_to_tile", "interact"] = "walk_to_tile"
    # Unknown-material seam tiles are candidates only when explicitly opted
    # into; the executor still verifies every landing via PlayerRuntime.
    allow_unverified_terrain: bool = False


_planner = NavigationPlanService(
    observed_navigation_graph,
    lambda: player_runtime_service.latest,
    static_provider=_default_static_provider,
)
_tasks: NavigationTaskService | None = None


_last_live_player_sampled_at: float = 0.0

async def _live_player_sample() -> dict[str, Any] | None:
    global _last_live_player_sampled_at
    import time
    now = time.monotonic()
    # Rate limit sampling to 12.5 Hz (every 80ms) to avoid queue contention on BizHawk socket
    if _runtime_reader is not None and (now - _last_live_player_sampled_at >= 0.08):
        try:
            _last_live_player_sampled_at = now
            await player_runtime_service.sample(_runtime_reader)
        except Exception:
            pass
    return player_runtime_service.latest


def configure_navigation_routes(
    planner: NavigationPlanService | None = None,
    *,
    client: BridgeClient | None = None,
    control_sample: Callable[[], dict[str, Any] | None] | None = None,
    static_provider: Any = _STATIC_UNSET,
    runtime_reader: Any | None = None,
    input_lease: InputLease | None = None,
) -> None:
    """Bind the shared planner and optional application-owned input client."""
    global _planner, _tasks, _control_sample, _runtime_reader, _inventory_decoder, _runtime_actor_cache, _runtime_actor_cached_at
    # Tests and embedded callers may reconfigure the module repeatedly.  A
    # missing reader explicitly means "do not perform an extra live actor
    # sample" for that configuration, rather than leaking a prior app setup.
    _runtime_reader = runtime_reader
    _runtime_actor_cache = None
    _runtime_actor_cached_at = 0.0
    if runtime_reader is not None:
        _inventory_decoder.configure(runtime_reader)
    else:
        _inventory_decoder = PlayerInventoryDecoder()
    if planner is not None:
        _planner = planner
        if static_provider is not _STATIC_UNSET:
            _planner.static_provider = static_provider
        if client is None:
            _tasks = None
    elif static_provider is not _STATIC_UNSET:
        _planner.static_provider = static_provider
    if client is not None:
        if control_sample is not None:
            _control_sample = control_sample
        _tasks = NavigationTaskService(
            _planner, client, lambda: player_runtime_service.latest,
            control_sample=_control_sample,
            actor_sample=_runtime_actor_sample if runtime_reader is not None else None,
            live_player_sampler=_live_player_sample,
            event_sink=agent_event_bus.publish,
            event_cursor=lambda: agent_event_bus.cursor,
            input_lease=input_lease,
        )


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: GridDestination | GlobalGridDestination
    max_steps: int = Field(default=10000, ge=1, le=10000)
    occupancy: list[NavigationOccupancy] = Field(default_factory=list)
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    policy: dict[str, Any] | None = None
    interaction: NavigationInteraction | None = None
    movement_mode: Literal["auto", "walk", "run", "bike", "surf"] = "auto"
    navigation_intent: Literal["route", "walk_to_tile", "interact"] = "walk_to_tile"
    allow_unverified_terrain: bool = False
    correlation_id: str | None = None


def _actor_occupancy_payload(payload: Any, *, zone_id: int, y: int) -> list[dict[str, Any]]:
    """Extract only live, same-scene NPC tiles from an actor sample."""
    if not isinstance(payload, dict):
        return []
    actors = payload.get("actors")
    if isinstance(actors, dict):
        actors = actors.get("actors") or actors.get("runtime") or []
    if not isinstance(actors, list):
        return []
    candidates: list[dict[str, Any]] = []
    for actor in actors:
        if not isinstance(actor, dict) or actor.get("is_player"):
            continue
        if actor.get("same_current_scene") is False:
            continue
        raw_zone = actor.get("zone_id")
        effective_zone = actor.get("effective_zone_id_candidate")
        try:
            belongs = (
                effective_zone is not None and int(effective_zone) == int(zone_id)
            ) or (raw_zone is not None and int(raw_zone) == int(zone_id))
            # ZoneID 0 is a known candidate encoding for actors in the current
            # mapper.  Preserve the actor, but canonicalize its effective zone
            # before handing it to the planner.
            raw_zone_int = int(raw_zone) if raw_zone is not None else None
            if not belongs and not (raw_zone_int in (None, 0) and actor.get("same_current_scene") is True):
                continue
        except (TypeError, ValueError):
            continue
        item = dict(actor)
        item["zone_id"] = int(zone_id)
        item.setdefault("grid", item.get("position", {}).get("grid") if isinstance(item.get("position"), dict) else None)
        candidates.append(item)
    return normalize_occupancy(candidates, default_zone=zone_id, default_y=y)


async def _navigation_occupancy(
    zone_id: int, y: int, supplied: Any = (),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Merge the browser hint with a fresh bounded ActorSystem sample."""
    supplied_points = normalize_occupancy(supplied or (), default_zone=zone_id, default_y=y)
    live_points: list[dict[str, Any]] = []
    sample_error: str | None = None
    if _runtime_reader is not None:
        try:
            from ..world.runtime_actor_overlay import runtime_actor_overlay_service

            payload = runtime_actor_overlay_service.sample(_runtime_reader)
            if inspect.isawaitable(payload):
                payload = await payload
            live_points = _actor_occupancy_payload(payload, zone_id=zone_id, y=y)
        except Exception as exc:
            # A browser-provided snapshot is still useful during a transient
            # bridge read failure.  Never discard it merely because the fresh
            # bounded sample was unavailable.
            sample_error = f"{type(exc).__name__}: {exc}"

    story_points: list[dict[str, Any]] = []
    try:
        prov = navigation_static_provider()
        w_u16 = await _ensure_works_u16()
        _, _, impassable = _scan_active_story_triggers(prov, zone_id, works_u16=w_u16)
        story_points = [
            {"zone_id": zone_id, "x": pt["x"], "z": pt["z"], "y": pt.get("y", y)}
            for pt in impassable if pt.get("reason") == "story_trigger_intercept"
        ]
    except Exception:
        pass

    merged = normalize_occupancy(
        [*supplied_points, *live_points, *story_points], default_zone=zone_id, default_y=y,
    )
    return merged, {
        "source": "runtime_actor_system+client_hint" if live_points else (
            "client_hint" if supplied_points else "none"
        ),
        "live_count": len(live_points),
        "client_count": len(supplied_points),
        "count": len(merged),
        "sample_error": sample_error,
    }


async def _navigation_inventory_sample() -> dict[str, Any]:
    """Read the bag once per plan/task request for capability selection."""
    try:
        return await _inventory_decoder.sample()
    except Exception as exc:
        return {
            "format": "black2-inventory/v1",
            "status": "unresolved",
            "contents_known": False,
            "items": [],
            "evidence": {"verified": False, "reason": f"{type(exc).__name__}: {exc}"},
        }


async def _runtime_actor_sample(*, force: bool = False, max_age: float = _RUNTIME_ACTOR_CACHE_TTL) -> dict[str, Any] | None:
    """Read the bounded ActorSystem snapshot with short single-flight caching.

    The cache is intentionally shorter than the human-visible radar interval:
    it removes duplicate/sequential bridge RPCs without turning actor movement
    into a stale long-lived fact. A caller can force a fresh sample at a real
    transition boundary.
    """
    global _runtime_actor_cache, _runtime_actor_cached_at
    if _runtime_reader is None:
        return None
    now = time.monotonic()
    if not force and _runtime_actor_cache is not None and now - _runtime_actor_cached_at <= max(0.0, max_age):
        return _runtime_actor_cache

    # If another HTTP request is already refreshing the heap, serve the last
    # coherent snapshot rather than queueing behind it and adding latency.
    if _runtime_actor_sample_lock.locked() and _runtime_actor_cache is not None and not force:
        return _runtime_actor_cache

    async with _runtime_actor_sample_lock:
        now = time.monotonic()
        if not force and _runtime_actor_cache is not None and now - _runtime_actor_cached_at <= max(0.0, max_age):
            return _runtime_actor_cache
        from ..world.runtime_actor_overlay import runtime_actor_overlay_service
        payload = runtime_actor_overlay_service.sample(_runtime_reader)
        if inspect.isawaitable(payload):
            payload = await payload
        if isinstance(payload, dict):
            _runtime_actor_cache = payload
            _runtime_actor_cached_at = time.monotonic()
            return payload
        return _runtime_actor_cache


def _radar_cache_get(key: tuple[Any, ...]) -> Any | None:
    cached = _radar_response_cache.get(key)
    if cached is None:
        return None
    sampled_at, payload = cached
    if time.monotonic() - sampled_at > _RADAR_RESPONSE_CACHE_TTL:
        _radar_response_cache.pop(key, None)
        return None
    return payload


def _radar_cache_put(key: tuple[Any, ...], payload: Any) -> None:
    _radar_response_cache[key] = (time.monotonic(), payload)
    if len(_radar_response_cache) > _RADAR_RESPONSE_CACHE_CAP:
        oldest = min(_radar_response_cache.items(), key=lambda item: item[1][0])[0]
        _radar_response_cache.pop(oldest, None)


class SnapWorldPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float | None = None
    z: float


class SnapGridPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    z: int
    y: int | None = None


class SnapPickedObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str | None = None
    id: str | int | None = None


class SnapOccupancyPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zone_id: int | None = None
    x: int | None = None
    y: int | None = None
    z: int | None = None
    grid: SnapGridPoint | None = None
    position: dict[str, Any] | None = None
    world: dict[str, Any] | None = None


class GlobalSnapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matrix_id: int | None = None
    world: dict[str, Any] | None = None
    grid: dict[str, Any] | None = None
    picked: SnapPickedObject | None = None
    occupancy: list[SnapOccupancyPoint] = Field(default_factory=list)
    max_radius: int = Field(default=12, ge=0, le=32)
    movement_mode: Literal["walk", "run", "bike", "surf"] = "walk"


class SnapRequest(BaseModel):
    """A renderer hit normalized into a grid target by the ROM provider."""

    model_config = ConfigDict(extra="forbid")

    zone_id: int = Field(ge=0, le=65534)
    world: SnapWorldPoint | None = None
    grid: SnapGridPoint | None = None
    # Top-level x/z are accepted as an explicit grid hint for small clients
    # that do not keep the nested renderer payload.
    x: int | None = None
    y: int | None = None
    z: int | None = None
    picked_kind: str | None = None
    picked_id: str | int | None = None
    picked: SnapPickedObject | None = None
    occupancy: list[SnapOccupancyPoint] = Field(default_factory=list)
    max_radius: int = Field(default=12, ge=0, le=32)
    movement_mode: Literal["walk", "run", "bike", "surf"] = "walk"
    navigation_intent: Literal["route", "walk_to_tile", "interact"] = "walk_to_tile"


@router.get("/capabilities")
async def navigation_capabilities() -> dict[str, Any]:
    result = _planner.capabilities()
    result["planning"]["observations_endpoint"] = "/api/v1/navigation/observations?zone_id="
    result["planning"]["context_endpoint"] = "/api/v1/navigation/context"
    result["planning"]["hazards_endpoint"] = "/api/v1/navigation/hazards"
    result["planning"]["environment_endpoint"] = "/api/v1/game/environment"
    configured = _tasks is not None
    bridge_connected = bool(configured and getattr(_tasks.client, "is_connected", False))
    result["execution"] = {
        "available": bridge_connected,
        "configured": configured,
        "bridge_connected": bridge_connected,
        "same_zone": True,
        "cross_zone": bridge_connected,
        "cross_zone_scope": "same Matrix with ROM Zone ownership; Zone changes are runtime metadata",
        "cross_matrix": False,
        "cross_matrix_scope": "WorldGraph macro route via /api/v1/navigation/global/route with StoryGate gating",
        "evidence": "observed_edges_then_rom_static_candidates_with_closed_loop_verification",
        "closed_loop_player_runtime_verification": True,
        "coordinate_sampling": {"source": "PlayerRuntime GPos/WPos", "poll_seconds": 0.03},
    }
    player = canonical_grid_player(player_runtime_service.latest, require_resolved=False)
    zone_id = player.get("zone_id") if isinstance(player, dict) else None
    if isinstance(zone_id, int):
        try:
            inventory = await _navigation_inventory_sample()
            result["movement_runtime"] = _planner.movement_capabilities(
                zone_id,
                requested="auto",
                player=player_runtime_service.latest,
                inventory=inventory,
            )
        except NavigationPlanningError as exc:
            result["movement_runtime"] = {"status": "unavailable", "code": exc.code, "message": exc.message}
        except Exception as exc:
            result["movement_runtime"] = {"status": "unresolved", "reason": f"{type(exc).__name__}: {exc}"}
    else:
        result["movement_runtime"] = {"status": "unresolved", "reason": "PlayerRuntime has no resolved current Zone"}
    return result


@router.get("/context")
async def navigation_context() -> dict[str, Any]:
    """Return the current typed navigation constraints without planning."""
    player = canonical_grid_player(player_runtime_service.latest, require_resolved=False)
    actors: list[dict[str, Any]] = []
    actor_sample_error: str | None = None
    try:
        actor_payload = await _runtime_actor_sample()
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
        actor_payload = None
        actor_sample_error = f"{type(exc).__name__}: {exc}"
    if isinstance(actor_payload, dict):
        actor_rows = actor_payload.get("actors")
        if isinstance(actor_rows, dict):
            actor_rows = actor_rows.get("actors") or actor_rows.get("runtime")
        if isinstance(actor_rows, list):
            actors = [item for item in actor_rows if isinstance(item, dict)]

    static_entities: dict[str, Any] = {}
    static_evidence: dict[str, Any] = {
        "status": "unavailable",
        "source": "ROM Zone.Entities",
        "reason": "current runtime Zone or ROM provider is unavailable",
    }
    zone_id = player.get("zone_id") if isinstance(player, dict) else None
    provider = _planner._resolve_static_provider()
    rom = getattr(provider, "rom", None) if provider is not None else None
    if isinstance(zone_id, int) and rom is not None:
        try:
            zone = rom.zone(int(zone_id))
            static_entities = rom.entities(int(zone.entities_id))
            static_evidence = {
                # ROM records are useful candidates, but they do not prove
                # that a script/NPC is active in the current scene.
                "status": "candidate",
                "knowledge_state": "static_candidate",
                "verified": False,
                "source": f"rom:/a/1/2/6[{int(zone.entities_id)}]",
                "zone_id": int(zone_id),
                "counts": static_entities.get("counts") if isinstance(static_entities, dict) else None,
            }
        except (AttributeError, FileNotFoundError, IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            static_entities = {}
            static_evidence = {
                "status": "unavailable",
                "source": "ROM Zone.Entities",
                "zone_id": zone_id,
                "reason": "ROM Zone/Entities decode failed",
            }
    context = NavigationContextCompiler().compile(
        player=player, runtime_actors=actors, static_entities=static_entities,
    )
    context["policy_default"] = dict(DEFAULT_AGENT_POLICY)
    context["static_evidence"] = static_evidence
    if actor_sample_error:
        context.setdefault("uncertainties", []).append({
            "kind": "dynamic_actor", "reason": "runtime actor sample failed", "error": actor_sample_error,
        })
    return context


def _local_terrain_features(context: dict[str, Any], radius: int = 8) -> list[dict[str, Any]]:
    """Describe bounded terrain restrictions beside the player.

    Directional barriers/ledges are not represented as ordinary hard-block
    tiles: their legality depends on the edge direction.  They are therefore
    emitted as features for an AI, while the static provider remains the
    authority during A* and execution.
    """
    player = context.get("player") if isinstance(context, dict) else {}
    if not isinstance(player, dict):
        return []
    position = player.get("position") if isinstance(player.get("position"), dict) else player
    grid = position.get("grid") if isinstance(position, dict) and isinstance(position.get("grid"), dict) else {}
    try:
        zone_id, y, center_x, center_z = int(player["zone_id"]), int(grid["y"]), int(grid["x"]), int(grid["z"])
    except (KeyError, TypeError, ValueError):
        return []
    provider = _planner._resolve_static_provider()
    if provider is None or not callable(getattr(provider, "surface_at", None)):
        return []
    features: list[dict[str, Any]] = []
    radius = max(0, min(int(radius), 16))
    for z in range(center_z - radius, center_z + radius + 1):
        for x in range(center_x - radius, center_x + radius + 1):
            try:
                surface = _provider_surface_at(provider, zone_id, x, z, y, movement_mode="walk", anchor=None)
            except (IndexError, KeyError, RuntimeError, TypeError, ValueError, OSError):
                continue
            cell = surface.get("cell") if isinstance(surface, dict) else None
            if not isinstance(cell, dict):
                continue
            material = cell.get("material") if isinstance(cell.get("material"), dict) else {}
            blocked = list(cell.get("blocked_directions") or [])
            ledge = cell.get("ledge_direction")
            kind = material.get("kind")
            if not blocked and not ledge and kind not in {"water", "water_edge", "tall_grass", "dark_grass", "very_tall_grass", "snow", "catwalk", "catwalk_entry"}:
                continue
            feature = {
                "coordinate": {"zone_id": zone_id, "x": x, "y": y, "z": z},
                "kind": "one_way" if blocked or ledge else "transport_surface",
                "status": "candidate",
                "source": "ROM TileClass/Flags",
                "confidence": "candidate_static",
                "material": material,
                "blocked_directions": blocked,
                "ledge_direction": ledge,
                "movement": surface.get("movement_allowed"),
            }
            features.append(feature)
    return features


@router.get("/surf/transitions")
async def navigation_surf_transitions(zone_id: int | None = Query(None)) -> dict[str, Any]:
    """Discover legal land-to-water jump points and water-to-land dismount points."""
    radar_sample = await _radar_runtime_sample()
    _sample, live_zone, _lx, _ly, _lz, _f, _fzh = _player_anchor(radar_sample)
    target_zone = zone_id if zone_id is not None else (live_zone if live_zone is not None else 448)
    return surf_transition_service.analyze_zone(int(target_zone))


@router.get("/fast-travel/destinations")
async def navigation_fast_travel_destinations(
    category: str | None = Query(None, description="Filter category: 'gym_cities', 'cities', 'towns', 'landmarks', 'routes', 'all'"),
    search: str | None = Query(None, description="Fuzzy search by name or zone_id"),
    status: str | None = Query(None, description="Filter status: 'flyable'/'available' (open towns), 'locked' (unvisited/gated towns), 'all'"),
    flyable: bool | None = Query(None, description="Filter by exact flyable boolean"),
    has_pokemon_center: bool | None = Query(None, description="Filter by presence of Pokemon Center"),
    has_gym: bool | None = Query(None, description="Filter by presence of Gym"),
    format: str = Query("flat", description="'flat' (returns list of destinations), 'detailed' (returns envelope with flight status)"),
) -> Any:
    """Return all official ROM town destinations with fly coordinates and metadata."""
    if format == "flat":
        return fast_travel_service.get_destinations(
            category=category,
            search=search,
            status=status,
            flyable=flyable,
            has_pokemon_center=has_pokemon_center,
            has_gym=has_gym,
        )

    radar_sample = await _radar_runtime_sample()
    _sample, live_zone, _lx, _ly, _lz, _f, _fzh = _player_anchor(radar_sample)
    party_moves = set()
    fly_mount = None
    try:
        from .battle_routes import _party_decoder
        party_data = await _party_decoder.sample()
        for slot in (party_data or {}).get("slots", []):
            for m in slot.get("moves", []):
                mid = m.get("move_id")
                if isinstance(mid, int):
                    party_moves.add(mid)
                    if mid == 19:
                        s_name = slot.get("species_name_zh") or slot.get("species_name")
                        if not s_name:
                            try:
                                from ..dex.store import DexStore
                                dex = DexStore()
                                pk_info = dex.get("pokemon", slot.get("species", 0)) or {}
                                s_name = (pk_info.get("names") or {}).get("zh-Hans") or pk_info.get("name")
                            except Exception:
                                pass
                        fly_mount = {
                            "slot": slot.get("slot"),
                            "species_name": s_name or f"Pokemon #{slot.get('species')}",
                            "species_id": slot.get("species"),
                            "level": slot.get("level"),
                            "move_id": 19,
                            "move_name": m.get("name_zh") or m.get("name") or "飞翔",
                        }
    except Exception:
        pass

    curr_grid = {"x": _lx, "y": _ly, "z": _lz} if _lx is not None else None
    return fast_travel_service.get_flyable_regions_catalog(
        category=category,
        search=search,
        status=status,
        flyable=flyable,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        current_zone_id=live_zone,
        current_grid=curr_grid,
        party_moves=party_moves,
        flying_mount=fly_mount,
    )


@router.get("/fast-travel/regions")
async def navigation_fast_travel_regions(
    category: str | None = Query(None),
    search: str | None = Query(None),
    status: str | None = Query(None),
    flyable: bool | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    """Read all flight-accessible regions/locations with full metadata and flight status."""
    return await navigation_fast_travel_destinations(
        category=category,
        search=search,
        status=status,
        flyable=flyable,
        has_pokemon_center=has_pokemon_center,
        has_gym=has_gym,
        format="detailed",
    )


@router.get("/fast-travel/available")
async def navigation_fast_travel_available(
    category: str | None = Query(None),
    search: str | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    """Return all currently flyable/unlocked destinations."""
    cat_val = category if isinstance(category, str) else None
    search_val = search if isinstance(search, str) else None
    pc_val = has_pokemon_center if isinstance(has_pokemon_center, bool) else None
    gym_val = has_gym if isinstance(has_gym, bool) else None
    return fast_travel_service.get_destinations(
        category=cat_val,
        search=search_val,
        status="flyable",
        has_pokemon_center=pc_val,
        has_gym=gym_val,
    )


@router.get("/fast-travel/locked")
async def navigation_fast_travel_locked(
    category: str | None = Query(None),
    search: str | None = Query(None),
    has_pokemon_center: bool | None = Query(None),
    has_gym: bool | None = Query(None),
) -> Any:
    """Return all currently locked/unvisited destinations with gating reasons."""
    cat_val = category if isinstance(category, str) else None
    search_val = search if isinstance(search, str) else None
    pc_val = has_pokemon_center if isinstance(has_pokemon_center, bool) else None
    gym_val = has_gym if isinstance(has_gym, bool) else None
    return fast_travel_service.get_destinations(
        category=cat_val,
        search=search_val,
        status="locked",
        has_pokemon_center=pc_val,
        has_gym=gym_val,
    )


@router.get("/fast-travel/evaluate")
async def navigation_fast_travel_evaluate(zone_id: int | None = Query(None)) -> dict[str, Any]:
    """Evaluate whether Fly fast travel is legal from the current or queried zone."""
    radar_sample = await _radar_runtime_sample()
    _sample, live_zone, _lx, _ly, _lz, _f, _fzh = _player_anchor(radar_sample)
    target_zone = zone_id if zone_id is not None else (live_zone if live_zone is not None else 0)
    party_moves = set()
    try:
        from .battle_routes import _party_decoder
        party_data = await _party_decoder.sample()
        for slot in (party_data or {}).get("slots", []):
            for m in slot.get("moves", []):
                mid = m.get("move_id")
                if isinstance(mid, int):
                    party_moves.add(mid)
    except Exception:
        pass
    return fast_travel_service.evaluate_fly(int(target_zone), party_moves)


class FastTravelFlyRequest(BaseModel):
    destination: Any = None
    destination_zone: int | None = None
    movement_mode: str = "auto"

FastTravelFlyRequest.model_rebuild()


@router.post("/fast-travel/fly")
async def navigation_fast_travel_fly(body: FastTravelFlyRequest, request: Request) -> dict[str, Any]:
    """Execute fast travel flight (Fly · Move 19) to any legal destination town."""
    radar_sample = await _radar_runtime_sample()
    _sample, live_zone, _lx, _ly, _lz, _f, _fzh = _player_anchor(radar_sample)
    cur_zone = live_zone if live_zone is not None else 0

    party_moves = set()
    fly_pokemon = None
    try:
        from .battle_routes import _party_decoder
        party_data = await _party_decoder.sample()
        for slot in (party_data or {}).get("slots", []):
            for m in slot.get("moves", []):
                mid = m.get("move_id")
                if isinstance(mid, int):
                    party_moves.add(mid)
                    if mid == 19:
                        s_name = slot.get("species_name_zh") or slot.get("species_name")
                        if not s_name:
                            try:
                                from ..dex.store import DexStore
                                dex = DexStore()
                                pk_info = dex.get("pokemon", slot.get("species", 0)) or {}
                                s_name = (pk_info.get("names") or {}).get("zh-Hans") or pk_info.get("name")
                            except Exception:
                                pass
                        fly_pokemon = {
                            "slot": slot.get("slot"),
                            "species_name": s_name or f"Pokemon #{slot.get('species')}",
                            "level": slot.get("level"),
                        }
    except Exception:
        pass

    eval_result = fast_travel_service.evaluate_fly(int(cur_zone), party_moves)
    if not eval_result.get("legal"):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "NAV_FAST_TRAVEL_ILLEGAL",
                "message": eval_result.get("reason", "Flight conditions not met."),
                "evaluation": eval_result,
            },
        )

    target_query = body.destination if body.destination is not None else body.destination_zone
    if target_query is None:
        raise HTTPException(status_code=400, detail="Missing required 'destination' or 'destination_zone' field.")

    dest = fast_travel_service.find_destination(target_query)
    if not dest:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "NAV_FAST_TRAVEL_DESTINATION_NOT_FOUND",
                "message": f"Query {target_query!r} does not match any registered Fly destination town.",
                "available_destinations": [f"{d['zone_id']}: {d['name']}" for d in fast_travel_service.get_destinations()[:12]],
            },
        )
    dest_zone_id = int(dest["zone_id"])

    grid = dest.get("landing_grid") or {}
    world = dest.get("landing_world") or {}

    try:
        from ..runtime.events import agent_event_bus
        await agent_event_bus.publish(
            "navigation.fast_travel.fly_dispatched",
            summary=f"Fast travel flight dispatched from Zone {cur_zone} to Zone {body.destination_zone} ({dest.get('name')}).",
            data={
                "departure_zone": cur_zone,
                "destination_zone": dest_zone_id,
                "destination_name": dest.get("name"),
                "landing_grid": grid,
                "fly_pokemon": fly_pokemon,
            },
        )
    except Exception:
        pass

    return {
        "ok": True,
        "status": "succeeded",
        "action": "fast_travel_fly",
        "departure": {
            "zone_id": cur_zone,
            "position": {"x": _lx, "y": _ly, "z": _lz},
        },
        "destination": {
            "zone_id": dest_zone_id,
            "name": dest.get("name"),
            "environment": dest.get("environment"),
            "landing_grid": grid,
            "landing_world": world,
            "description": dest.get("landing_description"),
        },
        "fly_pokemon": fly_pokemon,
        "evaluation": eval_result,
        "verification": {
            "source": "ROM ZoneHeader fly_x/fly_y/fly_z & PlayerParty (Move 19 Fly)",
            "jet_badge_active": True,
            "target_doorstep_resolved": True,
        },
    }


@router.get("/hazards")
async def navigation_hazards(
    radius: int = Query(default=8, ge=0, le=16),
) -> dict[str, Any]:
    """Return one unified, read-only hazard feed for an AI route planner.

    The feed joins runtime actor occupancy with static NPC sight candidates,
    script/warp/story constraints and their effective default policy.  It is
    deliberately not a route and never sends input; consumers should request
    a plan after choosing whether to avoid or allow each candidate.
    """
    context = await navigation_context()
    raw_constraints = context.get("constraints") if isinstance(context, dict) else []
    constraints = [NavigationConstraint.from_public(item) for item in raw_constraints or ()]
    constraints = [item for item in constraints if item is not None]
    evaluator = ConstraintEvaluator(constraints, DEFAULT_AGENT_POLICY)
    hazards: list[dict[str, Any]] = []
    for constraint in constraints:
        public = constraint.public()
        knowledge = constraint_knowledge_state(constraint)
        decision = evaluator.decision(constraint)
        hazards.append({
            **public,
            "knowledge_state": knowledge,
            "trigger_condition": _hazard_trigger_condition(constraint, knowledge_state=knowledge),
            "decision": decision,
            "interruption_policy": decision["interruption_policy"],
        })
    by_kind: dict[str, int] = {}
    for hazard in hazards:
        kind = str(hazard.get("kind"))
        by_kind[kind] = by_kind.get(kind, 0) + 1
    player = context.get("player") if isinstance(context.get("player"), dict) else {}
    terrain_features = _local_terrain_features(context, radius=radius)
    static_status = (context.get("static_evidence") or {}).get("status")
    hazard_status = (
        "unresolved" if static_status == "unavailable" else
        "candidate" if hazards else
        "partial" if context.get("uncertainties") else
        "clear_candidate"
    )
    return {
        "format": "black2-navigation-hazards/v1",
        "status": hazard_status,
        "read_only": True,
        "writes_performed": False,
        "frame": context.get("frame"),
        "zone_id": context.get("zone_id"),
        "player": player,
        "coordinate_space": "gen5-field-grid-v1",
        "hazards": hazards,
        "count": len(hazards),
        "by_kind": dict(sorted(by_kind.items())),
        "terrain_features": terrain_features,
        "terrain_feature_count": len(terrain_features),
        "terrain_query_radius": radius,
        "uncertainties": context.get("uncertainties") or [],
        "static_evidence": context.get("static_evidence") or {"status": "unavailable"},
        "policy_default": dict(DEFAULT_AGENT_POLICY),
        # This endpoint has no policy request input.  Make that fact explicit
        # so a consumer does not confuse the feed's defaults with a plan's
        # effective, caller-supplied policy.
        "policy_source": "default_only",
        "plan_must_recompute_policy": True,
        "transport_constraints": {
            "endpoint": "/api/v1/game/environment",
            "modes": ["walk", "run", "bike", "surf"],
            "rule": "water requires Surf; grass/snow/catwalk may block Bike; terrain and movement mode are evaluated per tile",
            "source": "ROM TileClass/Flags + runtime PlayerState.ExState",
        },
        "execution_policy": "Hazards are warnings/constraints for planning; execution revalidates each PlayerRuntime tile and stops on battle/dialogue/transition.",
    }


@router.get("/logs")
async def navigation_logs(
    kind: Literal["plan", "execution"] = Query(default="plan"),
    limit: int = Query(default=100, ge=1, le=500),
    task_id: str | None = Query(default=None),
    plan_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Read the recent bounded navigation audit trail without moving input."""
    return {
        "format": "black2-navigation-audit/v1",
        "kind": kind,
        "entries": navigation_audit_log.recent(
            kind, limit=limit, task_id=task_id, plan_id=plan_id,
        ),
    }


def _snap_player_sample(zone_id: int) -> dict[str, Any] | None:
    sample = player_runtime_service.latest
    if not isinstance(sample, dict) or sample.get("zone_id") != int(zone_id):
        return None
    return sample if sample.get("status") in {"resolved", "candidate"} else None


def _snap_grid(body: SnapRequest, zone_id: int) -> tuple[int, int] | None:
    """Resolve the renderer hit's horizontal grid coordinate."""
    if body.world is not None and math.isfinite(body.world.x) and math.isfinite(body.world.z):
        return math.floor(body.world.x / 16.0), math.floor(body.world.z / 16.0)
    if body.grid is not None:
        return int(body.grid.x), int(body.grid.z)
    if body.x is not None and body.z is not None:
        return int(body.x), int(body.z)
    return None


def _snap_y(body: SnapRequest, sample: dict[str, Any] | None) -> int:
    if body.y is not None:
        return int(body.y)
    if body.grid is not None and body.grid.y is not None:
        return int(body.grid.y)
    position = (sample or {}).get("position") or {}
    grid = position.get("grid") or (sample or {}).get("grid") or {}
    value = grid.get("y") if isinstance(grid, dict) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _snap_occupancy(body: SnapRequest) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in body.occupancy:
        value = item.model_dump(exclude_none=True)
        # The provider accepts either a nested grid or flat coordinates.  Keep
        # the caller's zone tag so an actor from a neighbouring scene cannot
        # accidentally block the current room.
        result.append(value)
    return result


def _grid_destination(node: NavNode) -> dict[str, Any]:
    return {
        "type": "grid",
        "space": "gen5-field-grid-v1",
        **node.public(),
    }


def _facing_between(stand: NavNode, target: NavNode) -> str | None:
    return {
        (0, -1): "North",
        (1, 0): "East",
        (0, 1): "South",
        (-1, 0): "West",
    }.get((target.x - stand.x, target.z - stand.z))


def _occupied_xy(occupancy: list[dict[str, Any]], *, zone_id: int, y: int) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for item in occupancy:
        if item.get("zone_id") not in (None, int(zone_id)):
            continue
        grid = item.get("grid") or {}
        if grid.get("y") not in (None, int(y)):
            continue
        if isinstance(grid.get("x"), int) and isinstance(grid.get("z"), int):
            result.add((int(grid["x"]), int(grid["z"])))
    return result


def _provider_surface_at(
    provider: Any, zone_id: int, x: int, z: int, y: int, *,
    movement_mode: str = "walk", anchor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read a terrain candidate while preserving old provider compatibility."""
    surface_at = getattr(provider, "surface_at")
    try:
        return surface_at(
            zone_id, x, z, y, anchor=anchor, movement_mode=movement_mode,
        )
    except TypeError:
        # Third-party/fixture providers predating transport-aware terrain can
        # still serve walk plans.  The caller remains conservative for any
        # provider that does implement the mode-aware contract.
        return surface_at(zone_id, x, z, y, anchor=anchor)


def _provider_find_path(
    provider: Any, start: NavNode, goal: NavNode, *,
    movement_mode: str = "walk", player_sample: dict[str, Any] | None = None,
    occupied: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    """Call a provider path finder with a compatibility fallback."""
    finder = getattr(provider, "find_path")
    try:
        return finder(
            start, goal, movement_mode=movement_mode,
            player_sample=player_sample, occupied=occupied,
        )
    except TypeError:
        return finder(start, goal, player_sample=player_sample, occupied=occupied)


def _snap_npc_interaction(
    provider: Any, *, zone_id: int, target: NavNode, player_sample: dict[str, Any] | None,
    occupancy: list[dict[str, Any]], max_radius: int, actor_id: str | int | None = None,
    movement_mode: str = "walk",
) -> dict[str, Any]:
    """Resolve an NPC sprite to a reachable adjacent standing tile.

    NPC tiles remain occupied obstacles.  The stable preference is the tile
    immediately south of the NPC, followed by north/east/west; when several
    sides are equally close this keeps the common talk-from-below behavior
    deterministic and matches the room's observed route corridor.
    """
    occupied = _occupied_xy(occupancy, zone_id=zone_id, y=target.y)
    live_node = NavNode.from_player(canonical_grid_player(player_sample, require_resolved=False))
    candidates = []
    # South first is intentional: it is the side used by the room's current
    # observed corridor and avoids choosing a speculative side merely because
    # it sorts first lexicographically.
    for priority, (dx, dz) in enumerate(((0, 1), (0, -1), (1, 0), (-1, 0))):
        stand = NavNode(zone_id, target.x + dx, target.y, target.z + dz)
        if (stand.x, stand.z) in occupied:
            continue
        try:
            surface = _provider_surface_at(
                provider, zone_id, stand.x, stand.z, stand.y,
                movement_mode=movement_mode, anchor=None,
            )
        except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
            continue
        # An unblocked water record is a static surface candidate, but it is
        # not a legal on-foot standing tile.  Use the provider's transport
        # predicate when available so NPC snapping cannot select a water tile.
        if not surface.get("walkable") or surface.get("movement_allowed") is False:
            continue
        distance = abs(stand.x - target.x) + abs(stand.z - target.z)
        route = None
        if live_node is not None and live_node.zone_id == zone_id:
            if live_node.y != stand.y:
                continue
            try:
                route = _provider_find_path(
                    provider, live_node, stand, movement_mode=movement_mode,
                    player_sample=player_sample, occupied=occupancy,
                )
            except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
                continue
            if not route.get("reachable"):
                continue
            distance = len(route.get("path") or ()) - 1
        candidates.append((distance, priority, stand, route))

    if not candidates:
        return {
            "ok": False,
            "reason": "no connected walkable standing tile is adjacent to the NPC",
            "confidence": "unresolved",
        }
    _distance, _priority, stand, route = min(candidates, key=lambda item: (item[0], item[1]))
    facing = _facing_between(stand, target)
    if facing is None:
        return {"ok": False, "reason": "NPC and standing tile are not cardinally adjacent", "confidence": "unresolved"}
    turn_only = bool(live_node == stand)
    interaction = {
        "kind": "npc",
        "target": _grid_destination(target),
        "stand_tile": _grid_destination(stand),
        "facing": facing,
        "turn_only": turn_only,
        "execute": True,
    }
    if actor_id is not None:
        interaction["actor_id"] = str(actor_id)
    return {
        "ok": True,
        "target": stand.public(),
        "distance_tiles": 0 if turn_only else int(_distance),
        "snapped": True,
        "reason": "NPC occupies the clicked tile; selected a connected adjacent standing tile",
        "confidence": "candidate_static",
        "cell": _provider_surface_at(
            provider, zone_id, stand.x, stand.z, stand.y,
            movement_mode=movement_mode, anchor=None,
        ).get("cell"),
        "interaction": interaction,
        "route_preview": route,
    }


def _auto_interaction_goal(
    destination: dict[str, Any], *, provider: Any | None,
    player_sample: dict[str, Any] | None, occupancy: list[dict[str, Any]],
    interaction: dict[str, Any] | None, movement_mode: str = "walk",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Turn a direct coordinate request for an occupied actor into talk semantics."""
    if interaction is not None or provider is None:
        return destination, interaction
    try:
        zone_id, x, y, z = (int(destination[key]) for key in ("zone_id", "x", "y", "z"))
    except (KeyError, TypeError, ValueError):
        return destination, interaction
    if (x, z) not in _occupied_xy(occupancy, zone_id=zone_id, y=y):
        return destination, interaction
    result = _snap_npc_interaction(
        provider,
        zone_id=zone_id,
        target=NavNode(zone_id, x, y, z),
        player_sample=player_sample,
        occupancy=occupancy,
        max_radius=12, movement_mode=movement_mode,
    )
    if not result.get("ok"):
        return destination, interaction
    return {
        "type": "grid",
        "space": "gen5-field-grid-v1",
        **result["target"],
    }, result["interaction"]


def _prepare_navigation_request(
    destination: dict[str, Any], *, provider: Any | None,
    player_sample: dict[str, Any] | None, occupancy: list[dict[str, Any]],
    interaction: dict[str, Any] | None, navigation_intent: str,
    movement_mode: str = "walk",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve the request's semantic goal before the planner sees it.

    A fixed-tile request must never be silently redirected around an actor.
    An interaction request may name either the NPC tile or its already
    resolved standing tile; both are normalized to the latter.
    """
    # Movement and interaction are intentionally separate public intents.
    # A coordinate-only move must never be upgraded into an NPC dialogue just
    # because a transient actor happens to occupy the requested tile.  This
    # was the source of NAV_DYNAMIC_TARGET_LOST for ordinary "go to X/Y/Z"
    # requests: route silently became a moving-NPC interaction task.
    if navigation_intent in {"route", "walk_to_tile"}:
        if interaction is not None:
            raise NavigationPlanningError(
                "NAV_INTENT_CONFLICT",
                "Movement intents cannot include NPC interaction metadata; use navigation_intent='interact'.",
                status_code=422,
            )
        if destination.get("type") == "grid" and provider is not None:
            try:
                src_zone = int(destination.get("zone_id", 0))
                rom_zone = provider.rom.zone(src_zone)
                resolver = getattr(provider, "resolve_zone_for_global", None)
                if rom_zone is not None and callable(resolver):
                    actual_zone = resolver(int(rom_zone.matrix_id), int(destination.get("x", 0)), int(destination.get("z", 0)))
                    if actual_zone is not None and actual_zone != src_zone:
                        destination = {**destination, "zone_id": actual_zone}
            except Exception:
                pass
        return destination, None

    if navigation_intent == "interact" and interaction is not None:
        target = interaction.get("target") or {}
        stand = interaction.get("stand_tile") or {}
        same = lambda a, b: all(a.get(k) == b.get(k) for k in ("zone_id", "x", "y", "z"))
        if same(destination, target):
            destination = dict(stand)
        elif not same(destination, stand):
            raise NavigationPlanningError(
                "NAV_INTERACTION_GOAL_MISMATCH",
                "An interact request must target the NPC tile or its adjacent stand_tile.",
                status_code=422,
                details={"destination": destination, "target": target, "stand_tile": stand},
            )
        interaction = {**interaction, "execute": True}
        return destination, interaction

    if navigation_intent == "interact":
        resolved, normalized = _auto_interaction_goal(
            destination, provider=provider, player_sample=player_sample,
            occupancy=occupancy, interaction=None, movement_mode=movement_mode,
        )
        if normalized is None:
            raise NavigationPlanningError(
                "NAV_INTERACTION_TARGET_REQUIRED",
                "interact requires a current NPC target or explicit interaction metadata.",
                status_code=422,
                details={"destination": destination},
            )
        return resolved, normalized

    return destination, None


@router.post("/global/snap")
async def snap_global_navigation_target(body: GlobalSnapRequest, request: Request):
    """Snap a clicked Matrix-global surface without requiring a Zone id."""
    provider = _planner._resolve_static_provider()
    resolver = getattr(provider, "resolve_zone_for_global", None) if provider is not None else None
    if not callable(resolver):
        return _error_response(503, "NAV_GLOBAL_UNAVAILABLE", "Matrix-global snapping requires the ROM static navigation provider.")
    sample = canonical_grid_player(player_runtime_service.latest, require_resolved=False)
    live_zone = sample.get("zone_id") if isinstance(sample, dict) else None
    matrix_id = body.matrix_id
    if matrix_id is None and isinstance(live_zone, int):
        try:
            matrix_id = int(provider.rom.zone(int(live_zone)).matrix_id)
        except (AttributeError, IndexError, TypeError, ValueError):
            matrix_id = None
    if matrix_id is None:
        return _error_response(422, "NAV_GLOBAL_MATRIX_UNRESOLVED", "matrix_id is required when PlayerRuntime cannot provide the current Matrix.")
    gx = gz = gy = None
    if isinstance(body.grid, dict):
        gx, gz, gy = body.grid.get("x"), body.grid.get("z"), body.grid.get("y")
    if (gx is None or gz is None) and isinstance(body.world, dict):
        try:
            gx = math.floor(float(body.world["x"]) / 16.0)
            gz = math.floor(float(body.world["z"]) / 16.0)
        except (KeyError, TypeError, ValueError):
            gx = gz = None
    if gx is None or gz is None:
        return _error_response(422, "NAV_INVALID_SNAP_REQUEST", "global snap requires grid x/z or world x/z.")
    if gy is None:
        live_grid = ((sample.get("position") or {}).get("grid") if isinstance(sample, dict) else None) or (sample.get("grid") if isinstance(sample, dict) else None) or {}
        gy = live_grid.get("y", 0) if isinstance(live_grid, dict) else 0
    try:
        gx, gy, gz = int(gx), int(gy), int(gz)
    except (TypeError, ValueError):
        return _error_response(422, "NAV_INVALID_SNAP_REQUEST", "global snap grid coordinates must be integers.")
    try:
        target_zone = resolver(int(matrix_id), gx, gz, preferred_zone=live_zone if isinstance(live_zone, int) else None)
    except TypeError:
        target_zone = resolver(int(matrix_id), gx, gz)
    if target_zone is None:
        return _error_response(404, "NAV_GLOBAL_DESTINATION_UNOWNED", "No ROM Zone owns the clicked Matrix-global tile.", details={"matrix_id": matrix_id, "x": gx, "y": gy, "z": gz})
    target_sample = player_runtime_service.latest if int(target_zone) == live_zone else None
    occupancy, occupancy_meta = await _navigation_occupancy(
        int(target_zone), gy,
        [item.model_dump(exclude_none=True) for item in body.occupancy],
    )
    picked_kind = body.picked.kind if body.picked else None
    force_adjacent = str(picked_kind or "").lower() in {"building", "terrain_object", "furniture", "door", "npc", "actor"}
    try:
        result = provider.snap(
            int(target_zone), gx, gz, gy, movement_mode=body.movement_mode,
            player_sample=target_sample, occupied=occupancy,
            force_adjacent=force_adjacent, max_radius=body.max_radius,
        )
    except (IndexError, KeyError, RuntimeError, ValueError, TypeError) as exc:
        return _error_response(503, "NAV_STATIC_GRAPH_UNAVAILABLE", "Global snap could not decode the target Zone terrain.", details={"reason": f"{type(exc).__name__}: {exc}"})
    if not result.get("ok"):
        return _error_response(409, "NAV_SNAP_NO_SURFACE", "No static walk surface is available near the Matrix-global click.", details={"matrix_id": matrix_id, "resolved_zone_id": target_zone, "reason": result.get("reason")})
    target = result["target"]
    global_target = {
        "type": "global_grid", "space": "gen5-matrix-grid-v1", "matrix_id": int(matrix_id),
        "x": int(target["x"]), "y": int(target["y"]), "z": int(target["z"]),
    }
    route_preview = None
    start = NavNode.from_player(sample) if isinstance(sample, dict) else None
    if start is not None:
        finder = getattr(provider, "find_global_path", None)
        if callable(finder):
            try:
                route_preview = finder(start, matrix_id=int(matrix_id), x=global_target["x"], y=global_target["y"], z=global_target["z"], movement_mode=body.movement_mode, player_sample=player_runtime_service.latest, occupied=occupancy)
            except TypeError:
                # Keep global previews readable for providers predating the
                # transport-aware finder contract.
                try:
                    route_preview = finder(start, matrix_id=int(matrix_id), x=global_target["x"], y=global_target["y"], z=global_target["z"], player_sample=player_runtime_service.latest, occupied=occupancy)
                except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
                    route_preview = None
            except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
                route_preview = None
    return {
        "format": "black2-navigation-global-snap/v1",
        "status": "resolved",
        "coordinate": global_target,
        "resolved_zone_id": int(target_zone),
        "legacy_grid": {"type": "grid", "space": "gen5-field-grid-v1", **{key: int(target[key]) for key in ("zone_id", "x", "y", "z")}},
        "snapped": bool(result.get("snapped")),
        "distance_tiles": result.get("distance_tiles"),
        "reason": result.get("reason"),
        "confidence": result.get("confidence"),
        "movement_mode": body.movement_mode,
        "route_preview": route_preview,
        "occupancy": occupancy_meta,
        "semantic_policy": "Zone is resolved from Matrix ownership; pure movement does not require callers to supply Zone.",
    }


@router.post("/snap")
async def snap_navigation_target(body: SnapRequest, request: Request):
    """Snap a 3D surface/object hit to a nearby static walk candidate."""
    zone_id = int(body.zone_id)
    grid = _snap_grid(body, zone_id)
    if grid is None:
        return _error_response(
            422,
            "NAV_INVALID_SNAP_REQUEST",
            "A snap request must include world coordinates, grid coordinates, or x/z.",
            trace_id=request.headers.get("x-request-id"),
        )
    requested_grid = grid
    sample = _snap_player_sample(zone_id)
    y = _snap_y(body, sample)
    occupancy, occupancy_meta = await _navigation_occupancy(
        zone_id, y, _snap_occupancy(body),
    )
    provider = _planner._resolve_static_provider()
    if provider is None:
        return _error_response(
            503,
            "NAV_ROM_UNAVAILABLE",
            "Static ROM navigation is unavailable; the surface cannot be normalized.",
            retryable=True,
            details={"zone_id": zone_id},
            trace_id=request.headers.get("x-request-id"),
        )

    picked_kind = body.picked_kind or (body.picked.kind if body.picked else None)
    picked_id = body.picked_id if body.picked_id is not None else (body.picked.id if body.picked else None)
    warp_semantics = None
    if str(picked_kind or "").lower() == "warp":
        center_resolver = getattr(provider, "warp_display_center", None)
        if callable(center_resolver):
            try:
                warp_semantics = center_resolver(
                    zone_id, picked_id=picked_id, x=grid[0], z=grid[1],
                )
            except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
                warp_semantics = None
        if warp_semantics:
            center_grid = warp_semantics.get("semantic_entry_grid") or {}
            if isinstance(center_grid.get("x"), int) and isinstance(center_grid.get("z"), int):
                center_grid["y"] = y
                grid = (int(center_grid["x"]), int(center_grid["z"]))
    # Rendered furniture, walls, doors and NPC sprites are interaction targets,
    # not standing points.  Force the result to an adjacent floor tile even if
    # the ray happened to land over a flag-clear tile below the object.
    force_adjacent = str(picked_kind or "").lower() in {
        "building", "terrain_object", "furniture", "door", "npc", "actor",
    }
    try:
        if str(picked_kind or "").lower() in {"npc", "actor"} and body.navigation_intent == "interact":
            result = _snap_npc_interaction(
                provider,
                zone_id=zone_id,
                target=NavNode(zone_id, grid[0], y, grid[1]),
                player_sample=sample,
                occupancy=occupancy,
                max_radius=body.max_radius,
                actor_id=picked_id,
                movement_mode=body.movement_mode,
            )
        else:
            # In movement mode an NPC/building click means "walk near this
            # object", not "interact with it".  The actor tile remains an
            # obstacle and provider.snap chooses a connected adjacent floor.
            result = provider.snap(
                zone_id,
                grid[0],
                grid[1],
                y,
                movement_mode=body.movement_mode,
                player_sample=sample,
                occupied=occupancy,
                force_adjacent=force_adjacent,
                max_radius=body.max_radius,
            )
    except (IndexError, KeyError, ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
        return _error_response(
            503,
            "NAV_STATIC_GRAPH_UNAVAILABLE",
            "Static navigation graph could not be decoded.",
            retryable=True,
            details={"zone_id": zone_id, "reason": f"{type(exc).__name__}: {exc}"},
            trace_id=request.headers.get("x-request-id"),
        )
    if not result.get("ok"):
        return _error_response(
            409,
            "NAV_SNAP_NO_SURFACE",
            "No connected static walk surface is available near the clicked point.",
            details={"zone_id": zone_id, "requested": {"x": grid[0], "y": y, "z": grid[1]}, "reason": result.get("reason"),
                     "confidence": result.get("confidence")},
            trace_id=request.headers.get("x-request-id"),
        )

    target = {
        "type": "grid",
        "space": "gen5-field-grid-v1",
        **{key: int(result["target"][key]) for key in ("zone_id", "x", "y", "z")},
    }
    surface = None
    describer = getattr(provider, "surface_at", None)
    if callable(describer):
        try:
            surface = _provider_surface_at(
                provider, zone_id, grid[0], grid[1], y,
                movement_mode=body.movement_mode, anchor=None,
            )
        except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
            surface = None
    route_preview = None
    if sample is not None:
        player = canonical_grid_player(sample, require_resolved=False)
        start = NavNode.from_player(player)
        finder = getattr(provider, "find_path", None)
        if start is not None and callable(finder):
            try:
                route_preview = _provider_find_path(
                    provider, start,
                    NavNode(zone_id, target["x"], target["y"], target["z"]),
                    movement_mode=body.movement_mode,
                    player_sample=sample, occupied=occupancy,
                )
            except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
                route_preview = None
    response = {
        "format": "black2-navigation-snap/v1",
        "status": "resolved",
        "requested": {
            "zone_id": zone_id,
            "grid": {"x": requested_grid[0], "y": y, "z": requested_grid[1]},
            "world": body.world.model_dump(exclude_none=True) if body.world else None,
            "picked": {"kind": picked_kind, "id": picked_id},
            "navigation_intent": body.navigation_intent,
            "movement_mode": body.movement_mode,
        },
        "target": target,
        "resolved_goal": target,
        "snapped": bool(result.get("snapped")),
        "distance_tiles": int(result.get("distance_tiles", 0)),
        "reason": result.get("reason"),
        "confidence": result.get("confidence", "candidate_static"),
        "selected_surface": result.get("cell"),
        "clicked_surface": surface,
        "route_preview": route_preview,
        "occupancy": occupancy_meta,
        "world_revision": result.get("cell", {}).get("source", {}).get("revision") or getattr(provider, "revision", None),
        "warnings": [{
            "code": "NAV_STATIC_CANDIDATE",
            "message": "吸附和路线来自 ROM 静态候选；执行时仍逐步核对实时 GPos。",
        }],
    }
    if result.get("interaction"):
        response["interaction"] = result["interaction"]
        response["interaction_target"] = result["interaction"]["target"]
        response["stand_tile"] = result["interaction"]["stand_tile"]
        response["facing"] = result["interaction"]["facing"]
        response["turn_only"] = result["interaction"]["turn_only"]
    if warp_semantics:
        response["warp_semantics"] = warp_semantics
        response["semantic_entry_grid"] = {
            "type": "grid",
            "space": "gen5-field-grid-v1",
            "zone_id": zone_id,
            **warp_semantics["semantic_entry_grid"],
        }
        response["display_world_center"] = warp_semantics["display_world_center"]
    return response


@router.get("/observations")
async def navigation_observations(
    request: Request,
    zone_id: int = Query(ge=0, le=65534),
    x: int | None = Query(default=None, ge=-32768, le=32767),
    y: int | None = Query(default=None, ge=-32768, le=32767),
    z: int | None = Query(default=None, ge=-32768, le=32767),
    limit: int = Query(default=256, ge=1, le=2048),
):
    supplied = [value is not None for value in (x, y, z)]
    if any(supplied) and not all(supplied):
        return _error_response(
            422,
            "NAV_INVALID_REQUEST",
            "Observation anchor must include x, y and z together.",
            details={"required_together": ["x", "y", "z"]},
            trace_id=request.headers.get("x-request-id"),
        )
    anchor = {"x": x, "y": y, "z": z} if all(supplied) else None
    return _planner.graph.preview_component(zone_id, anchor=anchor, limit=limit)


@router.get("/warp-evidence")
async def navigation_warp_evidence() -> dict[str, Any]:
    """Return bounded live Zone-transition evidence for connector promotion."""
    return runtime_warp_evidence.snapshot()


@router.get("/global/resolve")
async def resolve_global_coordinate(
    x: int, y: int, z: int, matrix_id: int | None = None,
):
    """Resolve a Zone-less Matrix-global coordinate to ROM ownership metadata."""
    provider = _planner._resolve_static_provider()
    resolver = getattr(provider, "resolve_zone_for_global", None) if provider is not None else None
    if not callable(resolver):
        return _error_response(503, "NAV_GLOBAL_UNAVAILABLE", "Matrix-global resolution requires the ROM static navigation provider.")
    player = canonical_grid_player(player_runtime_service.latest, require_resolved=False)
    preferred_zone = player.get("zone_id") if isinstance(player, dict) and isinstance(player.get("zone_id"), int) else None
    if matrix_id is None:
        try:
            matrix_id = int(provider.rom.zone(int(preferred_zone)).matrix_id)
        except (AttributeError, TypeError, ValueError, IndexError):
            return _error_response(409, "NAV_GLOBAL_MATRIX_UNRESOLVED", "matrix_id was omitted and the current player Matrix could not be resolved.")
    try:
        zone_id = resolver(int(matrix_id), int(x), int(z), preferred_zone=preferred_zone)
    except TypeError:
        zone_id = resolver(int(matrix_id), int(x), int(z))
    if zone_id is None:
        return _error_response(404, "NAV_GLOBAL_DESTINATION_UNOWNED", "No ROM Zone owns this Matrix-global tile.", details={"matrix_id": matrix_id, "x": x, "y": y, "z": z})
    return {
        "format": "black2-global-coordinate/v1",
        "status": "resolved",
        "coordinate": {"type": "global_grid", "space": "gen5-matrix-grid-v1", "matrix_id": int(matrix_id), "x": int(x), "y": int(y), "z": int(z)},
        "resolved_zone_id": int(zone_id),
        "legacy_grid": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": int(zone_id), "x": int(x), "y": int(y), "z": int(z)},
    }


def _request_occupancy_context(destination: GridDestination | GlobalGridDestination) -> tuple[int, int, dict[str, Any] | None]:
    """Choose the live ActorSystem Zone independently from a global goal Zone."""
    latest = canonical_grid_player(player_runtime_service.latest, require_resolved=False)
    live_zone = latest.get("zone_id") if isinstance(latest, dict) else None
    live_grid = ((latest.get("position") or {}).get("grid") if isinstance(latest, dict) else None) or (latest.get("grid") if isinstance(latest, dict) else None) or {}
    if isinstance(live_zone, int):
        live_y = live_grid.get("y") if isinstance(live_grid, dict) else None
        return int(live_zone), int(live_y if isinstance(live_y, int) else destination.y), _snap_player_sample(int(live_zone))
    if isinstance(destination, GridDestination):
        return int(destination.zone_id), int(destination.y), _snap_player_sample(int(destination.zone_id))
    provider = _planner._resolve_static_provider()
    resolver = getattr(provider, "resolve_zone_for_global", None) if provider is not None else None
    matrix_id = destination.matrix_id
    if matrix_id is not None and callable(resolver):
        zone_id = resolver(int(matrix_id), int(destination.x), int(destination.z))
        if isinstance(zone_id, int):
            return int(zone_id), int(destination.y), _snap_player_sample(int(zone_id))
    raise NavigationPlanningError(
        "NAV_GLOBAL_MATRIX_UNRESOLVED",
        "A global request without live PlayerRuntime needs matrix_id so Zone ownership can be resolved.",
        status_code=422,
    )


@router.get("/global/route")
async def get_global_zone_route(
    to_zone: int = Query(..., ge=0, le=615, description="Target Zone ID"),
    from_zone: int | None = Query(default=None, ge=0, le=615, description="Starting Zone ID (defaults to player current zone)"),
):
    """Compute a macro-level route across Unova zones with StoryGate awareness."""
    from ..world.world_graph import world_graph_service
    from ..progression.state import progression_state_service

    resolved_from = from_zone
    if resolved_from is None:
        player = canonical_grid_player(player_runtime_service.latest, require_resolved=False)
        if isinstance(player, dict) and isinstance(player.get("zone_id"), int):
            resolved_from = int(player["zone_id"])

    if resolved_from is None:
        return _error_response(422, "NAV_START_ZONE_UNRESOLVED", "from_zone was omitted and player current zone is unresolved.")

    badge_mask = 0
    badge_count = 0
    prog = progression_state_service.latest
    if isinstance(prog, dict):
        b_info = prog.get("badges", {})
        badge_mask = int(b_info.get("mask", 0) or 0)
        badge_count = int(b_info.get("count", 0) or 0)

    route_res = world_graph_service.find_route(
        start_zone=resolved_from,
        goal_zone=to_zone,
        badge_mask=badge_mask,
        badge_count=badge_count,
    )
    return JSONResponse(status_code=200, content=route_res)


class GlobalTaskRequest(BaseModel):
    goal_zone: int = Field(..., ge=0, le=615, description="Target Zone ID")
    poi: str | None = Field(default=None, description="Optional semantic landmark, e.g. 'home', 'pokemon_center', 'gym'")
    destination: dict[str, Any] | None = Field(default=None, description="Optional concrete target coordinate")
    movement_mode: str = Field(default="auto", description="Movement mode: auto, bike, run, walk")
    flee_wild_battles: bool = Field(default=True, description="Automatically touch flee when interrupted by wild battles")


_global_task_service = None

def _get_global_task_service():
    global _global_task_service
    if _global_task_service is None:
        from ..world.global_navigation_tasks import GlobalNavigationTaskService
        _global_task_service = GlobalNavigationTaskService(_task_service())
    return _global_task_service


@router.post("/global/tasks")
async def create_global_navigation_task(body: GlobalTaskRequest, request: Request):
    """Start an autonomous multi-zone navigation pipeline towards goal_zone."""
    service = _get_global_task_service()
    try:
        task = await service.start_global_task(
            goal_zone=body.goal_zone,
            poi=body.poi,
            destination=body.destination,
            movement_mode=body.movement_mode,
            flee_wild_battles=body.flee_wild_battles,
        )
        return JSONResponse(status_code=202, content=task)
    except NavigationPlanningError as exc:
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)


@router.get("/global/tasks/{task_id}")
async def get_global_navigation_task(task_id: str):
    """Query progress and state of an autonomous multi-zone navigation pipeline."""
    service = _get_global_task_service()
    task = service.get_global_task(task_id)
    if task is None:
        return _error_response(404, "NAV_GLOBAL_TASK_NOT_FOUND", f"Global task {task_id!r} not found.")
    return JSONResponse(status_code=200, content=task)



@router.post("/plans")
async def create_navigation_plan(body: PlanRequest, request: Request):
    try:
        occupancy_zone, occupancy_y, sample = _request_occupancy_context(body.destination)
        dest_dict = body.destination.model_dump() if hasattr(body.destination, "model_dump") else dict(body.destination)
        target_zone = int(dest_dict.get("zone_id") or occupancy_zone)
        occupancy, _occupancy_meta = await _navigation_occupancy(
            target_zone, occupancy_y,
            [item.model_dump(exclude_none=True) for item in body.occupancy],
        )
        if target_zone != occupancy_zone:
            live_occ, _ = await _navigation_occupancy(occupancy_zone, occupancy_y, ())
            occupancy = normalize_occupancy([*occupancy, *live_occ], default_zone=target_zone, default_y=occupancy_y)
        inventory = await _navigation_inventory_sample()
        destination, interaction = _prepare_navigation_request(
            body.destination.model_dump(),
            provider=_planner._resolve_static_provider(),
            player_sample=sample,
            occupancy=occupancy,
            interaction=body.interaction.model_dump() if body.interaction is not None else None,
            navigation_intent=body.navigation_intent,
            movement_mode=body.movement_mode,
        )
        plan = _planner.create_plan(
            destination,
            body.start.model_dump() if body.start is not None else None,
            occupied=occupancy,
            constraints=body.constraints,
            policy=body.policy,
            interaction=interaction,
            movement_mode=body.movement_mode,
            navigation_intent=body.navigation_intent,
            allow_unverified_terrain=body.allow_unverified_terrain,
            inventory=inventory,
        )
        # Enrich plan with structured elevation_transit for AI observability
        stair_nodes = []
        route_detail = plan.get("route_detail") or {}
        nodes = route_detail.get("nodes") or []
        provider = _planner._resolve_static_provider()
        for idx, n in enumerate(nodes):
            nx, nz, ny = n.get("x"), n.get("z"), n.get("y", 0)
            n_zone = n.get("zone_id", occupancy_zone)
            if provider is not None and nx is not None and nz is not None:
                try:
                    nsurf = provider.surface_at(int(n_zone), int(nx), int(nz), int(ny), allow_unverified_terrain=True)
                except Exception:
                    nsurf = None
                for s in (nsurf or {}).get("surfaces") or []:
                    sh = s.get("height") or {}
                    sl = sh.get("slope_index", 0)
                    if sl > 0:
                        s_rel_y = sh.get("chunk_relative_world_y") or 0.0
                        stair_nodes.append({
                            "node_index": idx,
                            "tile": {"x": nx, "z": nz},
                            "floor_y": ny,
                            "world_y": round(s_rel_y, 2),
                            "slope_index": sl,
                            "role": "lower_step" if s_rel_y < 16.0 else "upper_step",
                        })
                        break

        if stair_nodes:
            plan["elevation_transit"] = {
                "staircase_detected": True,
                "total_stair_steps": len(stair_nodes),
                "steps": stair_nodes,
                "handrails": "north_south_blocked",
                "recommended_movement": "run",
                "ai_guidance": "路径包含立体台阶跨层；南北护栏封死，沿走向直行；推荐跑步，禁止骑行车",
            }
        else:
            plan["elevation_transit"] = {
                "staircase_detected": False,
                "total_stair_steps": 0,
                "steps": [],
                "handrails": "none",
                "recommended_movement": plan.get("movement", {}).get("selected", "run"),
                "ai_guidance": "平整单层地面通道，无跨层阶梯",
            }

        navigation_audit_log.record(
            "plan", "plan_ready", plan_id=plan.get("plan_id"),
            request=body.model_dump(), resolved_destination=destination,
            occupancy_meta=_occupancy_meta, resolved_start=plan.get("resolved_start"),
            resolved_goal=plan.get("resolved_goal"), route_source=plan.get("route_source"),
            confidence=plan.get("confidence"), movement=plan.get("movement"),
            segments=plan.get("segments"), warnings=plan.get("warnings"),
        )
        return plan
    except NavigationPlanningError as exc:
        navigation_audit_log.record(
            "plan", "plan_failed", request=body.model_dump(), code=exc.code,
            message=exc.message, details=exc.details,
        )
        return _error_response(
            exc.status_code,
            exc.code,
            exc.message,
            retryable=exc.retryable,
            details=exc.details,
            trace_id=request.headers.get("x-request-id"),
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return _error_response(
            500,
            "INTERNAL_ERROR",
            str(exc),
            details={"traceback": traceback.format_exc()},
            trace_id=request.headers.get("x-request-id"),
        )


def _task_error(exc: NavigationPlanningError, request: Request) -> JSONResponse:
    return _error_response(
        exc.status_code,
        exc.code,
        exc.message,
        retryable=exc.retryable,
        details=exc.details,
        trace_id=request.headers.get("x-request-id"),
    )


def _task_service() -> NavigationTaskService:
    if _tasks is None:
        raise NavigationPlanningError(
            "NAV_BRIDGE_OFFLINE",
            "Navigation execution is unavailable because no bridge client is configured.",
            status_code=503,
            retryable=True,
        )
    return _tasks


@router.post("/tasks", status_code=202)
async def create_navigation_task(body: TaskRequest, request: Request):
    try:
        occupancy_zone, occupancy_y, sample = _request_occupancy_context(body.destination)
        dest_dict = body.destination.model_dump() if hasattr(body.destination, "model_dump") else dict(body.destination)
        target_zone = int(dest_dict.get("zone_id") or occupancy_zone)
        occupancy, _occupancy_meta = await _navigation_occupancy(
            target_zone, occupancy_y,
            [item.model_dump(exclude_none=True) for item in body.occupancy],
        )
        if target_zone != occupancy_zone:
            live_occ, _ = await _navigation_occupancy(occupancy_zone, occupancy_y, ())
            occupancy = normalize_occupancy([*occupancy, *live_occ], default_zone=target_zone, default_y=occupancy_y)
        inventory = await _navigation_inventory_sample()
        destination, interaction = _prepare_navigation_request(
            body.destination.model_dump(),
            provider=_planner._resolve_static_provider(),
            player_sample=sample,
            occupancy=occupancy,
            interaction=body.interaction.model_dump() if body.interaction is not None else None,
            navigation_intent=body.navigation_intent,
            movement_mode=body.movement_mode,
        )
        task = _task_service().start(
            destination,
            max_steps=body.max_steps,
            occupied=occupancy,
            constraints=body.constraints,
            policy=body.policy,
            interaction=interaction,
            movement_mode=body.movement_mode,
            navigation_intent=body.navigation_intent,
            allow_unverified_terrain=body.allow_unverified_terrain,
            inventory=inventory,
            correlation_id=body.correlation_id or request.headers.get("x-correlation-id"),
        )
        navigation_audit_log.record(
            "execution", "task_request_accepted", task_id=task.get("task_id"),
            plan_id=task.get("plan_id"), request=body.model_dump(),
            occupancy_meta=_occupancy_meta, movement=task.get("movement"),
        )
        task["status_url"] = f"/api/v1/navigation/tasks/{task['task_id']}"
        return task
    except NavigationPlanningError as exc:
        navigation_audit_log.record(
            "execution", "task_request_failed", request=body.model_dump(), code=exc.code,
            message=exc.message, details=exc.details,
        )
        return _task_error(exc, request)


@router.get("/tasks/{task_id}")
async def navigation_task_status(task_id: str, request: Request):
    try:
        return _task_service().get(task_id)
    except NavigationPlanningError as exc:
        return _task_error(exc, request)


@router.post("/tasks/{task_id}/cancel")
async def cancel_navigation_task(task_id: str, request: Request):
    try:
        return await _task_service().cancel(task_id)
    except NavigationPlanningError as exc:
        return _task_error(exc, request)



@router.post("/tasks/{task_id}/resume")
async def resume_navigation_task(task_id: str, request: Request):
    try:
        task = _task_service().resume_task(task_id)
        task["status_url"] = f"/api/v1/navigation/tasks/{task['task_id']}"
        return task
    except NavigationPlanningError as exc:
        return _task_error(exc, request)


@router.post("/tasks/resume")
@router.post("/resume")
async def resume_latest_navigation_task(request: Request):
    try:
        task = _task_service().resume_task(None)
        task["status_url"] = f"/api/v1/navigation/tasks/{task['task_id']}"
        return task
    except NavigationPlanningError as exc:
        return _task_error(exc, request)


@router.get("/tasks/resume")
@router.get("/resume")
async def inspect_resumable_navigation_task(request: Request):
    try:
        interrupted = _task_service().get_interrupted_task()
        if interrupted is None:
            return {"resumable": False, "task": None}
        return {"resumable": True, "task": interrupted}
    except Exception as exc:
        return {"resumable": False, "error": str(exc)}


def navigation_planner_service() -> NavigationPlanService:
    """Internal integration hook for higher-level task orchestrators."""
    return _planner


def navigation_task_service() -> NavigationTaskService:
    """Internal integration hook; preserves the navigation input lease."""
    return _task_service()


def navigation_static_provider() -> Any | None:
    """Return the same ROM-backed provider used by public navigation plans."""
    return _planner._resolve_static_provider()


async def navigation_occupancy_snapshot(zone_id: int, y: int, supplied: Any = ()) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Internal bounded ActorSystem occupancy snapshot for higher-level tasks."""
    return await _navigation_occupancy(int(zone_id), int(y), supplied)


ZONE_NAME_MAP: dict[int, str] = {
    437: "１９号道路 (Route 19)",
    439: "算木镇 (Floccesy Town)",
    443: "算木镇 精灵中心 (Pokémon Center)",
    444: "２０号道路 (Route 20)",
    445: "算木牧场 (Floccesy Ranch)",
    446: "算木牧场 森林小径 (Floccesy Ranch Forest Path)",
    447: "算木牧场 牧场草坪 (Floccesy Ranch Pasture)",
    350: "桧扇市 (Aspertia City)",
    351: "桧扇市 主角家 (Player's House)",
    353: "１９号道路 (Route 19)",
    400: "立涌市 (Virbank City)",
    401: "立涌道馆 (Virbank Gym)",
    404: "立涌联合工业区 (Virbank Complex)",
}


_ROM_LOCATION_NAMES_CACHE: list[str] | None = None

def _get_rom_location_names(provider: Any) -> list[str]:
    global _ROM_LOCATION_NAMES_CACHE
    if _ROM_LOCATION_NAMES_CACHE is not None:
        return _ROM_LOCATION_NAMES_CACHE
    if provider is not None and hasattr(provider, "rom") and hasattr(provider.rom, "rom"):
        try:
            from ..decoders.trainer_rom import decode_gen5_message_file
            arc = provider.rom.rom.archive("a/0/0/2")
            data = arc.files[109]
            blocks = decode_gen5_message_file(data)
            names = [e.get("text", "") for e in blocks[0]]
            clean = []
            from ..world.item_catalog import _clean_text
            for n in names:
                clean.append(_clean_text(n))
            _ROM_LOCATION_NAMES_CACHE = clean
            return clean
        except Exception:
            pass
    return []


def _resolve_zone_name(zone_id: int, provider: Any = None, *, include_environment: bool = True) -> str:
    loc_name = ""
    parent_loc = ""
    env = ""
    is_ext = False

    if provider is not None and hasattr(provider, "rom"):
        try:
            zone = provider.rom.zone(int(zone_id))
            if zone is not None:
                loc_id = getattr(zone, "location_name_id", 0)
                names = _get_rom_location_names(provider)
                if loc_id < len(names) and names[loc_id]:
                    loc_name = names[loc_id]
                if zone.parent_zone_id and zone.parent_zone_id != zone_id:
                    try:
                        pz = provider.rom.zone(zone.parent_zone_id)
                        if pz and pz.location_name_id < len(names):
                            parent_loc = names[pz.location_name_id]
                    except Exception:
                        pass
                area = provider.rom.area(zone.area_id)
                is_ext = getattr(area, "is_exterior", False)

                cave_kw = ("洞穴", "穴", "山", "岩洞", "秘道", "遗迹", "地下", "之间", "森", "林", "殿", "空洞")
                if int(zone_id) in (443, 352, 401) or "中心" in loc_name or "Center" in loc_name:
                    env = "宝可梦中心 / Pokémon Center"
                    if "中心" not in loc_name and "Center" not in loc_name:
                        loc_name = f"{loc_name} 宝可梦中心"
                elif "大门" in loc_name or "Gate" in loc_name:
                    env = "通道大门 / Gate"
                elif is_ext:
                    env = "室外 / Outdoor"
                elif any(k in loc_name for k in cave_kw) or any(k in parent_loc for k in cave_kw) or zone.map_type == 16:
                    env = "洞穴内 / Cave"
                else:
                    env = "室内 / Interior"
        except Exception:
            pass

    if not loc_name:
        loc_name = ZONE_NAME_MAP.get(int(zone_id), f"Zone {zone_id}")

    full = f"{parent_loc} {loc_name}".strip() if (parent_loc and parent_loc != loc_name) else loc_name
    if include_environment and env:
        return f"{full} [{env}]"
    return full


_TERRAIN_INTERACTION_PROFILES: dict[str, dict[str, Any]] = {
    "pc": {
        "symbol": "C", "kind": "宝可梦电脑 (PC Terminal)",
        "action": "open_pc", "target_type": "pc_terminal",
        "service": "pc_storage", "requires_adjacent": True,
    },
    "counter": {
        "symbol": "R", "kind": "服务柜台/接待台 (Service Counter)",
        "action": "talk", "target_type": "service_counter",
        "service": "service_candidate", "requires_adjacent": True,
    },
    "trash_can": {
        "symbol": "K", "kind": "垃圾桶 (Trash Can)",
        "action": "inspect_trash_can", "target_type": "trash_can",
        "service": None, "requires_adjacent": True,
    },
    "bookcase": {
        "symbol": "E", "kind": "书架 (Bookcase)",
        "action": "read_bookcase", "target_type": "bookcase",
        "service": None, "requires_adjacent": True,
    },
    "vending_machine": {
        "symbol": "V", "kind": "自动售货机 (Vending Machine)",
        "action": "use_vending_machine", "target_type": "vending_machine",
        "service": "shop_candidate", "requires_adjacent": True,
    },
    "inspect": {
        "symbol": "O", "kind": "一般可调查物件 (Inspectable Object)",
        "action": "inspect", "target_type": "terrain_object",
        "service": None, "requires_adjacent": True,
    },
    "strength_hole": {
        "symbol": "O", "kind": "力量机关 (Strength Interaction)",
        "action": "use_field_move", "target_type": "field_obstacle",
        "service": None, "requires_adjacent": True,
    },
}


def _npc_body_vectors(facing_raw: Any) -> dict[str, tuple[int, int]]:
    """Universal anatomical body vectors (North=Z-, South=Z+, West=X-, East=X+)."""
    try:
        fr = int(facing_raw)
    except (TypeError, ValueError):
        fr = 1
    return {
        0: {"front": (0, -1), "back": (0, 1), "left_hand": (-1, 0), "right_hand": (1, 0)},
        1: {"front": (0, 1), "back": (0, -1), "left_hand": (1, 0), "right_hand": (-1, 0)},
        2: {"front": (-1, 0), "back": (1, 0), "left_hand": (0, 1), "right_hand": (0, -1)},
        3: {"front": (1, 0), "back": (-1, 0), "left_hand": (0, -1), "right_hand": (0, 1)},
    }.get(fr, {"front": (0, 1), "back": (0, -1), "left_hand": (1, 0), "right_hand": (-1, 0)})


_STORY_ROADBLOCK_REGISTRY: list[dict[str, Any]] = [
    {
        "zone_id": 439,
        "name": "算木镇北出口角色 (阿戴克)",
        "npc_tile": {"x": 111, "z": 669, "y": 2},
        "facing_raw": 3,
        "facing": "East",
        "model_id": 97,
        "script_id": 8,
        "flag_id": 731,
        "trigger_variable": "0x40A5",
        "blocking_values": [0, 1, 2],
        "blocked_direction": "North",
        "passable_direction": "East",
        "target_destination": "２０号道路 (Route 20) / 算木牧场",
        "condition": "访问阿戴克的家并推进主线剧情",
        "dialogue_clue": "阿戴克站在路口阻拦，需要先拜访他的家推进剧情",
    },
    {
        "zone_id": 446,
        "name": "算木牧场登山大叔剧情阻挡",
        "npc_tile": {"x": 159, "z": 645, "y": 2},
        "facing_raw": 2,
        "facing": "West",
        "model_id": 64,
        "script_id": 4,
        "flag_id": 735,
        "trigger_variable": "0x40AB",
        "blocking_values": [0, 1, 2],
        "blocked_direction": "North",
        "passable_direction": "East",
        "target_destination": "立涌市方向 (Virbank City / 20号道路高台)",
        "passable_destination": "算木牧场 (Floccesy Ranch 下阶梯 X=160)",
        "condition": "击败桧扇市道馆黑连，获取基础徽章 (Basic Badge)",
        "dialogue_clue": "一个道馆徽章也没有的小孩子，也想过去吗！？跟旁边的训练师以及宝可梦多战斗战斗吧！",
    },
    {
        "zone_id": 427,
        "name": "桧扇市出口剧情阻挡",
        "npc_tile": {"x": 52, "z": 711, "y": 1},
        "facing_raw": 0,
        "facing": "North",
        "model_id": None,
        "script_id": None,
        "flag_id": 700,
        "trigger_variable": "0x40A1",
        "blocking_values": [0, 1, 2, 3, 4, 5],
        "blocked_direction": "North",
        "passable_direction": None,
        "target_destination": "１９号道路 (Route 19)",
        "condition": "完成新手初始剧情与领取宝可梦",
        "dialogue_clue": "领取初始宝可梦前不可擅自离城",
    },
]


def _is_story_gate_npc(
    zone_id: int, script_id: int | None, flag_id: int | None, model_id: int | None = None,
) -> tuple[bool, dict[str, Any] | None]:
    """Unified resolver for story roadblock NPCs and their flank gates."""
    zid = int(zone_id)
    for entry in _STORY_ROADBLOCK_REGISTRY:
        if entry["zone_id"] != zid:
            continue
        matched = False
        if entry.get("flag_id") and flag_id and entry["flag_id"] == int(flag_id):
            matched = True
        elif entry.get("script_id") and script_id and entry["script_id"] == int(script_id):
            matched = True
        elif entry.get("model_id") and model_id and entry["model_id"] == int(model_id):
            matched = True
        if not matched:
            continue

        info = dict(entry)
        npc_tile = entry.get("npc_tile")
        facing_raw = entry.get("facing_raw", 1)
        vecs = _npc_body_vectors(facing_raw)

        if npc_tile:
            nx, nz = int(npc_tile["x"]), int(npc_tile["z"])
            ny = int(npc_tile.get("y", 0))
            front_dx, front_dz = vecs["front"]
            left_dx, left_dz = vecs["left_hand"]
            right_dx, right_dz = vecs["right_hand"]

            info["talk_trigger_tiles"] = [{"x": nx + front_dx, "z": nz + front_dz, "y": ny, "facing": entry.get("facing", "front")}]
            # Flank gates: both left and right flanks are intercept triggers
            info["intercept_trigger_tiles"] = [
                {"x": nx + left_dx, "z": nz + left_dz},
                {"x": nx + right_dx, "z": nz + right_dz},
            ]
            info["verified_non_trigger_tiles"] = [
                {"x": nx + front_dx, "z": nz + front_dz},
                {"x": nx + vecs["back"][0], "z": nz + vecs["back"][1]},
            ]
        else:
            info["talk_trigger_tiles"] = []
            info["intercept_trigger_tiles"] = []
            info["verified_non_trigger_tiles"] = []
        return True, info
    return False, None


from ..world.tile_semantics import SLOPE_TABLE_FX32


def _slope_direction_and_symbol(slope_index: int) -> tuple[str, str]:
    """Determine the ascending direction and radar symbol of a 3D terrain slope."""
    if not (0 <= slope_index * 3 + 2 < len(SLOPE_TABLE_FX32)):
        return "Unknown", "▲"
    nx, ny, nz = SLOPE_TABLE_FX32[slope_index * 3 : slope_index * 3 + 3]
    if nz < 0:
        return "北 (North)", "▲"
    if nz > 0:
        return "南 (South)", "▼"
    if nx > 0:
        return "东 (East)", "▲"
    if nx < 0:
        return "西 (West)", "▲"
    return "坡道 (Slope)", "▲"


def _classify_signpost(provider: Any, zone_id: int, fx: int, fz: int) -> tuple[str, str]:
    """Distinguish between Area/Route signposts and Building/Facility signboards."""
    has_nearby_warp = False
    if provider is not None and hasattr(provider, "rom"):
        try:
            zone = provider.rom.zone(int(zone_id))
            entities = provider.rom.entities(int(zone.entities_id))
            for warp in entities.get("warps") or []:
                wx = int(float(warp.get("x_raw", 0)) // 16.0)
                wz = int(float(warp.get("y_raw", 0)) // 16.0)
                if max(abs(wx - fx), abs(wz - fz)) <= 3:
                    has_nearby_warp = True
                    break
        except Exception:
            pass
    if has_nearby_warp:
        return "facility_sign", "小建筑物/设施标识牌 (Facility Signboard)"
    return "area_sign", "区域/道路标识牌 (Area/Route Signpost)"


def _classify_facility(zone_id: int | None, name: str) -> str:
    """Classify a destination zone into a structured facility category for AI navigation."""
    name_lower = (name or "").lower()
    if "精灵中心" in name or "pokémon center" in name_lower or "pokemon center" in name_lower or zone_id in (443, 352, 401):
        return "pokemon_center"
    if "友好商店" in name or "poké mart" in name_lower or "poke mart" in name_lower or "shop" in name_lower:
        return "poke_mart"
    if "道馆" in name or "gym" in name_lower:
        return "gym"
    if "主角家" in name or "player's house" in name_lower or "players house" in name_lower or zone_id == 351:
        return "player_house"
    if "研究所" in name or "lab" in name_lower:
        return "lab"
    if "道路" in name or "route" in name_lower:
        return "route"
    if "牧场" in name or "ranch" in name_lower:
        return "ranch"
    if "洞" in name or "cave" in name_lower or "tunnel" in name_lower:
        return "cave"
    if "门" in name or "gate" in name_lower:
        return "gate"
    return "building_interior"


def _terrain_interaction_profile(material: dict[str, Any] | None) -> dict[str, Any] | None:
    material = material or {}
    interaction = str(material.get("interaction") or "").strip()
    # Water locomotion boundaries (surf_edge) are natural water edges, never furniture objects!
    if not interaction or interaction in ("surf_edge", "water"):
        return None
    profile = _TERRAIN_INTERACTION_PROFILES.get(interaction)
    if profile is None:
        profile = {
            "symbol": "O", "kind": str(material.get("label") or "可交互地形物件"),
            "action": interaction, "target_type": "terrain_object",
            "service": None, "requires_adjacent": True,
        }
    return {**profile, "interaction_semantics": "ROM tile semantic; execution still requires runtime/menu verification"}


def _npc_role_profile(overlay: dict[str, Any], zone_id: int) -> dict[str, Any]:
    """Attach evidence-ranked NPC role/service semantics to a radar marker."""
    try:
        classified = npc_classifier.classify_entity(overlay, int(zone_id))
    except Exception as exc:
        return {"semantic_kind": "NPC_UNRESOLVED", "role": "unknown", "confidence": "unresolved", "error": str(exc)}
    interaction = classified.get("interaction") if isinstance(classified.get("interaction"), dict) else {}
    script_id = overlay.get("script_id")
    # Documented/registered special NPCs in Pokémon Centers and story zones.
    if int(zone_id) == 443 and script_id == 2100:
        return {
            "semantic_kind": "SERVICE_NURSE", "role": "pokemon_center_nurse",
            "name": "宝可梦中心护士", "confidence": "verified_registry",
            "service": "recovery", "actions": ["heal_party"],
            "interaction": interaction,
            "evidence": {"zone_id": 443, "script_id": 2100, "source": "project_verified_service_registry"},
        }
    if int(zone_id) == 443 and (script_id == 10638 or overlay.get("sprite_id") == 360):
        return {
            "semantic_kind": "SPECIAL_EVENT_NPC", "role": "deliveryman",
            "name": "神秘礼物快递员", "confidence": "verified_registry",
            "service": "mystery_gift", "actions": ["receive_gift"],
            "interaction": interaction,
            "evidence": {"zone_id": 443, "script_id": 10638, "sprite_id": 360, "source": "mystery_gift_event_registry"},
        }
    if int(zone_id) == 443 and (script_id == 2109 or overlay.get("sprite_id") == 34):
        return {
            "semantic_kind": "SPECIAL_EVENT_NPC", "role": "medal_rally_man",
            "name": "奖牌大叔", "confidence": "verified_registry",
            "service": "medal_rally", "actions": ["check_medals"],
            "interaction": interaction,
            "evidence": {"zone_id": 443, "script_id": 2109, "sprite_id": 34, "source": "medal_rally_registry"},
        }
    return {
        "semantic_kind": classified.get("semantic_kind", "NPC_UNRESOLVED"),
        "role": classified.get("category_label") or classified.get("name") or "unknown",
        "name": classified.get("name"),
        "confidence": classified.get("semantics_confidence", "candidate"),
        "service": None,
        "actions": [interaction.get("type")] if interaction.get("type") else [],
        "interaction": interaction,
        "evidence": classified.get("evidence") or {},
    }


def _interaction_stand_tiles(provider: Any, zone_id: int, x: int, y: int, z: int) -> list[dict[str, Any]]:
    """Return adjacent passable tiles from which a blocked object can be used."""
    result: list[dict[str, Any]] = []
    directions = ((0, -1, "up"), (0, 1, "down"), (-1, 0, "left"), (1, 0, "right"))
    for dx, dz, approach in directions:
        tx, tz = int(x) + dx, int(z) + dz
        try:
            sample = provider.surface_at(int(zone_id), tx, tz, int(y), allow_unverified_terrain=True)
            if not sample or not sample.get("movement_allowed"):
                continue
        except Exception:
            continue
        result.append({
            "x": tx, "y": int(y), "z": tz,
            "approach_direction": approach,
            "facing": {"up": "down", "down": "up", "left": "right", "right": "left"}[approach],
        })
    return result


def _classify_surface_meta(
    tclass: int | None,
    flags: int | None,
    blocked: bool,
    walkable: bool,
    has_warp: bool = False,
    has_npc: bool = False,
    *,
    ledge_direction: str | None = None,
    material: dict[str, Any] | None = None,
    slope_index: int = 0,
    mode: str = "coarse",
    is_vertical_cliff: bool = False,
    surface_grid_y: int = 0,
    height_delta: int = 0,
    slope_meta: dict[str, Any] | None = None,
    catwalk_meta: dict[str, Any] | None = None,
    alternate_layer_available: bool = False,
    alternate_layer_y: int | None = None,
    is_stair_corridor: bool = False,
) -> tuple[str, str, str]:
    """Map decoded terrain/event facts to a stable AI legend symbol.

    In coarse mode (default): decorative walkable paths collapse into '.',
    and purely visual obstacle variants collapse into '#'. Mechanics that
    change gameplay (encounters, boulders, warps, gates, NPCs) remain distinct.
    In detail mode: full physical and visual material types (asphalt, dirt,
    lawn, dark grass, trees, building walls, etc.) are explicitly separated.
    """
    if is_vertical_cliff:
        return (f"高差断崖 (Cliff Y={surface_grid_y})", "#", f"❌ 垂直高差断崖：地块标高 Y={surface_grid_y} (相差 {abs(height_delta)} 层)")
    if slope_meta:
        return (f"{slope_meta['type']} ({slope_meta['description']})",
                slope_meta["symbol"],
                f"⛰️ {slope_meta['description']} | 护栏约束: {slope_meta['handrail_constraint']}")
    if catwalk_meta:
        return ("独木桥/窄桥 (Catwalk)", catwalk_meta["symbol"], catwalk_meta["status"])
    if alternate_layer_available and not walkable and not has_warp and not has_npc:
        other = f"Y={alternate_layer_y:+d}" if alternate_layer_y is not None else "其他高度层"
        if is_stair_corridor and abs(height_delta) == 1 and not is_vertical_cliff:
            if height_delta > 0:
                return (f"上行连接通道 (通往高台 {other})", "▲", f"▲ 上行通道：向此方向可登上上层高台 (标高 {other})")
            elif height_delta < 0:
                return (f"下行连接通道 (通往地面 {other})", "▼", f"▼ 下行通道：向此方向可走下下层地面 (标高 {other})")
        return (f"悬空高差/断崖 (下方存在 {other} 地面)", "↕", f"↕ 当前切片无路面，下方/上方 {other} 存在路面；此处为悬空高差非楼梯通道，不可直接通行，需寻找真实楼梯")
    material = material or {}
    material_kind = str(material.get("kind") or "")
    terrain_interaction = _terrain_interaction_profile(material)
    if terrain_interaction is not None:
        sym = str(terrain_interaction["symbol"])
        if mode == "coarse" and sym in ("K", "E", "V"):
            sym = "O"
        return (str(terrain_interaction["kind"]), sym,
                "❌ 地形不可进入；✅ 可从相邻可通行格交互 (interactive blocked tile)")
    if has_npc:
        return "NPC / 场景角色", "N", "⚠️ 当前或脚本候选实体占用 (Occupied candidate)"
    if has_warp:
        return "门 / 建筑出入口 / 传送点", "D", "🚪 传送出入口 (Warp candidate)"
    if ledge_direction or material_kind == "ledge" or tclass in (0x72, 0x73, 0x74, 0x75):
        direction = ledge_direction or {0x72: "right", 0x73: "left", 0x74: "up", 0x75: "down"}.get(tclass or 0)
        direction_zh = {"up": "北", "down": "南", "left": "西", "right": "东"}.get(direction or "", "指定")
        direction_symbol = {"up": "↑", "down": "↓", "left": "←", "right": "→"}.get(direction or "", "↕")
        return "单向跳台/台阶悬崖 (One-way Ledge)", direction_symbol, f"🔻 只能向{direction_zh}侧跳下/通过 (one-way)"
    if slope_index != 0 or material_kind in {"slope_up", "slope_down"}:
        slope_sym = "▲" if slope_index > 0 else "▼"
        slope_label = "上行坡道/台阶 (Slope Up)" if slope_index > 0 else "下行坡道/台阶 (Slope Down)"
        return slope_label, slope_sym, "⛰️ 沿走向高度跃迁"
    if material_kind == "water_edge" or tclass in (0x41, 0x44):
        return "水岸/河堤跃迁格 (Water Shore / Surf Edge)", "~", "🌊 水陆跃迁岸边；徒步阻挡，面对可触发冲浪下水，水中可跳跃上岸"
    if material_kind in {"water"} or tclass in (0x3C, 0x3D, 0x3F, 60):
        return "水域/河流 (Water)", "W", "🌊 需要冲浪/特殊移动能力"

    # Grass
    is_tall = (material_kind == "tall_grass" or tclass in (4, 5, 0x21))
    is_dark = (material_kind == "dark_grass" or tclass in (6, 7, 0x22))
    is_very_tall = (material_kind == "very_tall_grass" or tclass in (8, 9))
    if is_tall or is_dark or is_very_tall:
        if mode == "detail":
            if is_dark:
                return "深色草丛 (Dark Grass)", "X", "🌿 深色双打草丛：双打遇敌高概率区"
            if is_very_tall:
                return "茂密草丛 (Very Tall Grass)", "V", "🌿 茂密草丛：遇敌，阻挡骑行自行车"
        return "遇敌草丛 (Encounter Grass)", "*", "🌿 草丛：可通行；会触发野生宝可梦对战"

    # Walkable Road & Dirt paths (No wild encounter)
    is_paved = (tclass == 0 or material_kind in {"paved_road", "asphalt", "concrete"})
    is_dirt = (tclass == 2 or material_kind in {"dirt_path", "dirt", "soil", "sand"})
    is_ground = (tclass == 3 or material_kind == "ground_path")
    is_lawn = (tclass in (0x1F, 31) or material_kind in {"grass_path", "lawn"})
    is_floor = (tclass == 1 or material_kind == "floor")
    is_puddle = (tclass == 0x14 or material_kind == "puddle")
    is_sand = (tclass in (0x0B, 0x0C, 0x7C) or material_kind in {"sand", "deep_sand", "quicksand"})

    if is_paved or is_dirt or is_ground or is_lawn or is_floor or is_puddle or is_sand:
        if blocked or not walkable:
            return "道路障碍 (Road Obstacle)", "#", "❌ 静态碰撞阻挡"
        if mode == "detail":
            if is_paved:
                return "平坦道路/水泥柏油路 (Paved Road)", ".", "✅ 水泥柏油路面，平整安全通行，不遇怪"
            if is_dirt:
                return "黄土路面/沙土道路 (Yellow Dirt Path)", ",", "✅ 黄土泥土路面，平整安全通行，不遇怪"
            if is_lawn:
                return "平坦草坪道路 (Lawn / Grass Path)", '"', "✅ 平坦草坪，安全通行，不遇怪"
            if is_floor:
                return "室内地面/走廊地板 (Indoor Floor)", "_", "✅ 室内地面，平整安全通行"
            if is_puddle:
                return "浅滩水洼路面 (Puddle)", "%", "✅ 浅滩水洼，安全通行"
            if is_sand:
                return "沙漠沙地 (Desert Sand)", "S", "✅ 沙漠松软沙地，安全通行"
            return "地面道路 (Ground Path)", ".", "✅ 地面道路，平整安全通行，不遇怪"
        else:
            return "普通通路 (Walkable Path)", ".", "✅ 平坦畅通道路，安全通行，不遇怪"

    # Obstacles & Barriers
    is_tree = (material_kind in {"tree", "forest", "dense_forest"} or tclass in (64, 65))
    is_building = (material_kind in {"building", "wall"} or tclass == 128)
    is_fence = (material_kind in {"obstacle", "directional_barrier"} or tclass in (0x51, 0x52, 0x53, 0x54, 0x55, 0x56, 0x57, 0x58, 114))

    if is_tree or is_building or is_fence or blocked or not walkable:
        if mode == "detail":
            if is_building:
                return "建筑物墙体/岩壁 (Building / Wall)", "B", "❌ 建筑墙体阻挡"
            if is_tree:
                return "树木/密林障碍 (Tree / Dense Forest)", "T", "❌ 树木障碍阻挡"
            if is_fence:
                if blocked or not walkable:
                    return "围栏/方向铁栅栏 (Fence / Barrier)", "#", "❌ 静态碰撞阻挡"
                return "道路边界/方向栅栏 (Directional Barrier)", ".", "✅ 可通行，但有方向限制"
        return "障碍/墙体 (Obstacle)", "#", "❌ 障碍物阻挡不可通行"

    return "普通地面 (Ground)", ".", "✅ 可通行"


_LEGEND_COARSE: dict[str, str] = {
    "[P]": "主角所在位置 (Player)",
    "[p]": "主角跨层攀爬投影：主角正处于此处共享楼梯中段 (Player in Stair Transit)",
    "[↕]": "其他高度层存在可通行地面：当前切片不是墙体，需要切换楼层/走楼梯 (Other Walkable Layer)",
    "[╫]": "独木桥/窄桥主体：两侧是坠落边缘，只能沿桥轴通行，需监控停留平衡 (Catwalk Body)",
    "[╪]": "独木桥入口/出口：沿桥轴进入，侧面不可离开 (Catwalk Entry)", 
    "[!]": "剧情封路道闸拦截线 (Story Gate Trigger Line)",
    "[T]": "对战训练家：未击败时具有对战视线 (Trainer NPC)",
    "[^]": "训练家对战视线 (向北)：踏入将强制进入战斗 (Sight North)",
    "[v]": "训练家对战视线 (向南)：踏入将强制进入战斗 (Sight South)",
    "[<]": "训练家对战视线 (向西)：踏入将强制进入战斗 (Sight West)",
    "[>]": "训练家对战视线 (向东)：踏入将强制进入战斗 (Sight East)",
    "[*]": "遇敌草丛：可通行；会触发野生宝可梦对战 (Encounter Grass)",
    "[.]": "普通通路：平坦畅通道路，安全通行，不遇怪 (Walkable Path)",
    "[#]": "障碍物/墙体：不可通行障碍 (Blocked Obstacle)",
    "[W]": "水域/河流：完全处于水中，需处于冲浪状态 (Deep Water)",
    "[~]": "水岸/河堤跃迁格：可触发冲浪下水或跳跃上岸 (Surf Shore)",
    "[D]": "门/出入口/Warp 候选 (Door/Warp)",
    "[N]": "普通NPC/市民/非对战实体 (Peaceful NPC)",
    "[O]": "可交互家具/设施/物件 (Interactive Object)",
    "[h]": "地面埋藏隐藏道具：无实体碰撞，可自由踩踏通行，可从相邻格面向调查拾取 (Hidden Item)",
    "[I]": "地面道具球/物品：可从相邻格拾取 (Item Ball)",
    "[▲]": "上行阶梯/坡道：沿走向高度上升 (Higher Step / Slope Up)",
    "[▼]": "下行阶梯/坡道：沿走向高度下降 (Lower Step / Slope Down)",
    "[≡]": "同高度阶梯踏面：当前所处阶梯层 (Level Stair Tread)",
    "[G]": "怪力巨石：未推入坑洞，可使用怪力推动 (Pushable Boulder)",
    "[U]": "未填平巨石坑洞：深坑障碍不可通行 (Empty Boulder Hole)",
    "[=]": "已填平巨石路面/天桥高架：可安全通行 (Filled Boulder Path)",
    "[↑]": "单向跳台：只允许向北通过 (One-way North)",
    "[↓]": "单向跳台：只允许向南通过 (One-way South)",
    "[←]": "单向跳台：只允许向西通过 (One-way West)",
    "[→]": "单向跳台：只允许向东通过 (One-way East)",
    "[?]": "未知/未覆盖地块 (Unknown)",
}

_LEGEND_DETAIL: dict[str, str] = {
    "[P]": "主角所在位置 (Player)",
    "[p]": "主角跨层攀爬投影：主角正处于此处共享楼梯中段 (Player in Stair Transit)",
    "[↕]": "其他高度层存在可通行地面：当前切片不是墙体，需要切换楼层/走楼梯 (Other Walkable Layer)",
    "[╫]": "独木桥/窄桥主体：两侧是坠落边缘，只能沿桥轴通行，需监控停留平衡 (Catwalk Body)",
    "[╪]": "独木桥入口/出口：沿桥轴进入，侧面不可离开 (Catwalk Entry)", 
    "[.]": "平坦道路/水泥柏油硬化路：平整安全通行，不遇怪 (Paved Road)",
    "[,]": "黄土路面/沙土泥土道路：平整安全通行，不遇怪 (Dirt / Soil Path)",
    "[\"]": "平坦草坪道路：无遇敌开阔草坪 (Lawn / Grass Path)",
    "[_]": "室内地面/走廊地板 (Indoor Floor)",
    "[%]": "浅滩水洼路面：溅水，安全通行 (Puddle Path)",
    "[S]": "沙漠细沙/流沙：松软沙地 (Desert Sand)",
    "[*]": "常规高草丛：单打遇敌区 (Tall Grass)",
    "[X]": "深色草丛：双打遇敌高概率区 (Dark Grass)",
    "[V]": "茂密深草丛：遇敌，阻挡骑行自行车 (Very Tall Grass)",
    "[B]": "建筑物墙体/岩壁障碍 (Building / Rock Wall)",
    "[T]": "树木障碍/密林障碍 (Tree / Dense Forest)",
    "[#]": "围栏/铁栅栏/碰撞障碍 (Fence / Barrier)",
    "[!]": "剧情封路道闸拦截线 (Story Gate Trigger Line)",
    "[K]": "对战训练家 (Trainer NPC)",
    "[^/v/</>]": "训练家对战视线 (Trainer Sights)",
    "[N]": "普通NPC/市民居民 (Peaceful NPC)",
    "[C]": "宝可梦电脑终端 (PC Terminal)",
    "[R]": "服务接待台/柜台 (Service Counter)",
    "[E]": "书架/杂志架 (Bookcase)",
    "[V]": "自动售货机 (Vending Machine)",
    "[O]": "其他家具设施 (Furniture)",
    "[h]": "地面埋藏隐藏道具：无实体碰撞，可自由踩踏通行，可从相邻格面向调查拾取 (Hidden Item)",
    "[I]": "地面道具球 (Item Ball)",
    "[W]": "水域/河流 (Deep Water)",
    "[~]": "水岸/河堤跃迁格 (Surf Shore)",
    "[D]": "门/出入口/传送点 (Door / Warp)",
    "[▲]": "上行阶梯/坡道 (Higher Step / Slope Up)",
    "[▼]": "下行阶梯/坡道 (Lower Step / Slope Down)",
    "[≡]": "同高度阶梯踏面 (Level Stair Tread)",
    "[G]": "怪力巨石 (Pushable Boulder)",
    "[U]": "未填平深坑 (Empty Boulder Hole)",
    "[=]": "已填平路面 (Filled Boulder Path)",
    "[→/←/↑/↓]": "单向跳台 (One-way Ledges)",
    "[?]": "未知地块 (Unknown)",
}

_LEGEND = _LEGEND_COARSE

async def _radar_runtime_sample() -> dict[str, Any]:
    """Return a current RAM-backed player sample for radar polling.

    The lightweight PlayerRuntime resolver can temporarily be unresolved while
    MapTruth has already joined the same Main RAM snapshot successfully. Use
    that structured map truth as a short-lived radar fallback, never a hardcoded
    previous Zone/coordinate.
    """
    global _radar_truth_cache, _radar_truth_sampled_at
    latest = player_runtime_service.latest if isinstance(player_runtime_service.latest, dict) else {}
    canonical = canonical_grid_player(latest, require_resolved=False) or {}
    if canonical.get("status") in {"resolved", "candidate"}:
        return latest
    if _runtime_reader is not None:
        try:
            active_sample = await player_runtime_service.sample(_runtime_reader, allow_discovery=True)
            active_canonical = canonical_grid_player(active_sample, require_resolved=False) or {}
            if active_canonical.get("status") in {"resolved", "candidate"}:
                return active_sample
        except Exception:
            pass
    now = time.monotonic()
    if _radar_truth_cache is not None and now - _radar_truth_sampled_at < _RADAR_TRUTH_CACHE_TTL:
        return _radar_truth_cache
    if _runtime_reader is None:
        return latest
    try:
        truth = await _radar_truth_service.current(_runtime_reader)
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError):
        return latest
    runtime_evidence = truth.get("runtime_evidence") if isinstance(truth, dict) else None
    runtime_evidence = runtime_evidence if isinstance(runtime_evidence, dict) else {}
    runtime = runtime_evidence.get("player") if isinstance(runtime_evidence.get("player"), dict) else None
    if not isinstance(runtime, dict):
        runtime = truth.get("player") if isinstance(truth, dict) else None
    runtime = runtime if isinstance(runtime, dict) else {}
    actor = runtime.get("actor") if isinstance(runtime.get("actor"), dict) else {}
    grid = actor.get("grid_position") if isinstance(actor.get("grid_position"), dict) else {}
    zone_id = runtime.get("zone_id")
    if not (isinstance(zone_id, int) and all(isinstance(grid.get(key), int) for key in ("x", "y", "z"))):
        return latest
    orientation = runtime.get("orientation") if isinstance(runtime.get("orientation"), dict) else {}
    _radar_truth_cache = {
        "format": "black2-runtime-player-live/v2",
        "status": "resolved",
        "confidence": truth.get("confidence", "probable") if isinstance(truth, dict) else "probable",
        "source": "MapTruthService.current -> current Main RAM",
        "zone_id": int(zone_id),
        "position": {"grid": {"x": int(grid["x"]), "y": int(grid["y"]), "z": int(grid["z"])}},
        "orientation": orientation,
        "frame": runtime.get("frame"),
    }
    _radar_truth_sampled_at = now
    return _radar_truth_cache


def _player_anchor(raw_sample_override: dict[str, Any] | None = None) -> tuple[dict[str, Any], int | None, int | None, int | None, int | None, str, str]:
    raw_sample = raw_sample_override if isinstance(raw_sample_override, dict) else (player_runtime_service.latest if isinstance(player_runtime_service.latest, dict) else {})
    sample = canonical_grid_player(raw_sample, require_resolved=False) or {}
    zone_raw = sample.get("zone_id")
    grid = sample.get("position", {}).get("grid", {}) if isinstance(sample.get("position"), dict) else sample.get("grid", {})
    if not isinstance(grid, dict):
        grid = {}
    # Never invent the old Floccesy Ranch coordinates when the runtime chain
    # is unresolved. A stale fallback makes a live radar look valid while the
    # player may actually be in a Pokémon Center or another Zone.
    coordinate_values = (grid.get("x"), grid.get("y"), grid.get("z"))
    resolved = (isinstance(zone_raw, int) and not isinstance(zone_raw, bool)
                and all(isinstance(value, int) and not isinstance(value, bool)
                        for value in coordinate_values))
    zone_id = int(zone_raw) if resolved else None
    px, py, pz = (tuple(int(value) for value in coordinate_values)
                  if resolved else (None, None, None))
    # canonical_grid_player deliberately keeps only coordinate facts. Radar
    # also needs the live orientation, so read that non-coordinate field from
    # the same latest PlayerRuntime sample without introducing a second state.
    orientation = raw_sample.get("orientation") or sample.get("orientation") or {}
    if not isinstance(orientation, dict):
        orientation = {}
    facing = orientation.get("facing") or raw_sample.get("facing") or "Unknown"
    facing_zh = orientation.get("facing_zh") or raw_sample.get("facing_zh") or "未知"
    return sample, zone_id, px, py, pz, str(facing), str(facing_zh)


def _radar_anchor_error(sample: dict[str, Any]) -> JSONResponse:
    return _error_response(409, "RADAR_PLAYER_UNRESOLVED",
                           "当前 PlayerRuntime 尚未解析出 Zone/X/Y/Z；拒绝使用旧坐标生成雷达。请先调用 /api/v1/player/runtime，或在请求中显式提供 zone_id、x、y、z。",
                           retryable=True, details={
                               "runtime_player": sample,
                               "explicit_refresh_endpoint": "/api/v1/player/runtime",
                               "coordinate_policy": "unresolved runtime never falls back to a previous map",
                           })


def _live_actor_overlays_at(
    runtime_actors: list[dict[str, Any]] | None, zone_id: int, x: int, y: int, z: int,
) -> list[dict[str, Any]]:
    """Project current ActorSystem positions onto one radar tile.

    Static ROM NPC coordinates are spawn candidates. This overlay is the
    dynamic layer for NPCs that patrol or otherwise move away from spawn.
    Player actor UID 0xFF is intentionally omitted because the radar adds P
    from the authoritative PlayerRuntime/MapTruth anchor.
    """
    result: list[dict[str, Any]] = []
    for actor in runtime_actors or []:
        if not isinstance(actor, dict) or actor.get("is_player") is True:
            continue
        effective_zone = actor.get("effective_zone_id_candidate")
        raw_zone = actor.get("zone_id")
        actor_zone = effective_zone if effective_zone is not None else (raw_zone if raw_zone not in (None, 0) else None)
        grid = actor.get("grid") if isinstance(actor.get("grid"), dict) else actor.get("grid_position")
        if not isinstance(grid, dict) or actor_zone is None:
            continue
        try:
            if int(actor_zone) != int(zone_id) or abs(int(grid.get("y", y)) - int(y)) > 2:
                continue
            if int(grid.get("x")) != int(x) or int(grid.get("z")) != int(z):
                continue
        except (TypeError, ValueError):
            continue
        overlay = {
            "kind": "npc", "symbol": "N",
            "npc_id": actor.get("actor_uid"),
            "runtime_actor_uid": actor.get("actor_uid"),
            "runtime_slot": actor.get("slot"),
            "runtime_address": actor.get("address"),
            "sprite_id": actor.get("model_id"),
            "script_id": actor.get("script_id"),
            "movement_id": actor.get("move_code"),
            "facing": actor.get("facing", actor.get("face_direction")),
            "position": {"x": int(x), "y": int(y), "z": int(z)},
            "position_source": "live_ActorSystem",
            "presence": "runtime_present",
            "dynamic": True,
            "semantic_status": "live actor position; ROM identity binding remains evidence-ranked",
        }
        result.append(overlay)
    return result


def _mark_moved_static_npcs(overlays: list[dict[str, Any]], runtime_actors: list[dict[str, Any]] | None, zone_id: int) -> None:
    """Mark ROM spawn markers whose bound live actor has moved elsewhere or is unspawned."""
    for overlay in overlays:
        if overlay.get("kind") not in ("npc", "item"):
            continue
        script_id = overlay.get("script_id")
        sprite_id = overlay.get("sprite_id")
        static_pos = overlay.get("position") if isinstance(overlay.get("position"), dict) else {}
        matched_actor = None
        for actor in runtime_actors or []:
            if not isinstance(actor, dict) or actor.get("is_player") is True:
                continue
            effective_zone = actor.get("effective_zone_id_candidate")
            raw_zone = actor.get("zone_id")
            actor_zone = effective_zone if effective_zone is not None else (raw_zone if raw_zone not in (None, 0) else None)
            if actor_zone is None or int(actor_zone) != int(zone_id):
                continue
            if script_id is not None and actor.get("script_id") == script_id:
                matched = True
            elif sprite_id is not None and actor.get("model_id") == sprite_id and script_id in (None, 0):
                matched = True
            else:
                matched = False
            if not matched:
                continue
            matched_actor = actor
            break

        if matched_actor is not None:
            grid = matched_actor.get("grid") if isinstance(matched_actor.get("grid"), dict) else {}
            runtime_info = {
                "actor_uid": matched_actor.get("actor_uid"), "slot": matched_actor.get("slot"),
                "grid": grid, "facing": matched_actor.get("facing"),
                "movement_state": matched_actor.get("move_code"),
                "position_source": "live_ActorSystem",
                "identity_evidence": ["script_id", "sprite/model", "raw_zone_candidate"],
            }
            overlay["dynamic"] = True
            overlay["runtime"] = runtime_info
            if (grid.get("x"), grid.get("z")) != (static_pos.get("x"), static_pos.get("z")):
                overlay["presence"] = "static_spawn_not_current"
            else:
                overlay["presence"] = "runtime_present"
        elif runtime_actors is not None:
            # We have authoritative live ActorSystem data for this scene.
            # Static ROM entities with no matching live actor were not spawned
            # by the game engine (conditional event flag unfulfilled).
            overlay["presence"] = "unspawned" 



def _normalize_trainer_facing(facing: Any) -> tuple[int, str, int, int, str]:
    """Return (dir_idx, dir_name, dx, dz, ray_symbol).

    Gen-5 Grid Conventions:
    0: North (Z-) -> ray '^'
    1: South (Z+) -> ray 'v'
    2: West  (X-) -> ray '<'
    3: East  (X+) -> ray '>'
    """
    if isinstance(facing, str):
        low = facing.lower()
        if "north" in low or "up" in low:
            return 0, "North", 0, -1, "^"
        if "south" in low or "down" in low:
            return 1, "South", 0, 1, "v"
        if "west" in low or "left" in low:
            return 2, "West", -1, 0, "<"
        if "east" in low or "right" in low:
            return 3, "East", 1, 0, ">"
    try:
        idx = int(facing)
        if idx == 0:
            return 0, "North", 0, -1, "^"
        if idx == 1:
            return 1, "South", 0, 1, "v"
        if idx == 2:
            return 2, "West", -1, 0, "<"
        if idx == 3:
            return 3, "East", 1, 0, ">"
    except (TypeError, ValueError):
        pass
    return 1, "South", 0, 1, "v"


def _resolve_current_flag_bytes() -> bytes | None:
    try:
        fb = progression_state_service.get_flag_bytes()
        if fb:
            return fb
        latest = progression_state_service.latest
        if isinstance(latest, dict):
            ew = latest.get("event_work") or {}
            fb_dict = ew.get("flag_bytes") or {}
            raw_hex = fb_dict.get("raw_hex") or fb_dict.get("hex")
            if raw_hex:
                return bytes.fromhex(raw_hex)
    except Exception:
        pass
    return None


async def _ensure_flag_bytes() -> bytes | None:
    fb = _resolve_current_flag_bytes()
    if fb is not None:
        return fb
    if _runtime_reader is not None:
        try:
            await progression_state_service.sample()
            return progression_state_service.get_flag_bytes()
        except Exception:
            pass
    return None


def _build_trainer_sight_rays(
    provider: Any,
    zone_id: int,
    min_x: int,
    max_x: int,
    min_z: int,
    max_z: int,
    *,
    py: int = 0,
    runtime_actors: list[dict[str, Any]] | None = None,
    preferred_zone: int | None = None,
    flag_bytes: bytes | None = None,
) -> tuple[dict[tuple[int, int], dict[str, Any]], dict[tuple[int, int], dict[str, Any]], list[str]]:
    """Compute active trainer line-of-sight rays with raycast occlusion.

    Returns:
      (sight_tiles_map, trainer_npc_map, trainer_summaries)
    """
    if provider is None or not hasattr(provider, "rom") or not hasattr(provider.rom, "entities"):
        return {}, {}, []

    zones_to_check = {int(zone_id)}
    if preferred_zone is not None:
        zones_to_check.add(int(preferred_zone))

    matrix_id = None
    try:
        matrix_id = int(provider.rom.zone(int(zone_id)).matrix_id)
    except Exception:
        pass
    if matrix_id is not None and hasattr(provider, "matrix_catalog"):
        for corner_x, corner_z in ((min_x, min_z), (max_x, min_z), (min_x, max_z), (max_x, max_z)):
            owner = _matrix_zone_owner(provider, matrix_id, corner_x, corner_z, preferred_zone=zone_id)
            if owner is not None:
                zones_to_check.add(int(owner))

    if flag_bytes is None:
        flag_bytes = _resolve_current_flag_bytes()
    sight_tiles_map: dict[tuple[int, int], dict[str, Any]] = {}
    trainer_npc_map: dict[tuple[int, int], dict[str, Any]] = {}
    active_trainers: list[dict[str, Any]] = []

    latest_history: dict[str, Any] = {}
    try:
        from ..runtime.npc_battle_history import npc_battle_history
        latest_history = npc_battle_history.latest_by_npc()
    except Exception:
        pass

    for zid in sorted(zones_to_check):
        try:
            z_obj = provider.rom.zone(zid)
            entities = provider.rom.entities(int(z_obj.entities_id))
            npcs = entities.get("npcs") or []
        except Exception:
            continue

        for npc in npcs:
            if not isinstance(npc, dict):
                continue
            sight_raw = npc.get("sight_raw")
            if sight_raw is None or int(sight_raw) <= 0:
                continue
            sight_range = int(sight_raw)

            rec_idx = npc.get("record_index")
            script_id = npc.get("script_id")
            sprite_id = npc.get("sprite_id")
            npc_id = npc.get("id") or f"zone:{zid}:npc:{rec_idx}"

            is_defeated = False
            if npc.get("defeat_status") == "defeated" or npc.get("is_defeated") is True:
                is_defeated = True
            elif flag_bytes and script_id is not None:
                df = resolve_trainer_defeat_flag(script_id)
                if df is not None and is_event_flag_set(df, flag_bytes):
                    is_defeated = True
            elif str(npc_id) in latest_history:
                rec = latest_history[str(npc_id)]
                if rec.get("defeat_status") == "defeated" or rec.get("probe_outcome") == "trainer_defeated":
                    is_defeated = True

            if is_defeated:
                continue

            matched_actor = None
            if runtime_actors:
                for actor in runtime_actors:
                    if not isinstance(actor, dict) or actor.get("is_player") is True:
                        continue
                    effective_zone = actor.get("effective_zone_id_candidate")
                    raw_zone = actor.get("zone_id")
                    actor_zone = effective_zone if effective_zone is not None else (raw_zone if raw_zone not in (None, 0) else None)
                    if actor_zone is not None and int(actor_zone) != zid:
                        continue
                    if script_id is not None and script_id > 0 and actor.get("script_id") == script_id:
                        matched_actor = actor
                        break
                    elif sprite_id is not None and actor.get("model_id") == sprite_id and script_id in (None, 0):
                        matched_actor = actor
                        break

            if matched_actor is not None and not is_defeated and flag_bytes:
                act_sc = matched_actor.get("script_id")
                if act_sc is not None:
                    df = resolve_trainer_defeat_flag(act_sc)
                    if df is not None and is_event_flag_set(df, flag_bytes):
                        is_defeated = True
            if is_defeated:
                continue

            if matched_actor is not None:
                grid = matched_actor.get("grid") or {}
                tx = int(grid.get("x", npc.get("x")))
                tz = int(grid.get("z", npc.get("y")))
                ty = int(grid.get("y", py))
                facing_raw = matched_actor.get("face_dir_raw", matched_actor.get("facing"))
            else:
                tx = int(npc.get("x", -999999))
                tz = int(npc.get("y", -999999))
                ty = py
                facing_raw = npc.get("facing_id", npc.get("direction_raw", 1))

            dir_idx, dir_name, dx, dz, ray_sym = _normalize_trainer_facing(facing_raw)

            trainer_entry = {
                "trainer_id": npc_id,
                "tile": {"x": tx, "z": tz},
                "sight_range": sight_range,
                "facing": dir_name,
                "facing_raw": dir_idx,
                "symbol": "T",
                "defeat_status": "candidate",
                "active_sight": True,
                "script_id": script_id,
                "sprite_id": sprite_id,
                "zone_id": zid,
            }
            trainer_npc_map[(tx, tz)] = trainer_entry

            covered_tiles: list[dict[str, Any]] = []
            for step in range(1, sight_range + 1):
                rx = tx + dx * step
                rz = tz + dz * step

                surf = None
                try:
                    preview_fn = getattr(provider, "preview_surface_at", None)
                    surf = preview_fn(zid, rx, rz, ty) if callable(preview_fn) else provider.surface_at(zid, rx, rz, ty, allow_unverified_terrain=True)
                except Exception:
                    pass

                is_walkable = True
                if surf is not None:
                    surfs = surf.get("surfaces") or []
                    top = surfs[0] if surfs else {}
                    cell_data = surf.get("cell") or {}
                    static_blocked = bool(cell_data.get("static_blocked", top.get("static_blocked", True)))
                    mat = cell_data.get("material") or top.get("material") or {}
                    ledge = cell_data.get("ledge_direction") or mat.get("ledge_direction")
                    blocked_dirs = cell_data.get("blocked_directions") or mat.get("blocked_directions") or []
                    directional = bool(ledge or blocked_dirs)
                    if not surf.get("walkable", False) or (static_blocked and not directional):
                        is_walkable = False

                if not is_walkable:
                    break

                sight_info = {
                    "trainer_id": npc_id,
                    "trainer_tile": {"x": tx, "z": tz},
                    "step": step,
                    "max_distance": sight_range,
                    "facing": dir_name,
                    "facing_raw": dir_idx,
                    "symbol": ray_sym,
                    "hazard": "trainer_sight_battle",
                    "warning": f"进入第 {step}/{sight_range} 格视线将强制触发训练家对战",
                }
                if (rx, rz) not in sight_tiles_map or sight_tiles_map[(rx, rz)].get("step", 999) > step:
                    sight_tiles_map[(rx, rz)] = sight_info
                covered_tiles.append({"x": rx, "z": rz, "step": step})

            active_trainers.append({
                "trainer_id": npc_id,
                "tx": tx,
                "tz": tz,
                "sight_range": sight_range,
                "dir_name": dir_name,
                "ray_sym": ray_sym,
                "covered_tiles": covered_tiles,
            })

    trainer_summaries: list[str] = []
    facing_zh_map = {"North": "北 (North)", "South": "南 (South)", "West": "西 (West)", "East": "东 (East)"}
    for tr in active_trainers:
        tx, tz = tr["tx"], tr["tz"]
        sight_range = tr["sight_range"]
        dir_name = tr["dir_name"]
        ray_sym = tr["ray_sym"]
        covered_tiles = tr["covered_tiles"]
        near_window = (
            (min_x - 3 <= tx <= max_x + 3 and min_z - 3 <= tz <= max_z + 3)
            or any(min_x <= ct["x"] <= max_x and min_z <= ct["z"] <= max_z for ct in covered_tiles)
        )
        if near_window:
            zh_dir = facing_zh_map.get(dir_name, dir_name)
            if covered_tiles:
                start_c = f"(X={covered_tiles[0]['x']}, Z={covered_tiles[0]['z']})"
                end_c = f"(X={covered_tiles[-1]['x']}, Z={covered_tiles[-1]['z']})"
                span_str = f"{start_c} ➔ {end_c}" if len(covered_tiles) > 1 else start_c
                occ_str = f" [受障碍阻隔，原视线 {sight_range} 格]" if len(covered_tiles) < sight_range else ""
                trainer_summaries.append(
                    f"- (X={tx:3d}, Z={tz:3d}) [T] 对战训练家 ➔ 朝向: {zh_dir} | 视线全长: {len(covered_tiles)} 格 [{ray_sym}]{occ_str}\n"
                    f"  * 视线警戒射线: {span_str} (共 {len(covered_tiles)} 格)\n"
                    f"  * 战术避险建议: 避免踏入该射线区间以免被动拉入对战；若需交战可正面切入"
                )
            else:
                trainer_summaries.append(
                    f"- (X={tx:3d}, Z={tz:3d}) [T] 对战训练家 ➔ 朝向: {zh_dir} | 视线被正前方障碍完全遮挡 (有效警戒 0 格)"
                )

    return sight_tiles_map, trainer_npc_map, trainer_summaries


def _scan_boulder_mechanics(
    provider: Any,
    zone_id: int,
    runtime_actors: list[dict[str, Any]] | None,
) -> tuple[dict[tuple[int, int], dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Scan Strength boulders and pit holes in current zone with live filled-hole detection."""
    boulder_cell_map: dict[tuple[int, int], dict[str, Any]] = {}
    active_boulders: list[dict[str, Any]] = []
    boulder_summaries: list[str] = []

    live_boulders = []
    for a in runtime_actors or []:
        if not isinstance(a, dict) or a.get("is_player") is True:
            continue
        model_id = a.get("model_id")
        script_id = a.get("script_id")
        if model_id == 8197 or script_id == 10000:
            live_boulders.append(a)

    if not live_boulders and provider is not None and hasattr(provider, "rom"):
        try:
            z = provider.rom.zone(int(zone_id))
            ent = provider.rom.entities(int(z.entities_id))
            for n in ent.get("npcs") or []:
                if n.get("sprite_id") == 8197 or n.get("script_id") == 10000:
                    live_boulders.append({
                        "actor_uid": n.get("id"),
                        "model_id": n.get("sprite_id"),
                        "script_id": n.get("script_id"),
                        "grid": {"x": n.get("x"), "z": n.get("y"), "y": 0},
                        "source": "rom_static",
                    })
        except Exception:
            pass

    for b in live_boulders:
        grid = b.get("grid") or {}
        bx = int(grid.get("x", -999))
        bz = int(grid.get("z", -999))
        by = int(grid.get("y", 0))
        uid = b.get("actor_uid")

        # In Gen-5, a boulder covers a 2x2 multi-tile footprint.
        # When pushed into a hole (by < 0, e.g. y = -2):
        # It covers 2x2 hole: (bx..bx+1, bz-1..bz)
        if by < 0:
            filled_tiles = []
            for hx in (bx, bx + 1):
                for hz in (bz - 1, bz):
                    boulder_cell_map[(hx, hz)] = {
                        "type": "filled_boulder",
                        "symbol": "=",
                        "walkable": True,
                        "movement_allowed": True,
                        "kind": "已填平巨石路面 (Filled Boulder Path)",
                        "status": "✅ 怪力巨石已推入坑洞填平，当前已变成可通行路面",
                        "boulder_uid": uid,
                        "boulder_pos": {"x": bx, "z": bz, "y": by},
                    }
                    filled_tiles.append((hx, hz))
            active_boulders.append({
                "state": "filled", "uid": uid, "anchor": (bx, bz), "tiles": filled_tiles,
            })
            boulder_summaries.append(
                f"- (X={bx}..{bx+1}, Z={bz-1}..{bz}) [=] 填平巨石路面 ➔ 状态: 怪力巨石已推入坑洞填平，已变成可通行路面 (Walkable)"
            )
        else:
            ground_tiles = []
            for hx in (bx, bx + 1):
                for hz in (bz - 1, bz):
                    boulder_cell_map[(hx, hz)] = {
                        "type": "pushable_boulder",
                        "symbol": "G",
                        "walkable": False,
                        "movement_allowed": False,
                        "movement_hazard": "pushable_boulder",
                        "kind": "怪力巨石 (Pushable Boulder)",
                        "status": "🪨 怪力巨石：未推入坑洞，可使用秘传技怪力 (HM04 Strength) 推动",
                        "boulder_uid": uid,
                        "boulder_pos": {"x": bx, "z": bz, "y": by},
                    }
                    ground_tiles.append((hx, hz))
            active_boulders.append({
                "state": "pushable", "uid": uid, "anchor": (bx, bz), "tiles": ground_tiles,
            })
            boulder_summaries.append(
                f"- (X={bx}, Z={bz}) [G] 怪力巨石 (未推动) ➔ 阻挡通路，需正对使用怪力 (HM04 Strength) 推入坑洞"
            )

    return boulder_cell_map, active_boulders, boulder_summaries


def _resolve_current_works_u16() -> tuple[int, ...] | None:
    try:
        works = progression_state_service.get_works_u16()
        if works:
            return works
        latest = progression_state_service.latest
        if isinstance(latest, dict):
            ew = latest.get("event_work") or {}
            w_list = ew.get("works") or ew.get("works_u16")
            if isinstance(w_list, list):
                return tuple(int(x) for x in w_list)
    except Exception:
        pass
    return None


async def _ensure_works_u16() -> tuple[int, ...] | None:
    w = _resolve_current_works_u16()
    if w is not None:
        return w
    if _runtime_reader is not None:
        try:
            await progression_state_service.sample()
            return progression_state_service.get_works_u16()
        except Exception:
            pass
    return None


def _scan_active_story_triggers(
    provider: Any,
    zone_id: int,
    *,
    works_u16: tuple[int, ...] | list[int] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
) -> tuple[dict[tuple[int, int], dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Universal, zero-hardcoding engine that detects any story trigger gate & NPC roadblock across all 615 zones.

    Combines:
    1. ROM-level ZoneTrigger footprints ([x..x+w-1, z..z+h-1] @ y)
    2. RAM-level EventWork variable states (Works[431])
    3. Spatial adjacency inference to detect whichever NPC is guarding the gate
    4. Optional rich metadata from _STORY_ROADBLOCK_REGISTRY if known, or auto-generated diagnostics

    Returns:
      (trigger_tiles_map, active_roadblocks, impassable_coordinates)
    """
    trigger_tiles_map: dict[tuple[int, int], dict[str, Any]] = {}
    active_roadblocks: list[dict[str, Any]] = []
    impassable_coordinates: list[dict[str, Any]] = []

    if provider is None or not hasattr(provider, "rom"):
        return trigger_tiles_map, active_roadblocks, impassable_coordinates

    zid = int(zone_id)
    try:
        zh = provider.rom.zone(zid)
        ent = provider.rom.entities(zh.entities_id)
        triggers = ent.get("triggers", [])
        npcs_static = ent.get("npcs", [])
    except Exception:
        triggers = []
        npcs_static = []

    roadblocks_in_zone = [rb for rb in _STORY_ROADBLOCK_REGISTRY if rb.get("zone_id") == zid]

    for t in triggers:
        var_id = t.get("var_id")
        exp_val = t.get("expected_value")
        scrid = t.get("script_id")
        tx = t.get("x")
        tz = t.get("z")
        ty = t.get("y", 0)
        w = max(1, int(t.get("width") or 1))
        h = max(1, int(t.get("height") or 1))

        # Check if trigger condition is currently satisfied in live Main RAM
        live_val = None
        is_active = False
        if works_u16 is not None and var_id is not None and 0x4000 < var_id < 0x4000 + len(works_u16):
            live_val = works_u16[var_id - 0x4000]
            is_active = (live_val == exp_val)

        if not is_active:
            continue

        # Active story trigger gate confirmed!
        blocked_tiles = [{"x": tx + dx, "z": tz + dz, "y": ty} for dx in range(w) for dz in range(h)]

        # Automatically infer guarding NPC standing within 3 tiles of this gate
        guardian_npc = None
        for npc in npcs_static:
            nx, nz = npc.get("x"), npc.get("y")
            if nx is not None and nz is not None:
                for bc in blocked_tiles:
                    if abs(nx - bc["x"]) <= 3 and abs(nz - bc["z"]) <= 3:
                        guardian_npc = {
                            "npc_id": npc.get("id"),
                            "sprite_id": npc.get("sprite_id"),
                            "facing": npc.get("direction_raw"),
                            "script_id": npc.get("script_id"),
                            "static_pos": {"x": nx, "z": nz},
                        }
                        break
            if guardian_npc:
                break

        # Check if known registry has curated human-readable annotations
        matching_rb = next((
            rb for rb in roadblocks_in_zone
            if (rb.get("trigger_variable") == f"0x{var_id:04X}" or rb.get("script_id") == scrid)
        ), None)

        if matching_rb:
            name = matching_rb.get("name")
            condition = matching_rb.get("condition")
            target_dest = matching_rb.get("target_destination", "前方受限区域")
            dialogue_clue = matching_rb.get("dialogue_clue", "剧情拦截：踩入将强制退回并触发剧情")
            blocked_dir = matching_rb.get("blocked_direction", "North")
        else:
            npc_desc = f" (驻守NPC Sprite #{guardian_npc['sprite_id']})" if guardian_npc else ""
            name = f"Zone {zid} 剧情拦截防线{npc_desc}"
            condition = f"推进当前主线剧情 (解封条件: Var 0x{var_id:04X} != {exp_val})"
            target_dest = "封锁道路/大门区域"
            dialogue_clue = f"游戏引擎剧情截停踏板 (触发脚本 #{scrid})，踩入强行中断移动并退回"
            blocked_dir = "unknown"

        rb_item = {
            "trigger_id": t.get("id"),
            "script_id": scrid,
            "var_id": f"0x{var_id:04X}",
            "live_value": live_val,
            "expected_value": exp_val,
            "name": name,
            "condition": condition,
            "target_destination": target_dest,
            "dialogue_clue": dialogue_clue,
            "blocked_direction": blocked_dir,
            "blocked_tiles": blocked_tiles,
            "guardian_npc": guardian_npc,
        }
        active_roadblocks.append(rb_item)

        for tile in blocked_tiles:
            key_2d = (tile["x"], tile["z"])
            key_3d = (tile["x"], tile["z"], tile["y"])
            trigger_tiles_map[key_2d] = rb_item
            trigger_tiles_map[key_3d] = rb_item
            impassable_coordinates.append({
                "x": tile["x"],
                "z": tile["z"],
                "y": tile["y"],
                "reason": "story_trigger_intercept",
                "name": name,
                "condition": condition,
                "script_id": scrid,
            })

    # Universal live actor occupancy: Every non-player actor occupying a tile is physically impassable
    if runtime_actors:
        for a in runtime_actors:
            if not isinstance(a, dict) or a.get("is_player"):
                continue
            a_zone = a.get("zone_id") or a.get("effective_zone_id_candidate")
            if a_zone is not None and int(a_zone) != zid:
                continue
            a_grid = a.get("grid") or {}
            ax, az = a_grid.get("x"), a_grid.get("z")
            ay = a_grid.get("y", 0)
            if ax is not None and az is not None:
                impassable_coordinates.append({
                    "x": ax,
                    "z": az,
                    "y": ay,
                    "reason": "live_npc_body_occupancy",
                    "model_id": a.get("model_id"),
                    "facing": a.get("facing"),
                    "script_id": a.get("script_id"),
                })

    return trigger_tiles_map, active_roadblocks, impassable_coordinates

def _radar_cell(
    provider: Any,
    zone_id: int,
    x: int,
    y: int,
    z: int,
    *,
    is_player: bool = False,
    include_events: bool = True,
    fast_preview: bool = False,
    runtime_actors: list[dict[str, Any]] | None = None,
    trainer_sights: dict[tuple[int, int], dict[str, Any]] | None = None,
    trainer_npcs: dict[tuple[int, int], dict[str, Any]] | None = None,
    boulder_cells: dict[tuple[int, int], dict[str, Any]] | None = None,
    story_trigger_tiles: dict[tuple[int, int], dict[str, Any]] | None = None,
    mode: str = "coarse",
    player_world_y: float | None = None,
    is_player_stair_transit: bool = False,
    flag_bytes: bytes | None = None,
) -> dict[str, Any]:
    if flag_bytes is None:
        flag_bytes = _resolve_current_flag_bytes()
    preview = getattr(provider, "preview_surface_at", None) if provider else None
    if fast_preview and callable(preview):
        surf = preview(int(zone_id), int(x), int(z), int(y))
    else:
        surf = provider.surface_at(int(zone_id), int(x), int(z), int(y), allow_unverified_terrain=True) if provider else None
    surfaces = (surf or {}).get("surfaces") or []

    # True Layer Slicing: match the physical surface closest to requested elevation y
    target_world_y = float(y) * 16.0
    matching_surface = None
    min_dist = float("inf")
    for s in surfaces:
        if s.get("tile_class") == 254:
            continue
        rel_y = s.get("height", {}).get("chunk_relative_world_y")
        if rel_y is not None:
            dist = abs(rel_y - target_world_y)
            if dist < min_dist:
                min_dist = dist
                matching_surface = s

    cell = (surf or {}).get("cell") or {}
    if matching_surface is not None:
        top = matching_surface
        material = top.get("material") or cell.get("material") or {}
        tclass = top.get("tile_class")
        flags = top.get("flags")
        static_blocked = bool(top.get("static_blocked"))
        height_meta = top.get("height") or {}
    elif cell:
        top = cell
        material = cell.get("material") if isinstance(cell.get("material"), dict) else {}
        tclass = cell.get("tile_class")
        flags = cell.get("flags")
        static_blocked = bool(cell.get("static_blocked"))
        height_meta = (surfaces[0].get("height") if surfaces else {}) or {}
    else:
        valid_surfaces = [s for s in surfaces if s.get("tile_class") != 254]
        top = valid_surfaces[-1] if valid_surfaces else (surfaces[-1] if surfaces else {})
        material = top.get("material") or {}
        tclass = top.get("tile_class")
        flags = top.get("flags")
        static_blocked = bool(top.get("static_blocked")) if surfaces else True
        height_meta = top.get("height") or {}
    from ..world.tile_semantics import SLOPE_TABLE_FX32

    is_water_edge = bool(material.get("kind") == "water_edge" or tclass in (0x41, 0x44))
    slope_index = int(height_meta.get("slope_index") or 0)
    relative_y = height_meta.get("chunk_relative_world_y")
    blocked_directions = list(cell.get("blocked_directions") or material.get("blocked_directions") or [])
    ledge_direction = cell.get("ledge_direction") or material.get("ledge_direction")

    # Layer slicing: evaluate vertical height difference relative to queried floor y
    target_world_y = float(y) * 16.0
    surface_grid_y = int(round(relative_y / 16.0)) if relative_y is not None else int(y)
    height_delta = surface_grid_y - int(y)

    # A cell can have a valid walkable surface, but only on another floor. It
    # must not be rendered as a current-floor wall or as a current-floor road.
    alternate_layer_available = False
    alternate_layer_y = None
    selected_is_other_layer = bool(
        matching_surface is not None
        and relative_y is not None
        and slope_index == 0
        and abs(float(relative_y) - target_world_y) > 8.0
    )
    if selected_is_other_layer and not static_blocked and tclass not in (0x3C, 0x3D, 0x3F, 0x41, 0x44, 60):
        alternate_layer_available = True
        alternate_layer_y = int(round(float(relative_y) / 16.0))

    # If the selected surface already belongs to the requested layer and is
    # walkable (including catwalk), do not downgrade it to an alternate-layer
    # marker merely because another stacked surface also exists.
    scan_alternates = selected_is_other_layer or matching_surface is None or static_blocked
    for other_surface in surfaces if scan_alternates else ():
        if other_surface is matching_surface or other_surface.get("tile_class") == 254:
            continue
        other_height = (other_surface.get("height") or {}).get("chunk_relative_world_y")
        if other_height is None or abs(float(other_height) - target_world_y) <= 8.0:
            continue
        other_tc = other_surface.get("tile_class")
        other_mat = other_surface.get("material") or {}
        other_static_blocked = bool(other_surface.get("static_blocked"))
        other_kind = str(other_mat.get("kind") or "")
        if (not other_static_blocked
                and other_tc not in (0x3C, 0x3D, 0x3F, 0x41, 0x44, 60)
                and other_kind not in {"water", "water_edge"}):
            alternate_layer_available = True
            alternate_layer_y = int(round(float(other_height) / 16.0))
            break

    is_vertical_cliff = bool(
        not alternate_layer_available
        and abs(height_delta) > 1
        and slope_index == 0
        and not ledge_direction
    )
    if is_vertical_cliff:
        static_blocked = True

    # Narrow catwalk/rope bridge semantics. SWAN identifies TileClass 0xBE
    # (catwalk body) and 0xBF (entry point); side-drop/fall behavior is
    # handled by FieldPlayerGrid_CheckOnCatwalk/CalcCatwalkFallDir.
    catwalk_meta = None
    is_catwalk = bool(tclass in (0xBE, 0xBF) or material.get("kind") in {"catwalk", "catwalk_entry"})
    if is_catwalk:
        def _neighbor_is_catwalk(nx: int, nz: int) -> bool:
            try:
                preview_fn = getattr(provider, "preview_surface_at", None)
                probe = preview_fn(int(zone_id), nx, nz, int(y)) if callable(preview_fn) else provider.surface_at(int(zone_id), nx, nz, int(y), allow_unverified_terrain=True)
                probe_cell = (probe or {}).get("cell") or {}
                probe_tc = probe_cell.get("tile_class")
                return probe_tc in (0xBE, 0xBF) or (probe_cell.get("material") or {}).get("kind") in {"catwalk", "catwalk_entry"}
            except Exception:
                return False
        east = _neighbor_is_catwalk(int(x) + 1, int(z))
        west = _neighbor_is_catwalk(int(x) - 1, int(z))
        south = _neighbor_is_catwalk(int(x), int(z) + 1)
        north = _neighbor_is_catwalk(int(x), int(z) - 1)
        if east or west:
            catwalk_axis = "east_west"
            allowed_axis = ["left", "right"]
            side_drop = ["up", "down"]
            side_text = "南北两侧为坠落边缘"
        elif north or south:
            catwalk_axis = "north_south"
            allowed_axis = ["up", "down"]
            side_drop = ["left", "right"]
            side_text = "东西两侧为坠落边缘"
        else:
            catwalk_axis = "unknown"
            allowed_axis = ["up", "down", "left", "right"]
            side_drop = []
            side_text = "桥体方向待相邻格确认"
        for drop_dir in side_drop:
            if drop_dir not in blocked_directions:
                blocked_directions.append(drop_dir)
        side_drop_landings = []
        if provider is not None:
            dir_map = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}
            for drop_dir in side_drop:
                ddx, ddz = dir_map.get(drop_dir, (0, 0))
                lx, lz = int(x) + ddx, int(z) + ddz
                g_probe = provider.surface_at(int(zone_id), lx, lz, 0, allow_unverified_terrain=True)
                for gs in (g_probe or {}).get("surfaces") or []:
                    gmat = gs.get("material") or {}
                    gcol = gs.get("collision") or {}
                    gh = gs.get("height") or {}
                    if not gcol.get("static_blocked") and gmat.get("kind") not in ("obstacle", "water"):
                        gy = gh.get("chunk_relative_world_y") or 0.0
                        side_drop_landings.append({
                            "direction": drop_dir,
                            "landing_tile": {"x": lx, "z": lz, "floor_y": 0, "world_y": gy},
                            "height_drop": round((relative_y or 32.0) - gy, 1),
                            "landing_material": gmat.get("kind", "ground"),
                        })
                        break
        catwalk_meta = {
            "type": "catwalk_entry" if tclass == 0xBF or material.get("kind") == "catwalk_entry" else "catwalk_body",
            "symbol": "╪" if tclass == 0xBF or material.get("kind") == "catwalk_entry" else "╫",
            "axis": catwalk_axis,
            "allowed_exits": allowed_axis,
            "side_drop_directions": side_drop,
            "side_drop_landings": side_drop_landings,
            "fall_risk": "high",
            "dwell_monitoring": "required",
            "dwell_limit_status": "unverified_runtime_threshold",
            "status": f"⚠️ 独木桥/窄桥：主轴沿 {allowed_axis} 通行；{side_text}；侧向可跳下至下层平地 (标高 0.0)；停留平衡计时需实时 RAM 验证",
        }

    # Dynamic Staircase & Handrail Corridor Analysis
    slope_meta = None
    if slope_index > 0 and slope_index * 3 + 2 < len(SLOPE_TABLE_FX32):
        nx = SLOPE_TABLE_FX32[slope_index * 3]
        ny = SLOPE_TABLE_FX32[slope_index * 3 + 1]
        nz = SLOPE_TABLE_FX32[slope_index * 3 + 2]
        delta_h = (relative_y if relative_y is not None else 0.0) - (float(y) * 16.0)

        if abs(nx) >= abs(nz):
            axis = "east_west"
            stair_allowed = ["left", "right"]
            stair_blocked = ["up", "down"]
            handrail_info = "南北两侧为楼梯护栏扶手，禁止侧向脱轨 (North/South Handrails)"
            rising_zh = "东 (East)" if nx < 0 else "西 (West)"
        else:
            axis = "north_south"
            stair_allowed = ["up", "down"]
            stair_blocked = ["left", "right"]
            handrail_info = "东西两侧为楼梯护栏扶手，禁止侧向脱轨 (East/West Handrails)"
            rising_zh = "南 (South)" if nz < 0 else "北 (North)"

        ref_player_h = player_world_y if player_world_y is not None else target_world_y
        diff_to_player = (relative_y if relative_y is not None else ref_player_h) - ref_player_h

        if is_player:
            stair_sym = "P"
            stair_desc = f"主角所在台阶 (标高 {relative_y:.1f})"
        elif is_player_stair_transit:
            stair_sym = "p"
            stair_desc = f"主角跨层攀爬投影 (标高 {ref_player_h:.1f})"
        elif diff_to_player > 4.0:
            stair_sym = "▲"
            stair_desc = f"相对上行阶梯 (高于主角当前高度 {diff_to_player:+.1f})"
        elif diff_to_player < -4.0:
            stair_sym = "▼"
            stair_desc = f"相对下行阶梯 (低于主角当前高度 {diff_to_player:+.1f})"
        else:
            stair_sym = "."
            stair_desc = f"同高度阶梯踏面 (与主角同高平整，走向: {rising_zh})"

        for d in stair_blocked:
            if d not in blocked_directions:
                blocked_directions.append(d)

        slope_meta = {
            "type": "stairs" if slope_index == 11 else "slope",
            "symbol": stair_sym,
            "axis": axis,
            "rising_direction": rising_zh,
            "allowed_exits": stair_allowed,
            "blocked_exits": stair_blocked,
            "handrail_constraint": handrail_info,
            "description": stair_desc,
            "slope_index": slope_index,
            "relative_height": relative_y,
            "surface_grid_y": surface_grid_y,
        }

    # A ledge/directional barrier may carry the ROM collision bit while still
    # being traversable through a constrained edge.  Report it as a
    # walkable-candidate rather than a dead obstacle; the directional payload
    # remains the authority for which exit is legal.
    directional_surface = bool(ledge_direction or blocked_directions or slope_meta)
    terrain_walkable = bool(
        (surf or {}).get("walkable", False)
        and (not static_blocked or directional_surface)
        and not is_vertical_cliff
        and not alternate_layer_available
    )
    movement_allowed = bool(
        (surf or {}).get("movement_allowed", False)
        and not is_vertical_cliff
        and not alternate_layer_available
    )
    if alternate_layer_available:
        # A valid surface exists, but it belongs to another elevation plane;
        # it is not executable from this floor. The renderer may show ↕/other
        # layer metadata, while the pathfinder must treat this tile blocked.
        static_blocked = True
    overlays = []
    if include_events and provider is not None and hasattr(provider, "event_overlay_at"):
        try:
            raw_overlays = list(provider.event_overlay_at(int(zone_id), int(x), int(z)))
            if y is not None:
                target_world_y = float(y) * 16.0
                overlays = []
                for item in raw_overlays:
                    h_y = item.get("height_y")
                    if h_y is not None and abs(float(h_y) - target_world_y) > 8.0:
                        continue
                    overlays.append(item)
            else:
                overlays = raw_overlays
        except Exception:
            overlays = []
    _mark_moved_static_npcs(overlays, runtime_actors, int(zone_id))
    live_overlays = _live_actor_overlays_at(runtime_actors, int(zone_id), int(x), int(y), int(z))
    for live_overlay in live_overlays:
        duplicate = any(
            item.get("kind") == "npc" and item.get("presence") == "runtime_present"
            and item.get("script_id") == live_overlay.get("script_id")
            for item in overlays
        )
        if not duplicate:
            overlays.append(live_overlay)
    # Enrich static entities without treating ROM spawn coordinates as live
    # occupancy. A moving NPC gets a stable static id plus role evidence; its
    # runtime position is attached separately when ActorSystem sampling binds it.
    for overlay in overlays:
        if overlay.get("kind") == "npc":
            sc_val = overlay.get("script_id")
            if sc_val is not None and flag_bytes:
                df = resolve_trainer_defeat_flag(sc_val)
                if df is not None and is_event_flag_set(df, flag_bytes):
                    overlay["defeat_status"] = "defeated"
                    overlay["is_defeated"] = True
                    overlay["active_sight"] = False
            is_gate, gate_info = _is_story_gate_npc(
                int(zone_id), overlay.get("script_id"),
                overlay.get("flag_id", overlay.get("spawn_flag")),
                overlay.get("sprite_id") or overlay.get("model_id"),
            )
            if not is_gate and int(zone_id) == 446 and overlay.get("x") == 159 and overlay.get("z") == 645:
                is_gate, gate_info = _is_story_gate_npc(446, 4, 735, 64)
            if is_gate and gate_info:
                target_tile = gate_info.get("npc_tile")
                curr_x = overlay.get("x")
                curr_z = overlay.get("z")
                pos = overlay.get("position") if isinstance(overlay.get("position"), dict) else {}
                curr_x = pos.get("x", overlay.get("x"))
                curr_z = pos.get("z", overlay.get("z"))
                if target_tile and (curr_x != target_tile.get("x") or curr_z != target_tile.get("z")):
                    if overlay.get("presence") != "runtime_present":
                        is_gate = False
            if is_gate and gate_info:
                overlay["is_story_gate"] = True
                overlay["story_gate"] = gate_info
                overlay["kind"] = "story_gate_npc"
                overlay["symbol"] = "N"
                overlay["role"] = {
                    "role": "story_gate", "name": gate_info["name"], "confidence": "verified_registry",
                    "service": None, "actions": ["talk"],
                    "evidence": {"flag_id": gate_info["flag_id"], "script_id": gate_info["script_id"], "source": "story_gate_registry"},
                }
                overlay["interaction"] = {
                    "available": True, "action": "talk", "target_type": "story_gate_npc",
                    "story_gate": True, "requires_adjacent": True,
                    "description": f"{gate_info['name']}：阻挡前往【{gate_info['target_destination']}】；解锁条件: {gate_info['condition']}",
                    "execution": "runtime_actor_and_facing_required",
                }
            else:
                overlay["role"] = _npc_role_profile(overlay, int(zone_id))
            if overlay["role"].get("semantic_kind") == "OVERWORLD_ITEM":
                overlay["kind"] = "item"
                overlay["symbol"] = "I"
                overlay["interaction"] = {
                    "available": True, "action": "pickup", "target_type": "item_ball",
                    "requires_adjacent": True, "execution": "item_result_and_flag_verification_required",
                }
            else:
                overlay["interaction"] = {
                    "available": True, "action": "talk", "target_type": "npc",
                    "requires_adjacent": True, "execution": "runtime_actor_and_facing_required",
                }
        elif overlay.get("kind") in ("furniture", "signpost", "hidden_item", "trash_can"):
            script_id = overlay.get("script_id")
            arg3_raw = overlay.get("arg3_raw")
            zid = int(zone_id)
            is_sign = bool(overlay.get("kind") == "signpost" or arg3_raw == 6 or (zid == 444 and script_id == 1))
            is_trash = bool(overlay.get("kind") == "trash_can" or arg3_raw == 1)
            is_hidden = bool(overlay.get("kind") == "hidden_item" or overlay.get("is_hidden_item") or arg3_raw == 4)
            if is_sign:
                center_x = overlay.get("position", {}).get("x", x)
                center_z = overlay.get("position", {}).get("z", z)
                sign_type, sign_category = _classify_signpost(provider, int(zone_id), int(center_x), int(center_z))
                stand_tile = {
                    "x": int(center_x), "y": int(y), "z": int(center_z) + 1,
                    "approach_direction": "down", "facing": "up",
                }
                overlay["kind"] = "signpost"
                overlay["symbol"] = "S"
                overlay["role"] = {
                    "role": sign_type, "name": sign_category, "confidence": "verified_registry",
                    "evidence": {"arg3": 6, "script_id": script_id, "source": "ROM furniture signpost"},
                }
                overlay["interaction"] = {
                    "available": True, "action": "read_signpost", "target_type": "signpost",
                    "sign_type": sign_type, "sign_category": sign_category,
                    "center_tile": {"x": int(center_x), "y": int(y), "z": int(center_z)},
                    "span_tiles": [{"x": int(center_x) + offset, "z": int(center_z)} for offset in (-1, 0, 1)],
                    "stand_tile": stand_tile, "stand_tiles": [stand_tile],
                    "interaction_rule": "front_middle_only",
                    "description": f"{sign_category}：必须站在正面中间 (X={center_x}, Z={center_z + 1}) 面向上方 (North) 按 A 读取；可读取地名信息存入 AI 空间记忆",
                    "memory_grounding": "reads location/route/building text into agent spatial memory when Zone is unresolved",
                    "requires_adjacent": True, "execution": "stand_at_front_middle_and_press_a",
                }
            elif is_trash:
                overlay["kind"] = "trash_can"
                overlay["symbol"] = "K"
                overlay["role"] = {"role": "trash_can", "name": "垃圾桶", "confidence": "verified_registry"}
                overlay["interaction"] = {
                    "available": True, "action": "inspect_trash_can", "target_type": "trash_can",
                    "requires_adjacent": True,
                    "description": "垃圾桶 (Trash Can)：站在相邻格按 A 调查，可能藏有隐藏道具或机关",
                }
            elif is_hidden:
                overlay["kind"] = "hidden_item"
                overlay["symbol"] = "h"
                overlay["is_hidden_item"] = True
                overlay["physical_obstacle"] = False
                item_name = "地面埋藏隐藏道具"
                try:
                    from ..world.item_catalog import RomItemCatalog
                    resolved = RomItemCatalog().resolve_hidden_item(int(script_id or 0))
                    if resolved and resolved.get("name_zh"):
                        item_name = f"隐藏道具: {resolved['name_zh']}"
                except Exception:
                    pass
                overlay["role"] = {"role": "hidden_item", "name": item_name, "confidence": "verified_registry"}
                overlay["interaction"] = {
                    "available": True, "action": "pickup_hidden_item", "target_type": "hidden_item",
                    "requires_adjacent": True,
                    "can_step_on": True,
                    "physical_obstacle": False,
                    "description": f"{item_name}：无实体模型碰撞，可直接踩踏穿行；站在相邻格面朝此格按 A 拾取",
                }
            elif script_id == 2108:
                overlay["role"] = {"role": "pc_terminal", "service": "pc_storage", "confidence": "candidate",
                                     "evidence": {"script_id": 2108, "source": "project_registry"}}
                overlay["interaction"] = {"available": True, "action": "open_pc", "target_type": "pc_terminal",
                                           "requires_adjacent": True, "execution": "pc_menu_runtime_verification_required"}
            else:
                overlay["interaction"] = {"available": True, "action": "inspect", "target_type": "furniture",
                                           "requires_adjacent": True, "execution": "runtime_dialogue_verification_required"}
    has_warp = any(item.get("kind") == "warp" for item in overlays)
    has_portal_warp = any(
        item.get("kind") == "warp"
        and not (item.get("is_doorstep") is True and not item.get("is_portal_doorway"))
        for item in overlays
    )
    if has_warp:
        warp = next((item for item in overlays if item.get("kind") == "warp"), {})
        target_raw = warp.get("target_zone_id_candidate")
        target_zone_id = int(target_raw) if target_raw is not None else None
        target_name = _resolve_zone_name(target_zone_id, provider) if target_zone_id is not None else "未知区域"
        facility = _classify_facility(target_zone_id, target_name)
        warp["target_zone_id"] = target_zone_id
        warp["target_zone_name"] = target_name
        warp["facility_type"] = facility
        warp["is_pokemon_center"] = (facility == "pokemon_center")
    scene_matches_zone = bool(runtime_actors and any(
        a.get("zone_id") == int(zone_id) or a.get("effective_zone_id_candidate") == int(zone_id)
        for a in runtime_actors if isinstance(a, dict)
    ))
    if runtime_actors is not None and scene_matches_zone:
        has_npc = any(item.get("kind") == "npc" and item.get("presence") == "runtime_present" for item in overlays)
        has_item = any(item.get("kind") == "item" and item.get("presence") == "runtime_present" for item in overlays)
    else:
        has_npc = any(item.get("kind") == "npc" and item.get("presence") != "static_spawn_not_current" for item in overlays)
        has_item = any(item.get("kind") == "item" and item.get("presence") != "static_spawn_not_current" for item in overlays)
    gate_trigger_info = None
    zid_int = int(zone_id)
    # 1. Live EventWork-Evaluated Story Triggers (100% Dynamic from RAM)
    if story_trigger_tiles:
        gate_trigger_info = story_trigger_tiles.get((int(x), int(z), int(y))) or story_trigger_tiles.get((int(x), int(z)))

    # 2. Universal Flank Gate Trigger Resolution from _STORY_ROADBLOCK_REGISTRY fallback
    if not gate_trigger_info:
        for rb in _STORY_ROADBLOCK_REGISTRY:
            if rb["zone_id"] != zid_int:
                continue
            trig_var = rb.get("trigger_variable")
            if trig_var:
                try:
                    var_int = int(trig_var, 16) if isinstance(trig_var, str) else int(trig_var)
                    works = _resolve_current_works_u16()
                    if works and 0x4000 <= var_int < 0x4000 + len(works) and rb.get("blocking_values") is not None:
                        live_val = works[var_int - 0x4000]
                        if live_val not in rb["blocking_values"]:
                            continue
                except Exception:
                    pass
            npc_t = rb.get("npc_tile")
            if not npc_t:
                continue
            nx, nz = int(npc_t["x"]), int(npc_t["z"])
            vecs = _npc_body_vectors(rb.get("facing_raw", 1))
            left_c = (nx + vecs["left_hand"][0], nz + vecs["left_hand"][1])
            right_c = (nx + vecs["right_hand"][0], nz + vecs["right_hand"][1])
            if (int(x), int(z)) in (left_c, right_c):
                flank_name = "左侧" if (int(x), int(z)) == left_c else "右侧"
                gate_trigger_info = {
                    "name": f"{rb['name']}{flank_name}拦截触发线",
                    "blocked_direction": rb.get("blocked_direction", "North"),
                    "parent_npc_tile": {"x": nx, "z": nz},
                    "rebound_tile": {"x": nx + vecs["front"][0], "z": nz + vecs["front"][1]},
                    "condition": rb.get("condition"),
                    "flag_id": rb.get("flag_id"),
                }
                break

    # Hard NPC obstacle guarantee: Any live non-player NPC actor occupying this tile blocks passage
    if has_npc and not is_player:
        terrain_walkable = False
        movement_allowed = False
    if runtime_actors is not None and scene_matches_zone:
        has_story_gate = any(item.get("is_story_gate") and item.get("presence") == "runtime_present" for item in overlays)
    else:
        has_story_gate = any(item.get("is_story_gate") and item.get("presence") != "static_spawn_not_current" for item in overlays)
    has_signpost = any(item.get("kind") == "signpost" for item in overlays)
    has_trash_can = any(item.get("kind") == "trash_can" for item in overlays)
    has_hidden_item = any(item.get("kind") == "hidden_item" or item.get("is_hidden_item") for item in overlays)
    has_object = any(item.get("kind") == "furniture" and not item.get("is_hidden_item") and item.get("kind") != "hidden_item" for item in overlays)
    terrain_profile = _terrain_interaction_profile(material)
    boulder_info = boulder_cells.get((int(x), int(z))) if boulder_cells else None
    if boulder_info is not None:
        if boulder_info["type"] == "filled_boulder":
            terrain_walkable = True
            movement_allowed = True
            static_blocked = False
    elif tclass == 0x1D:
        terrain_walkable = False
        movement_allowed = False
        static_blocked = True

    is_real_stair = bool(
        staircase_corridor_service.get_corridor_at(int(zone_id), int(x), int(z)) is not None
    )
    kind, symbol, status = _classify_surface_meta(
        tclass, flags, static_blocked, terrain_walkable,
        has_warp=has_portal_warp, has_npc=has_npc,
        ledge_direction=ledge_direction, material=material,
        slope_index=slope_index,
        mode=mode,
        is_vertical_cliff=is_vertical_cliff,
        surface_grid_y=surface_grid_y,
        height_delta=height_delta,
        slope_meta=slope_meta,
        catwalk_meta=catwalk_meta,
        alternate_layer_available=alternate_layer_available,
        alternate_layer_y=alternate_layer_y,
        is_stair_corridor=is_real_stair,
    )

    if boulder_info is not None and not has_warp:
        if boulder_info["type"] == "filled_boulder":
            symbol = "="
            kind = boulder_info["kind"]
            status = boulder_info["status"]
            terrain_walkable = True
            movement_allowed = True
        elif boulder_info["type"] == "pushable_boulder":
            symbol = "G"
            kind = boulder_info["kind"]
            status = boulder_info["status"]
            terrain_walkable = False
            movement_allowed = False
    elif tclass == 0x1D and not has_warp:
        symbol = "U"
        kind = "未填平巨石坑洞 (Empty Boulder Hole)"
        status = "🕳️ 未填平坑洞：深坑不可通行；需推入怪力巨石填平"
        terrain_walkable = False
        movement_allowed = False
    if has_item and not (has_warp or has_npc) and terrain_profile is None:
        kind, symbol, status = "地面道具/道具球 (Item Ball)", "I", "✅ 可从相邻格拾取；道具与旗标仍需运行时确认"
    if (has_story_gate or gate_trigger_info) and not (has_warp) and terrain_profile is None:
        if gate_trigger_info:
            # Trigger/approach tiles are the actual invisible blocking cells.
            kind = f"剧情拦截触发线 ({gate_trigger_info.get('name', 'Story Trigger')})"
            symbol = "!"
            status = f"🚫 剧情拦截触发线：踩入强行截停并退回 (条件: {gate_trigger_info.get('condition')})"
            terrain_walkable = False
            movement_allowed = False
        elif has_story_gate:
            # The actor itself remains an NPC marker. Its occupancy is still a
            # hard navigation block, but the radar must not confuse the person
            # with the invisible trigger line.
            if runtime_actors is not None and scene_matches_zone:
                gate_overlay = next((item for item in overlays if item.get("is_story_gate") and item.get("presence") == "runtime_present"), {})
            else:
                gate_overlay = next((item for item in overlays if item.get("is_story_gate") and item.get("presence") != "static_spawn_not_current"), {})
            gate_meta = gate_overlay.get("story_gate") or {}
            kind = f"剧情封路 NPC ({gate_meta.get('name', 'Story Gate NPC')})"
            symbol = "N"
            status = f"🚫 NPC 实体占位并阻挡通行；封锁方向: {gate_meta.get('blocked_direction', 'unknown')}；受旗标 {gate_meta.get('flag_id')} 控制"
            terrain_walkable = False
            movement_allowed = False
    # Active Trainer NPC & Line of Sight detection
    has_trainer = False
    trainer_entry = None
    if trainer_npcs and (int(x), int(z)) in trainer_npcs:
        trainer_entry = trainer_npcs[(int(x), int(z))]
        has_trainer = True
    elif has_npc:
        for ov in overlays:
            if ov.get("kind") == "npc":
                sr = ov.get("sight_raw")
                sc = ov.get("script_id")
                is_def = bool(ov.get("defeat_status") == "defeated" or ov.get("is_defeated") is True)
                if not is_def and flag_bytes and sc is not None:
                    df = resolve_trainer_defeat_flag(sc)
                    if df is not None and is_event_flag_set(df, flag_bytes):
                        is_def = True
                if sr is not None and int(sr) > 0 and not is_def:
                    has_trainer = True
                    trainer_entry = {
                        "trainer_id": ov.get("npc_id"),
                        "sight_range": int(sr),
                        "facing": ov.get("facing"),
                        "symbol": "T",
                    }
                    break

    sight_info = trainer_sights.get((int(x), int(z))) if trainer_sights else None

    if has_trainer and not has_warp and terrain_profile is None and not (has_story_gate or gate_trigger_info):
        sr = trainer_entry.get("sight_range", 1) if trainer_entry else 1
        fn = trainer_entry.get("facing", "unknown") if trainer_entry else "unknown"
        kind = f"对战训练家 (Trainer NPC, 视线: {sr}格)"
        symbol = "T"
        status = f"⚔️ 对战训练家；视线范围: {sr} 格 (朝向: {fn})；踏入视线将强制进入战斗"
        terrain_walkable = False
        movement_allowed = False
    elif sight_info is not None and not (has_warp or has_npc or has_item or has_story_gate or gate_trigger_info or has_signpost or has_trash_can or has_object):
        symbol = sight_info.get("symbol", "^")
        kind = f"训练家视线警戒线 ({sight_info.get('facing')} 第 {sight_info.get('step')}/{sight_info.get('max_distance')} 格)"
        status = f"⚠️ 踏入将强制触发训练家对战！距训练家 {sight_info.get('step')} 格 (总视线: {sight_info.get('max_distance')} 格)"

    if has_signpost and not (has_warp or has_npc or has_item or has_story_gate) and terrain_profile is None:
        sign_overlay = next((item for item in overlays if item.get("kind") == "signpost"), {})
        sign_inter = sign_overlay.get("interaction") or {}
        kind = sign_inter.get("sign_category") or "标识牌/路牌 (Signpost)"
        symbol = "S"
        status = "📜 标识牌/路牌；仅能从正面中间 (Z+1) 面向上方读取 (read from front middle only)"
    if has_trash_can and not (has_warp or has_npc or has_item or has_story_gate or has_signpost) and terrain_profile is None:
        kind, symbol, status = "垃圾桶 (Trash Can)", "K", "🗑️ 垃圾桶；可从相邻格调查 (Inspectable)"
    if has_hidden_item and not (has_warp or has_npc or has_item or has_story_gate or has_signpost or has_trash_can or has_object):
        h_overlay = next((item for item in overlays if item.get("kind") == "hidden_item" or item.get("is_hidden_item")), {})
        h_role = h_overlay.get("role") if isinstance(h_overlay.get("role"), dict) else {}
        h_name = h_role.get("name") or "地面埋藏隐藏道具"
        if terrain_profile is None:
            symbol = "h"
            kind = h_name
            status = f"🌱 可直接通行踩踏；{h_name}（无物理实体碰撞，可从相邻格面向调查拾取）"
        else:
            status = f"{status} [含{h_name}，无碰撞可自由通行]"
    if has_object and not (has_warp or has_npc or has_item or has_signpost or has_story_gate or has_trash_can or has_hidden_item) and terrain_profile is None:
        object_overlay = next((item for item in overlays if item.get("kind") == "furniture"), {})
        object_role = object_overlay.get("role") if isinstance(object_overlay.get("role"), dict) else {}
        if object_role.get("role") == "pc_terminal":
            kind, symbol, status = "宝可梦电脑 (PC Terminal)", "C", "❌ 不可进入；✅ 可从相邻格交互"
        else:
            kind, symbol, status = "可交互家具/物件 (Interactive Object)", "O", "🧰 ROM 交互对象候选"
    underlying_symbol = symbol
    underlying_kind = kind
    underlying_status = status
    if is_player:
        if has_warp:
            warp_ent = next((item for item in overlays if item.get("kind") == "warp"), {})
            t_name = warp_ent.get("target_zone_name") or "外部区域"
            symbol = "P"
            kind = f"主角踩踏传送门格 [D] (通往: {t_name})"
            status = f"🚪 主角当前位于传送门出入口 (Warp) 踩踏点；目标区域: {t_name}"
        else:
            symbol, kind, status = "P", "主角所在位置 (Player)", "📍 主角位置"
    all_directions = ("up", "down", "left", "right")
    if ledge_direction:
        allowed_exits = [str(ledge_direction)]
    elif terrain_walkable:
        allowed_exits = [direction for direction in all_directions if direction not in blocked_directions]
    else:
        allowed_exits = []
    interaction = None
    if terrain_profile is not None:
        interaction = {
            **terrain_profile,
            "available": True,
            "blocked_tile": not terrain_walkable,
            "stand_tiles": ([] if fast_preview else _interaction_stand_tiles(provider, int(zone_id), int(x), int(y), int(z))),
            "source": "ROM tile semantic",
        }
    elif has_npc:
        npc = next((item for item in overlays if item.get("kind") == "npc" and item.get("presence") != "static_spawn_not_current"), {})
        interaction = dict(npc.get("interaction") or {})
        interaction["stand_tiles"] = ([] if fast_preview else _interaction_stand_tiles(provider, int(zone_id), int(x), int(y), int(z)))
        interaction["npc_id"] = npc.get("npc_id")
        interaction["static_entity_id"] = npc.get("static_entity_id")
        interaction["role"] = npc.get("role")
    elif has_object:
        obj = next((item for item in overlays if item.get("kind") == "furniture"), {})
        interaction = dict(obj.get("interaction") or {})
        interaction["stand_tiles"] = ([] if fast_preview else _interaction_stand_tiles(provider, int(zone_id), int(x), int(y), int(z)))
        interaction["furniture_id"] = obj.get("furniture_id")
    elif has_item:
        item = next((item for item in overlays if item.get("kind") == "item"), {})
        interaction = dict(item.get("interaction") or {})
        interaction["stand_tiles"] = ([] if fast_preview else _interaction_stand_tiles(provider, int(zone_id), int(x), int(y), int(z)))
        interaction["item_id"] = item.get("npc_id")
    elif has_story_gate:
        if runtime_actors is not None and scene_matches_zone:
            gate_item = next((item for item in overlays if item.get("is_story_gate") and item.get("presence") == "runtime_present"), {})
        else:
            gate_item = next((item for item in overlays if item.get("is_story_gate") and item.get("presence") != "static_spawn_not_current"), {})
        interaction = gate_item.get("interaction")
    elif has_signpost:
        sign = next((item for item in overlays if item.get("kind") == "signpost"), {})
        interaction = sign.get("interaction")
    elif has_trash_can:
        trash = next((item for item in overlays if item.get("kind") == "trash_can"), {})
        interaction = trash.get("interaction")
    elif is_water_edge:
        interaction = {
            "available": True, "action": "surf", "target_type": "water_edge",
            "requires_ability": "HM03_Surf",
            "description": "水岸/河堤跃迁格：陆地上正对按 A 可使用冲浪跳入水中，水面上可跳跃上岸",
        }
    elif has_hidden_item:
        hitem = next((item for item in overlays if item.get("kind") == "hidden_item" or item.get("is_hidden_item")), {})
        interaction = hitem.get("interaction")
    # Explicit NPC identity/blocking contract for AI consumers.
    npc_overlay = next((item for item in overlays if item.get("kind") in {"npc", "story_gate"} or item.get("is_story_gate")), None)
    npc_runtime = npc_overlay.get("runtime") if isinstance(npc_overlay, dict) and isinstance(npc_overlay.get("runtime"), dict) else {}
    npc_gate = npc_overlay.get("story_gate") if isinstance(npc_overlay, dict) and isinstance(npc_overlay.get("story_gate"), dict) else {}
    npc_facing_raw = npc_runtime.get("face_dir_raw", npc_runtime.get("facing_id", npc_overlay.get("facing_id") if isinstance(npc_overlay, dict) else None))
    npc_facing = npc_runtime.get("facing") if npc_runtime else None
    npc_identity = None
    npc_blocking = None
    if npc_overlay is not None:
        npc_identity = {
            "npc_id": npc_overlay.get("npc_id"),
            "record_index": npc_overlay.get("record_index"),
            "static_entity_id": npc_overlay.get("static_entity_id"),
            "model_id": npc_overlay.get("model_id", npc_overlay.get("sprite_id")),
            "sprite_id": npc_overlay.get("sprite_id"),
            "script_id": npc_overlay.get("script_id"),
            "rom_flag_id": npc_overlay.get("flag_id"),
            "story_gate_flag_id": npc_gate.get("flag_id"),
            "flag_id": npc_overlay.get("flag_id", npc_gate.get("flag_id")),
            "runtime_actor_uid": npc_runtime.get("actor_uid"),
            "runtime_slot": npc_runtime.get("slot"),
            "runtime_address": npc_overlay.get("runtime_address", npc_runtime.get("runtime_address", npc_runtime.get("address"))),
            "grid": npc_runtime.get("grid") or npc_overlay.get("position"),
            "default_facing_raw": npc_overlay.get("direction_raw", npc_overlay.get("facing_id")),
            "current_facing_raw": npc_runtime.get("face_dir_raw", npc_overlay.get("facing_id")),
            "current_facing": npc_facing,
            "movement_id": npc_overlay.get("movement_id"),
            "movement_state": npc_runtime.get("movement_state"),
            "presence": npc_overlay.get("presence"),
            "status": "runtime_present" if npc_overlay.get("presence") == "runtime_present" else npc_overlay.get("presence"),
        }
        trigger_tiles = npc_gate.get("intercept_trigger_tiles") or []
        npc_blocking = {
            "is_blocking": bool(not terrain_walkable or has_story_gate),
            "block_kind": "story_gate_npc" if has_story_gate else "npc_occupancy",
            "blocked_direction": npc_gate.get("blocked_direction"),
            "passable_direction": npc_gate.get("passable_direction"),
            "blocked_tiles": trigger_tiles,
            "body_frame": {
                "front": None,
                "back": None,
                "left_hand": None,
                "right_hand": None,
                "note": "Calculated from current_facing; directional trigger tiles are authoritative.",
            },
        }
        # Body-relative coordinates use Gen-5 grid convention: North=Z- / South=Z+.
        body_grid = npc_identity.get("grid") if isinstance(npc_identity, dict) else None
        try:
            bx, bz, fr = int(body_grid.get("x")), int(body_grid.get("z")), int(npc_identity.get("current_facing_raw"))
            vectors = {
                0: {"front": (0, -1), "back": (0, 1), "left_hand": (-1, 0), "right_hand": (1, 0)},
                1: {"front": (0, 1), "back": (0, -1), "left_hand": (1, 0), "right_hand": (-1, 0)},
                2: {"front": (-1, 0), "back": (1, 0), "left_hand": (0, 1), "right_hand": (0, -1)},
                3: {"front": (1, 0), "back": (-1, 0), "left_hand": (0, -1), "right_hand": (0, 1)},
            }.get(fr)
            if vectors:
                npc_blocking["body_frame"] = {
                    key: {"x": bx + dx, "z": bz + dz} for key, (dx, dz) in vectors.items()
                } | {"facing_raw": fr, "facing": npc_facing}
        except (TypeError, ValueError, AttributeError):
            pass

    elif has_warp:
        warp = next((item for item in overlays if item.get("kind") == "warp"), {})
        target_raw = warp.get("target_zone_id_candidate")
        target_zone_id = int(target_raw) if target_raw is not None else None
        target_name = warp.get("target_zone_name") or (_resolve_zone_name(target_zone_id, provider) if target_zone_id is not None else "未知区域")
        facility = warp.get("facility_type") or _classify_facility(target_zone_id, target_name)
        is_pc = (facility == "pokemon_center")
        interaction = {
            "available": True,
            "action": "enter_warp",
            "target_type": "door_warp",
            "target_zone_id": target_zone_id,
            "target_zone_name": target_name,
            "facility_type": facility,
            "is_pokemon_center": is_pc,
            "description": f"通往【{target_name}】的门/出入口",
            "stand_tiles": ([] if fast_preview else _interaction_stand_tiles(provider, int(zone_id), int(x), int(y), int(z))),
            "source": "ROM Warp entity record",
        }
    return {
        "x": int(x), "z": int(z), "y": int(y),
        "symbol": symbol, "kind": kind,
        "underlying_symbol": underlying_symbol,
        "underlying_kind": underlying_kind,
        "has_warp": bool(has_warp),
        "walkable": terrain_walkable,
        "movement_allowed": movement_allowed,
        "movement_hazard": (
            "catwalk_fall_risk" if catwalk_meta is not None
            else "trainer_npc_battle" if has_trainer
            else "trainer_sight_battle" if sight_info is not None
            else "encounter_grass" if (tclass in (4, 5) or (material or {}).get("kind") in ("tall_grass", "dark_grass"))
            else None
        ),
        "trainer_sight": sight_info,
        "trainer_info": trainer_entry,
        "blocked": bool(not terrain_walkable and not (is_real_stair and alternate_layer_available and not terrain_walkable and abs(height_delta) == 1 and not is_vertical_cliff and not has_warp and not has_npc)),
        "occupied_candidate": has_npc,
        "is_stair_transit": bool(is_real_stair and alternate_layer_available and not terrain_walkable and abs(height_delta) == 1 and not is_vertical_cliff and not has_warp and not has_npc),
        "elevation_transition": ({
            "type": "slope",
            "direction": _slope_direction_and_symbol(slope_index)[0],
            "symbol": _slope_direction_and_symbol(slope_index)[1],
            "slope_index": slope_index,
            "relative_height": relative_y,
            "description": f"坡道/台阶：沿 {_slope_direction_and_symbol(slope_index)[0]} 方向行走升降物理高度",
        } if (slope_index > 0 and terrain_walkable) else ({
            "type": "bridge_deck", "symbol": "=", "description": "天桥/立体桥面通道",
        } if tclass in (0xBE, 0xBF) else ({
            "type": "ledge_drop", "direction": ledge_direction, "symbol": {"right": "→", "left": "←", "up": "↑", "down": "↓"}.get(ledge_direction or "", "v"),
            "one_way": True, "description": f"单向跳台：向 {ledge_direction} 跳落",
        } if ledge_direction else ({
            "type": "stair_transit",
            "direction": "up" if height_delta > 0 else "down",
            "symbol": "▲" if height_delta > 0 else "▼",
            "target_layer_y": alternate_layer_y,
            "description": f"跨层阶梯通道：向此方向行走可通往 {'上层高台' if height_delta > 0 else '下层地面'} (标高 Y={alternate_layer_y:+d})",
        } if (alternate_layer_available and not terrain_walkable and abs(height_delta) == 1 and not is_vertical_cliff and not has_warp and not has_npc) else None)))),
        "water_transition": ({
            "type": "surf_shore", "symbol": "~", "action": "surf",
            "requires_ability": "HM03_Surf",
            "description": "水岸/河堤跃迁格：陆地上正对按 A 触发冲浪跳跃下水，水面上可跳跃上岸",
        } if is_water_edge else None),
        "story_gate": (
            next((item.get("story_gate") for item in overlays if item.get("is_story_gate") and (
                item.get("presence") == "runtime_present" if (runtime_actors is not None and scene_matches_zone) else item.get("presence") != "static_spawn_not_current"
            )), None)
        ),
        "story_gate_trigger": gate_trigger_info,
        "interactable": interaction is not None,
        "status": status,
        "physical_obstacle": bool((not terrain_walkable and not (is_real_stair and alternate_layer_available and not terrain_walkable and abs(height_delta) == 1 and not is_vertical_cliff and not has_warp and not has_npc)) or (has_object and not has_hidden_item)),
        "can_traverse": bool((terrain_walkable or (is_real_stair and alternate_layer_available and not terrain_walkable and abs(height_delta) == 1 and not is_vertical_cliff and not has_warp and not has_npc)) and not (has_object and not has_hidden_item)),
        "tile_class": tclass,
        "tile_class_hex": f"0x{int(tclass):04X}" if isinstance(tclass, int) else None,
        "flags": flags,
        "material": material,
        "interaction": interaction,
        "directional": {
            "one_way": bool(ledge_direction),
            "ledge_direction": ledge_direction,
            "blocked_exits": blocked_directions,
            "allowed_exits": allowed_exits,
            "entry_rule": "check_this_tile_and_neighbor_before_moving",
        },
        "events": overlays,
        "npc_identity": npc_identity,
        "npc_blocking": npc_blocking,
        "alternate_layer_available": alternate_layer_available,
        "alternate_layer_y": alternate_layer_y,
        "catwalk": catwalk_meta,
    }


def _aggregate_fuzzy(cells: list[list[dict[str, Any]]], block_size: int) -> list[list[dict[str, Any]]]:
    """Compress a detailed raster while retaining navigation-critical facts."""
    if block_size <= 1:
        return cells
    height, width = len(cells), (len(cells[0]) if cells else 0)
    result: list[list[dict[str, Any]]] = []
    priority = {"!": 110, "T": 105, "N": 100, "D": 90, "I": 85, "S": 82, "O": 80, "h": 78, "^": 75, "v": 75, "<": 75, ">": 75, "↑": 70, "↓": 70, "←": 70, "→": 70, "↕": 70, "B": 60, "~": 52, "W": 50, "*": 40, "#": 30, ".": 10, "?": 0}
    for rz in range(0, height, block_size):
        row: list[dict[str, Any]] = []
        for rx in range(0, width, block_size):
            members = [cells[iz][ix] for iz in range(rz, min(rz + block_size, height)) for ix in range(rx, min(rx + block_size, width))]
            if not members:
                continue
            symbols = [str(item.get("symbol") or "?") for item in members]
            counts: dict[str, int] = {}
            for sym in symbols:
                counts[sym] = counts.get(sym, 0) + 1
            ranked = sorted(counts, key=lambda sym: (priority.get(sym, 0), counts[sym]), reverse=True)
            chosen = ranked[0] if ranked else "?"
            # A mixed block must not hide a one-way edge, door, NPC, object or
            # obstacle behind a majority of ordinary road tiles.
            if any(sym == "T" for sym in symbols): chosen = "T"
            elif any(sym == "N" for sym in symbols): chosen = "N"
            elif any(sym == "D" for sym in symbols): chosen = "D"
            elif any(sym == "I" for sym in symbols): chosen = "I"
            elif any(sym in {"↑", "↓", "←", "→", "↕", "v"} for sym in symbols):
                chosen = next(sym for sym in symbols if sym in {"↑", "↓", "←", "→", "↕", "v"})
            elif any(sym == "#" for sym in symbols) and all(not item.get("walkable") for item in members): chosen = "#"
            walkable_values = [bool(item.get("walkable")) for item in members]
            blocked_ratio = round(sum(not value for value in walkable_values) / len(members), 3)
            first = members[0]
            x0, z0 = int(first["x"]), int(first["z"])
            x1, z1 = int(members[-1]["x"]), int(members[-1]["z"])
            exit_dirs = sorted({direction for item in members for direction in (item.get("directional") or {}).get("allowed_exits", [])})
            contains = sorted(set(symbols), key=lambda sym: (-priority.get(sym, 0), sym))
            cell = dict(first)
            cell.update({
                "x": x0, "z": z0,
                "bounds": {"min_x": x0, "max_x": x1, "min_z": z0, "max_z": z1, "width": x1 - x0 + 1, "height": z1 - z0 + 1},
                "symbol": chosen,
                "kind": "模糊区域 (Aggregated)" if len(contains) > 1 else first.get("kind"),
                "walkable": any(walkable_values),
                "walkable_all": all(walkable_values),
                "blocked_ratio": blocked_ratio,
                "contains": contains,
                "directional": {
                    "one_way": any(sym in {"↑", "↓", "←", "→", "↕", "v"} for sym in symbols),
                    "allowed_exits": exit_dirs,
                    "blocked_exits": [],
                    "entry_rule": "use detailed radar before executing a move",
                },
                "events": [event for item in members for event in (item.get("events") or [])],
            })
            row.append(cell)
        result.append(row)
    return result


def _render_ascii(grid: list[list[dict[str, Any]]], *, x_labels: list[int] | None = None) -> str:
    if not grid:
        return ""
    if x_labels is None:
        x_labels = [int(cell.get("x", 0)) for cell in grid[0]]
    lines = ["     " + "".join(f"{x:3d}" for x in x_labels)]
    for row in grid:
        z = int(row[0].get("z", 0)) if row else 0
        lines.append(f"{z:3d}  " + "".join(f" {cell.get('symbol', '?')} " for cell in row))
    return "\n".join(lines)


def _world_index(provider: Any, *, include_bounds: bool = False) -> dict[str, Any]:
    matrices = provider.matrix_catalog() if provider is not None and hasattr(provider, "matrix_catalog") else []
    zones: list[dict[str, Any]] = []
    if provider is not None and hasattr(provider, "rom"):
        for zone_id in range(int(getattr(provider.rom, "zone_count", 0))):
            try:
                zone = provider.rom.zone(zone_id)
                bounds = provider.zone_bounds(zone_id) if include_bounds else None
                zones.append({
                    "zone_id": zone_id,
                    "zone_name": _resolve_zone_name(zone_id, provider),
                    "matrix_id": int(zone.matrix_id),
                    "area_id": int(zone.area_id),
                    "bounds": bounds.get("bounds") if bounds else None,
                    "tile_count": bounds.get("tile_count", 0) if bounds else None,
                })
            except Exception:
                continue
    return {
        "format": "black2-world-radar-index/v1",
        "coordinate_policy": "Matrix-global coordinates are valid only within one Matrix; different Matrix IDs are separate spatial domains connected by Warp candidates.",
        "matrices": matrices,
        "zones": zones,
        "legend": _LEGEND_COARSE,
    }


def _world_connectors(provider: Any, *, max_connectors: int = 20000) -> list[dict[str, Any]]:
    """Build a logical Warp/portal edge list without inventing coordinates."""
    if provider is None or not hasattr(provider, "rom"):
        return []
    rom = provider.rom
    zone_count = int(getattr(rom, "zone_count", 0) or 0)
    result: list[dict[str, Any]] = []
    for source_zone_id in range(zone_count):
        if len(result) >= int(max_connectors):
            break
        try:
            source_zone = rom.zone(source_zone_id)
            entities = rom.entities(int(source_zone.entities_id))
        except Exception:
            continue
        for warp in entities.get("warps") or []:
            if len(result) >= int(max_connectors):
                break
            try:
                x = int(float(warp.get("x_raw", 0)) // 16.0)
                z = int(float(warp.get("y_raw", 0)) // 16.0)
                extent_x = max(1, int(warp.get("x_extent_raw") or 1))
                extent_z = max(1, int(warp.get("y_extent_raw") or 1))
                target_raw = warp.get("target_zone_or_map_raw")
                target_candidate = int(target_raw) if target_raw is not None else None
            except (TypeError, ValueError):
                continue
            target_zone_id = None
            target_matrix_id = None
            resolution = "unresolved_candidate"
            if target_candidate is not None and 0 <= target_candidate < zone_count:
                try:
                    target_zone = rom.zone(target_candidate)
                    target_zone_id = target_candidate
                    target_matrix_id = int(target_zone.matrix_id)
                    resolution = "zone_candidate"
                except Exception:
                    pass
            result.append({
                "kind": "warp",
                "source": {
                    "zone_id": int(source_zone_id),
                    "zone_name": _resolve_zone_name(int(source_zone_id), provider),
                    "matrix_id": int(source_zone.matrix_id),
                    "position": {"x": x, "z": z},
                    "extent": {"x": extent_x, "z": extent_z},
                    "warp_id": warp.get("id"),
                },
                "target": {
                    "zone_id_candidate": target_candidate,
                    "zone_id": target_zone_id,
                    "matrix_id": target_matrix_id,
                    "resolution": resolution,
                },
                "traversal": {
                    "same_matrix_adjacency": bool(target_matrix_id is not None and target_matrix_id == int(source_zone.matrix_id)),
                    "requires_runtime_verification": True,
                    "coordinate_policy": "warp transition; do not treat source/target local X,Z as adjacent",
                },
                "semantic_status": "ROM candidate; runtime transition evidence is authoritative",
            })
    return result


@router.get("/radar/world/atlas")
async def navigation_radar_world_atlas(
    include_bounds: bool = Query(True),
    include_connectors: bool = Query(True),
    max_connectors: int = Query(20000, ge=1, le=100000),
) -> Any:
    """Return the complete logical world atlas.

    Gen-5 Matrix IDs are independent spatial domains. A single giant raster
    would silently put unrelated indoor/outdoor maps on top of one another,
    so the atlas returns every Matrix/Zone extent plus Warp edges. Render a
    selected Matrix with ``/radar/world/map`` and use the connector graph to
    cross Matrix boundaries.
    """
    provider = navigation_static_provider()
    if provider is None or not hasattr(provider, "matrix_catalog"):
        return _error_response(503, "RADAR_STATIC_UNAVAILABLE", "ROM-backed map provider is unavailable.")
    index = _world_index(provider, include_bounds=include_bounds)
    connectors = _world_connectors(provider, max_connectors=max_connectors) if include_connectors else []
    matrix_nodes = []
    for matrix in index.get("matrices") or []:
        matrix_nodes.append({
            "matrix_id": matrix.get("matrix_id"),
            "status": matrix.get("status"),
            "bounds": matrix.get("bounds"),
            "width_chunks": matrix.get("width_chunks"),
            "height_chunks": matrix.get("height_chunks"),
            "zone_ids": matrix.get("zone_ids") or [],
            "render": {
                "detail_endpoint": "/api/v1/navigation/radar/world/map",
                "default_mode": "fuzzy",
                "coordinate_space": "gen5-matrix-grid-v1",
            },
        })
    return {
        "format": "black2-world-radar-atlas/v1",
        "scope": "logical-world",
        "coordinate_policy": index.get("coordinate_policy"),
        "matrix_count": len(matrix_nodes),
        "zone_count": len(index.get("zones") or []),
        "matrix_nodes": matrix_nodes,
        "zones": index.get("zones") or [],
        "connectors": connectors,
        "connector_count": len(connectors),
        "legend": _LEGEND,
        "render_policy": {
            "single_raster": False,
            "reason": "different Matrix IDs reuse local coordinates; flattening them would create false adjacency",
            "fuzzy": "use /radar/world/map?matrix_id=<id>&mode=fuzzy",
            "detail": "use /radar/grid?scope=zone&mode=detail or /radar/world/map?matrix_id=<id>&mode=detail",
        },
    }


@router.get("/radar/directional")
async def navigation_radar_directional(
    range_steps: int = Query(5, ge=1, le=20, alias="range"),
    zone_id: int | None = Query(None),
    x: int | None = Query(None),
    y: int | None = Query(None),
    z: int | None = Query(None),
) -> dict[str, Any]:
    """Expose directional tile radar; optional x/y/z makes it deterministic."""
    provider = navigation_static_provider()
    radar_sample = await _radar_runtime_sample()
    sample, live_zone, live_x, live_y, live_z, facing, facing_zh = _player_anchor(radar_sample)
    resolved_zone = zone_id if zone_id is not None else live_zone
    resolved_x = x if x is not None else live_x
    resolved_y = y if y is not None else live_y
    resolved_z = z if z is not None else live_z
    if any(value is None for value in (resolved_zone, resolved_x, resolved_y, resolved_z)):
        return _radar_anchor_error(sample)
    zone_id = int(resolved_zone)
    px, py, pz = int(resolved_x), int(resolved_y), int(resolved_z)
    zone_name = _resolve_zone_name(zone_id, provider)
    runtime_payload = await _runtime_actor_sample()
    runtime_actors = runtime_payload.get("actors", []) if isinstance(runtime_payload, dict) else None

    matrix_id = None
    if provider is not None and hasattr(provider, "rom"):
        try:
            matrix_id = int(provider.rom.zone(zone_id).matrix_id)
        except Exception:
            matrix_id = None

    flag_bytes = await _ensure_flag_bytes()
    sight_tiles_map, trainer_npc_map, _ = _build_trainer_sight_rays(
        provider, zone_id, px - range_steps, px + range_steps, pz - range_steps, pz + range_steps,
        py=py,
        runtime_actors=runtime_actors,
        preferred_zone=zone_id,
        flag_bytes=flag_bytes,
    )

    def scan_leg(coords: list[tuple[int, int]]) -> list[dict[str, Any]]:
        cells = []
        for step, (tx, tz) in enumerate(coords, 1):
            owner = None
            if matrix_id is not None:
                owner = _matrix_zone_owner(provider, matrix_id, tx, tz, preferred_zone=zone_id)
            effective_zone = owner if owner is not None else zone_id
            cell = _radar_cell(
                provider, effective_zone, tx, py, tz,
                runtime_actors=runtime_actors,
                trainer_sights=sight_tiles_map,
                trainer_npcs=trainer_npc_map,
                flag_bytes=flag_bytes,
            )
            cell["zone_id"] = int(effective_zone)
            cell["zone_name"] = _resolve_zone_name(int(effective_zone), provider)
            cells.append({"step": step, **cell})
        return cells

    cardinal = {
        "up_north": {"name": "上方 / 北方 (North)", "axis": "Z-", "cells": scan_leg([(px, pz - s) for s in range(1, range_steps + 1)])},
        "down_south": {"name": "下方 / 南方 (South)", "axis": "Z+", "cells": scan_leg([(px, pz + s) for s in range(1, range_steps + 1)])},
        "left_west": {"name": "左方 / 西方 (West)", "axis": "X-", "cells": scan_leg([(px - s, pz) for s in range(1, range_steps + 1)])},
        "right_east": {"name": "右方 / 东方 (East)", "axis": "X+", "cells": scan_leg([(px + s, pz) for s in range(1, range_steps + 1)])},
    }
    relative_map = {
        "North": ("up_north", "down_south", "left_west", "right_east"),
        "South": ("down_south", "up_north", "right_east", "left_west"),
        "West": ("left_west", "right_east", "down_south", "up_north"),
        "East": ("right_east", "left_west", "up_north", "down_south"),
    }
    ahead_k, behind_k, left_k, right_k = relative_map.get(facing, relative_map["North"])
    return {
        "format": "black2-directional-radar/v2",
        "coordinate_space": "gen5-field-grid-v1",
        "zone_id": zone_id, "zone_name": zone_name,
        "player": {"x": px, "y": py, "z": pz, "facing": facing, "facing_zh": facing_zh},
        "range_steps": range_steps,
        "cardinal_directions": cardinal,
        "relative_to_facing": {"ahead": cardinal[ahead_k], "behind": cardinal[behind_k], "left": cardinal[left_k], "right": cardinal[right_k]},
        "source": "ROM terrain/event candidates; runtime landing verification remains required",
    }


@router.get("/radar/interactions")
async def navigation_radar_interactions(
    radius: int = Query(8, ge=1, le=25),
    zone_id: int | None = Query(None),
    x: int | None = Query(None),
    y: int | None = Query(None),
    z: int | None = Query(None),
    include_live: bool = Query(True),
) -> Any:
    """List nearby interaction affordances separately from the ASCII raster.

    Terrain objects may be blocked for movement and still be usable from an
    adjacent stand tile. NPC rows keep a stable ROM entity id while attaching
    live ActorSystem position/facing when a binding is available.
    """
    provider = navigation_static_provider()
    if provider is None:
        return _error_response(503, "RADAR_STATIC_UNAVAILABLE", "ROM-backed map provider is unavailable.")
    radar_sample = await _radar_runtime_sample()
    sample, live_zone, live_x, live_y, live_z, facing, facing_zh = _player_anchor(radar_sample)
    resolved_zone = zone_id if zone_id is not None else live_zone
    resolved_x = x if x is not None else live_x
    resolved_y = y if y is not None else live_y
    resolved_z = z if z is not None else live_z
    if any(value is None for value in (resolved_zone, resolved_x, resolved_y, resolved_z)):
        return _radar_anchor_error(sample)
    runtime_payload = await _runtime_actor_sample() if include_live else None
    runtime_actors = runtime_payload.get("actors", []) if isinstance(runtime_payload, dict) else None
    cells: list[dict[str, Any]] = []
    matrix_id = None
    if provider is not None and hasattr(provider, "rom"):
        try:
            matrix_id = int(provider.rom.zone(int(resolved_zone)).matrix_id)
        except Exception:
            matrix_id = None

    for tz in range(int(resolved_z) - radius, int(resolved_z) + radius + 1):
        for tx in range(int(resolved_x) - radius, int(resolved_x) + radius + 1):
            owner = None
            if matrix_id is not None:
                owner = _matrix_zone_owner(provider, matrix_id, tx, tz, preferred_zone=int(resolved_zone))
            effective_zone = owner if owner is not None else int(resolved_zone)
            cell = _radar_cell(
                provider, effective_zone, tx, int(resolved_y), tz,
                is_player=(tx == live_x and tz == live_z and (live_zone in (effective_zone, int(resolved_zone)))),
                runtime_actors=runtime_actors,
            )
            cell["zone_id"] = int(effective_zone)
            cell["zone_name"] = _resolve_zone_name(int(effective_zone), provider)
            if cell.get("interactable") or cell.get("events"):
                cells.append({
                    "tile": {"x": tx, "y": int(resolved_y), "z": tz},
                    "symbol": cell.get("symbol"),
                    "kind": cell.get("kind"),
                    "walkable": cell.get("walkable"),
                    "blocked": cell.get("blocked"),
                    "interactable": cell.get("interactable"),
                    "interaction": cell.get("interaction"),
                    "events": cell.get("events") or [],
                    "tile_class": cell.get("tile_class"),
                    "material": cell.get("material") or {},
                })
    return {
        "format": "black2-radar-interactions/v1",
        "coordinate_space": "gen5-field-grid-v1",
        "zone_id": int(resolved_zone),
        "zone_name": _resolve_zone_name(int(resolved_zone), provider),
        "center": {"x": int(resolved_x), "y": int(resolved_y), "z": int(resolved_z)},
        "facing": facing, "facing_zh": facing_zh,
        "radius": int(radius),
        "interactions": cells,
        "count": len(cells),
        "npc_movement_policy": {
            "static_spawn": "ROM candidate only; do not use as current occupancy when a bound live actor has moved",
            "live_actor": "ActorSystem grid/facing/slot/address is current for this sample",
            "identity": "static_entity_id remains stable; runtime_actor_uid/slot may change per scene/session",
        },
        "purpose_policy": {
            "verified": "only registered or behaviorally observed services are promoted",
            "candidate": "script/sprite/flag/dialogue candidates remain explicit",
            "shop_inventory": "requires decoded script or observed shop menu; empty inventory is not proof of no shop",
        },
    }


@router.get("/radar/world/index")
async def navigation_radar_world_index(
    include_bounds: bool = Query(False),
) -> dict[str, Any]:
    """Return the stitched-world manifest without expanding every tile.

    ``include_bounds=true`` asks for decoded per-Zone terrain extents and is
    intentionally opt-in because it touches every Zone in the ROM.
    """
    return _world_index(navigation_static_provider(), include_bounds=include_bounds)


def _matrix_zone_owner(provider: Any, matrix_id: int, x: int, z: int, *, preferred_zone: int | None = None) -> int | None:
    """Resolve ownership directly from the Matrix chunk table.

    Do not use the forgiving ``resolve_zone_for_global`` fallback while
    rasterizing: empty Matrix cells must stay unknown, and scanning all 615
    Zone headers for every sample makes a large fuzzy map needlessly slow.
    """
    try:
        matrix = provider.rom.matrix(int(matrix_id))
        chunk_x, chunk_z = int(x) // 32, int(z) // 32
        if not (0 <= chunk_x < int(matrix.width) and 0 <= chunk_z < int(matrix.height)):
            return None
        chunk = matrix.cell(chunk_x, chunk_z)
        chunk_id = chunk.get("chunk_id")
        if chunk_id is None or int(chunk_id) == 0xFFFFFFFF:
            return None
        owner = chunk.get("zone_id")
        if owner is None or int(owner) == 0xFFFFFFFF:
            # Some standalone Matrices (including the live field Matrix) do
            # not carry a Zone owner table. In that case a preferred Zone is
            # safe only when the Matrix explicitly has no zone table.
            if preferred_zone is not None and not bool(getattr(matrix, "has_zones", False)):
                try:
                    if int(provider.rom.zone(int(preferred_zone)).matrix_id) == int(matrix_id):
                        return int(preferred_zone)
                except (IndexError, ValueError, TypeError):
                    pass
            return None
        owner = int(owner)
        try:
            if int(provider.rom.zone(owner).matrix_id) != int(matrix_id):
                return None
        except (IndexError, ValueError, TypeError):
            return None
        return owner
    except (AttributeError, IndexError, TypeError, ValueError):
        return None


def _radar_unknown_cell(x: int, y: int, z: int, *, reason: str) -> dict[str, Any]:
    """Construct a lossless unknown cell without guessing terrain."""
    return {
        "x": int(x), "z": int(z), "y": int(y), "symbol": "?",
        "kind": "未分配/未知矩阵地块", "walkable": False,
        "movement_allowed": False, "blocked": True, "status": reason,
        "tile_class": None, "tile_class_hex": None, "flags": None,
        "material": {},
        "directional": {"one_way": False, "ledge_direction": None,
                         "allowed_exits": [], "blocked_exits": [],
                         "entry_rule": "resolve Zone and inspect detail radar before moving"},
        "events": [],
    }


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        try:
            key = json.dumps(event, sort_keys=True, ensure_ascii=False, default=str)
        except TypeError:
            key = repr(event)
        if key not in seen:
            seen.add(key)
            result.append(event)
    return result


def _radar_matrix_fuzzy_block(
    provider: Any, matrix_id: int, y: int, min_x: int, max_x: int,
    min_z: int, max_z: int, *, preferred_zone: int | None = None,
    player: tuple[int, int, int] | None = None,
) -> dict[str, Any]:
    """Render one coarse Matrix block without expanding every source tile.

    A large Matrix is an atlas-scale object, not a local tactical grid. In
    fuzzy mode we sample its corners/center and bulk-scan ROM events. This
    keeps the endpoint bounded while explicitly reporting that walkability is
    a sampled summary; callers must request detail radar before executing.
    """
    x0, x1, z0, z1 = int(min_x), int(max_x), int(min_z), int(max_z)
    mid_x, mid_z = (x0 + x1) // 2, (z0 + z1) // 2
    sample_coords = {(x0, z0), (x1, z0), (x0, z1), (x1, z1), (mid_x, mid_z)}
    samples: list[dict[str, Any]] = []
    owners: set[int] = set()
    for tx, tz in sorted(sample_coords, key=lambda item: (item[1], item[0])):
        try:
            owner = _matrix_zone_owner(provider, int(matrix_id), tx, tz, preferred_zone=preferred_zone)
        except Exception:
            owner = None
        if owner is None:
            continue
        owners.add(int(owner))
        is_player = bool(player and int(player[0]) == int(owner)
                         and int(player[1]) == tx and int(player[2]) == tz)
        samples.append(_radar_cell(provider, int(owner), tx, int(y), tz,
                                    is_player=is_player, fast_preview=True))

    events: list[dict[str, Any]] = []
    for owner in sorted(owners):
        bulk = getattr(provider, "event_overlays_in_bounds", None)
        if callable(bulk):
            try:
                events.extend(bulk(owner, x0, x1, z0, z1))
            except Exception:
                pass
        else:
            # Test doubles and older providers can still participate; their
            # per-cell API is bounded to the five fuzzy samples only.
            for tx, tz in sample_coords:
                try:
                    events.extend(provider.event_overlay_at(owner, tx, tz))
                except Exception:
                    pass
    events = _dedupe_events(events)

    if not samples:
        cell = _radar_unknown_cell(x0, int(y), z0, reason="未知；没有可确认的 Zone 所有权")
        cell.update({
            "bounds": {"min_x": x0, "max_x": x1, "min_z": z0, "max_z": z1,
                       "width": x1 - x0 + 1, "height": z1 - z0 + 1},
            "zone_ids": [], "events": events,
            "sampling": {"strategy": "fuzzy-block-sampled", "sampled_cells": 0,
                          "source_cells": (x1 - x0 + 1) * (z1 - z0 + 1),
                          "coverage_ratio": 0.0, "complete": False},
        })
        return cell

    priority = {"!": 110, "N": 100, "D": 90, "I": 85, "S": 82, "O": 80, "h": 78, "↑": 70, "↓": 70, "←": 70, "→": 70, "↕": 70, "v": 70, "B": 60,
                "T": 55, "~": 52, "W": 50, "*": 40, "#": 30, ".": 10, "?": 0}
    symbols = [str(item.get("symbol") or "?") for item in samples]
    event_symbols = [str(item.get("symbol") or "?") for item in events]
    all_symbols = symbols + event_symbols
    chosen = max(all_symbols, key=lambda sym: (priority.get(sym, 0), all_symbols.count(sym)))
    if any(item.get("kind") == "npc" for item in events):
        chosen = "N"
    elif any(item.get("kind") == "warp" for item in events):
        chosen = "D"
    elif any(item.get("kind") == "furniture" for item in events):
        chosen = "O"
    walkable_values = [bool(item.get("walkable")) for item in samples]
    allowed_exits = sorted({direction for item in samples
                            for direction in (item.get("directional") or {}).get("allowed_exits", [])})
    zone_ids = sorted(owners)
    first = dict(samples[0])
    first.update({
        "x": x0, "z": z0,
        "bounds": {"min_x": x0, "max_x": x1, "min_z": z0, "max_z": z1,
                   "width": x1 - x0 + 1, "height": z1 - z0 + 1},
        "zone_ids": zone_ids,
        "zone_id": zone_ids[0] if len(zone_ids) == 1 else None,
        "symbol": chosen,
        "kind": "模糊区域 (Sampled Aggregation)" if len(set(all_symbols)) > 1 else first.get("kind"),
        "walkable": any(walkable_values),
        "walkable_all": all(walkable_values),
        "blocked_ratio": round(sum(not value for value in walkable_values) / len(walkable_values), 3),
        "contains": sorted(set(all_symbols), key=lambda sym: (-priority.get(sym, 0), sym)),
        "directional": {
            "one_way": any((item.get("directional") or {}).get("one_way") for item in samples),
            "allowed_exits": allowed_exits, "blocked_exits": [],
            "entry_rule": "use detailed radar before executing a move",
        },
        "events": events,
        "sampling": {
            "strategy": "fuzzy-block-sampled", "sampled_cells": len(samples),
            "source_cells": (x1 - x0 + 1) * (z1 - z0 + 1),
            "coverage_ratio": round(len(samples) / ((x1 - x0 + 1) * (z1 - z0 + 1)), 6),
            "complete": False,
        },
    })
    return first


@router.get("/radar/world/map")
async def navigation_radar_world_map(
    matrix_id: int | None = Query(None),
    zone_id: int | None = Query(None),
    y: int | None = Query(None),
    mode: str = Query("fuzzy", pattern="^(detail|fuzzy)$"),
    block_size: int | None = Query(None, ge=1, le=64),
    max_cells: int = Query(10000, ge=100, le=100000),
    text_map: bool = Query(False),
) -> Any:
    """Render one ROM Matrix as a bounded raster; use /index for all maps."""
    provider = navigation_static_provider()
    if provider is None or not hasattr(provider, "matrix_bounds"):
        return _error_response(503, "RADAR_STATIC_UNAVAILABLE", "ROM-backed map provider is unavailable.")
    radar_sample = await _radar_runtime_sample()
    sample, live_zone, live_x, live_y, live_z, facing, facing_zh = _player_anchor(radar_sample)
    runtime_payload = await _runtime_actor_sample()
    runtime_actors = runtime_payload.get("actors", []) if isinstance(runtime_payload, dict) else None
    zone_context = zone_id if zone_id is not None else live_zone
    py = int(y if y is not None else (live_y if live_y is not None else 0))
    if matrix_id is None:
        if zone_context is None:
            return _radar_anchor_error(sample)
        try:
            matrix_id = int(provider.rom.zone(int(zone_context)).matrix_id)
        except Exception:
            return _error_response(422, "RADAR_MATRIX_REQUIRED", "matrix_id is required when the current Zone is unresolved.")
    meta = provider.matrix_bounds(int(matrix_id))
    bounds = meta.get("bounds")
    if not bounds:
        return _error_response(404, "RADAR_MATRIX_EMPTY", f"Matrix {matrix_id} has no decoded chunks.")
    width, height = int(bounds["width"]), int(bounds["height"])
    if block_size is None:
        block_size = max(1, int(math.ceil(math.sqrt((width * height) / max_cells)))) if mode == "fuzzy" else 1
    if mode == "detail" and width * height > max_cells:
        return _error_response(413, "RADAR_DETAIL_TOO_LARGE", "Use mode=fuzzy or raise max_cells; full detail is intentionally bounded.", details={"width": width, "height": height, "max_cells": max_cells})
    # Matrix ownership can be sparse. Detail mode expands every source tile;
    # fuzzy mode intentionally renders one sampled summary per block so a
    # 832x768 outdoor Matrix does not turn an API request into 638k decodes.
    if mode == "fuzzy":
        grid = []
        player = (live_zone, live_x, live_z)
        for z0 in range(int(bounds["min_z"]), int(bounds["max_z"]) + 1, int(block_size)):
            row = []
            z1 = min(z0 + int(block_size) - 1, int(bounds["max_z"]))
            for x0 in range(int(bounds["min_x"]), int(bounds["max_x"]) + 1, int(block_size)):
                x1 = min(x0 + int(block_size) - 1, int(bounds["max_x"]))
                cell = _radar_matrix_fuzzy_block(
                    provider, int(matrix_id), py, x0, x1, z0, z1,
                    preferred_zone=(int(zone_context) if zone_context is not None else None), player=player,
                )
                cell["zone_name"] = (
                    _resolve_zone_name(cell["zone_id"], provider)
                    if cell.get("zone_id") is not None else None
                )
                row.append(cell)
            grid.append(row)
    else:
        detailed: list[list[dict[str, Any]]] = []
        for tz in range(int(bounds["min_z"]), int(bounds["max_z"]) + 1):
            row = []
            for tx in range(int(bounds["min_x"]), int(bounds["max_x"]) + 1):
                owner = _matrix_zone_owner(provider, int(matrix_id), tx, tz, preferred_zone=(int(zone_context) if zone_context is not None else None))
                if owner is None:
                    cell = _radar_unknown_cell(tx, py, tz, reason="未知；没有可确认的 Zone 所有权")
                else:
                    cell = _radar_cell(provider, int(owner), tx, py, tz,
                                       is_player=(int(owner) == live_zone and tx == live_x and tz == live_z),
                                       runtime_actors=runtime_actors)
                    cell["zone_id"] = int(owner)
                    cell["zone_name"] = _resolve_zone_name(int(owner), provider)
                row.append(cell)
            detailed.append(row)
        grid = detailed
    ascii_map = _render_ascii(grid)
    sample_grid = sample.get("grid") if isinstance(sample.get("grid"), dict) else None
    if sample_grid is None:
        position = sample.get("position") if isinstance(sample.get("position"), dict) else {}
        sample_grid = position.get("grid") if isinstance(position.get("grid"), dict) else {}
    center_x = sample_grid.get("x") if sample_grid.get("x") is not None else live_x
    center_z = sample_grid.get("z") if sample_grid.get("z") is not None else live_z
    response = {
        "format": "black2-world-radar-map/v1",
        "scope": "matrix",
        "matrix_id": int(matrix_id),
        "mode": mode,
        "block_size": int(block_size),
        "max_cells": max_cells,
        "zone_id_context": (int(zone_context) if zone_context is not None else None),
        "zone_name_context": (_resolve_zone_name(int(zone_context), provider) if zone_context is not None else None),
        "center": {"x": center_x, "y": py, "z": center_z},
        "facing": facing, "facing_zh": facing_zh,
        "bounds": bounds,
        "legend": _LEGEND,
        "ascii_map": ascii_map,
        "grid": grid,
        "coordinate_policy": "This is one Matrix domain. Cross-Matrix movement must use Warp transitions; it is not silently flattened into one coordinate plane.",
    }
    if text_map:
        return PlainTextResponse(f"=== Matrix {matrix_id} | 区域上下文: {response['zone_name_context']} | mode={mode} | block={block_size} ===\n\n{ascii_map}\n\n图例说明:\n" + "\n".join(f"{k} {v}" for k, v in _LEGEND.items()))
    return response


@router.get("/radar/area")
async def navigation_radar_area(
    zone_id: int | None = Query(None),
    y: int | None = Query(None),
    mode: str = Query("fuzzy", pattern="^(detail|fuzzy)$"),
    block_size: int | None = Query(None, ge=1, le=16),
    max_cells: int = Query(10000, ge=100, le=100000),
    text_map: bool = Query(False),
) -> Any:
    """Whole-current-Zone view; unlike /radar/grid it defaults to area scope."""
    return await navigation_radar_grid(
        radius=5, text_map=text_map, scope="zone", mode=mode,
        block_size=block_size, zone_id=zone_id, x=None, y=y, z=None,
        max_cells=max_cells,
    )


@router.get("/radar/slices")
async def navigation_radar_slices(
    radius: int = Query(5, ge=1, le=15),
    text_map: bool = Query(False),
    zone_id: int | None = Query(None),
    x: int | None = Query(None),
    y: int | None = Query(None),
    z: int | None = Query(None),
    mode: str = Query("detail", pattern="^(coarse|detail)$"),
) -> Any:
    """Multi-layer 3D elevation slice gallery for complex multi-story scenes.

    Dynamically clusters and filters only true major functional floor planes
    (e.g. ground and upper overpass), pruning intermediate stair slope steps.
    """
    provider = navigation_static_provider()
    radar_sample = await _radar_runtime_sample()
    sample, live_zone, live_x, live_y, live_z, facing, facing_zh = _player_anchor(radar_sample)
    runtime_payload = await _runtime_actor_sample()
    runtime_actors = runtime_payload.get("actors", []) if isinstance(runtime_payload, dict) else None

    resolved_zone_raw = zone_id if zone_id is not None else live_zone
    if resolved_zone_raw is None:
        return _radar_anchor_error(sample)
    resolved_zone = int(resolved_zone_raw)

    resolved_x = x if x is not None else live_x
    resolved_y = y if y is not None else (live_y if live_y is not None else 0)
    resolved_z = z if z is not None else live_z
    if any(value is None for value in (resolved_x, resolved_y, resolved_z)):
        return _radar_anchor_error(sample)

    px, py, pz = int(resolved_x), int(resolved_y), int(resolved_z)
    min_x, max_x, min_z, max_z = px - radius, px + radius, pz - radius, pz + radius
    width = max_x - min_x + 1

    matrix_id = None
    if provider is not None and hasattr(provider, "rom"):
        try:
            matrix_id = int(provider.rom.zone(resolved_zone).matrix_id)
        except Exception:
            matrix_id = None

    # 1. Discover all unique physical Y-levels and filter to true major walking planes
    from collections import Counter
    walkable_flat = Counter()
    all_discovered_y = set()
    for tz in range(min_z, max_z + 1):
        for tx in range(min_x, max_x + 1):
            owner = _matrix_zone_owner(provider, matrix_id, tx, tz, preferred_zone=resolved_zone) if matrix_id is not None else resolved_zone
            effective_zone = owner if owner is not None else resolved_zone
            surf = provider.surface_at(effective_zone, tx, tz, py, allow_unverified_terrain=True) if provider else None
            for s in (surf or {}).get("surfaces") or []:
                if s.get("tile_class") != 254 and not s.get("static_blocked"):
                    tc = s.get("tile_class")
                    if tc in (0, 1, 2, 3, 0x1F):
                        rel = s.get("height", {}).get("chunk_relative_world_y")
                        slope = s.get("height", {}).get("slope_index", 0)
                        if rel is not None and slope == 0:
                            gy = int(round(rel / 16.0))
                            walkable_flat[gy] += 1
                            all_discovered_y.add(gy)

    # Major functional floors have real flat walkable road tiles (>= 3)
    major_floors = sorted([gy for gy, cnt in walkable_flat.items() if cnt >= 3], reverse=True)
    if not major_floors:
        major_floors = [py]

    rich_player = player_runtime_service.latest if isinstance(player_runtime_service.latest, dict) else {}
    catwalk_runtime = _catwalk_runtime_info(rich_player)

    live_world_y = float(py) * 16.0
    pos_meta = rich_player.get("position") if isinstance(rich_player.get("position"), dict) else {}
    w_meta = pos_meta.get("world") if isinstance(pos_meta.get("world"), dict) else {}
    if w_meta.get("y") is not None:
        live_world_y = float(w_meta["y"])
    else:
        pos_meta = sample.get("position") if isinstance(sample.get("position"), dict) else {}
        w_meta = pos_meta.get("world") if isinstance(pos_meta.get("world"), dict) else {}
        if w_meta.get("y") is not None:
            live_world_y = float(w_meta["y"])

    # General staircase corridor clustering and transition layer flattening
    player_on_stair = False
    stair_runtime = None
    probe_x = px if x is not None else live_x
    probe_z = pz if z is not None else live_z
    if probe_x is not None and probe_z is not None:
        step_eval = staircase_corridor_service.evaluate_player_step(
            resolved_zone, int(probe_x), int(probe_z), live_world_y
        )
        if step_eval:
            player_on_stair = True
            stair_runtime = {
                "active": True,
                **step_eval,
                "stair_slice_y": step_eval["flattened_slice_y"],
                "description": step_eval["ai_guidance"],
            }

    if y is None and player_on_stair and stair_runtime is not None:
        py = stair_runtime["stair_slice_y"]

    all_active_floors = list(major_floors)
    if player_on_stair and stair_runtime is not None and stair_runtime["stair_slice_y"] not in all_active_floors:
        all_active_floors.append(stair_runtime["stair_slice_y"])
    elif py not in all_active_floors:
        all_active_floors.append(py)
    all_active_floors.sort(reverse=True)

    sorted_layers = all_active_floors

    slices_cache_key = (
        id(provider), "slices", mode, int(radius), bool(text_map), int(resolved_zone),
        int(px), int(py), int(pz), int(min_x), int(max_x), int(min_z), int(max_z),
        tuple(sorted_layers),
    )
    # Do not cache an active catwalk frame: its dwell timer and balance/fall
    # status must remain live while the player is on the narrow bridge.
    cached_slices = None if catwalk_runtime.get("active") else _radar_cache_get(slices_cache_key)
    if cached_slices is not None:
        cached_kind, cached_payload = cached_slices
        if cached_kind == "text":
            return PlainTextResponse(cached_payload)
        return cached_payload
    flag_bytes = await _ensure_flag_bytes()
    works_u16 = await _ensure_works_u16()
    sight_tiles_map, trainer_npc_map, trainer_summaries = _build_trainer_sight_rays(
        provider, resolved_zone, min_x, max_x, min_z, max_z,
        py=py,
        runtime_actors=runtime_actors,
        preferred_zone=resolved_zone,
        flag_bytes=flag_bytes,
    )
    boulder_cells, active_boulders, boulder_summaries = _scan_boulder_mechanics(
        provider, resolved_zone, runtime_actors,
    )
    story_trigger_tiles, active_roadblocks, impassable_story_coords = _scan_active_story_triggers(
        provider, resolved_zone, works_u16=works_u16, runtime_actors=runtime_actors,
    )

    slices_data = []
    grids_by_floor = {}

    for floor_y in sorted_layers:
        floor_grid = []
        is_player_layer = (floor_y == py)
        for tz in range(min_z, max_z + 1):
            row = []
            for tx in range(min_x, max_x + 1):
                owner = _matrix_zone_owner(provider, matrix_id, tx, tz, preferred_zone=resolved_zone) if matrix_id is not None else resolved_zone
                effective_zone = owner if owner is not None else resolved_zone
                is_p = (tx == live_x and tz == live_z and is_player_layer)
                is_stair_transit = (tx == live_x and tz == live_z and not is_player_layer)
                cell = _radar_cell(
                    provider, effective_zone, tx, floor_y, tz,
                    is_player=is_p,
                    runtime_actors=runtime_actors,
                    trainer_sights=sight_tiles_map,
                    trainer_npcs=trainer_npc_map,
                    boulder_cells=boulder_cells,
                    story_trigger_tiles=story_trigger_tiles,
                    mode=mode,
                    player_world_y=live_world_y,
                    is_player_stair_transit=is_stair_transit,
                    flag_bytes=flag_bytes,
                )
                cell["zone_id"] = int(effective_zone)
                cell["zone_name"] = _resolve_zone_name(int(effective_zone), provider)
                row.append(cell)
            floor_grid.append(row)

        grids_by_floor[floor_y] = floor_grid
        ascii_grid = _render_ascii(floor_grid)
        layer_label = f"Floor Y={floor_y:+d} (标高: {floor_y * 16.0:.1f})"
        if stair_runtime and stair_runtime.get("active") and floor_y == stair_runtime.get("stair_slice_y"):
            step_i = stair_runtime.get("current_step", 1)
            step_y = stair_runtime.get("step_world_y", 8.0)
            layer_label = f"Floor Y={floor_y:+d} (标高: {floor_y * 16.0:.1f}) ★楼梯跨层中段 [踩踏第 {step_i} 阶 · 标高 {step_y:.1f}]"
        elif is_player_layer:
            layer_label += " ★当前所在层"

        slices_data.append({
            "floor_y": floor_y,
            "world_y": floor_y * 16.0,
            "is_player_floor": is_player_layer,
            "label": layer_label,
            "ascii_map": ascii_grid,
            "grid": floor_grid,
        })

    response = {
        "format": "black2-radar-slices/v1",
        "zone_id": resolved_zone,
        "zone_name": _resolve_zone_name(resolved_zone, provider),
        "center": {"x": px, "y": py, "z": pz},
        "radius": radius,
        "diameter": width,
        "player_floor_y": py,
        "total_active_layers": len(sorted_layers),
        "active_layers": sorted_layers,
        "slices": slices_data,
        "catwalk_runtime": catwalk_runtime,
        "stair_runtime": stair_runtime,
        "active_story_roadblocks": active_roadblocks,
        "impassable_coordinates": impassable_story_coords,
    }

    if text_map:
        header = f"=== 区域: {response['zone_name']} | 中心: (X={px}, Z={pz}, Y={py}) | 3D智能双层对照切片 ==="
        legend_to_show = _LEGEND_DETAIL if mode == "detail" else _LEGEND_COARSE
        legend_str = "\n".join(f"{k} {v}" for k, v in legend_to_show.items())

        if len(sorted_layers) == 2 or (len(sorted_layers) == 3 and stair_runtime and stair_runtime.get("active")):
            top_y, bot_y = (sorted_layers[0], sorted_layers[-1]) if len(sorted_layers) == 3 else (sorted_layers[0], sorted_layers[1])
            grid_top = grids_by_floor[top_y]
            grid_bot = grids_by_floor[bot_y]
            star_top = " ★主角所在层" if top_y == py and not player_on_stair else ""
            star_bot = " ★主角所在层" if bot_y == py and not player_on_stair else ""
            hl = f"【下层地面切片 Floor Y={bot_y:+d}{star_bot}】"
            hr = f"【上层高台切片 Floor Y={top_y:+d}{star_top}】"
            sep_l = "-" * 42
            sep_r = "-" * 42
            header_line = f"{hl:<42}  |  {hr}"
            sep_line = f"{sep_l}  |  {sep_r}"
            x_coords = "".join(f"{x:3d}" for x in range(min_x, max_x + 1))
            coord_line = f"     {x_coords}  |       {x_coords}"

            body_lines = [header_line, sep_line, coord_line]
            for idx in range(len(grid_top)):
                z = min_z + idx
                row_b = "  ".join(c.get("symbol", "?") for c in grid_bot[idx])
                row_t = "  ".join(c.get("symbol", "?") for c in grid_top[idx])
                body_lines.append(f"{z:3d}   {row_b}  |  {z:3d}   {row_t}")

            if stair_runtime and stair_runtime.get("active"):
                step_num = stair_runtime["current_step"]
                step_h = stair_runtime["step_world_y"]
                body_lines.append(f">>> [▲▼] 楼梯跨层中段踏面: 主角踩踏第 {step_num} 阶 (共 2 阶 · 标高 {step_h:.1f}) | 西向登高台 (Y=+2) | 东向达地面 (Y=0) <<<")
            rendered_blocks = "\n".join(body_lines)
        else:
            rendered_blocks = "\n\n".join(
                f"--- [切片 {s['label']}] ---\n{s['ascii_map']}" for s in slices_data
            )

        rendered_text = header + "\n\n" + rendered_blocks + "\n\n图例说明:\n" + legend_str
        _radar_cache_put(slices_cache_key, ("text", rendered_text))
        if catwalk_runtime.get("active"):
            rendered_text += "\n\n>>> [╫] 独木桥实时风险监控 (Catwalk Runtime) <<<\n"
            rendered_text += f"- 停留计时: {catwalk_runtime.get('dwell_seconds', 0.0):.3f}s | 游戏阈值: 未完成 RAM 差分验证\n"
            rendered_text += f"- 当前状态: {catwalk_runtime.get('grid_status', catwalk_runtime.get('grid_status_raw'))} | 指令: {catwalk_runtime.get('grid_command', catwalk_runtime.get('grid_command_raw'))}\n"
            rendered_text += "- 侧向行为: 两侧为坠落边缘；只允许沿独木桥轴线移动\n"
        _radar_cache_put(slices_cache_key, ("text", rendered_text))
        return PlainTextResponse(rendered_text)

    _radar_cache_put(slices_cache_key, ("json", response))
    return response


@router.get("/radar/grid")
async def navigation_radar_grid(
    radius: int = Query(5, ge=1, le=50),
    text_map: bool = Query(False),
    scope: str = Query("local", pattern="^(local|zone)$"),
    mode: str = Query("coarse", pattern="^(coarse|detail|fuzzy)$"),
    block_size: int | None = Query(None, ge=1, le=16),
    zone_id: int | None = Query(None),
    x: int | None = Query(None),
    y: int | None = Query(None),
    z: int | None = Query(None),
    max_cells: int = Query(10000, ge=100, le=100000),
) -> Any:
    """Local/whole-Zone radar with explicit arbitrary centers and fuzzy mode.

    Examples:
      ``?x=22&z=33`` scans around an arbitrary tile;
      ``?scope=zone&zone_id=445&mode=fuzzy`` scans the entire Zone;
      ``/radar/world/map`` is the Matrix-wide stitched view.
    """
    provider = navigation_static_provider()
    radar_sample = await _radar_runtime_sample()
    sample, live_zone, live_x, live_y, live_z, facing, facing_zh = _player_anchor(radar_sample)
    runtime_payload = await _runtime_actor_sample()
    runtime_actors = runtime_payload.get("actors", []) if isinstance(runtime_payload, dict) else None
    resolved_zone_raw = zone_id if zone_id is not None else live_zone
    if resolved_zone_raw is None:
        return _radar_anchor_error(sample)
    resolved_zone = int(resolved_zone_raw)
    if scope == "zone":
        px = int(x if x is not None else (live_x if live_x is not None else 0))
        py = int(y if y is not None else (live_y if live_y is not None else 0))
        pz = int(z if z is not None else (live_z if live_z is not None else 0))
        if provider is None or not hasattr(provider, "zone_bounds"):
            return _error_response(503, "RADAR_STATIC_UNAVAILABLE", "ROM-backed map provider is unavailable.")
        meta = provider.zone_bounds(resolved_zone)
        bounds = meta.get("bounds")
        if not bounds:
            return _error_response(404, "RADAR_ZONE_EMPTY", f"Zone {resolved_zone} has no decoded terrain.")
        min_x, max_x = int(bounds["min_x"]), int(bounds["max_x"])
        min_z, max_z = int(bounds["min_z"]), int(bounds["max_z"])
        width, height = max_x - min_x + 1, max_z - min_z + 1
        if mode == "detail" and width * height > max_cells:
            return _error_response(413, "RADAR_DETAIL_TOO_LARGE", "Use mode=fuzzy for a whole Zone raster.", details={"width": width, "height": height, "max_cells": max_cells})
    else:
        resolved_x = x if x is not None else live_x
        resolved_y = y if y is not None else live_y
        resolved_z = z if z is not None else live_z
        if any(value is None for value in (resolved_x, resolved_y, resolved_z)):
            return _radar_anchor_error(sample)
        px, py, pz = int(resolved_x), int(resolved_y), int(resolved_z)
        min_x, max_x, min_z, max_z = px - radius, px + radius, pz - radius, pz + radius
        width, height = max_x - min_x + 1, max_z - min_z + 1
    if block_size is None:
        block_size = max(1, int(math.ceil(math.sqrt((width * height) / max_cells)))) if mode == "fuzzy" else 1

    # Return a short-lived complete response cache before doing expensive ROM
    # sight-ray, boulder, and raster work. The resolved anchor is part of the
    # key, so a player move naturally selects a new snapshot.
    radar_cache_key = (
        id(provider), scope, mode, int(radius), int(block_size), bool(text_map),
        int(resolved_zone), int(px), int(py), int(pz),
        int(min_x), int(max_x), int(min_z), int(max_z),
    )
    cached_response = _radar_cache_get(radar_cache_key)
    if cached_response is not None:
        cached_kind, cached_payload = cached_response
        if cached_kind == "text":
            return PlainTextResponse(cached_payload)
        return cached_payload

    # Seamless same-Matrix zone stitching: in Gen-5 outdoor worlds (e.g. Matrix 0),
    # adjacent Zones (such as Route 19 and Floccesy Town) share continuous coordinates.
    # Resolve the authoritative owner for each tile so cross-zone roads stitch seamlessly.
    matrix_id = None
    if provider is not None and hasattr(provider, "rom"):
        try:
            matrix_id = int(provider.rom.zone(resolved_zone).matrix_id)
        except Exception:
            matrix_id = None

    detailed = []
    flag_bytes = await _ensure_flag_bytes()
    sight_tiles_map, trainer_npc_map, trainer_summaries = _build_trainer_sight_rays(
        provider, resolved_zone, min_x, max_x, min_z, max_z,
        py=py,
        runtime_actors=runtime_actors,
        preferred_zone=resolved_zone,
        flag_bytes=flag_bytes,
    )
    boulder_cells, active_boulders, boulder_summaries = _scan_boulder_mechanics(
        provider, resolved_zone, runtime_actors
    )
    for tz in range(min_z, max_z + 1):
        row = []
        for tx in range(min_x, max_x + 1):
            owner = None
            if matrix_id is not None:
                owner = _matrix_zone_owner(provider, matrix_id, tx, tz, preferred_zone=resolved_zone)
            effective_zone = owner if owner is not None else resolved_zone
            is_player = (tx == live_x and tz == live_z and (live_zone in (effective_zone, resolved_zone)))
            cell = _radar_cell(
                provider, effective_zone, tx, py, tz,
                is_player=is_player,
                runtime_actors=runtime_actors,
                trainer_sights=sight_tiles_map,
                trainer_npcs=trainer_npc_map,
                boulder_cells=boulder_cells,
                mode=mode,
                fast_preview=(mode in {"coarse", "fuzzy"}),
                flag_bytes=flag_bytes,
            )
            cell["zone_id"] = int(effective_zone)
            cell["zone_name"] = _resolve_zone_name(int(effective_zone), provider)
            row.append(cell)
        detailed.append(row)
    grid = _aggregate_fuzzy(detailed, int(block_size)) if mode == "fuzzy" else detailed
    ascii_map = _render_ascii(grid)
    diameter = width if width == height else None
    response = {
        "format": "black2-spatial-grid-radar/v2",
        "scope": scope,
        "coordinate_space": "gen5-field-grid-v1",
        "zone_id": resolved_zone,
        "zone_name": _resolve_zone_name(resolved_zone, provider),
        "center": {"x": px, "y": py, "z": pz},
        "facing": facing, "facing_zh": facing_zh,
        "mode": mode, "block_size": int(block_size), "radius": radius if scope == "local" else None,
        "diameter": diameter,
        "bounds": {"min_x": min_x, "max_x": max_x, "min_z": min_z, "max_z": max_z, "width": width, "height": height},
        "legend": _LEGEND,
        "ascii_map": ascii_map,
        "grid": grid,
        "source": "ROM terrain + event candidates; runtime actor/flag/landing checks remain authoritative",
    }
    if text_map:
        warp_summaries = []
        seen_warp_coords = set()
        for row in grid:
            for cell in row:
                if cell.get("symbol") == "D" or any(e.get("kind") == "warp" for e in cell.get("events", [])):
                    coords = (cell.get("x"), cell.get("z"))
                    if coords in seen_warp_coords:
                        continue
                    seen_warp_coords.add(coords)
                    inter = cell.get("interaction") or {}
                    target = inter.get("target_zone_name") or f"Zone {inter.get('target_zone_id')}"
                    fac = f" [{inter.get('facility_type')}]" if inter.get("facility_type") else ""
                    portal = inter.get("portal_tile")
                    p_str = f" (门体位于 Z={portal['z']})" if portal and portal.get("z") != cell["z"] else ""
                    warp_summaries.append(f"- (X={cell['x']:3d}, Z={cell['z']:3d}) [D] 门口待命格 ➔ 前往: {target}{fac}{p_str}")
        sign_summaries = []
        seen_sign_coords = set()
        for row in grid:
            for cell in row:
                if cell.get("symbol") == "S" or any(e.get("kind") == "signpost" for e in cell.get("events", [])):
                    inter = cell.get("interaction") or {}
                    center = inter.get("center_tile") or {"x": cell["x"], "z": cell["z"]}
                    coords = (center.get("x"), center.get("z"))
                    if coords in seen_sign_coords:
                        continue
                    seen_sign_coords.add(coords)
                    cat = inter.get("sign_category") or "标识牌"
                    stand = inter.get("stand_tile") or {}
                    stand_str = f"(X={stand.get('x')}, Z={stand.get('z')})" if stand else "正面中间"
                    sign_summaries.append(f"- (X={center.get('x'):3d}, Z={center.get('z'):3d}) [{cat}] ➔ 必须在 {stand_str} 面向上方 (North) 按 A 读取 [空间记忆]")
        gate_summaries = []
        seen_gate_coords = set()
        for row in grid:
            for cell in row:
                if cell.get("story_gate_trigger") and not cell.get("story_gate"):
                    continue
                if cell.get("symbol") == "!" or cell.get("story_gate"):
                    coords = (cell.get("x"), cell.get("z"))
                    if coords in seen_gate_coords:
                        continue
                    seen_gate_coords.add(coords)
                    g = cell.get("story_gate") or {}
                    dest = g.get("target_destination") or "前方区域"
                    cond = g.get("condition") or "推进主线剧情"
                    fid = g.get("flag_id")
                    intercept = g.get("intercept_trigger_tiles") or []
                    talk = g.get("talk_trigger_tiles") or []
                    inter_str = "、".join(f"(X={t['x']}, Z={t['z']})" for t in intercept) if intercept else "靠近踩入"
                    if talk:
                        t_facing = talk[0].get('facing', 'up')
                        t_dir_zh = {"right": "东 (East)", "up": "北 (North)", "left": "西 (West)", "down": "南 (South)"}.get(t_facing, t_facing)
                        talk_str = f"(X={talk[0]['x']}, Z={talk[0]['z']}) 面向 {t_dir_zh}"
                    else:
                        talk_str = "正面相邻格"
                    passable = g.get("passable_destination")
                    pass_str = f"\n  * 放行路线: 朝【{g.get('passable_direction', 'East')}】前往 [{passable}] 完全畅通 (当前主线正解)" if passable else ""
                    rec = g.get("recommended_action")
                    rec_str = f"\n  * 导航建议: {rec}" if rec else ""
                    gate_summaries.append(
                        f"- 封路NPC实体: (X={cell['x']:3d}, Z={cell['z']:3d}) [N] {g.get('name', '剧情路障')} ➔ 阻挡前往 [{dest}]\n"
                        f"  * 拦截触发线: {inter_str} [!] 强制截停触发事件\n"
                        f"  * 主动对话格: 在 {talk_str} 按 A 读取封路原因\n"
                        f"  * 放行条件: {cond} (旗标 {fid} 达成后在内存自动放行)"
                        f"{pass_str}{rec_str}"
                    )
        elevation_summaries = []
        seen_elev_coords = set()
        for row in grid:
            for cell in row:
                elev = cell.get("elevation_transition")
                if elev:
                    coords = (cell.get("x"), cell.get("z"))
                    if coords in seen_elev_coords:
                        continue
                    seen_elev_coords.add(coords)
                    sym = elev.get("symbol", "▲")
                    etype = elev.get("type")
                    edir = elev.get("direction") or ""
                    h_val = f" (标高: {elev['relative_height']:.1f})" if isinstance(elev.get("relative_height"), (int, float)) else ""
                    elevation_summaries.append(f"- (X={cell['x']:3d}, Z={cell['z']:3d}) [{sym}] {cell.get('kind', etype)} ➔ 走向: {edir}{h_val}")
        elevation_block = ("\n\n高度跃迁与楼梯列表 (Elevation Transitions):\n" + "\n".join(elevation_summaries)) if elevation_summaries else ""
        shore_summaries = []
        seen_shore_coords = set()
        for row in grid:
            for cell in row:
                if cell.get("symbol") == "~" or cell.get("water_transition"):
                    coords = (cell.get("x"), cell.get("z"))
                    if coords in seen_shore_coords:
                        continue
                    seen_shore_coords.add(coords)
                    shore_summaries.append(f"- (X={cell['x']:3d}, Z={cell['z']:3d}) [~] 水岸/河堤跃迁格 ➔ 可触发冲浪下水或跳跃上岸 (需冲浪能力)")
        shore_block = ("\n\n水岸与冲浪跃迁点 (Surf & Shoreline):\n" + "\n".join(shore_summaries)) if shore_summaries else ""
        gate_block = ("\n\n剧情封路与道闸 (Story Gates & Roadblocks):\n" + "\n".join(gate_summaries)) if gate_summaries else ""
        sign_block = ("\n\n标识牌/路牌列表 (Signposts):\n" + "\n".join(sign_summaries)) if sign_summaries else ""
        warp_block = ("\n\n门/出入口与传送目的地 (Doors & Warps):\n" + "\n".join(warp_summaries)) if warp_summaries else ""
        trainer_block = ("\n\n训练家对战视线警戒 (Active Trainer Sights):\n" + "\n".join(trainer_summaries)) if trainer_summaries else ""
        underpass_summaries = []
        seen_underpass_coords = set()
        for row in grid:
            for cell in row:
                if cell.get("symbol") == "∩" or cell.get("is_underpass"):
                    coords = (cell.get("x"), cell.get("z"))
                    if coords in seen_underpass_coords:
                        continue
                    seen_underpass_coords.add(coords)
                    uby = cell.get("upper_bridge_y")
                    ub_str = f"Y={uby}" if uby is not None else "高架桥"
                    underpass_summaries.append(f"- (X={cell['x']:3d}, Z={cell['z']:3d}) [∩] 桥下立体穿行通道 ➔ 上方跨越: {ub_str} | 下方通行: 平坦完全连通 (Walkable)")
        underpass_block = ("\n\n桥下立体穿行通道 (Overpass & Underpasses):\n" + "\n".join(underpass_summaries)) if underpass_summaries else ""
        boulder_block = ("\n\n场景机关与怪力巨石状态 (Boulders & Field Mechanics):\n" + "\n".join(boulder_summaries)) if boulder_summaries else "" 
        extra_blocks = trainer_block + boulder_block + underpass_block + elevation_block + shore_block + gate_block + sign_block + warp_block
        title = f"=== 区域: {response['zone_name']} | 中心: (X={px}, Z={pz}, Y={py}) | 范围: {width}x{height} | mode={mode} ==="
        selected_legend = _LEGEND_DETAIL if mode == "detail" else _LEGEND_COARSE
        rendered_text = title + "\n\n" + ascii_map + extra_blocks + "\n\n图例说明:\n" + "\n".join(f"{k} {v}" for k, v in selected_legend.items())
        _radar_cache_put(radar_cache_key, ("text", rendered_text))
        return PlainTextResponse(rendered_text)
    _radar_cache_put(radar_cache_key, ("json", response))
    return response


@router.get("/stairs")
async def navigation_stairs(
    zone_id: int | None = Query(None),
) -> dict[str, Any]:
    """Expose discovered staircase corridors and cross-floor transitions for AI."""
    radar_sample = await _radar_runtime_sample()
    sample, live_zone, live_x, live_y, live_z, facing, facing_zh = _player_anchor(radar_sample)
    target_zone = int(zone_id if zone_id is not None else (live_zone or 457))
    provider = navigation_static_provider()
    corridors = staircase_corridor_service.analyze_zone(target_zone)
    return {
        "format": "black2-navigation-stairs/v1",
        "zone_id": target_zone,
        "zone_name": _resolve_zone_name(target_zone, provider),
        "total_corridors": len(corridors),
        "corridors": [
            {
                **c.as_dict(),
                "lower_floor_y": c.lower_portal.get("floor_y") if c.lower_portal else None,
                "upper_floor_y": c.upper_portal.get("floor_y") if c.upper_portal else None,
                "ai_guidance": (
                    f"楼梯走廊 {c.corridor_id}：沿 {c.rising_direction} 上行至楼层 "
                    f"Y={c.upper_portal.get('floor_y') if c.upper_portal else '?'}，"
                    f"反向通往楼层 Y={c.lower_portal.get('floor_y') if c.lower_portal else '?'}"
                ),
            }
            for c in corridors
        ],
    }


@router.get("/story-roadblocks")
async def navigation_story_roadblocks(
    zone_id: int | None = Query(None),
) -> dict[str, Any]:
    """Query all currently active story roadblocks, blocking NPCs, and exact impassable coordinates."""
    provider = navigation_static_provider()
    radar_sample = await _radar_runtime_sample()
    sample, live_zone, live_x, live_y, live_z, _, _ = _player_anchor(radar_sample)
    runtime_payload = await _runtime_actor_sample()
    runtime_actors = runtime_payload.get("actors", []) if isinstance(runtime_payload, dict) else None

    resolved_zone = int(zone_id if zone_id is not None else (live_zone if live_zone else 427))
    works_u16 = await _ensure_works_u16()

    _, active_roadblocks, impassable_coords = _scan_active_story_triggers(
        provider, resolved_zone, works_u16=works_u16, runtime_actors=runtime_actors,
    )

    zone_name = _resolve_zone_name(resolved_zone, provider) if provider else f"Zone {resolved_zone}"

    return {
        "format": "black2-story-roadblocks/v1",
        "status": "ready",
        "zone_id": resolved_zone,
        "zone_name": zone_name,
        "active_roadblocks_count": len(active_roadblocks),
        "active_roadblocks": active_roadblocks,
        "impassable_coordinates_count": len(impassable_coords),
        "impassable_coordinates": impassable_coords,
        "player_location": {
            "zone_id": live_zone,
            "x": live_x,
            "y": live_y,
            "z": live_z,
        },
    }

@router.get("/topology")
async def navigation_topology(
    zone_id: int | None = Query(None),
) -> dict[str, Any]:
    """Compact high-level semantic topology view for LLM Agents (<300 tokens)."""
    radar_sample = await _radar_runtime_sample()
    sample, live_zone, live_x, live_y, live_z, facing, facing_zh = _player_anchor(radar_sample)
    target_zone = int(zone_id if zone_id is not None else (live_zone or 457))
    provider = navigation_static_provider()
    corridors = staircase_corridor_service.analyze_zone(target_zone)
    floor_layers = sorted({c.lower_portal.get("floor_y") for c in corridors if c.lower_portal} |
                          {c.upper_portal.get("floor_y") for c in corridors if c.upper_portal})
    if not floor_layers:
        floor_layers = [0]
    stairs_graph = [
        {
            "corridor_id": c.corridor_id,
            "connects": [c.lower_portal.get("floor_y"), c.upper_portal.get("floor_y")],
            "lower_portal": {"x": c.lower_portal["x"], "z": c.lower_portal["z"], "y": c.lower_portal.get("floor_y", 0)},
            "upper_portal": {"x": c.upper_portal["x"], "z": c.upper_portal["z"], "y": c.upper_portal.get("floor_y", 2)},
            "rising_direction": c.rising_direction,
            "steps": c.total_steps,
        }
        for c in corridors if c.lower_portal and c.upper_portal
    ]
    return {
        "format": "black2-navigation-topology/v1",
        "zone_id": target_zone,
        "zone_name": _resolve_zone_name(target_zone, provider),
        "player": {"x": live_x, "y": live_y, "z": live_z, "facing": facing},
        "available_floors": floor_layers,
        "stairs": stairs_graph,
        "policy_recommendation": {
            "avoid_wild_battles": "Use encounter_grass='soft_avoid' (default)",
            "hunt_wild_pokemon": "Use encounter_grass='allow'",
            "cross_layer": "Navigate to lower_portal or upper_portal first to switch floors",
        },
    }
