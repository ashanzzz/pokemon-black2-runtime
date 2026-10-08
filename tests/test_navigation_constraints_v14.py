from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from backend.black2.world.navigation_constraints import (
    ConstraintEvaluator,
    NavigationConstraint,
    compile_occupancy_as_constraints,
)
from backend.black2.world.navigation_context import NavigationContextCompiler, _rom_static_grid
from backend.black2.world.navigation_planning import NavigationPlanService, NavigationPlanningError
from backend.black2.world.observed_navigation import NavNode, ObservedNavigationGraph
from tests.test_global_navigation_v13 import FakeGlobalProvider, player_sample


def constraint(kind="field_obstacle", behavior="hard_block", *, zone=1, x=2, y=0, z=3, status="active"):
    return NavigationConstraint(
        constraint_id=f"{kind}:{zone}:{x}:{y}:{z}", kind=kind, behavior=behavior,
        tiles=((zone, x, y, z),), cost=None, dynamic=False,
        confidence="verified", source="test", status=status, metadata={},
    )


def test_actor_constraint_is_hard_occupancy():
    constraints = compile_occupancy_as_constraints([{"zone_id": 1, "x": 2, "y": 0, "z": 3}])
    result = ConstraintEvaluator(constraints).evaluate(NavNode(1, 2, 0, 3))
    assert result["blocked"] is True
    assert result["constraints"][0]["kind"] == "dynamic_actor"


def test_unknown_trigger_is_not_automatically_active_gate():
    result = ConstraintEvaluator([constraint("script_trigger", "unknown", status="activation_unresolved")]).evaluate(
        NavNode(1, 2, 0, 3)
    )
    assert result["blocked"] is False
    assert result["extra_cost"] == 25.0


def test_policy_can_allow_unknown_trigger():
    item = constraint("script_trigger", "unknown", status="activation_unresolved")
    evaluator = ConstraintEvaluator([item], policy={"script_trigger_unknown": "allow"})
    result = evaluator.evaluate(NavNode(1, 2, 0, 3))
    assert result["blocked"] is False
    assert result["extra_cost"] == 0.0
    assert evaluator.decision(item)["interruption_policy"] == "allow"


def test_active_story_gate_hard_blocks():
    result = ConstraintEvaluator([constraint("story_gate", "hard_block")]).evaluate(NavNode(1, 2, 0, 3))
    assert result["blocked"] is True
    assert result["blocking_constraints"][0]["kind"] == "story_gate"


def test_target_trigger_can_be_terminal():
    item = constraint("script_trigger", "terminal", status="target")
    result = ConstraintEvaluator([item]).evaluate(NavNode(1, 2, 0, 3), is_goal=True)
    assert result["blocked"] is False
    assert result["terminal"] is True


def test_terminal_does_not_override_a_separate_hard_block_on_the_goal():
    terminal = constraint("warp", "terminal", status="target")
    gate = constraint("story_gate", "hard_block", status="active")
    result = ConstraintEvaluator([terminal, gate]).evaluate(NavNode(1, 2, 0, 3), is_goal=True)
    assert result["terminal"] is True
    assert result["blocked"] is True
    assert result["blocking_constraints"] == [gate.public()]


def test_terminal_constraint_remains_allowed_when_a_warp_policy_is_hard_avoid():
    terminal = constraint("warp", "terminal", status="target")
    evaluator = ConstraintEvaluator([terminal], policy={"warp": "hard_avoid"})
    assert evaluator.decision(terminal)["effective_behavior"] == "terminal"
    assert evaluator.decision(terminal)["interruption_policy"] == "allow"


def test_unexpected_warp_is_avoided():
    result = ConstraintEvaluator([constraint("warp", "hard_block", status="unverified")]).evaluate(NavNode(1, 2, 0, 3))
    assert result["blocked"] is True


def test_constraint_uses_full_xyz_layer():
    evaluator = ConstraintEvaluator([constraint()])
    assert evaluator.evaluate(NavNode(1, 2, 1, 3))["constraints"] == []
    assert evaluator.evaluate(NavNode(2, 2, 0, 3))["constraints"] == []


