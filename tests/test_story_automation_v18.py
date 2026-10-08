import asyncio
from unittest.mock import patch
from types import SimpleNamespace

from backend.black2.runtime.events import AgentEventBus
from backend.black2.world.navigation_planning import NavigationPlanningError
from backend.black2.world.runtime_player_state import player_runtime_service
from backend.black2.world.story_automation import StoryAutomationService


def test_story_target_resolution_ignores_malformed_script_ids():
    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=None,  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    service._entities = lambda _zone_id: {  # type: ignore[method-assign]
        "npcs": [
            {"id": "bad", "script_id": "not-a-number", "x": 99, "y": 99, "z": 99},
            {"id": "nurse", "script_id": 2100, "x": 7, "y": 10, "z": 0},
        ],
    }

    target = service._resolve_npc_target(
        {"script_id": 2100},
        {"zone_id": 443, "x": 6, "y": 0, "z": 19},
    )

    assert target == {
        "zone_id": 443,
        "x": 7,
        "y": 0,
        "z": 10,
        "source": "ROM_npc_selector",
        "npc_id": "nurse",
        "script_id": 2100,
    }


def test_story_selector_binds_to_live_actor_position_before_navigation():
    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=None,  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    service._entities = lambda _zone_id: {  # type: ignore[method-assign]
        "npcs": [
            {"id": 4, "script_id": 11, "sprite_id": 28, "x": 112, "y": 661, "z": 2},
        ],
    }

    target = service._resolve_npc_target(
        {"zone_id": 439, "script_id": 11},
        {"zone_id": 439, "x": 112, "y": 2, "z": 667},
        runtime_payload={
            "status": "resolved",
            "actors": [
                {
                    "actor_uid": 4,
                    "slot": 3,
                    "zone_id": 439,
                    "same_current_scene": True,
                    "script_id": 11,
                    "grid": {"x": 112, "y": 2, "z": 661},
                    "facing": "South",
                },
            ],
        },
    )

    assert target["source"] == "runtime_actor_binding"
    assert target["x"] == 112
    assert target["y"] == 2
    assert target["z"] == 661
    assert target["static_coordinate"]["y"] == 2
    assert target["runtime_slot"] == 3


