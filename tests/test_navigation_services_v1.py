import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.black2.world.navigation_planning import NavigationPlanService, NavigationPlanningError
from backend.black2.world.navigation_tasks import NavigationTaskService
from backend.black2.world.observed_navigation import ObservedNavigationGraph


def raw_player(frame, x, y, z, zone=427):
    return {
        "status": "resolved", "confidence": "verified", "frame": frame, "zone_id": zone,
        "position": {
            "grid": {"x": x, "y": y, "z": z},
            "world": {"x": x * 16 + 8, "y": y * 16, "z": z * 16 + 8},
        },
        "locomotion": {"phase": "Idle", "semantic_state": "Standing"},
    }


def controllable_snapshot(screen_type="OVERWORLD", can_move=True, dialogue=False):
    return {
        "runtime": {"status": "ready"},
        "semantic": {
            "map_loaded": True,
            "ready_for_input": True,
            "context": {
                "screen_type": screen_type,
                "can_move_player": can_move,
                "is_dialogue_active": dialogue,
            },
        },
    }


def graph_player(frame, x, y, z, zone=427):
    return {
        "frame": frame, "zone_id": zone,
        "grid": {"x": x, "y": y, "z": z},
        "world": {"x": x * 16 + 8, "y": y * 16, "z": z * 16 + 8},
    }


class FakeBridge:
    is_connected = True

    def __init__(self, latest, *, move=True):
        self.latest = latest
        self.move = move
        self.clear_count = 0

    async def press_buttons(self, buttons, frames=4):
        if self.move:
            assert buttons == ["Right"]
            self.latest["position"]["grid"]["x"] += 1
            self.latest["position"]["world"]["x"] += 16
            self.latest["frame"] += frames
        return {"queued": True}

    async def clear_inputs(self):
        self.clear_count += 1
        return {"ok": True}


class BlockingClearBridge(FakeBridge):
    def __init__(self, latest):
        super().__init__(latest)
        self.clear_started = asyncio.Event()
        self.allow_clear = asyncio.Event()

    async def clear_inputs(self):
        self.clear_count += 1
        self.clear_started.set()
        await self.allow_clear.wait()
        return {"ok": True}


def make_services(tmp, *, move=True):
    graph = ObservedNavigationGraph(project_root=Path(tmp))
    graph.observe_player(graph_player(1, 10, 0, 10))
    graph.observe_player(graph_player(2, 11, 0, 10))
    latest = raw_player(3, 10, 0, 10)
    bridge = FakeBridge(latest, move=move)
    planner = NavigationPlanService(graph, lambda: latest)
    tasks = NavigationTaskService(
        planner, bridge, lambda: latest, control_sample=controllable_snapshot,
        poll_seconds=0.001, step_timeout_seconds=0.01
    )
    return planner, tasks, bridge


def test_planner_returns_drawable_direct_observation_path():
    with TemporaryDirectory() as td:
        planner, _tasks, _bridge = make_services(td)
        plan = planner.create_plan(
            {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10}
        )
        assert plan["segments"][0]["path"] == [
            {"zone_id": 427, "x": 10, "y": 0, "z": 10},
            {"zone_id": 427, "x": 11, "y": 0, "z": 10},
        ]


