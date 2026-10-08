"""Conservative closed-loop execution for observed same-Zone paths."""
from __future__ import annotations

import asyncio
import copy
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import inspect
import math
from typing import Any, Callable
from uuid import uuid4

from .navigation_planning import NavigationPlanService, NavigationPlanningError, normalize_occupancy
from .navigation_audit import navigation_audit_log
from .navigation_constraints import ConstraintEvaluator
from .observed_navigation import NavNode
from .player_coordinates import canonical_grid_player
from ..actions.input_lease import InputLease


_ACTIVE = {"queued", "prechecking", "executing", "cancelling"}
_TERMINAL = {"succeeded", "failed", "cancelled"}
_IDLE_PHASES = {"Idle", "Turning", "Brake"}
_DEFAULT_INTERACTION_HOLD_FRAMES = 8
_DEFAULT_INTERACTION_VERIFY_SECONDS = 1.5
# Live bridge evidence: a four-frame walk press was accepted and cleared but
# did not advance the avatar from (140,2,664); the same edge advanced with an
# eight-frame press.  Keep this as the bounded single-tile budget for an
# uncalibrated/static route.  Landing verification still clears the queue as
# soon as the expected GPos is observed, so this is a ceiling, not a blind
# movement duration.
_CONSERVATIVE_SINGLE_TILE_HOLD_FRAMES = 8
# Live bridge evidence: on the uncalibrated/static ranch return corridor a
# ten-frame Run press from one tile before the target was consumed as two
# tiles.  Six frames still crosses the same one-tile fixture edge while
# leaving the closed-loop landing check time to clear the queue.  This is a
# maximum for conservative Run, not a claimed universal gait duration.
_CONSERVATIVE_RUN_SINGLE_TILE_HOLD_FRAMES = 6
# When a live PlayerRuntime sampler is available, an uncalibrated gait must
# not force a visible stop after every tile.  Keep the first chunks short, then
# let the landing verifier clear the queued input at the exact endpoint.
_ADAPTIVE_UNCALIBRATED_SEGMENT_LIMIT = {"walk": 12, "run": 16, "bike": 24, "surf": 12}
# A static route can cross a staircase/raised seam whose ROM candidate keeps
# the same X/Z but publishes a different live elevation layer.  Replan from
# the observed layer instead of treating that valid landing as a hard stop.
# The cap prevents a corrupt runtime sample from turning one task into an
# unbounded replan loop.
_ELEVATION_REPLAN_LIMIT = 8
_WORLD_UNITS_PER_TILE = 16.0
_WORLD_TILE_TOLERANCE = 8.001
# A bridge/raised path can report a valid GPos one half-tile above the
# decoder's nominal WPos centre while FieldActor.TileClass is still the
# unknown-but-valid class-0/flags-128 candidate. Keep this bounded; X/Z and
# the live GPos remain authoritative and every route edge is still landed.
_UNKNOWN_HEIGHT_CANDIDATE_TOLERANCE = 12.0


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
        # direction differs from the current FaceDir.  Keep a single step
        # bounded to the game's one-tile movement window while allowing
        # turn+move in one queued input.
        hold_frames: int = 14,
        turn_frames: int = 1,
        step_timeout_seconds: float = 2.0,
        poll_seconds: float = 0.03,
        post_clear_settle_seconds: float | None = None,
        continuous_segment_limit: int = 16,
        live_player_sampler: Callable[[], Any] | None = None,
        dynamic_actor_wait_seconds: float = 0.35,
        dynamic_replan_limit: int = 8,
        interaction_hold_frames: int = _DEFAULT_INTERACTION_HOLD_FRAMES,
        interaction_verify_seconds: float = _DEFAULT_INTERACTION_VERIFY_SECONDS,
        event_sink: Callable[..., Any] | None = None,
        event_cursor: Callable[[], int] | None = None,
        input_lease: InputLease | None = None,
    ) -> None:
        self.planner = planner
        self.client = client
        self.player_sample = player_sample
        self.live_player_sampler = live_player_sampler
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
        self.post_clear_settle_seconds = max(
            self.poll_seconds,
            float(post_clear_settle_seconds) if post_clear_settle_seconds is not None
            # A real Gen-5 field can keep the locomotion phase at Moving for
            # roughly 1.5 seconds after a short input is cleared.  The old
            # 1.25-second window still misclassified a correctly landed tile
            # during the ranch return route.  Keep the wait bounded and only
            # use the extra time after GPos already matches the expected tile.
            else min(0.35, max(0.05, self.step_timeout_seconds)),
        )
        self.continuous_segment_limit = max(1, int(continuous_segment_limit))
        self.dynamic_actor_wait_seconds = max(0.0, float(dynamic_actor_wait_seconds))
        self.dynamic_replan_limit = max(1, int(dynamic_replan_limit))
        # A one-frame A edge is not reliable on the live bridge: the request
        # can be accepted while the emulator consumes it after the navigation
        # task has already moved on. Keep this bounded (one semantic press,
        # not a blind sequence) and verify the resulting modal state below.
        self.interaction_hold_frames = max(1, int(interaction_hold_frames))
        self.interaction_verify_seconds = max(0.0, float(interaction_verify_seconds))
        self.event_sink = event_sink
        self.event_cursor = event_cursor
        self._shared_input_lease = input_lease
        self._tasks: dict[str, dict[str, Any]] = {}
        self._runners: dict[str, asyncio.Task] = {}
        self._input_lock = input_lease or asyncio.Lock()
        self._last_landing_diagnostics: dict[str, Any] = {}
        self._last_interrupted_task: dict[str, Any] | None = None

    @staticmethod
    def _interaction_semantics(snapshot: Any) -> dict[str, Any]:
        """Extract only modal transition facts from a cached runtime snapshot."""
        if not isinstance(snapshot, dict):
            return {
                "observed": False,
                "reason": "control_snapshot_unavailable",
                "screen_type": None,
                "dialogue_active": None,
                "battle_active": None,
            }
        semantic = snapshot.get("semantic") or {}
        context = semantic.get("context") or {}
        screen_value = context.get("screen_type")
        screen_type = str(getattr(screen_value, "value", screen_value) or "")
        dialogue = snapshot.get("dialogue") or {}
        battle = snapshot.get("battle") or {}
        dialogue_active = (
            context.get("is_dialogue_active") is True
            or dialogue.get("active") is True
            or screen_type in {"DIALOGUE", "DIALOGUE_ACTIVE", "DIALOGUE_CHOICE"}
        )
        battle_active = battle.get("active") is True or screen_type == "BATTLE"
        observed = bool(dialogue_active or battle_active)
        return {
            "observed": observed,
            "reason": "dialogue_started" if dialogue_active else ("battle_started" if battle_active else "modal_transition_not_observed"),
            "screen_type": screen_type or None,
            "dialogue_active": bool(dialogue_active),
            "battle_active": bool(battle_active),
            "can_move_player": context.get("can_move_player"),
            "frame": snapshot.get("frame") or semantic.get("frame"),
        }

    async def _verify_interaction_transition(self) -> dict[str, Any]:
        """Wait briefly for the semantic result of the one A interaction.

        This is a cache read, not a second input and not a screenshot loop.
        A missing transition is reported as evidence so the caller can stop
        or retry under its own policy; it never causes an automatic extra A.
        """
        deadline = asyncio.get_running_loop().time() + self.interaction_verify_seconds
        latest = self._interaction_semantics(self.control_sample())
        while not latest.get("observed") and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(self.poll_seconds)
            latest = self._interaction_semantics(self.control_sample())
        latest["verification_seconds"] = self.interaction_verify_seconds
        return latest

    async def _press_interaction_button(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Press A with bridge completion evidence, then verify semantics."""
        sent_frame = None
        frame_before = None
        queue_before = None
        try:
            state = await self.client.get_emu_state()
            frame_before = int(state.get("frame", 0) or 0)
            sent_frame = frame_before
        except Exception:
            pass
        try:
            queue_before = int((await self.client.get_input_state()).get("queue_len", 0))
        except Exception:
            pass

        result = await self.client.press_buttons(["A"], frames=self.interaction_hold_frames)

        # Do not assume that the bridge's ``queued`` response means the input
        # reached the game. If this client exposes frame/queue probes, wait
        # for the bounded press to drain. Test doubles and older clients can
        # omit these probes; semantic verification still remains active.
        completed = None
        frame_after = None
        queue_after = None
        if sent_frame is not None and hasattr(self.client, "get_emu_state") and hasattr(self.client, "get_input_state"):
            deadline = asyncio.get_running_loop().time() + max(1.5, self.interaction_verify_seconds)
            while asyncio.get_running_loop().time() < deadline:
                try:
                    after = await self.client.get_emu_state()
                    frame_after = int(after.get("frame", sent_frame) or sent_frame)
                    queue_after = int((await self.client.get_input_state()).get("queue_len", 0))
                    if frame_after - sent_frame >= self.interaction_hold_frames and queue_after == 0:
                        completed = True
                        break
                except Exception:
                    break
                await asyncio.sleep(self.poll_seconds)
            if completed is not True:
                completed = False

        input_evidence = {
            "accepted": bool(result.get("queued", True)) if isinstance(result, dict) else True,
            "hold_frames": self.interaction_hold_frames,
            "sent_frame": sent_frame,
            "frame_before": frame_before,
            "frame_after": frame_after,
            "queue_before": queue_before,
            "queue_after": queue_after,
            "completed": completed,
        }
        observation = await self._verify_interaction_transition()
        return input_evidence, observation

    @asynccontextmanager
    async def _input_guard(self, owner_id: str):
        if self._shared_input_lease is not None:
            async with self._shared_input_lease.acquire(owner_kind="navigation_task", owner_id=owner_id):
                yield
        else:
            async with self._input_lock:
                yield

    def _emit(self, event_type: str, **payload: Any) -> None:
        if self.event_sink is None:
            return
        try:
            result = self.event_sink(event_type, **payload)
            if inspect.isawaitable(result):
                asyncio.create_task(result)
        except Exception:
            pass

    def active_task(self) -> dict[str, Any] | None:
        active = [record for record in self._tasks.values() if record.get("status") in _ACTIVE]
        return self._public(active[-1]) if active else None

    async def interrupt_for_session(self, previous: str | None, current: str | None) -> None:
        """Cancel work tied to an emulator session that no longer exists."""
        for record in list(self._tasks.values()):
            if record.get("status") not in _ACTIVE:
                continue
            record["session_id"] = current
            record["status"] = "cancelling"
            record["stop_reason"] = {
                "code": "NAV_SESSION_CHANGED",
                "message": "The emulator session changed while this task was active.",
                "details": {"previous_session_id": previous, "current_session_id": current},
            }
            runner = self._runners.get(record["task_id"])
            if runner is not None and not runner.done():
                runner.cancel()

    def start(
        self,
        destination: dict[str, Any],
        *,
        max_steps: int = 2000,
        occupied: Any = (),
        constraints: Any = (),
        policy: dict[str, Any] | None = None,
        interaction: dict[str, Any] | None = None,
        movement_mode: str = "auto",
        navigation_intent: str = "walk_to_tile",
        allowed_nodes: Any = (),
        allow_unverified_terrain: bool = False,
        inventory: dict[str, Any] | None = None,
        correlation_id: str | None = None,
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
        constraints_snapshot = tuple(copy.deepcopy(item) for item in (constraints or ()))
        policy_snapshot = copy.deepcopy(policy) if policy is not None else None
        allowed_snapshot = tuple(allowed_nodes or ())
        try:
            plan = self.planner.create_plan(
                destination, occupied=occupied_snapshot,
                constraints=constraints_snapshot, policy=policy_snapshot,
                interaction=interaction,
                movement_mode=movement_mode, navigation_intent=navigation_intent,
                allowed_nodes=allowed_snapshot,
                allow_unverified_terrain=allow_unverified_terrain,
                inventory=inventory,
            )
        except NavigationPlanningError as exc:
            navigation_audit_log.record(
                "plan", "plan_failed", source="task_start", request={
                    "destination": destination, "movement_mode": movement_mode,
                    "interaction": interaction, "navigation_intent": navigation_intent,
                    "allow_unverified_terrain": bool(allow_unverified_terrain),
                    "inventory_known": bool(isinstance(inventory, dict) and inventory.get("contents_known")),
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
                     "occupied_count": len(occupied_snapshot),
                     "allowed_tile_count": len(allowed_snapshot)},
            resolved_start=plan.get("resolved_start"), resolved_goal=plan.get("resolved_goal"),
            route_source=plan.get("route_source"), confidence=plan.get("confidence"),
            movement=plan.get("movement"), segments=plan.get("segments"),
        )
        record = {
            "format": "black2-navigation-task/v1", "task_id": task_id, "plan_id": plan["plan_id"],
            "status": "queued", "created_at": _now(), "updated_at": _now(),
            "progress": {"completed_steps": 0, "total_steps": steps},
            "current": plan["resolved_start"], "goal": plan["resolved_goal"],
            "zone_transitions": copy.deepcopy(plan.get("zone_transitions") or []),
            "interaction": plan.get("interaction"),
            "navigation_intent": plan.get("navigation_intent", navigation_intent),
            "movement": plan.get("movement"),
            "movement_mode": (plan.get("movement") or {}).get("selected", "walk"),
            "allow_unverified_terrain": bool(allow_unverified_terrain),
            "continuous_segments": [],
            "dynamic_replans": [],
            "elevation_replans": [],
            "stop_reason": None, "arrival": None, "_cleanup_done": False,
            "_dynamic_replan_count": 0,
            "_elevation_replan_count": 0,
            "_occupied": occupied_snapshot,
            "_constraints": constraints_snapshot,
            "_policy": policy_snapshot,
            "_allowed_nodes": allowed_snapshot,
            "_original_destination": copy.deepcopy(destination),
            "_requested_movement_mode": movement_mode,
            "_requested_navigation_intent": navigation_intent,
            "_allow_unverified_terrain": bool(allow_unverified_terrain),
            "_inventory": copy.deepcopy(inventory) if isinstance(inventory, dict) else None,
            "correlation_id": correlation_id,
            "session_id": None,
        }
        self._tasks[task_id] = record
        navigation_audit_log.record(
            "execution", "task_queued", task_id=task_id, plan_id=plan["plan_id"],
            destination=destination, movement=plan.get("movement"),
            total_steps=steps, occupied_count=len(occupied_snapshot),
        )
        watch_cursor = self.event_cursor() if self.event_cursor is not None else None
        if watch_cursor is not None:
            record["watch"] = {
                "event_cursor": watch_cursor,
                "event_endpoint": f"/api/v1/agent/events/wait?after={watch_cursor}",
            }
        self._emit(
            "navigation.task.queued", task_id=task_id, plan_id=plan["plan_id"],
            correlation_id=correlation_id,
            summary="Navigation task queued.",
            resources={"task": f"/api/v1/navigation/tasks/{task_id}"},
            data={"goal": plan.get("resolved_goal"), "total_steps": steps},
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
        self._emit(
            "navigation.task.cancelled", task_id=task_id, plan_id=record.get("plan_id"),
            correlation_id=record.get("correlation_id"), summary="Navigation task cancellation requested.",
            resources={"task": f"/api/v1/navigation/tasks/{task_id}"}, data={},
        )
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
            async with self._input_guard(task_id):
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

    def get_interrupted_task(self) -> dict[str, Any] | None:
        if self._last_interrupted_task is not None:
            return self._public(self._last_interrupted_task)
        for t in reversed(list(self._tasks.values())):
            stop_reason = t.get("stop_reason") or {}
            if t.get("status") in {"failed", "cancelled"} and (
                stop_reason.get("code") in {"NAV_INTERRUPTED_BY_BATTLE", "NAV_NOT_CONTROLLABLE"}
                or "battle" in str(stop_reason.get("message", "")).lower()
                or "battle" in str(stop_reason.get("details", {})).lower()
            ):
                return self._public(t)
        return None

    def resume_task(self, task_id: str | None = None) -> dict[str, Any]:
        record = None
        if task_id:
            record = self._tasks.get(task_id)
        if record is None:
            if self._last_interrupted_task:
                record = self._last_interrupted_task
            else:
                for t in reversed(list(self._tasks.values())):
                    stop_reason = t.get("stop_reason") or {}
                    if t.get("status") in {"failed", "cancelled"} and (
                        stop_reason.get("code") in {"NAV_INTERRUPTED_BY_BATTLE", "NAV_NOT_CONTROLLABLE"}
                        or "battle" in str(stop_reason.get("message", "")).lower()
                        or "battle" in str(stop_reason.get("details", {})).lower()
                    ):
                        record = t
                        break
        if record is None and self._tasks:
            record = list(self._tasks.values())[-1]

        if record is None:
            raise NavigationPlanningError("NAV_NO_TASK_TO_RESUME", "No interrupted navigation task was found to resume.", status_code=404)

        destination = record.get("_original_destination") or record.get("goal")
        if not destination:
            raise NavigationPlanningError("NAV_NO_DESTINATION", "The interrupted task has no valid destination.", status_code=400)

        control_error = self._not_controllable()
        if control_error:
            if control_error.get("reason") == "battle_started" or str(control_error.get("screen_type")) == "BATTLE":
                raise NavigationPlanningError(
                    "NAV_STILL_IN_BATTLE",
                    "Player is currently in battle. Please conclude or flee the battle before resuming navigation.",
                    status_code=409,
                    details=control_error
                )
            raise NavigationPlanningError(
                "NAV_NOT_CONTROLLABLE",
                f"Player is currently not controllable on overworld: {control_error.get('reason')}",
                status_code=409,
                details=control_error
            )

        mode = record.get("_requested_movement_mode", "auto")
        intent = record.get("_requested_navigation_intent", "walk_to_tile")
        allow_unverified = record.get("_allow_unverified_terrain", False)

        new_task = self.start(
            destination,
            movement_mode=mode,
            navigation_intent=intent,
            allow_unverified_terrain=allow_unverified,
        )
        self._last_interrupted_task = None
        return new_task

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

    def _same_spatial_node(self, a: NavNode | None, b: NavNode | None) -> bool:
        return self.planner.same_spatial_node(a, b)

    def _in_spatial_set(self, node: NavNode | None, values: list[NavNode] | tuple[NavNode, ...] | set[NavNode]) -> bool:
        return node is not None and any(self._same_spatial_node(node, value) for value in values)

    def _node_index_in_path(self, node: NavNode | None, path: list[NavNode]) -> int | None:
        """Find the index of node in path matching spatial equality."""
        if node is None:
            return None
        for idx, item in enumerate(path):
            if self._same_spatial_node(node, item):
                return idx
        return None

    @staticmethod
    def _same_horizontal_location(a: NavNode | None, b: NavNode | None) -> bool:
        """Recognise a transient layer update at an already reached X/Z.

        The live FieldActor can publish the new tile's X/Z before its
        elevation/layer field catches up by one sample.  This is safe to
        tolerate only at the expected horizontal tile; it must never make an
        overshoot or an unrelated tile part of the allowed path.
        """
        return bool(
            a is not None
            and b is not None
            and a.zone_id == b.zone_id
            and a.x == b.x
            and a.z == b.z
        )

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

    async def _sample_live_occupancy(
        self, *, zone_id: int, y: int,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Read a fresh bounded ActorSystem sample for execution safety."""
        if self.actor_sample is None:
            return [], {"status": "unavailable", "frame": None, "actor_count": 0}
        try:
            payload = self.actor_sample()
            if inspect.isawaitable(payload):
                payload = await payload
        except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
            return [], {"status": "unresolved", "frame": None, "actor_count": 0, "error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(payload, dict):
            return [], {"status": "unresolved", "frame": None, "actor_count": 0}
        actors = payload.get("actors")
        if isinstance(actors, dict):
            actors = actors.get("actors") or actors.get("runtime") or []
        if not isinstance(actors, list):
            return [], {"status": "unresolved", "frame": payload.get("frame"), "actor_count": 0}
        selected: list[dict[str, Any]] = []
        for actor in actors:
            if not isinstance(actor, dict) or actor.get("is_player"):
                continue
            if actor.get("same_current_scene") is False:
                continue
            node = self._actor_grid(actor)
            if node is None or node.y != y:
                continue
            if node.zone_id not in {zone_id, 0}:
                continue
            item = dict(actor)
            item["zone_id"] = zone_id
            selected.append(item)
        return normalize_occupancy(selected, default_zone=zone_id, default_y=y), {
            "status": payload.get("status", "candidate"),
            "frame": payload.get("frame"),
            "actor_count": len(selected),
        }

    @staticmethod
    def _path_conflicts(
        path: list[NavNode], start_index: int, occupied: list[dict[str, Any]], *, horizon: int = 3,
    ) -> list[dict[str, Any]]:
        occupied_set: set[tuple[int | None, int, int | None, int]] = set()
        for item in occupied:
            grid = item.get("grid") or {}
            try:
                occupied_set.add((item.get("zone_id"), int(grid["x"]), grid.get("y"), int(grid["z"])))
            except (KeyError, TypeError, ValueError):
                continue
        hits: list[dict[str, Any]] = []
        for distance, node in enumerate(path[start_index:start_index + horizon], start=1):
            if any(
                x == node.x and z == node.z and (zone in {None, node.zone_id})
                and (layer is None or int(layer) == node.y)
                for zone, x, layer, z in occupied_set
            ):
                hits.append({"distance": distance, "node": node.public()})
        return hits

    async def _replan_for_dynamic_occupancy(
        self, record: dict[str, Any], plan: dict[str, Any],
        occupied: list[dict[str, Any]], meta: dict[str, Any], conflict: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        count = int(record.get("_dynamic_replan_count", 0))
        if count >= self.dynamic_replan_limit:
            return None, {
                "code": "NAV_DYNAMIC_LIMIT",
                "message": "A dynamic actor blocked the route too many times.",
                "details": {"blocked_tile": conflict.get("node"), "actor_frame": meta.get("frame"), "replan_count": count},
            }
        current, _player = self._current_node()
        if current is None:
            return None, {"code": "NAV_DYNAMIC_BLOCKED", "message": "Current player tile became unresolved while replanning."}
        try:
            refreshed = self.planner.create_plan(
                copy.deepcopy(record["_original_destination"]),
                occupied=normalize_occupancy(
                    [*record.get("_occupied", ()), *occupied],
                    default_zone=current.zone_id, default_y=current.y,
                ),
                constraints=record.get("_constraints", ()), policy=record.get("_policy"),
                interaction=record.get("interaction"),
                movement_mode=record.get("_requested_movement_mode", record.get("movement_mode", "auto")),
                navigation_intent=record.get("_requested_navigation_intent", record.get("navigation_intent", "walk_to_tile")),
                allowed_nodes=record.get("_allowed_nodes", ()),
                allow_unverified_terrain=bool(record.get("_allow_unverified_terrain", False)),
            )
        except NavigationPlanningError as exc:
            return None, {
                "code": "NAV_DYNAMIC_BLOCKED",
                "message": "The dynamic actor has no safe route to the original goal.",
                "details": {
                    "blocked_tile": conflict.get("node"), "actor_frame": meta.get("frame"),
                    "replan_count": count, "planner_code": exc.code,
                },
            }
        record["_occupied"] = tuple(occupied)
        record["_dynamic_replan_count"] = count + 1
        record["dynamic_replans"].append({
            "at": _now(), "reason": "actor_entered_path", "old_plan_id": record.get("plan_id"),
            "new_plan_id": refreshed.get("plan_id"), "actor_frame": meta.get("frame"),
            "blocked_tile": conflict.get("node"),
        })
        self._emit(
            "navigation.task.replanned", task_id=record["task_id"], plan_id=refreshed.get("plan_id"),
            correlation_id=record.get("correlation_id"),
            summary="Navigation replanned around a dynamic actor.",
            resources={"task": f"/api/v1/navigation/tasks/{record['task_id']}"},
            data={"blocked_tile": conflict.get("node"), "actor_frame": meta.get("frame"), "replan_count": count + 1},
        )
        return refreshed, None

    @staticmethod
    def _rebase_destination_layer(
        destination: dict[str, Any], *, zone_id: int, y: int,
    ) -> dict[str, Any] | None:
        """Rebase a same-zone destination's unresolved elevation to live RAM.

        The public global/grid destination carries a layer because the static
        mapper needs one for A*.  On raised paths that layer is a candidate,
        while PlayerRuntime's settled GPos is authoritative.  Keep this
        operation limited to a same-zone destination so it cannot silently
        rewrite a cross-zone connector's landing semantics.
        """
        if not isinstance(destination, dict):
            return None
        kind = str(destination.get("type") or "")
        if kind not in {"grid", "global_grid"}:
            return None
        destination_zone = destination.get("zone_id")
        if destination_zone is not None:
            try:
                if int(destination_zone) != int(zone_id):
                    return None
            except (TypeError, ValueError):
                return None
        rebased = copy.deepcopy(destination)
        rebased["y"] = int(y)
        return rebased

    @staticmethod
    def _rebase_interaction_layer(
        interaction: dict[str, Any] | None, *, zone_id: int, y: int,
    ) -> dict[str, Any] | None:
        if interaction is None:
            return None
        if not isinstance(interaction, dict):
            return None
        rebased = copy.deepcopy(interaction)
        for key in ("target", "stand_tile"):
            point = rebased.get(key)
            if not isinstance(point, dict):
                continue
            point_zone = point.get("zone_id")
            if point_zone is not None:
                try:
                    if int(point_zone) != int(zone_id):
                        return None
                except (TypeError, ValueError):
                    return None
            point["y"] = int(y)
        return rebased

    async def _replan_for_elevation(
        self,
        record: dict[str, Any],
        plan: dict[str, Any],
        expected: NavNode,
        landed: NavNode,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Rebuild the remaining route after a settled same-X/Z layer change."""
        count = int(record.get("_elevation_replan_count", 0))
        if count >= _ELEVATION_REPLAN_LIMIT:
            return None, {
                "code": "NAV_ELEVATION_LIMIT",
                "message": "The runtime elevation layer changed too often; navigation stopped.",
                "details": {"replan_count": count, "limit": _ELEVATION_REPLAN_LIMIT},
            }
        resolved_goal = plan.get("resolved_goal") or {}
        try:
            goal_zone = int(resolved_goal["zone_id"])
        except (KeyError, TypeError, ValueError):
            return None, {
                "code": "NAV_ELEVATION_UNRESOLVED",
                "message": "The plan goal has no resolved zone for a safe elevation replan.",
                "details": {"expected": expected.public(), "observed": landed.public()},
            }
        if goal_zone != landed.zone_id or landed.zone_id != expected.zone_id:
            return None, {
                "code": "NAV_ELEVATION_UNRESOLVED",
                "message": "A live elevation change crossed a zone boundary and cannot be rebased safely.",
                "details": {"expected": expected.public(), "observed": landed.public(), "goal_zone": goal_zone},
            }
        orig_dest = record.get("_original_destination") or {}
        orig_y = orig_dest.get("y")
        if orig_y is not None and orig_y != expected.y:
            destination = copy.deepcopy(orig_dest)
        else:
            destination = self._rebase_destination_layer(
                orig_dest, zone_id=landed.zone_id, y=landed.y,
            )
        if destination is None:
            return None, {
                "code": "NAV_ELEVATION_UNRESOLVED",
                "message": "The destination type cannot be safely rebased to the live layer.",
                "details": {"expected": expected.public(), "observed": landed.public()},
            }
        interaction = self._rebase_interaction_layer(
            record.get("interaction") or plan.get("interaction"), zone_id=landed.zone_id, y=landed.y,
        )
        if (record.get("interaction") or plan.get("interaction")) is not None and interaction is None:
            return None, {
                "code": "NAV_ELEVATION_UNRESOLVED",
                "message": "The interaction target cannot be safely rebased to the live layer.",
                "details": {"expected": expected.public(), "observed": landed.public()},
            }
        try:
            refreshed = self.planner.create_plan(
                destination,
                occupied=record.get("_occupied", ()),
                constraints=record.get("_constraints", ()),
                policy=record.get("_policy"),
                interaction=interaction,
                movement_mode=record.get("_requested_movement_mode", record.get("movement_mode", "auto")),
                navigation_intent=record.get("_requested_navigation_intent", record.get("navigation_intent", "walk_to_tile")),
                allowed_nodes=record.get("_allowed_nodes", ()),
                allow_unverified_terrain=bool(record.get("_allow_unverified_terrain", False)),
            )
        except NavigationPlanningError as exc:
            return None, {
                "code": "NAV_ELEVATION_REPLAN_FAILED",
                "message": "The live elevation layer was observed, but no safe route was found on it.",
                "details": {
                    "expected": expected.public(), "observed": landed.public(),
                    "planner_code": exc.code, "planner_details": exc.details,
                },
            }
        replan_record = {
            "at": _now(),
            "reason": "runtime_elevation_changed",
            "from_layer": expected.y,
            "to_layer": landed.y,
            "position": landed.public(),
            "old_plan_id": record.get("plan_id"),
            "new_plan_id": refreshed.get("plan_id"),
        }
        record["_elevation_replan_count"] = count + 1
        record.setdefault("elevation_replans", []).append(replan_record)
        self._emit(
            "navigation.task.replanned",
            task_id=record["task_id"],
            plan_id=refreshed.get("plan_id"),
            correlation_id=record.get("correlation_id"),
            summary="Navigation rebased to the live elevation layer.",
            resources={"task": f"/api/v1/navigation/tasks/{record['task_id']}"},
            data=replan_record,
        )
        record["plan_id"] = refreshed.get("plan_id", record["plan_id"])
        record["goal"] = refreshed.get("resolved_goal", record.get("goal"))
        record["interaction"] = refreshed.get("interaction")
        record["movement"] = refreshed.get("movement")
        record["movement_mode"] = (refreshed.get("movement") or {}).get("selected", "walk")
        record["progress"] = {
            "completed_steps": 0,
            "total_steps": int((refreshed.get("cost") or {}).get("steps", 0)),
        }
        navigation_audit_log.record(
            "execution", "elevation_replan", task_id=record["task_id"],
            plan_id=refreshed.get("plan_id"), **replan_record,
        )
        return refreshed, None

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
        if int(record.get("_dynamic_replan_count", 0)) >= self.dynamic_replan_limit:
            return None, {
                "code": "NAV_DYNAMIC_LIMIT",
                "message": "The NPC moved too often; navigation stopped after the dynamic replan limit.",
                "details": {"replans": int(record.get("_dynamic_replan_count", 0)), "limit": self.dynamic_replan_limit},
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
                    constraints=record.get("_constraints", ()), policy=record.get("_policy"),
                    interaction=refreshed_interaction, movement_mode=requested_mode,
                    navigation_intent="interact",
                    allow_unverified_terrain=bool(record.get("_allow_unverified_terrain", False)),
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
        tile_under = (raw_player.get("environment") or {}).get("tile_under")
        unknown_height_candidate = False
        if isinstance(tile_under, dict):
            try:
                unknown_height_candidate = (
                    int(tile_under.get("class")) == 0
                    and (int(tile_under.get("flags")) & 0x80) != 0
                )
            except (TypeError, ValueError):
                unknown_height_candidate = False
        locomotion = raw_player.get("locomotion") if isinstance(raw_player.get("locomotion"), dict) else {}
        is_surfing = (
            str(locomotion.get("transport_mode") or "").lower() == "surf"
            or int(raw_player.get("player", {}).get("ex_state_raw", 0) if isinstance(raw_player.get("player"), dict) else 0) == 2
        )
        # X/Z use the public floor projection exactly.  Y can carry a small
        # vertical placement offset, so keep the same half-tile bound used by
        # the scene coordinate contract.  _not_controllable only admits
        # Idle/Turning/Brake states; movement interpolation is never used to
        # authorize the next input.
        if (
            projected_grid["x"] != node.x
            or projected_grid["z"] != node.z
            or (
                residual["y"] > _WORLD_TILE_TOLERANCE
                and not (
                    (unknown_height_candidate or is_surfing)
                    and residual["y"] <= _UNKNOWN_HEIGHT_CANDIDATE_TOLERANCE
                )
            )
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
        snapshot = self.control_sample() or {}
        runtime = snapshot.get("runtime") or {}
        semantic = snapshot.get("semantic") or {}
        context = semantic.get("context") or {}
        screen_value = context.get("screen_type")
        screen_type = str(getattr(screen_value, "value", screen_value) or "")
        semantic_details = {
            "phase": phase,
            "movement_state": movement,
            "screen_type": screen_type,
            "can_move_player": context.get("can_move_player"),
            "is_dialogue_active": context.get("is_dialogue_active"),
        }
        if screen_type == "BATTLE":
            return {"reason": "battle_started", **semantic_details}

        if raw_player.get("status") not in {"resolved", "candidate"}:
            return {"reason": "player_runtime_unresolved", "player_status": raw_player.get("status")}
        if context.get("is_dialogue_active") is True or screen_type in {"DIALOGUE", "DIALOGUE_ACTIVE"}:
            return {"reason": "dialogue_started", **semantic_details}
        if runtime.get("status") != "ready":
            return {"reason": "runtime_not_ready", "runtime_status": runtime.get("status"), **semantic_details}
        if semantic.get("map_loaded") is not True or semantic.get("ready_for_input") is not True:
            # On the live DS field, a short D-pad input can leave the semantic
            # mapper's map_loaded bit false for a few frames while the actor
            # is already in a normal turn/brake transition.  This is not a
            # new map/warp when the screen remains OVERWORLD, input is still
            # allowed, and locomotion exposes the transitional phase.  Treat
            # it as unsettled locomotion so the landing loop keeps sampling;
            # otherwise the first direction change aborts every static route.
            if (
                screen_type == "OVERWORLD"
                and context.get("can_move_player") is True
                and semantic.get("ready_for_input") is True
                and phase in {"Turning", "Brake", "Idle", "Moving"}
            ):
                return {
                    "reason": "locomotion_not_idle",
                    "phase": phase,
                    "movement_state": movement,
                    "field_semantics_transient": True,
                    "map_loaded": semantic.get("map_loaded"),
                    "ready_for_input": semantic.get("ready_for_input"),
                }
            return {"reason": "field_not_ready", "map_loaded": semantic.get("map_loaded"),
                    "ready_for_input": semantic.get("ready_for_input"), **semantic_details}
        if screen_type != "OVERWORLD":
            return {"reason": "screen_not_overworld", **semantic_details}
        if context.get("can_move_player") is not True:
            return {"reason": "semantic_input_locked", **semantic_details}
        # During a real Gen-5 tile transition GPos is updated before WPos has
        # returned to the next tile centre.  That intermediate WPos is valid
        # movement evidence, not a coordinate contradiction; strict centre
        # consistency remains required at every Idle/pre-input checkpoint.
        if phase not in {"Moving", "Brake"}:
            coordinate_error = self._coordinate_error(raw_player, canonical)
            if coordinate_error:
                return coordinate_error
        if phase not in _IDLE_PHASES:
            return {"reason": "locomotion_not_idle", "phase": phase, "movement_state": movement}
        if any(token in movement.lower() for token in ("locked", "loading", "menu", "dialogue", "battle", "cutscene")):
            return {"reason": "locomotion_locked", "phase": phase, "movement_state": movement}
        return None

    def _control_failure(self, record: dict[str, Any], details: dict[str, Any], *, message: str) -> dict[str, Any]:
        self._last_interrupted_task = record
        screen_type = str(details.get("screen_type") or "")
        if screen_type == "BATTLE" or details.get("reason") == "battle_started":
            self._emit(
                "navigation.task.interrupted", task_id=record.get("task_id"), plan_id=record.get("plan_id"),
                correlation_id=record.get("correlation_id"), severity="warning",
                summary="Navigation interrupted because a battle started.",
                resources={"task": f"/api/v1/navigation/tasks/{record.get('task_id')}", "battle": "/api/v1/battle/state"},
                data={"reason": "battle_started", "resume_policy": "requires_new_agent_decision"},
            )
            return _stop("NAV_INTERRUPTED_BY_BATTLE", "Navigation was interrupted by the battle modal.", **details)
        if details.get("reason") == "dialogue_started":
            self._emit(
                "navigation.task.interrupted", task_id=record.get("task_id"), plan_id=record.get("plan_id"),
                correlation_id=record.get("correlation_id"), severity="warning",
                summary="Navigation interrupted because field dialogue started.",
                resources={"task": f"/api/v1/navigation/tasks/{record.get('task_id')}"},
                data={"reason": "dialogue_started", "resume_policy": "requires_new_agent_decision"},
            )
            return _stop("NAV_INTERRUPTED_BY_DIALOGUE", "Navigation was interrupted by field dialogue.", **details)
        if details.get("reason") in {"field_not_ready", "screen_not_overworld", "runtime_not_ready"}:
            return _stop("NAV_INTERRUPTED_BY_FIELD_EVENT", "Navigation was interrupted by a field state transition.", **details)
        return _stop("NAV_NOT_CONTROLLABLE", message, **details)

    async def _run(self, task_id: str, plan: dict[str, Any], *, max_steps: int) -> None:
        record = self._tasks[task_id]
        try:
            async with self._input_guard(task_id):
                try:
                    outcome = await self._execute_under_lock(record, plan, max_steps=max_steps)
                except asyncio.CancelledError:
                    outcome = {"status": "cancelled", "stop_reason": {
                        "code": "NAV_CANCELLED", "message": "Task cancelled by client."
                    }}
                except (ConnectionError, TimeoutError, OSError) as exc:
                    outcome = _stop("NAV_BRIDGE_OFFLINE", f"Bridge input failed: {exc}")
                except Exception as exc:
                    outcome = _stop("NAV_INTERNAL", f"Navigation task failed: {type(exc).__name__}: {exc}")
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
            async with self._input_guard(task_id):
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

    @staticmethod
    def _actor_gpos_stop_condition(player: dict[str, Any] | None, target: NavNode, *, same_zone: bool) -> dict[str, Any] | None:
        """Build a BizHawk frame-loop stop condition from the live actor pointer.

        PlayerRuntime already exposes the structure-coherent FieldActor address
        discovered from the current RAM snapshot. Passing its GPos fields to
        the bridge makes the input queue self-terminating in emulated frames;
        Python polling remains the authoritative postcondition verifier.
        """
        if not same_zone or not isinstance(player, dict):
            return None
        root = player.get("root") if isinstance(player.get("root"), dict) else {}
        raw_player = player.get("player") if isinstance(player.get("player"), dict) else {}
        actor = raw_player.get("actor") if isinstance(raw_player.get("actor"), dict) else {}
        address = root.get("player_actor") or actor.get("address") or player.get("player_actor")
        if not isinstance(address, str):
            return None
        try:
            actor_address = int(address, 16)
        except (TypeError, ValueError):
            return None
        if not 0x02000000 <= actor_address < 0x02400000:
            return None
        return {
            "kind": "actor_gpos",
            "domain": "Main RAM",
            "x_addr": actor_address + 0x3C,
            "y_addr": actor_address + 0x3E,
            "z_addr": actor_address + 0x40,
            "x": int(target.x),
            "y": int(target.y),
            "z": int(target.z),
        }

    async def _execute_under_lock(
        self, record: dict[str, Any], plan: dict[str, Any], *, max_steps: int
    ) -> dict[str, Any]:
        record["status"] = "prechecking"
        record["updated_at"] = _now()
        self._emit(
            "navigation.task.started", task_id=record["task_id"], plan_id=plan.get("plan_id"),
            correlation_id=record.get("correlation_id"),
            summary="Navigation task started preflight.",
            resources={"task": f"/api/v1/navigation/tasks/{record['task_id']}"}, data={},
        )
        public_path = plan["segments"][0]["path"]
        path = [NavNode(int(p["zone_id"]), int(p["x"]), int(p["y"]), int(p["z"])) for p in public_path]
        if len(path) - 1 > max_steps:
            return _stop("NAV_LIMIT_EXCEEDED", "Route exceeds max_steps.")
        current, _player = self._current_node()
        same_start = self._same_spatial_node(current, path[0]) or (current is not None and current.x == path[0].x and current.z == path[0].z and abs(current.y - path[0].y) <= 1)
        if not same_start:
            return _stop("NAV_POSITION_DIVERGED", "Player moved after planning; no input was issued.",
                         current=current.public() if current else None)
        control_error = self._not_controllable()
        if control_error:
            return self._control_failure(
                record, control_error,
                message="The runtime cannot prove the player is idle in the controllable Field state.",
            )

        record["status"] = "executing"
        record["updated_at"] = _now()
        segment = (plan.get("segments") or [{}])[0]
        route_source = str(plan.get("route_source") or segment.get("source") or "verified_observed")
        static_candidate = route_source == "candidate_static" or segment.get("evidence") == "rom_static_collision_candidate"
        occupied = record.get("_occupied") or ()
        constraint_evaluator = ConstraintEvaluator(
            plan.get("navigation_constraints") or record.get("_constraints", ()),
            policy=plan.get("policy") or record.get("_policy"),
        )
        movement = plan.get("movement") or {"selected": "walk"}
        selected_mode = str(movement.get("selected") or "walk")
        # A ROM-static route has not earned a calibrated long-hold budget yet.
        # On the live Black 2 field, an 18-frame hold crossed the expected
        # eighth tile and landed on the ninth before the polling loop could
        # clear it.  Keep the high-throughput grouping for directly observed
        # edges, but execute static candidates as one-tile, 4-frame steps with
        # an explicit in-place turn.  This preserves the user's directional
        # rule and makes the API's closed loop the source of truth.
        # Static ROM edges have no runtime timing calibration.  Run is faster
        # and can cross one extra tile before the polling loop observes the
        # intended GPos, so it uses the same one-tile closed loop as walk for
        # story travel.  The bounded continuous-run probe remains available
        # for calibration, but it is not allowed to overshoot a plot tile.
        #
        # The same guard is needed for observed edges when the live PlayerRuntime
        # explicitly reports gait_calibration.status=needs_samples.  In that
        # state a 14-frame/10-frame hold is not yet a reliable one-tile budget;
        # grouping several edges lets the asynchronous input queue outrun the
        # landing reader.  Offline fixtures and calibrated runtimes without this
        # field retain the high-throughput observed-edge behavior.
        runtime_player = self.player_sample() or {}
        gait_calibration = (runtime_player.get("locomotion") or {}).get("gait_calibration")
        calibration_status = gait_calibration.get("status") if isinstance(gait_calibration, dict) else None
        calibration_present = isinstance(gait_calibration, dict) and "status" in gait_calibration
        timing_unverified = calibration_present and str(calibration_status or "").lower() not in {
            "ready", "verified", "calibrated",
        }
        conservative_static = static_candidate and selected_mode in {"walk", "run"}
        conservative_timing = timing_unverified and selected_mode in {"walk", "run", "bike", "surf"}
        conservative_execution = conservative_static or conservative_timing

        # 自动上车/下车机制：闭环等待动画完成，避免动画锁定导致首段按键吞键
        current_transport = str((runtime_player.get("locomotion") or {}).get("transport_mode") or "")
        loop = asyncio.get_running_loop()
        if selected_mode == "bike" and current_transport not in {"Cycling"}:
            try:
                await self.client.press_buttons(["Y"], frames=4)
                mount_deadline = loop.time() + 1.2
                while loop.time() < mount_deadline:
                    await asyncio.sleep(0.08)
                    if self.live_player_sampler is not None:
                        res = self.live_player_sampler()
                        if inspect.isawaitable(res):
                            await res
                    trans = str(((self.player_sample() or {}).get("locomotion") or {}).get("transport_mode") or "")
                    if trans == "Cycling":
                        break
            except Exception:
                pass
        elif selected_mode in {"walk", "run"} and current_transport == "Cycling":
            try:
                await self.client.press_buttons(["Y"], frames=4)
                dismount_deadline = loop.time() + 1.2
                while loop.time() < dismount_deadline:
                    await asyncio.sleep(0.08)
                    if self.live_player_sampler is not None:
                        res = self.live_player_sampler()
                        if inspect.isawaitable(res):
                            await res
                    trans = str(((self.player_sample() or {}).get("locomotion") or {}).get("transport_mode") or "")
                    if trans not in {"Cycling"}:
                        break
            except Exception:
                pass
        # A live sampler lets us keep a short directional hold active while
        # _wait_for_landing polls the authoritative GPos/WPos.  The previous
        # implementation collapsed every uncalibrated route to one tile,
        # causing a stop/settle cycle after each step.  Offline fixtures still
        # retain the one-tile conservative contract because they cannot prove
        # an intermediate coordinate in real time.
        if conservative_execution and self.live_player_sampler is not None:
            effective_segment_limit = min(
                self.continuous_segment_limit,
                _ADAPTIVE_UNCALIBRATED_SEGMENT_LIMIT.get(selected_mode, 2),
            )
        else:
            effective_segment_limit = 1 if conservative_execution else self.continuous_segment_limit
        # These are maximum hold budgets, not assumed movement durations.
        # _wait_for_landing clears the queue as soon as the verified endpoint
        # is reached.  Use the known one-tile budgets as a starting estimate;
        # endpoint polling, not a blind sleep, decides when a chunk ends.
        frames_per_tile = {"walk": self.continuous_hold_frames, "run": 8, "bike": 4, "surf": 14}.get(
            selected_mode, self.hold_frames,
        )
        if conservative_execution and self.live_player_sampler is not None and selected_mode == "walk":
            frames_per_tile = max(self.hold_frames, 14)

        # Prefer frame-domain gait calibration whenever it is ready.  One
        # Gen-5 field tile is 16 world units; deriving the budget from the
        # observed WPos displacement keeps the queue correct when the live
        # core, turbo setting, or input latency differs from the fixture.
        profile = gait_calibration if isinstance(gait_calibration, dict) else {}
        profile_key = "run_median" if selected_mode == "run" else "walk_median" if selected_mode == "walk" else None
        calibrated_speed = profile.get(profile_key) if profile_key else None
        if profile.get("status") in {"ready", "verified", "calibrated"} and isinstance(calibrated_speed, (int, float)) and calibrated_speed > 0:
            frames_per_tile = max(4, min(32, int(math.ceil(16.0 / float(calibrated_speed))) + 1))

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
            start_y = path[run_start - 1].y
            first_step_y = path[run_start].y
            is_stair_transition = (first_step_y != start_y)
            if is_stair_transition:
                while (
                    run_end < len(path)
                    and self._button(path[run_end - 1], path[run_end]) == button
                    and path[run_end].y != start_y
                    and run_end - run_start < 4
                ):
                    run_end += 1
            else:
                while (
                    run_end < len(path)
                    and self._button(path[run_end - 1], path[run_end]) == button
                    and path[run_end].y == start_y
                    and run_end - run_start < effective_segment_limit
                ):
                    run_end += 1
            runs.append((run_start, run_end, button))
            run_start = run_end

        for segment_index, (start_index, end_index, button) in enumerate(runs, start=1):
            cur_check_node, cur_check_player = self._current_node()
            if segment_index > 1 and cur_check_node and int(cur_check_node.zone_id) != int(path[0].zone_id):
                provider = self.planner._resolve_static_provider() if hasattr(self.planner, "_resolve_static_provider") else None
                prev_had_warp = False
                if provider and hasattr(provider, "event_overlay_at") and start_index >= 2:
                    try:
                        prev_tile = path[start_index - 2]
                        overlays = list(provider.event_overlay_at(int(prev_tile.zone_id), int(prev_tile.x), int(prev_tile.z)))
                        prev_had_warp = any(it.get("kind") == "warp" for it in overlays)
                    except Exception:
                        pass
                if prev_had_warp:
                    record["zone_transitions"].append({
                        "from_zone": int(path[0].zone_id),
                        "to_zone": int(cur_check_node.zone_id),
                        "landing": cur_check_node.public(),
                    })
                    break
            previous = path[start_index - 1]
            expected = path[end_index - 1]
            is_terminal = (segment_index == len(runs))
            next_button = runs[segment_index][2] if segment_index < len(runs) else None
            is_pipeline_continuation = (segment_index > 1)
            # Re-sample just before every input segment.  A route may have
            # been planned against a clear tile that an NPC entered while an
            # earlier segment was being verified.
            live_occupancy, occupancy_meta = await self._sample_live_occupancy(
                zone_id=previous.zone_id, y=previous.y,
            )
            occupancy_for_check = normalize_occupancy(
                [*occupied, *live_occupancy], default_zone=previous.zone_id, default_y=previous.y,
            )
            conflicts = self._path_conflicts(path, start_index, live_occupancy, horizon=3)
            if conflicts:
                first_conflict = conflicts[0]
                if first_conflict["distance"] >= 2:
                    # Keep an actor on the horizon from becoming an
                    # overshoot: issue at most one tile before the next check.
                    end_index = min(end_index, start_index + 1)
                    expected = path[end_index - 1]
                else:
                    self._emit(
                        "navigation.dynamic_actor.blocked", task_id=record["task_id"],
                        plan_id=record.get("plan_id"), correlation_id=record.get("correlation_id"),
                        severity="warning", summary="The next navigation tile is occupied by a dynamic actor.",
                        resources={"task": f"/api/v1/navigation/tasks/{record['task_id']}", "runtime": "/api/v1/runtime/snapshot"},
                        data={"blocked_tile": first_conflict.get("node"), "actor_frame": occupancy_meta.get("frame")},
                    )
                    record["_dynamic_actor_wait_started"] = _now()
                    await asyncio.sleep(self.dynamic_actor_wait_seconds)
                    live_after_wait, meta_after_wait = await self._sample_live_occupancy(
                        zone_id=previous.zone_id, y=previous.y,
                    )
                    remaining_conflicts = self._path_conflicts(path, start_index, live_after_wait, horizon=1)
                    if remaining_conflicts:
                        refreshed_plan, dynamic_error = await self._replan_for_dynamic_occupancy(
                            record, plan, live_after_wait, meta_after_wait, remaining_conflicts[0],
                        )
                        if dynamic_error:
                            return _stop(
                                dynamic_error["code"], dynamic_error["message"],
                                waited_seconds=self.dynamic_actor_wait_seconds,
                                **(dynamic_error.get("details") or {}),
                            )
                        if refreshed_plan is not None:
                            record["plan_id"] = refreshed_plan.get("plan_id", record["plan_id"])
                            record["goal"] = refreshed_plan.get("resolved_goal", record.get("goal"))
                            record["interaction"] = refreshed_plan.get("interaction")
                            record["movement"] = refreshed_plan.get("movement")
                            record["movement_mode"] = (refreshed_plan.get("movement") or {}).get("selected", "walk")
                            record["progress"] = {
                                "completed_steps": 0,
                                "total_steps": int((refreshed_plan.get("cost") or {}).get("steps", 0)),
                            }
                            return await self._execute_under_lock(record, refreshed_plan, max_steps=max_steps)
                    else:
                        # The transient actor left; safely continue with the
                        # original goal and a fresh occupancy snapshot.
                        occupancy_for_check = normalize_occupancy(
                            [*occupied, *live_after_wait],
                            default_zone=previous.zone_id, default_y=previous.y,
                        )
            occupied = tuple(occupancy_for_check)
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
                    self._emit(
                        "navigation.task.replanned", task_id=record["task_id"],
                        plan_id=refreshed_plan.get("plan_id"), correlation_id=record.get("correlation_id"),
                        summary="Navigation retargeted a moving interaction actor.",
                        resources={"task": f"/api/v1/navigation/tasks/{record['task_id']}"},
                        data={"reason": "actor_moved"},
                    )
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
                if is_pipeline_continuation and control_error.get("reason") == "locomotion_not_idle":
                    pass
                else:
                    return self._control_failure(
                        record, control_error,
                        message="The player left the controllable Field/Idle state before the next segment.",
                    )
            for edge_index in range(start_index, end_index):
                edge_from, edge_to = path[edge_index - 1], path[edge_index]
                if static_candidate:
                    if not self.planner.has_static_edge(
                        edge_from, edge_to,
                        movement_mode=selected_mode,
                        constraint_evaluator=constraint_evaluator,
                        allow_unverified_terrain=bool(record.get("_allow_unverified_terrain", False)),
                    ):
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
            turn_move_consumed = False
            if facing_now is not None and self._button_facing(button) not in (None, facing_now):
                # 只有在 NPC 面对面对话或离线无实时采样时需要阻塞式 _turn_to_facing；
                # 普通大地图移动转弯直接计算 turn_overhead (6~8帧) 原地自适应拐弯，彻底消除 1 秒卡顿！
                need_explicit_turn_settle = (plan.get("navigation_intent") == "interact") or (self.live_player_sampler is None)
                if conservative_execution and need_explicit_turn_settle:
                    turn_result = await self._turn_to_facing(
                        record,
                        previous,
                        self._button_facing(button) or "",
                        expected_node=expected,
                    )
                    if not turn_result.get("ok"):
                        return _stop(
                            "NAV_TURN_FAILED",
                            "The player could not turn in place before a conservative single-step move.",
                            expected_facing=self._button_facing(button),
                            observed_facing=turn_result.get("observed_facing"),
                            turn=turn_result,
                        )
                    turn_move_consumed = bool(turn_result.get("moved"))
                    facing_now = self._facing_name(self.player_sample())
                else:
                    target_facing = self._button_facing(button)
                    is_opposite = (
                        (facing_now == "North" and target_facing == "South") or
                        (facing_now == "South" and target_facing == "North") or
                        (facing_now == "East" and target_facing == "West") or
                        (facing_now == "West" and target_facing == "East")
                    )
                    base_turn = 6 if is_opposite else 2
                    if selected_mode == "bike":
                        base_turn = 8 if is_opposite else 4
                    elif selected_mode == "walk":
                        base_turn = 8 if is_opposite else 4
                    turn_overhead = base_turn
            # From a dead stop, 1st tile needs minimal acceleration overhead (2f for run, 4f for bike)
            accel_overhead = 0
            if not is_pipeline_continuation:
                accel_overhead = 10 if selected_mode == "bike" else (2 if selected_mode == "run" else 0)
            
            is_cross_layer = (previous.y != expected.y)
            fpt_effective = 14 if is_cross_layer else frames_per_tile
            
            adaptive_live_chunk = conservative_execution and self.live_player_sampler is not None and step_count > 1
            frames = (
                fpt_effective * step_count + turn_overhead + accel_overhead
                if adaptive_live_chunk
                else (
                    min(self.hold_frames, _CONSERVATIVE_SINGLE_TILE_HOLD_FRAMES)
                    if selected_mode == "walk"
                    else min(
                        self.hold_frames,
                        _CONSERVATIVE_RUN_SINGLE_TILE_HOLD_FRAMES + (2 if not is_pipeline_continuation and self.live_player_sampler is not None else 0),
                    )
                    if selected_mode == "run"
                    else max(14, frames_per_tile + turn_overhead + accel_overhead)
                )
                if conservative_execution
                else self.hold_frames
                if selected_mode == "walk" and step_count == 1 and turn_overhead == 0
                else frames_per_tile * step_count + turn_overhead + accel_overhead
            )
            record.pop("_clear_task", None)
            record["_cleanup_done"] = False
            stop_when = self._actor_gpos_stop_condition(
                self.player_sample(), expected,
                same_zone=(previous.zone_id == expected.zone_id),
            )

            navigation_audit_log.record(
                "execution", "segment_started", task_id=record["task_id"], plan_id=record["plan_id"],
                segment=segment_index, button=button, buttons=buttons,
                start=previous.public(), expected=expected.public(), steps=step_count,
                frames=frames, movement_mode=selected_mode, facing_before=facing_now,
                turn_overhead_frames=turn_overhead,
                execution_mode=(
                    "conservative_static_single_step" if conservative_static and effective_segment_limit == 1
                    else "adaptive_live_closed_loop" if conservative_execution
                    else "continuous_observed"
                ),
                gait_calibration_status=calibration_status,
                turn_move_consumed=turn_move_consumed,
                emulator_side_stop_condition=bool(stop_when),
            )
            if not turn_move_consumed:
                if stop_when is not None:
                    await self.client.press_buttons(buttons, frames=frames, stop_when=stop_when)
                else:
                    await self.client.press_buttons(buttons, frames=frames)
            landing_timeout = max(
                self.step_timeout_seconds,
                frames / 30.0 + 0.75 if step_count > 1 else self.step_timeout_seconds,
            )
            # Allow forward progression along the verified path. Running momentum
            # can advance 1-2 tiles beyond an intermediate conservative checkpoint
            # while still strictly staying on the route.
            allowed_forward_slice = path[start_index - 1:min(len(path), end_index + 3)]
            landed, landed_player = await self._wait_for_landing(
                record, previous, expected, allowed_nodes=allowed_forward_slice,
                timeout_seconds=landing_timeout,
                is_terminal=is_terminal,
                next_button=next_button,
                current_button=button,
            )
            landed_idx = self._node_index_in_path(landed, path)
            # Accept if landed on expected OR successfully advanced forward along the planned route
            forward_progress = landed_idx is not None and landed_idx >= end_index - 1
            if not self._same_spatial_node(landed, expected) and not forward_progress:
                landing_diagnostics = self._last_landing_diagnostics or {}
                if (
                    landed is not None
                    and (landing_diagnostics.get("control_error") or {}).get("reason")
                    == "position_elevation_rebased"
                ):
                    refreshed_plan, elevation_error = await self._replan_for_elevation(
                        record, plan, expected, landed,
                    )
                    if elevation_error:
                        return _stop(
                            elevation_error["code"], elevation_error["message"],
                            **(elevation_error.get("details") or {}),
                        )
                    if refreshed_plan is not None:
                        # The current tile was already physically reached and
                        # the input queue was cleared by _wait_for_landing.
                        # Restart the remaining route from that live tile;
                        # no speculative key is sent during the replan.
                        return await self._execute_under_lock(
                            record, refreshed_plan, max_steps=max_steps,
                        )
                post_control_error = self._not_controllable()
                if post_control_error and post_control_error.get("reason") != "locomotion_not_idle":
                    post_control_error = {
                        **post_control_error,
                        "landing_diagnostics": copy.deepcopy(self._last_landing_diagnostics),
                    }
                    return self._control_failure(
                        record, post_control_error,
                        message="The player became non-controllable while waiting for the next segment.",
                    )
                diagnostic_stationary = float((self._last_landing_diagnostics or {}).get("stationary_seconds", 0) or 0)
                if post_control_error and post_control_error.get("reason") == "locomotion_not_idle":
                    code = "NAV_LANDING_UNSETTLED"
                elif self._same_spatial_node(landed, previous) or diagnostic_stationary >= min(2.0, landing_timeout):
                    code = "NAV_STUCK"
                elif self._in_spatial_set(landed, path[start_index - 1:end_index]):
                    code = "NAV_PARTIAL_SEGMENT"
                else:
                    code = "NAV_POSITION_DIVERGED"
                return _stop(code, "The player did not land on the next verified path node.",
                             expected=expected.public(), observed=landed.public() if landed else None,
                             segment={"button": button, "start_step": start_index, "end_step": end_index - 1},
                             landing_diagnostics=copy.deepcopy(self._last_landing_diagnostics),
                             navigation_intent=plan.get("navigation_intent", "route"),
                             movement_mode=selected_mode)
            effective_completed = landed_idx if landed_idx is not None and landed_idx > end_index - 1 else (end_index - 1)
            record["progress"]["completed_steps"] = effective_completed
            record["current"] = {"zone_id": landed.zone_id,
                                 "position": {"x": landed.x, "y": landed.y, "z": landed.z},
                                 "frame": landed_player.get("frame")}
            actual_steps = (landed_idx - (start_index - 1)) if landed_idx is not None and landed_idx >= start_index else step_count
            record["continuous_segments"].append({
                "segment": segment_index, "button": button, "buttons": buttons,
                "from": previous.public(), "to": (landed.public() if landed else expected.public()), "steps": actual_steps,
                "frames": frames, "verified_landing": True,
                "facing_before": facing_now, "turn_overhead_frames": turn_overhead,
            })
            record["updated_at"] = _now()
            navigation_audit_log.record(
                "execution", "segment_landed", task_id=record["task_id"], plan_id=record["plan_id"],
                segment=record["continuous_segments"][-1], frame=landed_player.get("frame"),
            )
            self._emit(
                "navigation.task.progress", task_id=record["task_id"], plan_id=record.get("plan_id"),
                correlation_id=record.get("correlation_id"),
                summary="Navigation task reached a verified segment endpoint.",
                resources={"task": f"/api/v1/navigation/tasks/{record['task_id']}"},
                data={"completed_steps": record["progress"]["completed_steps"], "total_steps": record["progress"]["total_steps"]},
            )
            if effective_completed >= len(path) - 1:
                # Target tile reached ahead of segmented schedule
                break

        arrived, arrival_player = self._current_node()
        goal = path[-1]

        # 检查是否抵达门前待命格并执行自动顶门 (Push-Through)
        door_warp = await self._resolve_and_push_door(record, goal)
        if door_warp and door_warp.get("transited"):
            arrived, arrival_player = self._current_node()
            record["zone_transitions"].append({
                "from_zone": int(goal.zone_id),
                "to_zone": int(door_warp["target_zone"]),
                "landing": door_warp.get("landing"),
            })

        # 检查是否成功完成跨区 Warp 传送
        zone_switched_to_target = False
        if arrived and int(arrived.zone_id) != int(goal.zone_id):
            provider = self.planner._resolve_static_provider() if hasattr(self.planner, "_resolve_static_provider") else None
            if provider and hasattr(provider, "event_overlay_at"):
                try:
                    overlays = list(provider.event_overlay_at(int(goal.zone_id), int(goal.x), int(goal.z)))
                except Exception:
                    overlays = []
                warp_on_goal = next((it for it in overlays if it.get("kind") == "warp"), None)
                if warp_on_goal and int(arrived.zone_id) == int(warp_on_goal.get("target_zone_id_candidate", -1)):
                    zone_switched_to_target = True

        if not self._same_spatial_node(arrived, goal) and not zone_switched_to_target and not (door_warp and door_warp.get("transited")):
            return _stop("NAV_POSITION_DIVERGED", "Final Matrix-global GPos does not satisfy destination.",
                         expected=goal.public(), observed=arrived.public() if arrived else None)
        interaction = plan.get("interaction")
        if interaction is not None:
            facing = str(interaction.get("facing") or "")
            turn_result = await self._turn_to_facing(record, goal, facing)
            if not turn_result.get("ok"):
                return _stop(
                    "NAV_INTERACTION_FACING_FAILED",
                    "The player reached the NPC standing tile but facing could not be verified.",
                    interaction=interaction,
                    **{key: value for key, value in turn_result.items() if key != "ok"},
                )
            _turned_node, turned_player = self._current_node()
            if not self._same_spatial_node(_turned_node, goal):
                return _stop(
                    "NAV_POSITION_DIVERGED",
                    "The player moved away from the NPC standing tile while turning.",
                    expected=goal.public(),
                    observed=_turned_node.public() if _turned_node else None,
                )
            arrival_player = turned_player
            if interaction.get("execute") is True:
                record.pop("_clear_task", None)
                record["_cleanup_done"] = False
                input_evidence, interaction_observation = await self._press_interaction_button()
                navigation_audit_log.record(
                    "execution", "interaction_pressed", task_id=record["task_id"],
                    plan_id=record["plan_id"], target=interaction.get("target"),
                    stand_tile=interaction.get("stand_tile"), facing=facing,
                    input_evidence=input_evidence,
                    interaction_observation=interaction_observation,
                )
            else:
                input_evidence = None
                interaction_observation = None
        return {"status": "succeeded", "stop_reason": None, "arrival": {
            "zone_id": arrived.zone_id, "position": {"x": arrived.x, "y": arrived.y, "z": arrived.z},
            "world": arrival_player.get("world"), "frame": arrival_player.get("frame"),
            "evidence": "canonical_player_runtime",
            **({"interaction": interaction, "facing": facing,
                "interact_pressed": bool(interaction.get("execute")),
                "interaction_input": input_evidence,
                "interaction_observation": interaction_observation,
            } if interaction is not None else {}),
        }}

    async def _resolve_and_push_door(self, record: dict[str, Any], stand: NavNode) -> dict[str, Any] | None:
        """Resolve building door portal at doorstep and execute directional push-through."""
        provider = self.planner._resolve_static_provider() if hasattr(self.planner, "_resolve_static_provider") else None
        if provider is None or not hasattr(provider, "event_overlay_at"):
            return None
        try:
            overlays = list(provider.event_overlay_at(int(stand.zone_id), int(stand.x), int(stand.z)))
        except Exception:
            overlays = []
        warp = next((it for it in overlays if it.get("kind") == "warp"), None)
        if warp is None:
            return None
        geom = warp.get("door_geometry") or {}
        door_type = geom.get("type")
        target_zone = warp.get("target_zone_id_candidate")
        entry_dir = geom.get("entry_direction")
        
        # 若是建筑嵌入门 (building_portal)，向 entry_direction 顶门并等待切图
        button_map = {"North": "Up", "South": "Down", "West": "Left", "East": "Right"}
        push_button = None
        # 1. 若是建筑嵌入门 (building_portal)，向 entry_direction 顶门
        if door_type == "building_portal" and entry_dir and target_zone is not None:
            push_button = button_map.get(entry_dir)
        # 2. 若是地面门垫 (walkable_mat)，且当前尚未触发转场，顺着当前面朝方向继续推进一步触发切图
        elif door_type == "walkable_mat" and target_zone is not None and int(target_zone) != int(stand.zone_id):
            if stand.x <= 2:
                push_button = "Left"
            elif stand.z <= 2:
                push_button = "Up"
            elif stand.x >= 14 and stand.x < 32:
                push_button = "Right"
            elif stand.z >= 18:
                push_button = "Down"
            else:
                facing_name = self._facing_name(self.player_sample())
                push_button = self._facing_button(facing_name) or "Down"

        if push_button and target_zone is not None:
            self._emit("navigation.door.push_through", task_id=record["task_id"],
                       summary=f"Approached door/mat; pushing {push_button} towards Zone {target_zone}.")
            await self.client.press_buttons([push_button], frames=12)
            
            # 等待转场切图 (最多 6 秒)
            deadline = asyncio.get_running_loop().time() + 6.0
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.2)
                if self.live_player_sampler is not None:
                    try:
                        res = self.live_player_sampler()
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass
                cur_node, _ = self._current_node()
                if cur_node and int(cur_node.zone_id) == int(target_zone):
                    return {"transited": True, "target_zone": int(target_zone), "landing": cur_node.public()}
        return None

    async def _turn_to_facing(
        self,
        record: dict[str, Any],
        stand: NavNode,
        expected_facing: str,
        expected_node: NavNode | None = None,
    ) -> dict[str, Any]:
        """Turn and prove facing without losing a turn+move edge.

        On the live Gen-5 field a short D-pad press can be consumed as both a
        facing change and the first tile of movement.  That remains safe when
        the observed landing is exactly ``expected_node``; the caller then
        treats this press as the edge input and does not press the same
        direction a second time.
        """
        button = self._facing_button(expected_facing)
        if button is None:
            return {"ok": False, "reason": "invalid_facing", "expected_facing": expected_facing}
        attempts = 0
        last_facing = None
        while attempts < 2:
            current, player = self._current_node()
            last_facing = self._facing_name(self.player_sample())
            if not self._same_spatial_node(current, stand):
                return {"ok": False, "reason": "stand_tile_changed", "observed": current.public() if current else None}
            if last_facing == expected_facing:
                return {"ok": True, "facing": last_facing, "attempts": attempts}
            control_error = self._not_controllable()
            if control_error:
                return {"ok": False, "reason": "not_controllable", **control_error}
            record.pop("_clear_task", None)
            record["_cleanup_done"] = False
            await self.client.press_buttons([button], frames=self.turn_frames)
            attempts += 1
            # The bridge can deliver a one-frame turn asynchronously.  A live
            # task must refresh PlayerRuntime while waiting; otherwise the
            # cached FaceDir stays stale and the executor may queue the same
            # turn a second time.  Keep the shorter fixture timeout when no
            # live sampler is configured.
            turn_timeout = (
                max(0.6, min(1.5, self.step_timeout_seconds))
                if self.live_player_sampler is not None
                else min(0.6, self.step_timeout_seconds)
            )
            deadline = asyncio.get_running_loop().time() + turn_timeout
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(self.poll_seconds)
                if self.live_player_sampler is not None:
                    try:
                        res = self.live_player_sampler()
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass
                current, _player = self._current_node()
                last_facing = self._facing_name(self.player_sample())
                if not self._same_spatial_node(current, stand):
                    if (
                        expected_node is not None
                        and self._same_spatial_node(current, expected_node)
                        and last_facing == expected_facing
                    ):
                        return {
                            "ok": True,
                            "facing": last_facing,
                            "attempts": attempts,
                            "moved": True,
                            "observed": current.public() if current else None,
                        }
                    if (
                        expected_node is not None
                        and self._same_spatial_node(current, expected_node)
                        and last_facing is None
                        and asyncio.get_running_loop().time() < deadline
                    ):
                        # FaceDir can lag the first GPos sample by one poll.
                        # Keep observing briefly instead of treating a
                        # successful turn+move as a divergent landing.
                        continue
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

    def _publish_terminal(self, record: dict[str, Any], outcome: dict[str, Any]) -> None:
        record["stop_reason"] = outcome.get("stop_reason")
        record["arrival"] = outcome.get("arrival")
        record["updated_at"] = _now()
        record["status"] = outcome["status"]  # Must remain last: this gates the input lease.
        terminal_reason = (outcome.get("stop_reason") or {}).get("code") if outcome.get("stop_reason") else "arrived"
        event_type = {
            "succeeded": "navigation.task.completed",
            "failed": "navigation.task.failed",
            "cancelled": "navigation.task.cancelled",
        }.get(record["status"], "navigation.task.failed")
        self._emit(
            event_type, task_id=record.get("task_id"), plan_id=record.get("plan_id"),
            correlation_id=record.get("correlation_id"),
            severity="error" if record["status"] == "failed" else "info",
            summary=f"Navigation task {record['status'] }.",
            resources={"task": f"/api/v1/navigation/tasks/{record.get('task_id')}"},
            data={
                "terminal_reason": terminal_reason,
                "goal": record.get("goal"),
                "arrival": record.get("arrival"),
                "completed_steps": (record.get("progress") or {}).get("completed_steps", 0),
            },
        )

    @staticmethod
    def _record_current(record: dict[str, Any], node: NavNode | None, player: dict[str, Any]) -> None:
        """Publish the last canonical runtime location, including failed landings."""
        if node is None:
            return
        record["current"] = {
            "zone_id": node.zone_id,
            "position": {"x": node.x, "y": node.y, "z": node.z},
            "frame": player.get("frame"),
        }
        record["updated_at"] = _now()

    async def _clear_segment_inputs(self, record: dict[str, Any]) -> None:
        """Clear one issued segment exactly once while this task owns input."""
        if record.get("_cleanup_done"):
            return
        clear_task = record.get("_clear_task")
        if clear_task is None:
            clear_task = asyncio.create_task(self.client.clear_inputs())
            record["_clear_task"] = clear_task
        try:
            await asyncio.shield(clear_task)
        except asyncio.CancelledError:
            # Do not start a competing clear after cancellation.  The task
            # runner will publish cancellation only after this one completes.
            try:
                await clear_task
            finally:
                raise
        except Exception as exc:
            raise RuntimeError(f"input cleanup failed: {type(exc).__name__}") from exc
        record["_cleanup_done"] = True

    async def _settle_after_segment_clear(
        self,
        record: dict[str, Any],
        *,
        allowed_nodes: list[NavNode],
        allow_moving_pipeline: bool = False,
        expected: NavNode | None = None,
    ) -> tuple[NavNode | None, dict[str, Any], dict[str, Any] | None, bool]:
        """Wait briefly for a normal Moving/Brake transition after key-up."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.post_clear_settle_seconds
        last_node, last_player = self._current_node()
        self._record_current(record, last_node, last_player)
        while True:
            horizontal_transition = (
                last_node is not None
                and any(self._same_horizontal_location(last_node, allowed) for allowed in allowed_nodes)
                and not self._in_spatial_set(last_node, allowed_nodes)
            )
            if last_node is None or (
                not self._in_spatial_set(last_node, allowed_nodes)
                and not horizontal_transition
            ):
                return last_node, last_player, {"reason": "position_left_allowed_path"}, False
            if horizontal_transition:
                # X/Z has landed, but the live layer is still settling.  Do
                # not reject it as a route divergence.  Once the field is
                # controllable and idle, a stable different layer is a valid
                # raised-path landing; the caller will replan from that live
                # layer before issuing another input.
                control_error = self._not_controllable()
                if control_error is None:
                    return last_node, last_player, {
                        "reason": "position_elevation_rebased",
                        "expected_layers": sorted({item.y for item in allowed_nodes}),
                        "observed_layer": last_node.y,
                    }, True
                if control_error.get("reason") in {"battle_started", "dialogue_started", "screen_not_overworld"}:
                    return last_node, last_player, control_error, False
                if loop.time() >= deadline:
                    return last_node, last_player, {"reason": "position_elevation_unsettled"}, False
                await asyncio.sleep(self.poll_seconds)
                if self.live_player_sampler is not None:
                    try:
                        res = self.live_player_sampler()
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass
                last_node, last_player = self._current_node()
                self._record_current(record, last_node, last_player)
                continue
            control_error = self._not_controllable()
            if control_error is None:
                return last_node, last_player, None, True
            if allow_moving_pipeline and control_error.get("reason") == "locomotion_not_idle":
                if last_node is not None and (self._same_spatial_node(last_node, expected) or self._in_spatial_set(last_node, allowed_nodes)):
                    return last_node, last_player, None, True
            if control_error.get("reason") != "locomotion_not_idle":
                return last_node, last_player, control_error, False
            if loop.time() >= deadline:
                if last_node is not None and self._same_spatial_node(last_node, expected):
                    return last_node, last_player, None, True
                return last_node, last_player, control_error, False
            await asyncio.sleep(self.poll_seconds)
            if self.live_player_sampler is not None:
                try:
                    res = self.live_player_sampler()
                    if inspect.isawaitable(res):
                        await res
                except Exception:
                    pass
            last_node, last_player = self._current_node()
            self._record_current(record, last_node, last_player)

    async def _wait_for_landing(
        self,
        record: dict[str, Any],
        previous: NavNode,
        expected: NavNode,
        *,
        allowed_nodes: list[NavNode] | None = None,
        timeout_seconds: float | None = None,
        is_terminal: bool = True,
        next_button: str | None = None,
        current_button: str | None = None,
    ) -> tuple[NavNode | None, dict[str, Any]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + (timeout_seconds if timeout_seconds is not None else self.step_timeout_seconds)
        allowed = list(allowed_nodes or (previous, expected))
        last_node: NavNode | None = previous
        last_player: dict[str, Any] = {}
        started_at = loop.time()
        last_change_at = started_at
        last_key = previous.public()
        samples = 0
        last_control_error: dict[str, Any] | None = None

        async def finish(outcome: str) -> tuple[NavNode | None, dict[str, Any]]:
            # Every path after press_buttons, including an intermediate or
            # timeout result, key-ups before inspecting the next state.
            continue_pipeline = (not is_terminal and next_button is not None)
            await self._clear_segment_inputs(record)
            settled_node, settled_player, settle_error, settled = await self._settle_after_segment_clear(
                record, allowed_nodes=allowed, allow_moving_pipeline=continue_pipeline, expected=expected,
            )
            final_node = settled_node if settled_node is not None else last_node
            final_player = settled_player or last_player
            final_error = settle_error if settle_error is not None else last_control_error
            self._last_landing_diagnostics = {
                "outcome": outcome,
                "timeout_seconds": round(loop.time() - started_at, 3),
                "samples": samples,
                "stationary_seconds": round(loop.time() - last_change_at, 3),
                "last_gpos": final_node.public() if final_node else None,
                "last_wpos": final_player.get("world"),
                "expected_gpos": expected.public(),
                "locomotion": final_player.get("locomotion"),
                "control_error": final_error,
                "input_clear": "cleared_at_expected" if outcome == "expected" else "cleared",
                "settled": settled,
                "settle_window_seconds": self.post_clear_settle_seconds,
            }
            # A semantic interruption at the expected tile is not a verified
            # landing.  Keep its physical position in record.current but make
            # the executor take the interruption branch.
            if self._same_spatial_node(final_node, expected) and not settled:
                return None, final_player
            return final_node, final_player

        while loop.time() < deadline:
            await asyncio.sleep(self.poll_seconds)
            if self.live_player_sampler is not None:
                try:
                    res = self.live_player_sampler()
                    if inspect.isawaitable(res):
                        await res
                except Exception:
                    pass
            node, player = self._current_node()
            samples += 1
            last_node, last_player = node, player
            self._record_current(record, node, player)
            control_error = self._not_controllable()
            last_control_error = control_error
            current_key = node.public() if node is not None else None
            if current_key != last_key:
                last_key = current_key
                last_change_at = loop.time()

            if self._same_spatial_node(node, expected):
                return await finish("expected")
            if self._same_horizontal_location(node, expected):
                # A stair/ledge can commit X/Z before the mapper's static
                # layer candidate.  If the field is already idle, finish the
                # bounded segment and let the executor replan from the live
                # elevation; otherwise keep sampling the same endpoint.
                if control_error is None:
                    return await finish("elevation_rebased")
                continue
            if node and previous and int(node.zone_id) != int(previous.zone_id):
                # 检查当前步是否踩上地面跨区门垫 (walkable_mat warp)
                provider = self.planner._resolve_static_provider() if hasattr(self.planner, "_resolve_static_provider") else None
                if provider and hasattr(provider, "event_overlay_at"):
                    try:
                        overlays = list(provider.event_overlay_at(int(previous.zone_id), int(expected.x), int(expected.z)))
                    except Exception:
                        overlays = []
                    warp = next((it for it in overlays if it.get("kind") == "warp"), None)
                    if warp and int(node.zone_id) == int(warp.get("target_zone_id_candidate", -1)):
                        return await finish("expected")

            if node is None or (
                not self._in_spatial_set(node, allowed)
                and not any(self._same_horizontal_location(node, a) for a in allowed)
            ):
                return await finish("divergent")
            if control_error and control_error.get("reason") != "locomotion_not_idle":
                return await finish("interrupted")
            # An Idle intermediate node means the bounded hold ended before
            # its intended endpoint.  It is a real partial segment, not a
            # reason to consume the entire movement timeout.
            if not self._same_spatial_node(node, previous) and control_error is None:
                return await finish("partial")

        return await finish("timeout")
