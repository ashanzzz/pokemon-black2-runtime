"""High-level same-Zone patrol tasks over exact Encounter Region tile sets."""
from __future__ import annotations

import asyncio
import copy
import inspect
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .encounter_regions import EncounterRegionService
from .navigation_planning import NavigationPlanService, NavigationPlanningError
from .navigation_tasks import NavigationTaskService
from .player_coordinates import canonical_grid_player


_ACTIVE = {"queued", "entering_region", "patrolling", "cancelling"}
_TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _grid(sample: dict[str, Any] | None) -> dict[str, Any] | None:
    player = canonical_grid_player(sample, require_resolved=False)
    if not isinstance(player, dict):
        return None
    position = player.get("position") if isinstance(player.get("position"), dict) else player
    grid = position.get("grid") if isinstance(position, dict) and isinstance(position.get("grid"), dict) else player.get("grid")
    if not isinstance(grid, dict):
        return None
    try:
        return {
            "zone_id": int(player.get("zone_id")),
            "x": int(grid["x"]), "y": int(grid["y"]), "z": int(grid["z"]),
        }
    except (KeyError, TypeError, ValueError):
        return None


class EncounterTaskError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 409, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class EncounterTaskService:
    """Orchestrate safe navigation while keeping patrol movement inside a Region."""

    def __init__(
        self,
        regions: EncounterRegionService,
        planner: NavigationPlanService,
        navigation: NavigationTaskService,
        player_sample: Callable[[], dict[str, Any] | None],
        *,
        occupancy_sample: Callable[[int, int, Any], Awaitable[tuple[list[dict[str, Any]], dict[str, Any]]] | tuple[list[dict[str, Any]], dict[str, Any]]] | None = None,
        poll_seconds: float = 0.05,
        blocker_retry_seconds: float = 0.12,
        blocker_retry_count: int = 8,
    ) -> None:
        self.regions = regions
        self.planner = planner
        self.navigation = navigation
        self.player_sample = player_sample
        self.occupancy_sample = occupancy_sample
        self.poll_seconds = max(0.02, float(poll_seconds))
        self.blocker_retry_seconds = max(0.05, float(blocker_retry_seconds))
        self.blocker_retry_count = max(1, int(blocker_retry_count))
        self._tasks: dict[str, dict[str, Any]] = {}
        self._runners: dict[str, asyncio.Task] = {}

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in record.items() if not key.startswith("_")}

    def _live_grid(self) -> dict[str, Any]:
        grid = _grid(self.player_sample())
        if grid is None:
            raise EncounterTaskError("ENCOUNTER_PLAYER_UNRESOLVED", "PlayerRuntime does not expose a resolved Zone/GPos.")
        return grid

    @staticmethod
    def _destination(point: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "grid", "space": "gen5-field-grid-v1",
            "zone_id": int(point["zone_id"]), "x": int(point["x"]),
            "y": int(point["y"]), "z": int(point["z"]),
        }

    @staticmethod
    def _contains(region: dict[str, Any], grid: dict[str, Any]) -> bool:
        key = (int(grid["zone_id"]), int(grid["x"]), int(grid["y"]), int(grid["z"]))
        return any(
            key == (int(tile["zone_id"]), int(tile["x"]), int(tile["y"]), int(tile["z"]))
            for tile in region.get("tiles") or []
        )

    async def _occupancy(self, zone_id: int, y: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if self.occupancy_sample is None:
            return [], {"source": "none", "count": 0}
        value = self.occupancy_sample(int(zone_id), int(y), ())
        if inspect.isawaitable(value):
            value = await value
        if not isinstance(value, tuple) or len(value) != 2:
            return [], {"source": "unresolved", "count": 0}
        points, meta = value
        return list(points or []), dict(meta or {})

    async def _choose_entry(self, region: dict[str, Any], movement_mode: str) -> dict[str, Any]:
        live = self._live_grid()
        occupancy, occupancy_meta = await self._occupancy(live["zone_id"], live["y"])
        candidates = region.get("entry_tiles") or region.get("boundary_tiles") or region.get("tiles") or []
        candidates = sorted(
            candidates,
            key=lambda point: (
                abs(int(point["x"]) - live["x"]) + abs(int(point["z"]) - live["z"]),
                int(point["z"]), int(point["x"]),
            ),
        )
        last_error: Exception | None = None
        for point in candidates[:24]:
            try:
                plan = self.planner.create_plan(
                    self._destination(point), occupied=occupancy,
                    movement_mode=movement_mode, navigation_intent="walk_to_tile",
                )
                return {"tile": point, "plan": plan, "occupancy": occupancy_meta}
            except NavigationPlanningError as exc:
                last_error = exc
        raise EncounterTaskError(
            "ENCOUNTER_REGION_UNREACHABLE",
            "No reachable entry tile was found for the selected Encounter Region.",
            details={"region_id": region.get("region_id"), "last_error": str(last_error) if last_error else None},
        )

    @staticmethod
    def _strategy_route(region: dict[str, Any], requested: str) -> tuple[str, list[dict[str, Any]]]:
        patrol = region.get("patrol") or {}
        if not patrol.get("possible"):
            raise EncounterTaskError(
                "ENCOUNTER_PATROL_UNAVAILABLE",
                patrol.get("reason") or "The region does not contain enough connected tiles for patrol.",
                details={"region_id": region.get("region_id")},
            )
        recommended = str(patrol.get("recommended_strategy") or "line_shuttle")
        strategy = recommended if requested == "auto" else requested
        raw_route = list(patrol.get("route") or [])
        if strategy == "ping_pong":
            if len(raw_route) < 2:
                raise EncounterTaskError("ENCOUNTER_PATROL_UNAVAILABLE", "ping_pong requires at least two route tiles.")
            route = [raw_route[0], raw_route[-1]]
        elif strategy == "line_shuttle":
            if len(raw_route) < 2:
                raise EncounterTaskError("ENCOUNTER_PATROL_UNAVAILABLE", "line_shuttle requires at least two route tiles.")
            route = [raw_route[0], raw_route[-1]]
        elif strategy == "loop":
            if len(raw_route) < 3:
                raise EncounterTaskError("ENCOUNTER_PATROL_UNAVAILABLE", "loop requires a cyclic route with at least three waypoints.")
            route = raw_route
        else:
            raise EncounterTaskError(
                "ENCOUNTER_INVALID_STRATEGY",
                "strategy must be auto, ping_pong, loop, or line_shuttle.",
                status_code=422,
            )
        return strategy, route

    def start(
        self,
        *,
        region_id: str,
        zone_id: int,
        y: int,
        strategy: str = "auto",
        movement_mode: str = "auto",
        max_cycles: int = 1000,
        max_steps_per_leg: int = 2000,
    ) -> dict[str, Any]:
        if any(record.get("status") in _ACTIVE for record in self._tasks.values()):
            raise EncounterTaskError("ENCOUNTER_INPUT_BUSY", "Another encounter patrol task is active.", status_code=423)
        sample = self.player_sample()
        live = _grid(sample)
        if live is None:
            raise EncounterTaskError("ENCOUNTER_PLAYER_UNRESOLVED", "PlayerRuntime does not expose a resolved Zone/GPos.")
        if int(live["zone_id"]) != int(zone_id):
            raise EncounterTaskError(
                "ENCOUNTER_CROSS_ZONE_UNVERIFIED",
                "Encounter patrol execution is currently same-Zone only.",
                details={"player_zone_id": live["zone_id"], "region_zone_id": int(zone_id)},
            )
        region = self.regions.find_region(region_id, int(zone_id), int(y), player_sample=sample)
        if region is None:
            raise EncounterTaskError("ENCOUNTER_REGION_NOT_FOUND", "Encounter Region was not found.", status_code=404)
        if region.get("encounter_method") == "surf_candidate":
            transport = ((sample or {}).get("locomotion") or {}).get("transport_mode")
            if transport != "Surf":
                raise EncounterTaskError(
                    "ENCOUNTER_SURF_NOT_ACTIVE",
                    "Surf candidate patrol requires PlayerRuntime transport_mode='Surf'; navigation does not auto-start Surf.",
                    details={"transport_mode": transport},
                )
        selected_strategy, route = self._strategy_route(region, strategy)
        task_id = f"enc_{uuid4().hex}"
        record = {
            "format": "black2-encounter-task/v1",
            "task_id": task_id,
            "status": "queued",
            "created_at": _now(), "updated_at": _now(),
            "region": {
                "region_id": region["region_id"], "zone_id": region["zone_id"], "y": region["y"],
                "terrain_kind": region["terrain_kind"], "encounter_method": region["encounter_method"],
                "tile_count": region["tile_count"], "evidence": region["evidence"],
            },
            "strategy": selected_strategy,
            "movement_mode": movement_mode,
            "until": "overworld_interrupted",
            "battle_confirmed": False,
            "progress": {"cycles_completed": 0, "max_cycles": int(max_cycles), "legs_completed": 0, "blocker_waits": 0},
            "current_navigation_task": None,
            "last_navigation_task": None,
            "stop_reason": None,
            "warnings": [
                "Overworld interruption stops patrol safely but is not promoted to a confirmed wild battle until BattleRuntime is verified."
            ],
            "_region": region,
            "_route": route,
            "_max_steps_per_leg": int(max_steps_per_leg),
        }
        self._tasks[task_id] = record
        self._runners[task_id] = asyncio.create_task(self._run(task_id), name=f"encounter:{task_id}")
        return self._public(record)

    def get(self, task_id: str) -> dict[str, Any]:
        record = self._tasks.get(task_id)
        if record is None:
            raise EncounterTaskError("ENCOUNTER_TASK_NOT_FOUND", "Encounter task was not found.", status_code=404)
        return self._public(record)

    async def cancel(self, task_id: str) -> dict[str, Any]:
        record = self._tasks.get(task_id)
        if record is None:
            raise EncounterTaskError("ENCOUNTER_TASK_NOT_FOUND", "Encounter task was not found.", status_code=404)
        if record.get("status") in _TERMINAL:
            return self._public(record)
        record["status"] = "cancelling"; record["updated_at"] = _now()
        nav = record.get("current_navigation_task") or {}
        nav_id = nav.get("task_id") if isinstance(nav, dict) else None
        if nav_id:
            try:
                await self.navigation.cancel(str(nav_id))
            except NavigationPlanningError:
                pass
        runner = self._runners.get(task_id)
        if runner and not runner.done():
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        if record.get("status") not in _TERMINAL:
            record["status"] = "cancelled"
            record["stop_reason"] = {"code": "ENCOUNTER_CANCELLED", "message": "Encounter patrol was cancelled."}
            record["updated_at"] = _now()
        return self._public(record)

    async def _wait_navigation(self, record: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        task_id = str(task["task_id"])
        record["current_navigation_task"] = task
        while True:
            status = self.navigation.get(task_id)
            record["current_navigation_task"] = status
            record["updated_at"] = _now()
            if status.get("status") not in {"queued", "prechecking", "executing", "cancelling"}:
                record["last_navigation_task"] = status
                record["current_navigation_task"] = None
                return status
            await asyncio.sleep(self.poll_seconds)

    @staticmethod
    def _interruption(status: dict[str, Any]) -> bool:
        reason = status.get("stop_reason") or {}
        return status.get("status") == "failed" and reason.get("code") == "NAV_NOT_CONTROLLABLE"

    async def _navigate(
        self,
        record: dict[str, Any],
        point: dict[str, Any],
        *,
        allowed_nodes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        last_error: NavigationPlanningError | None = None
        for attempt in range(self.blocker_retry_count):
            live = self._live_grid()
            occupancy, occupancy_meta = await self._occupancy(live["zone_id"], live["y"])
            record["runtime_occupancy"] = occupancy_meta
            try:
                task = self.navigation.start(
                    self._destination(point),
                    max_steps=int(record["_max_steps_per_leg"]),
                    occupied=occupancy,
                    movement_mode=str(record["movement_mode"]),
                    navigation_intent="walk_to_tile",
                    allowed_nodes=allowed_nodes or (),
                )
                return await self._wait_navigation(record, task)
            except NavigationPlanningError as exc:
                last_error = exc
                if exc.code not in {"NAV_DESTINATION_OCCUPIED", "NAV_NO_ROUTE"} or attempt + 1 >= self.blocker_retry_count:
                    raise
                record["progress"]["blocker_waits"] = int(record["progress"].get("blocker_waits", 0)) + 1
                record["updated_at"] = _now()
                await asyncio.sleep(self.blocker_retry_seconds)
        assert last_error is not None
        raise last_error

    async def _run(self, task_id: str) -> None:
        record = self._tasks[task_id]
        region = record["_region"]
        allowed = list(region.get("tiles") or [])
        try:
            live = self._live_grid()
            if not self._contains(region, live):
                record["status"] = "entering_region"; record["updated_at"] = _now()
                entry = (await self._choose_entry(region, str(record["movement_mode"])))["tile"]
                result = await self._navigate(record, entry)
                if self._interruption(result):
                    record["status"] = "interrupted"
                    record["stop_reason"] = {
                        "code": "ENCOUNTER_OVERWORLD_INTERRUPTED",
                        "message": "Navigation lost controllable OVERWORLD state while entering the region; movement is stopped safely.",
                        "battle_confirmed": False,
                    }
                    return
                if result.get("status") != "succeeded":
                    record["status"] = "failed"
                    record["stop_reason"] = {
                        "code": "ENCOUNTER_ENTRY_FAILED", "message": "Navigation to the region entry failed.",
                        "navigation": result.get("stop_reason"),
                    }
                    return
            live = self._live_grid()
            if not self._contains(region, live):
                record["status"] = "failed"
                record["stop_reason"] = {
                    "code": "ENCOUNTER_ENTRY_NOT_CONFIRMED",
                    "message": "Navigation completed but PlayerRuntime is not inside the exact region Tile Set.",
                    "player": live,
                }
                return

            route = list(record["_route"])
            # Move to a deterministic patrol waypoint while already constrained
            # to the region, then begin the repeating sequence.
            nearest_index = min(
                range(len(route)),
                key=lambda i: abs(int(route[i]["x"]) - live["x"]) + abs(int(route[i]["z"]) - live["z"]),
            )
            if (live["x"], live["z"]) != (int(route[nearest_index]["x"]), int(route[nearest_index]["z"])):
                result = await self._navigate(record, route[nearest_index], allowed_nodes=allowed)
                if result.get("status") != "succeeded":
                    if self._interruption(result):
                        record["status"] = "interrupted"
                        record["stop_reason"] = {"code": "ENCOUNTER_OVERWORLD_INTERRUPTED", "message": "OVERWORLD was interrupted during patrol setup.", "battle_confirmed": False}
                    else:
                        record["status"] = "failed"
                        record["stop_reason"] = {"code": "ENCOUNTER_PATROL_SETUP_FAILED", "message": "Could not reach a patrol waypoint inside the region.", "navigation": result.get("stop_reason")}
                    return

            record["status"] = "patrolling"; record["updated_at"] = _now()
            strategy = str(record["strategy"])
            index = nearest_index
            direction = 1
            max_cycles = int(record["progress"]["max_cycles"])
            while record["progress"]["cycles_completed"] < max_cycles:
                if strategy == "loop":
                    next_index = (index + 1) % len(route)
                    if next_index == 0:
                        record["progress"]["cycles_completed"] += 1
                else:
                    next_index = index + direction
                    if next_index >= len(route) or next_index < 0:
                        direction *= -1
                        next_index = index + direction
                        record["progress"]["cycles_completed"] += 1
                target = route[next_index]
                result = await self._navigate(record, target, allowed_nodes=allowed)
                record["progress"]["legs_completed"] += 1
                record["updated_at"] = _now()
                if self._interruption(result):
                    record["status"] = "interrupted"
                    record["stop_reason"] = {
                        "code": "ENCOUNTER_OVERWORLD_INTERRUPTED",
                        "message": "The controllable OVERWORLD state ended during patrol. Inputs were cleared; battle identity remains unresolved.",
                        "battle_confirmed": False,
                    }
                    return
                if result.get("status") != "succeeded":
                    record["status"] = "failed"
                    record["stop_reason"] = {
                        "code": "ENCOUNTER_PATROL_NAVIGATION_FAILED",
                        "message": "A constrained patrol leg failed.",
                        "navigation": result.get("stop_reason"),
                    }
                    return
                live = self._live_grid()
                if not self._contains(region, live):
                    record["status"] = "failed"
                    record["stop_reason"] = {
                        "code": "ENCOUNTER_REGION_ESCAPE",
                        "message": "Invariant violation: PlayerRuntime left the exact Encounter Region Tile Set.",
                        "player": live,
                    }
                    return
                index = next_index

            record["status"] = "succeeded"
            record["stop_reason"] = {
                "code": "ENCOUNTER_CYCLE_LIMIT_REACHED",
                "message": "Patrol cycle limit reached without a confirmed battle signal.",
                "battle_confirmed": False,
            }
        except asyncio.CancelledError:
            if record.get("status") not in _TERMINAL:
                record["status"] = "cancelled"
                record["stop_reason"] = {"code": "ENCOUNTER_CANCELLED", "message": "Encounter patrol was cancelled."}
            raise
        except (EncounterTaskError, NavigationPlanningError) as exc:
            record["status"] = "failed"
            record["stop_reason"] = {
                "code": getattr(exc, "code", "ENCOUNTER_INTERNAL"),
                "message": getattr(exc, "message", str(exc)),
                "details": getattr(exc, "details", {}),
            }
        except Exception as exc:  # pragma: no cover - final safety fence
            record["status"] = "failed"
            record["stop_reason"] = {"code": "ENCOUNTER_INTERNAL", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            record["updated_at"] = _now()
            record["current_navigation_task"] = None