def test_task_groups_a_straight_corridor_into_one_continuous_hold():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            graph.observe_player(graph_player(3, 12, 0, 10))
            latest = raw_player(4, 10, 0, 10)

            class ContinuousBridge(FakeBridge):
                def __init__(self, sample):
                    super().__init__(sample)
                    self.calls = []

                async def press_buttons(self, buttons, frames=4):
                    self.calls.append((buttons, frames))
                    self.latest["position"]["grid"]["x"] += max(1, frames // 14)
                    self.latest["position"]["world"]["x"] += max(1, frames // 14) * 16
                    self.latest["frame"] += frames
                    return {"queued": True}

            bridge = ContinuousBridge(latest)
            tasks = NavigationTaskService(
                NavigationPlanService(graph, lambda: latest), bridge, lambda: latest,
                control_sample=controllable_snapshot, poll_seconds=0.001, step_timeout_seconds=0.02,
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 12, "y": 0, "z": 10},
                max_steps=2,
            )
            await tasks._runners[started["task_id"]]
            result = tasks.get(started["task_id"])
            assert result["status"] == "succeeded", result["stop_reason"]
            assert bridge.calls == [(["Right"], 36)]
            assert result["continuous_segments"][0]["steps"] == 2

    asyncio.run(scenario())


def test_explicit_start_is_read_only_candidate_and_does_not_need_live_player():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.observe_player(graph_player(1, 10, 0, 10))
        graph.observe_player(graph_player(2, 11, 0, 10))
        planner = NavigationPlanService(graph, lambda: None)

        plan = planner.create_plan(
            {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
            {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 10, "y": 0, "z": 10},
        )

        assert plan["resolved_start"]["source"] == "explicit_grid"
        assert plan["resolved_start"]["confidence"] == "candidate"
        assert plan["resolved_start"]["frame"] is None


def test_invalid_explicit_start_is_rejected_by_planning_service():
    with TemporaryDirectory() as td:
        planner = NavigationPlanService(ObservedNavigationGraph(project_root=Path(td)), lambda: None)
        destination = {
            "type": "grid", "space": "gen5-field-grid-v1",
            "zone_id": 427, "x": 11, "y": 0, "z": 10,
        }
        invalid_starts = [
            {"type": "grid", "space": "wrong", "zone_id": 427, "x": 10, "y": 0, "z": 10},
            {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 10, "z": 10},
        ]

        for start in invalid_starts:
            try:
                planner.create_plan(destination, start)
            except NavigationPlanningError as exc:
                assert exc.code == "NAV_INVALID_START"
                assert exc.status_code == 422
            else:
                raise AssertionError("expected explicit start rejection")


def test_executor_replans_from_live_player_instead_of_explicit_preview_start():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            graph.observe_player(graph_player(3, 12, 0, 10))
            latest = raw_player(4, 11, 0, 10)
            bridge = FakeBridge(latest)
            planner = NavigationPlanService(graph, lambda: latest)
            tasks = NavigationTaskService(
                planner, bridge, lambda: latest,
                control_sample=controllable_snapshot, poll_seconds=0.001,
            )

            preview = planner.create_plan(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 12, "y": 0, "z": 10},
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 10, "y": 0, "z": 10},
            )
            assert preview["resolved_start"]["source"] == "explicit_grid"
            assert preview["cost"]["steps"] == 2

            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 12, "y": 0, "z": 10},
                max_steps=1,
            )
            assert started["current"]["source"] == "player_runtime"
            assert started["progress"]["total_steps"] == 1
            await tasks._runners[started["task_id"]]
            assert tasks.get(started["task_id"])["status"] == "succeeded"

    asyncio.run(scenario())


def test_task_verifies_arrival_and_always_clears_inputs():
    async def scenario():
        with TemporaryDirectory() as td:
            _planner, tasks, bridge = make_services(td)
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await tasks._runners[started["task_id"]]
            result = tasks.get(started["task_id"])
            assert result["status"] == "succeeded"
            assert result["arrival"]["position"] == {"x": 11, "y": 0, "z": 10}
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_task_stuck_fails_and_clears_inputs():
    async def scenario():
        with TemporaryDirectory() as td:
            _planner, tasks, bridge = make_services(td, move=False)
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await tasks._runners[started["task_id"]]
            result = tasks.get(started["task_id"])
            assert result["status"] == "failed"
            assert result["stop_reason"]["code"] == "NAV_STUCK"
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_task_rejects_max_step_truncation_before_input():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            graph.observe_player(graph_player(3, 12, 0, 10))
            latest = raw_player(4, 10, 0, 10)
            bridge = FakeBridge(latest)
            tasks = NavigationTaskService(
                NavigationPlanService(graph, lambda: latest), bridge, lambda: latest,
                control_sample=controllable_snapshot,
            )
            try:
                tasks.start(
                    {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 12, "y": 0, "z": 10},
                    max_steps=1,
                )
            except NavigationPlanningError as exc:
                assert exc.code == "NAV_LIMIT_EXCEEDED"
            else:
                raise AssertionError("expected max_steps rejection")
            assert bridge.clear_count == 0
    asyncio.run(scenario())


