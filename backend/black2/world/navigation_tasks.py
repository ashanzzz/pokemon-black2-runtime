"""Conservative closed-loop execution for observed same-Zone paths."""
from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
import inspect
import math
from typing import Any, Callable
from uuid import uuid4

from .navigation_planning import NavigationPlanService, NavigationPlanningError, normalize_occupancy
from .navigation_audit import navigation_audit_log
from .observed_navigation import NavNode
from .player_coordinates import canonical_grid_player


_ACTIVE = {"queued", "prechecking", "executing", "cancelling"}
_TERMINAL = {"succeeded", "failed", "cancelled"}
_IDLE_PHASES = {"Idle", "Turning", "Brake"}
_WORLD_UNITS_PER_TILE = 16.0
_WORLD_TILE_TOLERANCE = 8.001


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stop(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"status": "failed", "stop_reason": {"code": code, "message": message, "details": details}}


class NavigationTaskService:
    """Execute one verified grid edge at a time and verify every landing."""

    def __init__(
        self,
        planner: NavigationPlanService,
        client: Any,
        player_sample: Callable[[], dict[str, Any] | None],
        *,
        control_sample: Callable[[], dict[str, Any] | None] | None = None,
        actor_sample: Callable[[], Any] | None = None,
        # A Gen-5 D-pad press first turns the actor when the requested
        # direction differs from the current FaceDir.  Four frames are enough
        # when already aligned, but can be consumed entirely by the turn (as
        # observed in the live room).  Keep a single step bounded to the
        # game's one-tile movement window while allowing turn+move in one
        # queued input.
        hold_frames: int = 14,
        turn_frames: int = 1,
        step_timeout_seconds: float = 2.0,
        poll_seconds: float = 0.03,
        continuous_segment_limit: int = 8,
    ) -> None:
        self.planner = planner
        self.client = client
        self.player_sample = player_sample
        self.control_sample = control_sample or (lambda: None)
        self.actor_sample = actor_sample
        self.hold_frames = hold_frames
        # A long held direction needs a little more than the one-tile press
        # budget once the game is already in its moving state.  The value is
        # deliberately bounded by the segment limit and is still stopped by
        # clear_inputs as soon as the expected endpoint is observed.
        self.continuous_hold_frames = max(self.hold_frames, 18)
        self.turn_frames = max(1, int(turn_frames))
        self.step_timeout_seconds = step_timeout_seconds
        self.poll_seconds = poll_seconds
        self.continuous_segment_limit = max(1, int(continuous_segment_limit))
        self._tasks: dict[str, dict[str, Any]] = {}
        self._runners: dict[str, asyncio.Task] = {}
        self._input_lock = asyncio.Lock()
        self._last_landing_diagnostics: dict[str, Any] = {}

    def start(
        self,
        destination: dict[str, Any],
        *,
        max_steps: int = 2000,
        occupied: Any = (),
        interaction: dict[str, Any] | None = None,
        movement_mode: str = "auto",
        navigation_intent: str = "walk_to_tile",
    ) -> dict[str, Any]:
        # Terminal status is published after input clear, so active status also
        # covers the preceding owner's cleanup interval.
        if any(record.get("status") in _ACTIVE for record in self._tasks.values()):
            raise NavigationPlanningError(
                "NAV_INPUT_BUSY", "Another navigation task owns the input lease.", status_code=423, retryable=True
            )
        if not bool(getattr(self.client, "is_connected", False)):
            raise NavigationPlanningError(
                "NAV_BRIDGE_OFFLINE", "The BizHawk bridge is offline.", status_code=503, retryable=True
            )
        occupied_snapshot = tuple(occupied or ())
        try:
            plan = self.planner.create_plan(
                destination, occupied=occupied_snapshot, interaction=interaction,
                movement_mode=movement_mode, navigation_intent=navigation_intent,
            )
        except NavigationPlanningError as exc:
            navigation_audit_log.record(
                "plan", "plan_failed", source="task_start", request={
                    "destination": destination, "movement_mode": movement_mode,
                    "interaction": interaction, "navigation_intent": navigation_intent,
                    "occupied_count": len(occupied_snapshot),
                }, code=exc.code, message=exc.message, details=exc.details,
            )
            raise
        steps = int((plan.get("cost") or {}).get("steps", 0))
        if steps > max_steps:
            raise NavigationPlanningError(
                "NAV_LIMIT_EXCEEDED", "The verified route exceeds max_steps.",
                details={"steps": steps, "max_steps": max_steps},
            )

        task_id = f"nav_{uuid4().hex}"
        navigation_audit_log.record(
            "plan", "plan_ready", source="task_start", plan_id=plan["plan_id"],
            request={"destination": destination, "movement_mode": movement_mode,
                     "interaction": interaction, "navigation_intent": navigation_intent,
                     "occupied_count": len(occupied_snapshot)},
            resolved_start=plan.get("resolved_start"), resolved_goal=plan.get("resolved_goal"),
            route_source=plan.get("route_source"), confidence=plan.get("confidence"),
            movement=plan.get("movement"), segments=plan.get("segments"),
        )
        record = {
            "format": "black2-navigation-task/v1", "task_id": task_id, "plan_id": plan["plan_id"],
            "status": "queued", "created_at": _now(), "updated_at": _now(),
            "progress": {"completed_steps": 0, "total_steps": steps},
            "current": plan["resolved_start"], "goal": plan["resolved_goal"],
            "interaction": plan.get("interaction"),
            "navigation_intent": plan.get("navigation_intent", navigation_intent),
            "movement": plan.get("movement"),
            "movement_mode": (plan.get("movement") or {}).get("selected", "walk"),
            "continuous_segments": [],
            "dynamic_replans": [],
            "stop_reason": None, "arrival": None, "_cleanup_done": False,
            "_dynamic_replan_count": 0,
            "_occupied": occupied_snapshot,
        }
        self._tasks[task_id] = record
        navigation_audit_log.record(
            "execution", "task_queued", task_id=task_id, plan_id=plan["plan_id"],
            destination=destination, movement=plan.get("movement"),
            total_steps=steps, occupied_count=len(occupied_snapshot),
        )
        self._runners[task_id] = asyncio.create_task(
            self._run(task_id, plan, max_steps=max_steps), name=f"navigation:{task_id}"
        )
        return self._public(record)

    def get(self, task_id: str) -> dict[str, Any]:
        record = self._tasks.get(task_id)
        if record is None:
            raise NavigationPlanningError("NAV_TASK_NOT_FOUND", "Navigation task was not found.", status_code=404)
        return self._public(record)

    async def cancel(self, task_id: str) -> dict[str, Any]:
        record = self._tasks.get(task_id)
        if record is None:
            raise NavigationPlanningError("NAV_TASK_NOT_FOUND", "Navigation task was not found.", status_code=404)
        if record.get("status") in _TERMINAL:
            return self._public(record)
        record["status"] = "cancelling"
        record["updated_at"] = _now()
        runner = self._runners.get(task_id)
        if runner is not None and not runner.done():
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                # A coroutine cancelled before its first instruction cannot
                # execute its cleanup block.  The fallback below owns it.
                pass
        if record.get("status") not in _TERMINAL:
            async with self._input_lock:
                if record.get("status") not in _TERMINAL:
                    try:
                        await self._cleanup_under_lock(record)
                        outcome = {
                            "status": "cancelled",
                            "stop_reason": {"code": "NAV_CANCELLED", "message": "Task cancelled by client."},
                        }
                    except Exception as exc:
                        outcome = _stop(
                            "NAV_INPUT_CLEANUP_FAILED",
                            "Cancellation could not prove the input queue is clear.",
                            error_type=type(exc).__name__,
                        )
                    self._publish_terminal(record, outcome)
        return self._public(record)

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {k: copy.deepcopy(v) for k, v in record.items() if not k.startswith("_")}

    @staticmethod
    def _button(a: NavNode, b: NavNode) -> str | None:
        return {(0, -1): "Up", (0, 1): "Down", (-1, 0): "Left", (1, 0): "Right"}.get(
            (b.x - a.x, b.z - a.z)
        )

    @staticmethod
    def _facing_name(player: dict[str, Any] | None) -> str | None:
        orientation = (player or {}).get("orientation") or {}
        raw = orientation.get("face_dir_raw")
        try:
            raw = int(raw)
        except (TypeError, ValueError):
            raw = None
        return {0: "North", 1: "South", 2: "West", 3: "East"}.get(raw)

    @staticmethod
    def _facing_button(name: str | None) -> str | None:
        return {"North": "Up", "South": "Down", "West": "Left", "East": "Right"}.get(name)

    @staticmethod
    def _button_facing(button: str | None) -> str | None:
        return {"Up": "North", "Down": "South", "Left": "West", "Right": "East"}.get(button)

    @staticmethod
    def _actor_grid(actor: dict[str, Any]) -> NavNode | None:
        grid = actor.get("grid") if isinstance(actor.get("grid"), dict) else None
        position = actor.get("position") if isinstance(actor.get("position"), dict) else None
        if grid is None and position and isinstance(position.get("grid"), dict):
            grid = position["grid"]
        if grid is None and isinstance(actor.get("world"), dict):
            world = actor["world"]
            try:
                return NavNode(
                    int(actor.get("zone_id")),
                    math.floor(float(world["x"]) / 16.0),
                    int(actor.get("y", 0)),
                    math.floor(float(world["z"]) / 16.0),
                )
            except (KeyError, TypeError, ValueError):
                return None
        if not isinstance(grid, dict):
            return None
        try:
            zone = actor.get("effective_zone_id_candidate")
            if zone is None:
                zone = actor.get("zone_id")
            return NavNode(int(zone), int(grid["x"]), int(grid.get("y", actor.get("y", 0))), int(grid["z"]))
        except (KeyError, TypeError, ValueError):
            return None

    async def _maybe_replan_dynamic_interaction(
        self, record: dict[str, Any], plan: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Return a fresh plan when the selected NPC moved.

        The bounded ActorSystem callback is optional, so old/test clients keep
        the fixed-route behavior.  When available, an identified actor is
        re-read before each continuous segment and the route is rebuilt from
        the player's live tile with a small deterministic standing-side set.
        """
        if self.actor_sample is None or plan.get("navigation_intent") != "interact":
            return None, None
        if int(record.get("_dynamic_replan_count", 0)) >= 4:
            return None, {
                "code": "NAV_DYNAMIC_LIMIT",
                "message": "The NPC moved too often; navigation stopped after four safe replans.",
                "details": {"replans": int(record.get("_dynamic_replan_count", 0))},
            }
        interaction = plan.get("interaction") or {}
        target_data = interaction.get("target") or {}
        try:
            old_target = NavNode(
                int(target_data["zone_id"]), int(target_data["x"]),
                int(target_data["y"]), int(target_data["z"]),
            )
        except (KeyError, TypeError, ValueError):
            return None, {"code": "NAV_DYNAMIC_TARGET_INVALID", "message": "The interaction target is not a complete grid coordinate."}
        try:
            payload = self.actor_sample()
            if inspect.isawaitable(payload):
                payload = await payload
        except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
            # A transient actor read failure must not inject a speculative
            # route.  The next segment will retry from the same plan.
            navigation_audit_log.record(
                "execution", "dynamic_actor_sample_failed", task_id=record["task_id"],
                plan_id=record["plan_id"], reason=f"{type(exc).__name__}: {exc}",
            )
            return None, None
        if not isinstance(payload, dict):
            return None, None
        actors = payload.get("actors")
        if isinstance(actors, dict):
            actors = actors.get("actors") or actors.get("runtime") or []
        if not isinstance(actors, list):
            return None, None
        actor_id = interaction.get("actor_id")
        matched = None
        for actor in actors:
            if not isinstance(actor, dict) or actor.get("is_player") or actor.get("same_current_scene") is False:
                continue
            identifiers = {str(actor.get(key)) for key in ("slot", "actor_uid", "uid", "address") if actor.get(key) is not None}
            actor_node = self._actor_grid(actor)
            if actor_id is not None and str(actor_id) in identifiers:
                matched = (actor, actor_node)
                break
            if actor_id is None and actor_node == old_target:
                matched = (actor, actor_node)
                break
        if matched is None:
            # An unresolved/empty bounded sample is allowed to recover.  A
            # resolved sample with a known actor id means the NPC disappeared.
            if actor_id is not None and payload.get("status") in {"resolved", "candidate"}:
                return None, {
                    "code": "NAV_DYNAMIC_TARGET_LOST",
                    "message": "The selected NPC is no longer present in the current ActorSystem sample.",
                    "details": {"actor_id": str(actor_id), "last_target": old_target.public()},
                }
            return None, None
        _actor, new_target = matched
        if new_target is None or new_target.zone_id != old_target.zone_id or new_target.y != old_target.y:
            return None, {
                "code": "NAV_DYNAMIC_TARGET_UNRESOLVED",
                "message": "The selected NPC sample does not contain a usable same-layer grid position.",
                "details": {"last_target": old_target.public()},
            }
        if new_target == old_target:
            return None, None
        stand_data = interaction.get("stand_tile") or {}
        try:
            old_stand = NavNode(
                int(stand_data["zone_id"]), int(stand_data["x"]),
                int(stand_data["y"]), int(stand_data["z"]),
            )
        except (KeyError, TypeError, ValueError):
            return None, {"code": "NAV_DYNAMIC_STAND_INVALID", "message": "The previous NPC standing tile is invalid."}
        preferred = (old_target.x - old_stand.x, old_target.z - old_stand.z)
        offsets = [preferred, (0, -1), (0, 1), (-1, 0), (1, 0)]
        candidates = []
        for offset in offsets:
            if offset in candidates or offset == (0, 0):
                continue
            candidates.append(offset)
        occupied = normalize_occupancy(actors, default_zone=new_target.zone_id, default_y=new_target.y)
        occupied = normalize_occupancy(
            [*record.get("_occupied", ()), *occupied],
            default_zone=new_target.zone_id, default_y=new_target.y,
        )
        # Remove the selected NPC's old tile from the merged snapshot.  It is
        # a stale transient obstacle after a move; retaining it can make a
        # valid detour look blocked even though the NPC has left that tile.
        occupied = [
            item for item in occupied
            if (item.get("grid") or {}).get("x") != old_target.x
            or (item.get("grid") or {}).get("z") != old_target.z
        ]
        requested_mode = (plan.get("movement") or {}).get("requested", record.get("movement_mode", "auto"))
        last_reason = None
        for offset_x, offset_z in candidates:
            stand = NavNode(new_target.zone_id, new_target.x - offset_x, new_target.y, new_target.z - offset_z)
            facing = {
                (0, -1): "North", (1, 0): "East", (0, 1): "South", (-1, 0): "West",
            }.get((new_target.x - stand.x, new_target.z - stand.z))
            if facing is None:
                continue
            refreshed_interaction = {
                "kind": "npc",
                "target": self.planner._grid_destination(new_target),
                "stand_tile": self.planner._grid_destination(stand),
                "facing": facing,
                "turn_only": False,
                "execute": True,
                **({"actor_id": str(actor_id)} if actor_id is not None else {}),
            }
            try:
                refreshed = self.planner.create_plan(
                    self.planner._grid_destination(stand), occupied=occupied,
                    interaction=refreshed_interaction, movement_mode=requested_mode,
                    navigation_intent="interact",
                )
            except NavigationPlanningError as exc:
                last_reason = {"code": exc.code, "message": exc.message, "details": exc.details or {}}
                continue
            navigation_audit_log.record(
                "execution", "dynamic_replan", task_id=record["task_id"],
                old_target=old_target.public(), new_target=new_target.public(),
                stand_tile=stand.public(), plan_id=refreshed.get("plan_id"),
            )
            # The old target tile must not remain as a ghost obstacle during
            # static-edge verification of the freshly replanned route.
            record["_occupied"] = tuple(occupied)
            return refreshed, None
        return None, {
            "code": "NAV_DYNAMIC_BLOCKED",
            "message": "The moved NPC has no connected standing tile on the current layer.",
            "details": {"last_target": old_target.public(), "new_target": new_target.public(), "last_reason": last_reason},
        }

    def _current_node(self) -> tuple[NavNode | None, dict[str, Any]]:
        # Planning still requires a resolved live start, but execution may
        # briefly receive a structurally complete ``candidate`` sample when
        # the resolver loses only a secondary confidence cross-check.  The
        # execution preflight below validates that candidate before input is
        # sent, and every landing is still checked against this node.
        player = canonical_grid_player(self.player_sample(), require_resolved=False)
        return NavNode.from_player(player), player

    @staticmethod
    def _coordinate_error(raw_player: dict[str, Any], canonical: dict[str, Any]) -> dict[str, Any] | None:
        """Return a reason when GPos/WPos cannot safely identify one tile."""
        node = NavNode.from_player(canonical)
        grid = canonical.get("grid") or {}
        world = canonical.get("world") or {}
        if node is None or any(world.get(axis) is None for axis in ("x", "y", "z")):
            return {"reason": "player_coordinates_incomplete", "player_status": raw_player.get("status")}

        expected_world = {
            "x": node.x * _WORLD_UNITS_PER_TILE + _WORLD_UNITS_PER_TILE / 2.0,
            "y": node.y * _WORLD_UNITS_PER_TILE,
            "z": node.z * _WORLD_UNITS_PER_TILE + _WORLD_UNITS_PER_TILE / 2.0,
        }
        projected_grid = {
            "x": math.floor(float(world["x"]) / _WORLD_UNITS_PER_TILE),
            "z": math.floor(float(world["z"]) / _WORLD_UNITS_PER_TILE),
        }
        residual = {
            axis: abs(float(world[axis]) - expected_world[axis])
            for axis in ("x", "y", "z")
        }
        # X/Z use the public floor projection exactly.  Y can carry a small
        # vertical placement offset, so keep the same half-tile bound used by
        # the scene coordinate contract.  _not_controllable only admits
        # Idle/Turning/Brake states; movement interpolation is never used to
        # authorize the next input.
        if (
            projected_grid["x"] != node.x
            or projected_grid["z"] != node.z
            or residual["y"] > _WORLD_TILE_TOLERANCE
        ):
            return {
                "reason": "player_coordinates_inconsistent",
                "player_status": raw_player.get("status"),
                "grid": {key: grid.get(key) for key in ("x", "y", "z")},
                "world": {key: world.get(key) for key in ("x", "y", "z")},
                "projected_grid": projected_grid,
                "expected_world": expected_world,
                "residual_world": residual,
            }
        return None

    def _not_controllable(self) -> dict[str, Any] | None:
        raw_player = self.player_sample() or {}
        canonical = canonical_grid_player(raw_player, require_resolved=False)
        locomotion = raw_player.get("locomotion") or {}
        phase = locomotion.get("phase")
        movement = str(locomotion.get("semantic_state") or "")
        if raw_player.get("status") not in {"resolved", "candidate"}:
            return {"reason": "player_runtime_unresolved", "player_status": raw_player.get("status")}
        # During a real Gen-5 tile transition GPos is updated before WPos has
        # returned to the next tile centre.  That intermediate WPos is valid
        # movement evidence, not a coordinate contradiction; strict centre
        # consistency remains required at every Idle/pre-input checkpoint.
        if phase != "Moving":
            coordinate_error = self._coordinate_error(raw_player, canonical)
            if coordinate_error:
                return coordinate_error
        if phase not in _IDLE_PHASES:
            return {"reason": "locomotion_not_idle", "phase": phase, "movement_state": movement}
        if any(token in movement.lower() for token in ("locked", "loading", "menu", "dialogue", "battle", "cutscene")):
            return {"reason": "locomotion_locked", "phase": phase, "movement_state": movement}

        snapshot = self.control_sample() or {}
        runtime = snapshot.get("runtime") or {}
        semantic = snapshot.get("semantic") or {}
        context = semantic.get("context") or {}
        screen_value = context.get("screen_type")
        screen_type = str(getattr(screen_value, "value", screen_value) or "")
        if runtime.get("status") != "ready":
            return {"reason": "runtime_not_ready", "runtime_status": runtime.get("status")}
        if semantic.get("map_loaded") is not True or semantic.get("ready_for_input") is not True:
            return {"reason": "field_not_ready", "map_loaded": semantic.get("map_loaded"),
                    "ready_for_input": semantic.get("ready_for_input")}
        if screen_type != "OVERWORLD":
            return {"reason": "screen_not_overworld", "screen_type": screen_type}
        if context.get("can_move_player") is not True or context.get("is_dialogue_active") is not False:
            return {"reason": "semantic_input_locked", "can_move_player": context.get("can_move_player"),
                    "is_dialogue_active": context.get("is_dialogue_active")}
        return None

    async def _run(self, task_id: str, plan: dict[str, Any], *, max_steps: int) -> None:
        record = self._tasks[task_id]
        try:
            async with self._input_lock:
                try:
                    outcome = await self._execute_under_lock(record, plan, max_steps=max_steps)
                except asyncio.CancelledError:
                    outcome = {"status": "cancelled", "stop_reason": {
                        "code": "NAV_CANCELLED", "message": "Task cancelled by client."
                    }}
                except (ConnectionError, TimeoutError, OSError) as exc:
                    outcome = _stop("NAV_BRIDGE_OFFLINE", f"Bridge input failed: {exc}")
                except Exception as exc:
                    outcome = _stop("NAV_INTERNAL", f"Navigation task failed: {type(exc).__name__}")
                # Keep active status and the ownership lock until clear finishes.
                try:
                    await self._cleanup_under_lock(record)
                except Exception as exc:
                    outcome = _stop(
                        "NAV_INPUT_CLEANUP_FAILED",
                        "The input queue could not be proven clear; task success is withheld.",
                        error_type=type(exc).__name__,
                    )
                if record.get("status") == "cancelling" or record.get("_cancel_during_cleanup"):
                    outcome = {"status": "cancelled", "stop_reason": {
                        "code": "NAV_CANCELLED", "message": "Task cancelled by client."
                    }}
                self._publish_terminal(record, outcome)
                navigation_audit_log.record(
                    "execution", "task_terminal", task_id=record["task_id"], plan_id=record["plan_id"],
                    status=record["status"], stop_reason=record.get("stop_reason"),
                    arrival=record.get("arrival"), progress=record.get("progress"),
                )
        except asyncio.CancelledError:
            # Cancellation while waiting to acquire the ownership lock.
            async with self._input_lock:
                try:
                    await self._cleanup_under_lock(record)
                    outcome = {"status": "cancelled", "stop_reason": {
                        "code": "NAV_CANCELLED", "message": "Task cancelled by client."
                    }}
                except Exception as exc:
                    outcome = _stop(
                        "NAV_INPUT_CLEANUP_FAILED",
                        "Cancellation could not prove the input queue is clear.",
                        error_type=type(exc).__name__,
                    )
                self._publish_terminal(record, outcome)
                navigation_audit_log.record(
                    "execution", "task_terminal", task_id=record["task_id"], plan_id=record["plan_id"],
                    status=record["status"], stop_reason=record.get("stop_reason"),
                    arrival=record.get("arrival"), progress=record.get("progress"),
                )

    async def _execute_under_lock(
        self, record: dict[str, Any], plan: dict[str, Any], *, max_steps: int
    ) -> dict[str, Any]:
        record["status"] = "prechecking"
        record["updated_at"] = _now()
        public_path = plan["segments"][0]["path"]
        path = [NavNode(int(p["zone_id"]), int(p["x"]), int(p["y"]), int(p["z"])) for p in public_path]
        if len(path) - 1 > max_steps:
            return _stop("NAV_LIMIT_EXCEEDED", "Route exceeds max_steps.")
        current, _player = self._current_node()
        if current != path[0]:
            return _stop("NAV_POSITION_DIVERGED", "Player moved after planning; no input was issued.",
                         current=current.public() if current else None)
        control_error = self._not_controllable()
        if control_error:
            return _stop("NAV_NOT_CONTROLLABLE",
                         "The runtime cannot prove the player is idle in the controllable Field state.",
                         **control_error)

        record["status"] = "executing"
        record["updated_at"] = _now()
        segment = (plan.get("segments") or [{}])[0]
        route_source = str(plan.get("route_source") or segment.get("source") or "verified_observed")
        static_candidate = route_source == "candidate_static" or segment.get("evidence") == "rom_static_collision_candidate"
        occupied = record.get("_occupied") or ()
        movement = plan.get("movement") or {"selected": "walk"}
        selected_mode = str(movement.get("selected") or "walk")
        # These are maximum hold budgets, not assumed movement durations.
        # _wait_for_landing clears the queue as soon as the verified endpoint
        # is reached.  A generous budget therefore handles a turn/startup
        # animation without causing overshoot, while still ending early from
        # live GPos feedback.
        frames_per_tile = {"walk": self.continuous_hold_frames, "run": 10, "bike": 8, "surf": 14}.get(
            selected_mode, self.hold_frames,
        )

        # Consecutive edges are sent as one directional hold.  We retain a
        # bounded landing checkpoint at every straight run/turn so a changed
        # NPC position or scripted event still stops navigation safely, but
        # the player no longer visibly pauses after every individual tile.
        runs: list[tuple[int, int, str]] = []
        run_start = 1
        while run_start < len(path):
            button = self._button(path[run_start - 1], path[run_start])
            if button is None:
                return _stop("NAV_EDGE_UNEXECUTABLE", "A verified edge has no safe cardinal input mapping.",
                             edge={"from": path[run_start - 1].public(), "to": path[run_start].public()})
            run_end = run_start
            while (
                run_end < len(path)
                and self._button(path[run_end - 1], path[run_end]) == button
                and run_end - run_start < self.continuous_segment_limit
            ):
                run_end += 1
            runs.append((run_start, run_end, button))
            run_start = run_end

        for segment_index, (start_index, end_index, button) in enumerate(runs, start=1):
            previous = path[start_index - 1]
            expected = path[end_index - 1]
            if plan.get("navigation_intent") == "interact":
                refreshed_plan, dynamic_error = await self._maybe_replan_dynamic_interaction(record, plan)
                if dynamic_error:
                    return _stop(
                        dynamic_error["code"], dynamic_error["message"],
                        **(dynamic_error.get("details") or {}),
                    )
                if refreshed_plan is not None:
                    record["_dynamic_replan_count"] = int(record.get("_dynamic_replan_count", 0)) + 1
                    record["dynamic_replans"].append({
                        "from_plan_id": record["plan_id"],
                        "to_plan_id": refreshed_plan.get("plan_id"),
                        "previous_goal": record.get("goal"),
                        "new_goal": refreshed_plan.get("resolved_goal"),
                    })
                    record["plan_id"] = refreshed_plan.get("plan_id", record["plan_id"])
                    record["goal"] = refreshed_plan.get("resolved_goal")
                    record["interaction"] = refreshed_plan.get("interaction")
                    record["movement"] = refreshed_plan.get("movement")
                    record["movement_mode"] = (refreshed_plan.get("movement") or {}).get("selected", "walk")
                    record["progress"] = {
                        "completed_steps": 0,
                        "total_steps": int((refreshed_plan.get("cost") or {}).get("steps", 0)),
                    }
                    # Restart from the freshly observed player tile.  The
                    # input lease remains held and cleanup still happens only
                    # once in _run, so no key-up gap is introduced here.
                    return await self._execute_under_lock(record, refreshed_plan, max_steps=max_steps)
            control_error = self._not_controllable()
            if control_error:
                return _stop("NAV_NOT_CONTROLLABLE",
                             "The player left the controllable Field/Idle state before the next segment.",
                             **control_error)
            for edge_index in range(start_index, end_index):
                edge_from, edge_to = path[edge_index - 1], path[edge_index]
                if static_candidate:
                    if not self.planner.has_static_edge(edge_from, edge_to, occupied=occupied):
                        return _stop(
                            "NAV_GRAPH_REVISION_CHANGED",
                            "The next static candidate edge is no longer present in the ROM navigation graph.",
                            edge={"from": edge_from.public(), "to": edge_to.public()},
                        )
                elif not self.planner.graph.has_direct_edge(edge_from, edge_to):
                    return _stop("NAV_GRAPH_REVISION_CHANGED", "The next edge lost direct observed evidence.",
                                 edge={"from": edge_from.public(), "to": edge_to.public()})

            buttons = [button] if selected_mode != "run" else ["B", button]
            step_count = end_index - start_index
            # Walk keeps the calibrated one-tile 14-frame press.  Faster
            # transports have their own bounded one-tile budgets; reusing the
            # walk budget for Run would skip a tile (observed as a safe
            # NAV_POSITION_DIVERGED stop in the live room).
            facing_now = self._facing_name(self.player_sample())
            turn_overhead = 0
            if facing_now is not None and self._button_facing(button) not in (None, facing_now):
                turn_overhead = {"walk": 6, "run": 8, "bike": 6, "surf": 8}.get(selected_mode, 6)
            frames = (
                self.hold_frames
                if selected_mode == "walk" and step_count == 1 and turn_overhead == 0
                else frames_per_tile * step_count + turn_overhead
            )
            navigation_audit_log.record(
                "execution", "segment_started", task_id=record["task_id"], plan_id=record["plan_id"],
                segment=segment_index, button=button, buttons=buttons,
                start=previous.public(), expected=expected.public(), steps=step_count,
                frames=frames, movement_mode=selected_mode, facing_before=facing_now, turn_overhead_frames=turn_overhead,
            )
            await self.client.press_buttons(buttons, frames=frames)
            landing_timeout = max(
                self.step_timeout_seconds,
                frames / 30.0 + 0.75 if step_count > 1 else self.step_timeout_seconds,
            )
            landed, landed_player = await self._wait_for_landing(
                previous, expected, allowed_nodes=path[start_index - 1:end_index],
                timeout_seconds=landing_timeout,
            )
            if landed != expected:
                post_control_error = self._not_controllable()
                if post_control_error:
                    return _stop("NAV_NOT_CONTROLLABLE",
                                 "The player became non-controllable while waiting for the next segment.",
                                 **post_control_error)
                diagnostic_stationary = float((self._last_landing_diagnostics or {}).get("stationary_seconds", 0) or 0)
                code = "NAV_STUCK" if landed == previous or diagnostic_stationary >= min(2.0, landing_timeout) else "NAV_POSITION_DIVERGED"
                return _stop(code, "The player did not land on the next verified path node.",
                             expected=expected.public(), observed=landed.public() if landed else None,
                             segment={"button": button, "start_step": start_index, "end_step": end_index - 1},
                             landing_diagnostics=copy.deepcopy(self._last_landing_diagnostics),
                             navigation_intent=plan.get("navigation_intent", "route"),
                             movement_mode=selected_mode)
            record["progress"]["completed_steps"] = end_index - 1
            record["current"] = {"zone_id": landed.zone_id,
                                 "position": {"x": landed.x, "y": landed.y, "z": landed.z},
                                 "frame": landed_player.get("frame")}
            record["continuous_segments"].append({
                "segment": segment_index, "button": button, "buttons": buttons,
                "from": previous.public(), "to": expected.public(), "steps": step_count,
                "frames": frames, "verified_landing": True,
                "facing_before": facing_now, "turn_overhead_frames": turn_overhead,
            })
            record["updated_at"] = _now()
            navigation_audit_log.record(
                "execution", "segment_landed", task_id=record["task_id"], plan_id=record["plan_id"],
                segment=record["continuous_segments"][-1], frame=landed_player.get("frame"),
            )

        arrived, arrival_player = self._current_node()
        goal = path[-1]
        if arrived != goal:
            return _stop("NAV_POSITION_DIVERGED", "Final canonical Zone/GPos does not satisfy destination.",
                         expected=goal.public(), observed=arrived.public() if arrived else None)
        interaction = plan.get("interaction")
        if interaction is not None:
            facing = str(interaction.get("facing") or "")
            turn_result = await self._turn_to_facing(goal, facing)
            if not turn_result.get("ok"):
                return _stop(
                    "NAV_INTERACTION_FACING_FAILED",
                    "The player reached the NPC standing tile but facing could not be verified.",
                    interaction=interaction,
                    **{key: value for key, value in turn_result.items() if key != "ok"},
                )
            _turned_node, turned_player = self._current_node()
            if _turned_node != goal:
                return _stop(
                    "NAV_POSITION_DIVERGED",
                    "The player moved away from the NPC standing tile while turning.",
                    expected=goal.public(),
                    observed=_turned_node.public() if _turned_node else None,
                )
            arrival_player = turned_player
            if interaction.get("execute") is True:
                await self.client.press_buttons(["A"], frames=1)
                navigation_audit_log.record(
                    "execution", "interaction_pressed", task_id=record["task_id"],
                    plan_id=record["plan_id"], target=interaction.get("target"),
                    stand_tile=interaction.get("stand_tile"), facing=facing,
                )
        return {"status": "succeeded", "stop_reason": None, "arrival": {
            "zone_id": arrived.zone_id, "position": {"x": arrived.x, "y": arrived.y, "z": arrived.z},
            "world": arrival_player.get("world"), "frame": arrival_player.get("frame"),
            "evidence": "canonical_player_runtime",
            **({"interaction": interaction, "facing": facing,
                "interact_pressed": bool(interaction.get("execute"))} if interaction is not None else {}),
        }}

    async def _turn_to_facing(self, stand: NavNode, expected_facing: str) -> dict[str, Any]:
        """Turn in place and prove the final facing without entering the NPC tile."""
        button = self._facing_button(expected_facing)
        if button is None:
            return {"ok": False, "reason": "invalid_facing", "expected_facing": expected_facing}
        attempts = 0
        last_facing = None
        while attempts < 2:
            current, player = self._current_node()
            last_facing = self._facing_name(self.player_sample())
            if current != stand:
                return {"ok": False, "reason": "stand_tile_changed", "observed": current.public() if current else None}
            if last_facing == expected_facing:
                return {"ok": True, "facing": last_facing, "attempts": attempts}
            control_error = self._not_controllable()
            if control_error:
                return {"ok": False, "reason": "not_controllable", **control_error}
            await self.client.press_buttons([button], frames=self.turn_frames)
            attempts += 1
            deadline = asyncio.get_running_loop().time() + min(0.6, self.step_timeout_seconds)
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(self.poll_seconds)
                current, _player = self._current_node()
                last_facing = self._facing_name(self.player_sample())
                if current != stand:
                    return {"ok": False, "reason": "turn_input_moved_player", "observed": current.public() if current else None}
                if last_facing == expected_facing:
                    return {"ok": True, "facing": last_facing, "attempts": attempts}
        return {"ok": False, "reason": "facing_not_observed", "expected_facing": expected_facing,
                "observed_facing": last_facing, "attempts": attempts}

    async def _cleanup_under_lock(self, record: dict[str, Any]) -> None:
        if record.get("_cleanup_done"):
            return
        clear_task = record.get("_clear_task")
        if clear_task is None:
            clear_task = asyncio.create_task(self.client.clear_inputs())
            record["_clear_task"] = clear_task
        try:
            await asyncio.shield(clear_task)
        except asyncio.CancelledError:
            # A cancel during cleanup must wait for this owner's existing clear
            # operation.  Starting another clear could erase a future owner's
            # freshly queued input.
            record["_cancel_during_cleanup"] = True
            try:
                await clear_task
            except Exception as exc:
                raise RuntimeError(f"input cleanup failed: {type(exc).__name__}") from exc
        except Exception as exc:
            raise RuntimeError(f"input cleanup failed: {type(exc).__name__}") from exc
        record["_cleanup_done"] = True

    @staticmethod
    def _publish_terminal(record: dict[str, Any], outcome: dict[str, Any]) -> None:
        record["stop_reason"] = outcome.get("stop_reason")
        record["arrival"] = outcome.get("arrival")
        record["updated_at"] = _now()
        record["status"] = outcome["status"]  # Must remain last: this gates the input lease.

    async def _wait_for_landing(
        self, previous: NavNode, expected: NavNode, *,
        allowed_nodes: list[NavNode] | None = None,
        timeout_seconds: float | None = None,
    ) -> tuple[NavNode | None, dict[str, Any]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout_seconds if timeout_seconds is not None else self.step_timeout_seconds)
        last_node: NavNode | None = previous
        last_player: dict[str, Any] = {}
        started_at = loop.time()
        last_change_at = started_at
        last_key = previous.public()
        samples = 0
        last_control_error: dict[str, Any] | None = None
        input_stopped = False
        while loop.time() < deadline:
            await asyncio.sleep(self.poll_seconds)
            node, player = self._current_node()
            samples += 1
            last_node, last_player = node, player
            control_error = self._not_controllable()
            last_control_error = control_error
            current_key = node.public() if node is not None else None
            if current_key != last_key:
                last_key = current_key
                last_change_at = loop.time()
            if node == expected:
                if control_error is None:
                    self._last_landing_diagnostics = {
                        "timeout_seconds": round(loop.time() - started_at, 3),
                        "samples": samples,
                        "last_gpos": node.public(),
                        "last_wpos": last_player.get("world"),
                        "expected_gpos": expected.public(),
                        "locomotion": last_player.get("locomotion"),
                        "control_error": None,
                    }
                    return node, player
                if control_error.get("reason") == "locomotion_not_idle":
                    # A continuous hold can reach the expected tile while the
                    # actor is still in its movement interpolation phase.  A
                    # queued hold must be stopped immediately at that safe
                    # endpoint, then we wait for the normal idle settle.  This
                    # avoids both a false NAV_NOT_CONTROLLABLE failure and an
                    # overshoot into the tile after the target.
                    if not input_stopped:
                        try:
                            await self.client.clear_inputs()
                        except (ConnectionError, TimeoutError, OSError):
                            self._last_landing_diagnostics = {
                                "timeout_seconds": round(loop.time() - started_at, 3),
                                "samples": samples, "last_gpos": node.public() if node else None,
                                "last_wpos": player.get("world"), "expected_gpos": expected.public(),
                                "locomotion": player.get("locomotion"),
                                "control_error": control_error, "input_clear": "failed",
                            }
                            return None, player
                        input_stopped = True
                    continue
                return None, player
            # In a continuous segment, intermediate nodes are expected and
            # must not be mistaken for divergence.  A node outside the
            # planned straight run still aborts immediately.
            if node is not None and node != previous and node not in set(allowed_nodes or (previous, expected)):
                return node, player
            if control_error and control_error.get("reason") != "locomotion_not_idle":
                return None, player
        # An expected GPos that never settled is not a verified landing.
        final_control_error = self._not_controllable()
        if last_node == expected and final_control_error is not None:
            last_control_error = final_control_error
        self._last_landing_diagnostics = {
            "timeout_seconds": round(loop.time() - started_at, 3),
            "samples": samples,
            "stationary_seconds": round(loop.time() - last_change_at, 3),
            "last_gpos": last_node.public() if last_node else None,
            "last_wpos": last_player.get("world"),
            "expected_gpos": expected.public(),
            "locomotion": last_player.get("locomotion"),
            "control_error": last_control_error,
            "input_stopped_at_expected": input_stopped,
        }
        if last_node == expected and final_control_error is not None:
            return None, last_player
        return last_node, last_player