def test_story_selector_rejects_dynamic_obstacle_before_live_binding():
    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=None,  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    service._entities = lambda _zone_id: {  # type: ignore[method-assign]
        "npcs": [
            {"id": 0, "script_id": 8, "sprite_id": 97, "flag_id": 731, "x": 110, "y": 695, "z": 1},
        ],
    }

    with patch("backend.black2.world.story_automation.playtest_memory.record_event") as record_event:
        try:
            service._resolve_npc_target(
                {"zone_id": 439, "script_id": 8},
                {"zone_id": 439, "x": 112, "y": 2, "z": 667},
                runtime_payload={"status": "resolved", "actors": []},
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "AUTOMATION_TARGET_OBSTACLE"
            assert "no input was sent" in str(exc)
        else:
            raise AssertionError("obstacle selector unexpectedly resolved")
    record_event.assert_called_once()


def test_story_selector_allows_verified_live_story_actor_over_static_obstacle():
    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=None,  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    service._entities = lambda _zone_id: {  # type: ignore[method-assign]
        "npcs": [
            {"id": 0, "script_id": 8, "sprite_id": 97, "flag_id": 731, "x": 110, "y": 695, "z": 1},
        ],
    }

    target = service._resolve_npc_target(
        {"zone_id": 439, "script_id": 8},
        {"zone_id": 439, "x": 112, "y": 2, "z": 670},
        runtime_payload={
            "status": "resolved",
            "actors": [
                {
                    "actor_uid": 0,
                    "slot": 0,
                    "zone_id": 439,
                    "same_current_scene": True,
                    "script_id": 8,
                    "model_id": 97,
                    "grid": {"x": 111, "y": 2, "z": 669},
                    "facing": "East",
                },
            ],
        },
    )

    assert target["source"] == "runtime_actor_binding"
    assert target["x"] == 111
    assert target["y"] == 2
    assert target["z"] == 669


def test_story_selector_requires_live_binding_when_sample_is_supplied():
    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=None,  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    service._entities = lambda _zone_id: {  # type: ignore[method-assign]
        "npcs": [
            {"id": 4, "script_id": 11, "sprite_id": 28, "x": 112, "y": 661, "z": 2},
        ],
    }

    with patch("backend.black2.world.story_automation.playtest_memory.record_event"):
        try:
            service._resolve_npc_target(
                {"zone_id": 439, "script_id": 11},
                {"zone_id": 439, "x": 112, "y": 2, "z": 667},
                runtime_payload={
                    "status": "resolved",
                    "actors": [{"zone_id": 439, "script_id": 9, "grid": {"x": 108, "y": 1, "z": 684}}],
                },
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == "AUTOMATION_NPC_RUNTIME_UNRESOLVED"
            assert "no input was sent" in str(exc)
        else:
            raise AssertionError("unbound selector unexpectedly resolved")


def test_event_bus_persistent_tail_is_restart_safe(tmp_path):
    async def scenario():
        path = tmp_path / "agent_events.ndjson"
        bus = AgentEventBus(max_events=4, persist_path=path)
        await bus.publish("map.zone.transition.observed", summary="zone transition", data={"source_zone": 439})
        await bus.publish("dialogue.text.changed", summary="dialogue", data={"text": "hello"})
        payload = bus.persisted_recent(10)
        assert payload["status"] == "ok"
        assert payload["count"] == 2
        assert payload["events"][0]["type"] == "map.zone.transition.observed"
        assert payload["events"][1]["data"]["text"] == "hello"

    asyncio.run(scenario())


def test_story_capabilities_keep_pc_candidate_read_only():
    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=None,  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    capabilities = service.capabilities()
    assert capabilities["services"]["pc_storage"]["execution_available"] is False
    assert capabilities["services"]["npc_dialogue"]["execution_available"] is True


def test_interaction_route_accepts_ready_plan_without_legacy_reachable_field():
    class ReadyPlanner:
        def create_plan(self, *args, **kwargs):
            return {"status": "ready", "route_detail": {"nodes": []}}

    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=ReadyPlanner(),  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    interaction, stand = service._select_interaction_route(
        {},
        {"zone_id": 443, "x": 7, "y": 0, "z": 10},
        movement_mode="auto",
    )
    assert stand["z"] == 11
    assert interaction["facing"] == "North"


def test_interaction_route_accepts_runtime_verified_current_stand_when_graph_has_no_route():
    class SparsePlanner:
        def create_plan(self, *args, **kwargs):
            raise NavigationPlanningError("NAV_NO_ROUTE", "graph not seeded")

    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=SparsePlanner(),  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    with patch.object(
        player_runtime_service,
        "latest",
        {
            "status": "resolved",
            "zone_id": 443,
            "position": {"grid": {"x": 7, "y": 0, "z": 11}},
        },
    ):
        interaction, stand = service._select_interaction_route(
            {},
            {"zone_id": 443, "x": 7, "y": 0, "z": 10},
            movement_mode="auto",
        )
    assert stand["z"] == 11
    assert interaction["route_validation"] == "runtime_verified_current_stand"


def test_navigation_dialogue_trigger_at_verified_stand_is_handed_to_auto_dialogue():
    class FakeNavigation:
        def get(self, _task_id):
            return {
                "status": "failed",
                "current": {"zone_id": 439, "position": {"x": 111, "y": 2, "z": 670}},
                "interaction": {
                    "kind": "npc",
                    "stand_tile": {"zone_id": 439, "x": 111, "y": 2, "z": 670},
                    "facing": "North",
                },
                "stop_reason": {
                    "code": "NAV_INTERRUPTED_BY_DIALOGUE",
                    "message": "field dialogue started",
                },
            }

    service = StoryAutomationService(
        navigation=FakeNavigation(),  # type: ignore[arg-type]
        planner=None,  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    record = {
        "interaction": {
            "kind": "npc",
            "stand_tile": {"zone_id": 439, "x": 111, "y": 2, "z": 670},
            "facing": "North",
        }
    }
    with patch.object(
        player_runtime_service,
        "latest",
        {
            "status": "resolved",
            "zone_id": 439,
            "frame": 123,
            "position": {
                "grid": {"x": 111, "y": 2, "z": 670},
                "world": {"x": 1784.0, "y": 32.0, "z": 10728.0},
            },
        },
    ):
        result = asyncio.run(service._wait_navigation(record, "nav_dialogue"))

    assert result["status"] == "succeeded"
    assert result["arrival"]["dialogue_started_during_navigation"] is True
    assert record["navigation_dialogue_handoff"]["status"] == "verified"


def test_recovery_connector_waits_for_delayed_zone_transition():
    class FakeAction:
        def __init__(self):
            self.calls = []

        async def press_button(self, button, *, hold_frames, wait_frames):
            self.calls.append((button, hold_frames, wait_frames))

    class FakeHub:
        def snapshot(self):
            return {"transport": {"session_id": "test-session"}}

    def player_payload(zone_id, x, y, z):
        return {
            "status": "resolved",
            "zone_id": zone_id,
            "position": {"grid": {"x": x, "y": y, "z": z}},
        }

    async def scenario():
        action = FakeAction()
        samples = [
            player_payload(439, 50, 2, 650),
            player_payload(439, 105, 1, 694),
            player_payload(439, 105, 1, 693),
            player_payload(443, 7, 0, 19),
        ]

        async def refresh():
            return samples.pop(0) if samples else player_payload(443, 7, 0, 19)

        service = StoryAutomationService(
            navigation=None,  # type: ignore[arg-type]
            planner=None,  # type: ignore[arg-type]
            action_engine=action,
            hub=FakeHub(),
            static_provider=lambda: None,
            player_refresh=refresh,
        )
        service._run_navigation_to = lambda *args, **kwargs: _immediate_sleep()  # type: ignore[method-assign]
        service._emit = lambda *args, **kwargs: _immediate_sleep()  # type: ignore[method-assign]
        record = {
            "task_id": "auto_recovery_test",
            "correlation_id": "recovery-test",
            "phase": "routing",
            "status": "routing",
            "recovery": {},
            "current": None,
        }
        with patch(
            "backend.black2.world.story_automation.runtime_warp_evidence.match",
            return_value=[
                {
                    "source_grid": {"x": 105, "y": 1, "z": 693},
                    "landing_grid": {"x": 7, "y": 0, "z": 19},
                    "frame_before": 100,
                    "frame_after": 200,
                }
            ],
        ), patch("backend.black2.world.story_automation.asyncio.sleep", new=_immediate_sleep):
            result = await service._ensure_recovery_center(record, movement_mode="walk", max_steps=30)

        assert result == {"zone_id": 443, "x": 7, "y": 0, "z": 19}
        assert action.calls == [("Up", 4, 15)]
        assert record["recovery"]["connector"]["status"] == "succeeded"
        assert record["recovery"]["connector"]["polls"] >= 1

    async def _immediate_sleep(*_args):
        return None

    asyncio.run(scenario())


def test_auto_dialogue_waits_through_page_gap_and_requires_overworld_settle():
    class FakeHub:
        def __init__(self):
            self.samples = [
                {
                    "dialogue": {
                        "active": True,
                        "screen_type": "DIALOGUE_ACTIVE",
                        "loaded_text": "第一段",
                        "can_move_player": False,
                        "choices": [],
                    }
                },
                # This is the transient gap that previously caused a false
                # completion before the final page became visible.
                {
                    "dialogue": {
                        "active": False,
                        "screen_type": "DIALOGUE_ACTIVE",
                        "loaded_text": "",
                        "can_move_player": False,
                        "choices": [],
                    }
                },
                {
                    "dialogue": {
                        "active": True,
                        "screen_type": "DIALOGUE_ACTIVE",
                        "loaded_text": "最终段",
                        "can_move_player": False,
                        "choices": [],
                    }
                },
            ]

        async def sample_once(self):
            if self.samples:
                return self.samples.pop(0)
            return {
                "dialogue": {
                    "active": False,
                    "screen_type": "OVERWORLD",
                    "loaded_text": "",
                    "can_move_player": True,
                    "choices": [],
                }
            }

        def snapshot(self):
            return {"dialogue": {}}

    class FakeAction:
        async def advance_dialogue_once(self):
            return None

    async def scenario():
        service = StoryAutomationService(
            navigation=None,  # type: ignore[arg-type]
            planner=None,  # type: ignore[arg-type]
            action_engine=FakeAction(),
            hub=FakeHub(),
            static_provider=lambda: None,
        )
        service._emit = lambda *args, **kwargs: asyncio.sleep(0)  # type: ignore[method-assign]
        record = {
            "task_id": "auto_test",
            "kind": "recover",
            "dialogue": {"auto": True, "steps": 0, "max_steps": 8, "texts": [], "choice": None},
        }
        with patch("backend.black2.world.story_automation.asyncio.sleep", new=lambda *_args: _immediate_sleep()):
            result = await service._auto_dialogue(record)
        assert result["status"] == "ended"
        assert record["dialogue"]["texts"] == ["第一段", "最终段"]
        assert result["steps"] == 2

    async def _immediate_sleep():
        return None

    asyncio.run(scenario())


def test_story_capabilities_expose_verified_recovery():
    service = StoryAutomationService(
        navigation=None,  # type: ignore[arg-type]
        planner=None,  # type: ignore[arg-type]
        action_engine=None,  # type: ignore[arg-type]
        hub=SimpleNamespace(snapshot=lambda: {}),
        static_provider=lambda: None,
    )
    capabilities = service.capabilities()
    rec = capabilities["services"]["recovery"]
    assert rec["execution_available"] is True
    assert "verified" in rec["party_hp_completion"]