def test_cancel_before_runner_starts_is_terminal_and_clears_inputs():
    async def scenario():
        with TemporaryDirectory() as td:
            _planner, tasks, bridge = make_services(td, move=False)
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            result = await tasks.cancel(started["task_id"])
            assert result["status"] == "cancelled"
            assert result["stop_reason"]["code"] == "NAV_CANCELLED"
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_terminal_is_not_published_until_clear_finishes_and_new_task_is_blocked():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            latest = raw_player(3, 10, 0, 10)
            bridge = BlockingClearBridge(latest)
            planner = NavigationPlanService(graph, lambda: latest)
            tasks = NavigationTaskService(
                planner, bridge, lambda: latest, control_sample=controllable_snapshot,
                poll_seconds=0.001,
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await bridge.clear_started.wait()
            assert tasks.get(started["task_id"])["status"] == "executing"
            try:
                tasks.start(
                    {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                    max_steps=1,
                )
            except NavigationPlanningError as exc:
                assert exc.code == "NAV_INPUT_BUSY"
            else:
                raise AssertionError("cleanup interval must retain input ownership")
            bridge.allow_clear.set()
            await tasks._runners[started["task_id"]]
            assert tasks.get(started["task_id"])["status"] == "succeeded"
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_cancel_during_clear_uses_the_same_clear_and_retains_input_lease():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            latest = raw_player(3, 10, 0, 10)
            bridge = BlockingClearBridge(latest)
            tasks = NavigationTaskService(
                NavigationPlanService(graph, lambda: latest), bridge, lambda: latest,
                control_sample=controllable_snapshot, poll_seconds=0.001,
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await bridge.clear_started.wait()
            cancelling = asyncio.create_task(tasks.cancel(started["task_id"]))
            await asyncio.sleep(0)
            assert tasks.get(started["task_id"])["status"] == "cancelling"
            try:
                tasks.start(
                    {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                    max_steps=1,
                )
            except NavigationPlanningError as exc:
                assert exc.code == "NAV_INPUT_BUSY"
            else:
                raise AssertionError("cancelled owner must retain lease through clear")
            bridge.allow_clear.set()
            result = await cancelling
            assert result["status"] == "cancelled"
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_each_step_rechecks_controllable_state():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            graph.observe_player(graph_player(3, 12, 0, 10))
            latest = raw_player(4, 10, 0, 10)
            state = {"screen": "OVERWORLD", "can_move": True, "dialogue": False}

            class LockAfterOneBridge(FakeBridge):
                def __init__(self, sample):
                    super().__init__(sample)
                    self.press_count = 0
                async def press_buttons(self, buttons, frames=4):
                    self.press_count += 1
                    result = await super().press_buttons(buttons, frames)
                    if self.press_count == 1:
                        state.update(screen="DIALOGUE_ACTIVE", can_move=False, dialogue=True)
                    return result

            bridge = LockAfterOneBridge(latest)
            tasks = NavigationTaskService(
                NavigationPlanService(graph, lambda: latest), bridge, lambda: latest,
                control_sample=lambda: controllable_snapshot(
                    state["screen"], state["can_move"], state["dialogue"]
                ),
                poll_seconds=0.001,
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 12, "y": 0, "z": 10},
                max_steps=2,
            )
            await tasks._runners[started["task_id"]]
            result = tasks.get(started["task_id"])
            assert result["status"] == "failed"
            assert result["stop_reason"]["code"] == "NAV_NOT_CONTROLLABLE"
            assert bridge.press_count == 1
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_candidate_player_with_coherent_coordinates_can_execute():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            planner_player = raw_player(3, 10, 0, 10)
            runtime_player = raw_player(3, 10, 0, 10)
            runtime_player["status"] = "candidate"
            runtime_player["confidence"] = "candidate"
            bridge = FakeBridge(runtime_player)
            planner = NavigationPlanService(graph, lambda: planner_player)
            tasks = NavigationTaskService(
                planner, bridge, lambda: bridge.latest,
                control_sample=controllable_snapshot, poll_seconds=0.001, step_timeout_seconds=0.01,
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await tasks._runners[started["task_id"]]
            result = tasks.get(started["task_id"])
            assert result["status"] == "succeeded"
            assert result["progress"]["completed_steps"] == 1
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_candidate_player_with_inconsistent_world_position_is_rejected():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            graph.observe_player(graph_player(1, 10, 0, 10))
            graph.observe_player(graph_player(2, 11, 0, 10))
            planner_player = raw_player(3, 10, 0, 10)
            runtime_player = raw_player(3, 10, 0, 10)
            runtime_player["status"] = "candidate"
            runtime_player["position"]["world"]["x"] += 16
            bridge = FakeBridge(runtime_player)
            planner = NavigationPlanService(graph, lambda: planner_player)
            tasks = NavigationTaskService(
                planner, bridge, lambda: bridge.latest,
                control_sample=controllable_snapshot, poll_seconds=0.001, step_timeout_seconds=0.01,
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await tasks._runners[started["task_id"]]
            result = tasks.get(started["task_id"])
            assert result["status"] == "failed"
            assert result["stop_reason"]["code"] == "NAV_NOT_CONTROLLABLE"
            assert result["stop_reason"]["details"]["reason"] == "player_coordinates_inconsistent"
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_non_controllable_screen_fails_before_sending_input():
    async def scenario():
        with TemporaryDirectory() as td:
            planner, _tasks, bridge = make_services(td)
            bridge.press_count = 0
            original = bridge.press_buttons
            async def counted(buttons, frames=4):
                bridge.press_count += 1
                return await original(buttons, frames)
            bridge.press_buttons = counted
            tasks = NavigationTaskService(
                planner, bridge, lambda: bridge.latest,
                control_sample=lambda: controllable_snapshot("DIALOGUE_ACTIVE", False, True),
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await tasks._runners[started["task_id"]]
            result = tasks.get(started["task_id"])
            assert result["status"] == "failed"
            assert result["stop_reason"]["code"] == "NAV_NOT_CONTROLLABLE"
            assert bridge.press_count == 0
            assert bridge.clear_count == 1
    asyncio.run(scenario())


def test_control_predicate_accepts_string_enum_overworld():
    from enum import Enum

    class Screen(str, Enum):
        OVERWORLD = "OVERWORLD"

    async def scenario():
        with TemporaryDirectory() as td:
            planner, _tasks, bridge = make_services(td)
            tasks = NavigationTaskService(
                planner, bridge, lambda: bridge.latest,
                control_sample=lambda: controllable_snapshot(Screen.OVERWORLD),
                poll_seconds=0.001,
            )
            started = tasks.start(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 427, "x": 11, "y": 0, "z": 10},
                max_steps=1,
            )
            await tasks._runners[started["task_id"]]
            assert tasks.get(started["task_id"])["status"] == "succeeded"
    asyncio.run(scenario())


def test_wait_for_landing_clears_queue_before_accepting_idle_expected_tile():
    async def scenario():
        with TemporaryDirectory() as td:
            graph = ObservedNavigationGraph(project_root=Path(td))
            latest = raw_player(1, 11, 0, 10)
            bridge = FakeBridge(latest, move=False)
            tasks = NavigationTaskService(
                NavigationPlanService(graph, lambda: latest),
                bridge,
                lambda: latest,
                control_sample=controllable_snapshot,
                poll_seconds=0.001,
                step_timeout_seconds=0.02,
            )

            record = {"_cleanup_done": False}
            previous = tasks._current_node()[0]
            landed, _player = await tasks._wait_for_landing(
                record,
                previous,
                previous,
            )

            assert landed == previous
            assert bridge.clear_count == 1
            assert record.get("_clear_task") is not None
            assert tasks._last_landing_diagnostics["input_clear"] == "cleared_at_expected"

    asyncio.run(scenario())