def test_policy_blocked_has_distinct_error_code():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.observe_player({"frame": 1, "zone_id": 1, "grid": {"x": 2, "y": 0, "z": 3}})
        graph.observe_player({"frame": 2, "zone_id": 1, "grid": {"x": 3, "y": 0, "z": 3}})
        planner = NavigationPlanService(
            graph,
            lambda: {"status": "resolved", "confidence": "verified", "frame": 3, "zone_id": 1,
                     "grid": {"x": 2, "y": 0, "z": 3}, "position": {"grid": {"x": 2, "y": 0, "z": 3},
                     "world": {"x": 40, "y": 0, "z": 56}},
                     "world": {"x": 40, "y": 0, "z": 56},
                     "locomotion": {"transport_mode": "OnFoot"}},
        )
        with pytest.raises(NavigationPlanningError) as raised:
            planner.create_plan(
                {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 1, "x": 3, "y": 0, "z": 3},
                constraints=[constraint("story_gate", "hard_block", zone=1, x=3, y=0, z=3)],
            )
        assert raised.value.code == "NAV_POLICY_BLOCKED"
        assert raised.value.details["blocking_constraints"][0]["kind"] == "story_gate"


def test_observed_and_static_planners_use_same_constraint_policy():
    item = constraint("story_gate", "hard_block", zone=10, x=31, y=0, z=5)
    provider = FakeGlobalProvider()
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: player_sample(), static_provider=provider)
    with pytest.raises(NavigationPlanningError) as raised:
        planner.create_plan(
            {"type": "global_grid", "space": "gen5-matrix-grid-v1", "x": 34, "y": 0, "z": 5},
            constraints=[item],
        )
    assert raised.value.code == "NAV_POLICY_BLOCKED"


def test_context_compiler_keeps_event_semantics_typed_and_explainable():
    result = NavigationContextCompiler().compile(
        player={"frame": 10, "zone_id": 1},
        runtime_actors=[{"uid": 7, "zone_id": 1, "grid": {"x": 2, "y": 0, "z": 3}}],
        static_entities={"triggers": [{"record_index": 4, "zone_id": 1, "x": 3, "y": 0, "z": 3}],
                         "warps": [{"record_index": 5, "zone_id": 1, "x": 4, "y": 0, "z": 3}]},
    )
    by_kind = {item["kind"]: item for item in result["constraints"]}
    assert by_kind["dynamic_actor"]["behavior"] == "occupancy"
    assert by_kind["script_trigger"]["behavior"] == "unknown"
    assert by_kind["script_trigger"]["status"] == "activation_unresolved"
    assert by_kind["warp"]["behavior"] == "hard_block"


def test_zone_446_rom_static_events_use_canonical_axes_and_current_zone_provenance():
    # Zone 446 ROM records use {x, y=horizontal_z, z=elevation_y}; neither
    # record carries a Zone id because the entity archive belongs to Zone 446.
    npc = {"record_index": 3, "x": 154, "y": 651, "z": 2}
    trigger = {"record_index": 0, "x": 160, "y": 660, "z": 6}
    assert _rom_static_grid(npc, default_zone=446) == (446, 154, 2, 651)
    assert _rom_static_grid(trigger, default_zone=446) == (446, 160, 6, 660)

    result = NavigationContextCompiler().compile(
        player={"frame": 10, "zone_id": 446},
        # Dynamic actor grids already use the public canonical order.
        runtime_actors=[{"uid": 7, "zone_id": 446, "grid": {"x": 154, "y": 651, "z": 2}}],
        static_entities={"triggers": [trigger]},
    )
    by_kind = {item["kind"]: item for item in result["constraints"]}
    assert by_kind["dynamic_actor"]["tiles"] == [{"zone_id": 446, "x": 154, "y": 651, "z": 2}]
    assert by_kind["script_trigger"]["constraint_id"] == "trigger:446:0"
    assert by_kind["script_trigger"]["tiles"] == [{"zone_id": 446, "x": 160, "y": 6, "z": 660}]


def _route_constraint(
    constraint_id, kind, behavior, *, zone, x, status, source="rom:/a/1/2/6",
    confidence="rom_record", metadata=None,
):
    return NavigationConstraint(
        constraint_id=constraint_id,
        kind=kind,
        behavior=behavior,
        tiles=((zone, x, 0, 5),),
        cost=None,
        dynamic=False,
        confidence=confidence,
        source=source,
        status=status,
        metadata=dict(metadata or {}),
    )


