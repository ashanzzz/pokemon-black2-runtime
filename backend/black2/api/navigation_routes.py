"""Stable public API for read-only navigation planning."""
from __future__ import annotations

import math
import inspect
from typing import Any, Callable, Literal

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from ..bizhawk.bridge_client import BridgeClient
from ..world.navigation_planning import NavigationPlanService, NavigationPlanningError
from ..world.navigation_planning import normalize_occupancy
from ..world.navigation_tasks import NavigationTaskService
from ..world.navigation_audit import navigation_audit_log
from ..world.observed_navigation import NavNode, observed_navigation_graph
from ..world.player_coordinates import canonical_grid_player
from ..world.runtime_player_state import player_runtime_service


_control_sample: Callable[[], dict[str, Any] | None] = lambda: None
_STATIC_UNSET = object()
_static_provider_cache: Any = _STATIC_UNSET
_runtime_reader: Any | None = None


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
    interaction: NavigationInteraction | None = None
    movement_mode: Literal["auto", "walk", "run", "bike", "surf"] = "auto"
    navigation_intent: Literal["route", "walk_to_tile", "interact"] = "walk_to_tile"


_planner = NavigationPlanService(
    observed_navigation_graph,
    lambda: player_runtime_service.latest,
    static_provider=_default_static_provider,
)
_tasks: NavigationTaskService | None = None


async def _live_player_sample() -> dict[str, Any] | None:
    if _runtime_reader is not None:
        try:
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
) -> None:
    """Bind the shared planner and optional application-owned input client."""
    global _planner, _tasks, _control_sample, _runtime_reader
    # Tests and embedded callers may reconfigure the module repeatedly.  A
    # missing reader explicitly means "do not perform an extra live actor
    # sample" for that configuration, rather than leaking a prior app setup.
    _runtime_reader = runtime_reader
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
        )


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: GridDestination | GlobalGridDestination
    max_steps: int = Field(default=10000, ge=1, le=10000)
    occupancy: list[NavigationOccupancy] = Field(default_factory=list)
    interaction: NavigationInteraction | None = None
    movement_mode: Literal["auto", "walk", "run", "bike", "surf"] = "auto"
    navigation_intent: Literal["route", "walk_to_tile", "interact"] = "walk_to_tile"


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

    merged = normalize_occupancy(
        [*supplied_points, *live_points], default_zone=zone_id, default_y=y,
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


async def _runtime_actor_sample() -> dict[str, Any] | None:
    """Read the bounded ActorSystem snapshot used while following an NPC."""
    if _runtime_reader is None:
        return None
    from ..world.runtime_actor_overlay import runtime_actor_overlay_service

    payload = runtime_actor_overlay_service.sample(_runtime_reader)
    if inspect.isawaitable(payload):
        payload = await payload
    return payload if isinstance(payload, dict) else None


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
    navigation_intent: Literal["route", "walk_to_tile", "interact"] = "walk_to_tile"


@router.get("/capabilities")
async def navigation_capabilities() -> dict[str, Any]:
    result = _planner.capabilities()
    result["planning"]["observations_endpoint"] = "/api/v1/navigation/observations?zone_id="
    configured = _tasks is not None
    bridge_connected = bool(configured and getattr(_tasks.client, "is_connected", False))
    result["execution"] = {
        "available": bridge_connected,
        "configured": configured,
        "bridge_connected": bridge_connected,
        "same_zone": True,
        "cross_zone": False,
        "evidence": "observed_edges_then_rom_static_candidates_with_closed_loop_verification",
        "closed_loop_player_runtime_verification": True,
    }
    return result


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


def _snap_npc_interaction(
    provider: Any, *, zone_id: int, target: NavNode, player_sample: dict[str, Any] | None,
    occupancy: list[dict[str, Any]], max_radius: int, actor_id: str | int | None = None,
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
            surface = provider.surface_at(zone_id, stand.x, stand.z, stand.y, anchor=None)
        except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
            continue
        if not surface.get("walkable"):
            continue
        distance = abs(stand.x - target.x) + abs(stand.z - target.z)
        route = None
        if live_node is not None and live_node.zone_id == zone_id:
            if live_node.y != stand.y:
                continue
            try:
                route = provider.find_path(
                    live_node, stand, player_sample=player_sample, occupied=occupancy,
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
        "cell": getattr(provider, "surface_at")(zone_id, stand.x, stand.z, stand.y, anchor=None).get("cell"),
        "interaction": interaction,
        "route_preview": route,
    }


def _auto_interaction_goal(
    destination: dict[str, Any], *, provider: Any | None,
    player_sample: dict[str, Any] | None, occupancy: list[dict[str, Any]],
    interaction: dict[str, Any] | None,
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
        max_radius=12,
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
            occupancy=occupancy, interaction=None,
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
    occupancy = [item.model_dump(exclude_none=True) for item in body.occupancy]
    picked_kind = body.picked.kind if body.picked else None
    force_adjacent = str(picked_kind or "").lower() in {"building", "terrain_object", "furniture", "door", "npc", "actor"}
    try:
        result = provider.snap(
            int(target_zone), gx, gz, gy, player_sample=target_sample, occupied=occupancy,
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
                route_preview = finder(start, matrix_id=int(matrix_id), x=global_target["x"], y=global_target["y"], z=global_target["z"], player_sample=player_runtime_service.latest, occupied=occupancy)
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
        "route_preview": route_preview,
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
            surface = describer(zone_id, grid[0], grid[1], y, anchor=None)
        except (IndexError, KeyError, RuntimeError, ValueError, TypeError):
            surface = None
    route_preview = None
    if sample is not None:
        player = canonical_grid_player(sample, require_resolved=False)
        start = NavNode.from_player(player)
        finder = getattr(provider, "find_path", None)
        if start is not None and callable(finder):
            try:
                route_preview = finder(start, NavNode(zone_id, target["x"], target["y"], target["z"]),
                                       player_sample=sample, occupied=occupancy)
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


@router.post("/plans")
async def create_navigation_plan(body: PlanRequest, request: Request):
    try:
        occupancy_zone, occupancy_y, sample = _request_occupancy_context(body.destination)
        occupancy, _occupancy_meta = await _navigation_occupancy(
            occupancy_zone, occupancy_y,
            [item.model_dump(exclude_none=True) for item in body.occupancy],
        )
        destination, interaction = _prepare_navigation_request(
            body.destination.model_dump(),
            provider=_planner._resolve_static_provider(),
            player_sample=sample,
            occupancy=occupancy,
            interaction=body.interaction.model_dump() if body.interaction is not None else None,
            navigation_intent=body.navigation_intent,
        )
        plan = _planner.create_plan(
            destination,
            body.start.model_dump() if body.start is not None else None,
            occupied=occupancy,
            interaction=interaction,
            movement_mode=body.movement_mode,
            navigation_intent=body.navigation_intent,
        )
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
        occupancy, _occupancy_meta = await _navigation_occupancy(
            occupancy_zone, occupancy_y,
            [item.model_dump(exclude_none=True) for item in body.occupancy],
        )
        destination, interaction = _prepare_navigation_request(
            body.destination.model_dump(),
            provider=_planner._resolve_static_provider(),
            player_sample=sample,
            occupancy=occupancy,
            interaction=body.interaction.model_dump() if body.interaction is not None else None,
            navigation_intent=body.navigation_intent,
        )
        task = _task_service().start(
            destination,
            max_steps=body.max_steps,
            occupied=occupancy,
            interaction=interaction,
            movement_mode=body.movement_mode,
            navigation_intent=body.navigation_intent,
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