def test_route_hazards_only_describe_the_chosen_path_with_evidence_and_policy():
    provider = FakeGlobalProvider()
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: player_sample(), static_provider=provider)
    trainer = _route_constraint(
        "trainer_sight:rom:4", "trainer_sight", "soft_cost", zone=10, x=31,
        status="candidate", metadata={"trainer_id": "rom-npc-4", "sight_raw": 5},
    )
    trigger = _route_constraint(
        "trigger:rom:7", "script_trigger", "unknown", zone=11, x=32,
        status="activation_unresolved", metadata={"entity_id": 7},
    )
    gate = _route_constraint(
        "story_gate:rom:2", "story_gate", "warning", zone=11, x=33,
        status="unresolved", metadata={"flag_id": 42},
    )
    warp = _route_constraint(
        "warp:rom:1", "warp", "terminal", zone=11, x=34,
        status="target", metadata={"navigation_target": True},
    )

    plan = planner.create_plan(
        {"type": "global_grid", "space": "gen5-matrix-grid-v1", "x": 34, "y": 0, "z": 5},
        constraints=[trainer, trigger, gate, warp],
    )

    hazards = plan["route_hazards"]
    assert [hazard["constraint_id"] for hazard in hazards] == [
        "trainer_sight:rom:4", "trigger:rom:7", "story_gate:rom:2", "warp:rom:1",
    ]
    assert [hazard["first_route_index"] for hazard in hazards] == [1, 2, 3, 4]
    assert [hazard["route_indices"] for hazard in hazards] == [[1], [2], [3], [4]]
    assert all(hazard["knowledge_state"] == "static_candidate" for hazard in hazards)
    assert hazards[0]["metadata"] == {"trainer_id": "rom-npc-4", "sight_raw": 5}
    assert hazards[0]["trigger_condition"]["event"] == "enter_trainer_sight_tile"
    assert hazards[0]["trigger_condition"]["runtime_confirmation_required"] is True
    assert [hazard["interruption_policy"] for hazard in hazards] == ["avoid", "avoid", "warn", "allow"]
    assert plan["route_hazard_summary"] == {
        "total": 4,
        "by_kind": {
            "script_trigger": 1, "story_gate": 1, "trainer_sight": 1, "warp": 1,
        },
        "by_interruption_policy": {"allow": 1, "avoid": 2, "warn": 1},
        "by_knowledge_state": {"static_candidate": 4},
        "soft_cost_crossed": 2,
        "terminal_crossed": 1,
        "runtime_revalidation_required": 4,
    }
    assert plan["route_hazard_decisions"][0]["policy_key"] == "trainer_sight"
    assert plan["route_hazard_decisions"][0]["policy_value"] == "soft_avoid"
    assert [item["constraint_id"] for item in plan["constraints_encountered"]] == [
        hazard["constraint_id"] for hazard in hazards
    ]


def test_final_route_invariant_rejects_a_provider_that_ignores_hard_constraints():
    class IgnoringProvider:
        def find_path(self, start, goal, **_kwargs):
            return {
                "reachable": True,
                "path": [start.public(), goal.public()],
                "cost": 1.0,
                "confidence": "candidate_static",
            }

    start = player_sample()
    start["zone_id"] = 1
    start["grid"] = {"x": 2, "y": 0, "z": 3}
    start["position"]["grid"] = dict(start["grid"])
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: start, static_provider=IgnoringProvider())
    blocked = NavigationConstraint(
        constraint_id="story_gate:runtime:1",
        kind="story_gate",
        behavior="hard_block",
        tiles=((1, 3, 0, 3),),
        cost=None,
        dynamic=True,
        confidence="runtime",
        source="runtime_story_state",
        status="active",
        metadata={"flag_id": 9},
    )

    with pytest.raises(NavigationPlanningError) as raised:
        planner.create_plan(
            {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 1, "x": 3, "y": 0, "z": 3},
            constraints=[blocked],
        )

    assert raised.value.code == "NAV_POLICY_BLOCKED"
    assert raised.value.details["blocking_constraints"] == [{
        **blocked.public(), "route_index": 1,
    }]
