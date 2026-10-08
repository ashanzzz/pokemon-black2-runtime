"""Comprehensive acceptance test suite for Pokémon Black 2 Locomotion and Terrain Navigation (V24).

Validates:
1. Catwalk (0xBE/0xBF) axis traversal and side-drop mechanics;
2. Multi-width doors (1/2/3/4 wide) and approach vector resolution;
3. Bidirectional 4-level staircase planning and AI elevation semantics;
4. Surf shoreline mounting and dismounting transitions;
5. Fast-travel (Fly) destinations extraction and ZoneHeader gate evaluation.
"""
from __future__ import annotations

import pytest
from backend.black2.world.static_navigation import RomStaticNavigationGraph
from backend.black2.world.observed_navigation import NavNode, observed_navigation_graph
from backend.black2.world.runtime_player_state import player_runtime_service
from backend.black2.world.surf_transitions import surf_transition_service
from backend.black2.world.fast_travel import fast_travel_service
from backend.black2.world.staircase_corridors import staircase_corridor_service
from backend.black2.world.navigation_planning import NavigationPlanService


@pytest.fixture(scope="module")
def static_graph():
    return RomStaticNavigationGraph()


@pytest.fixture(scope="module")
def planner(static_graph):
    return NavigationPlanService(
        observed_navigation_graph,
        lambda: player_runtime_service.latest,
        static_provider=static_graph,
    )


# TC-01: Catwalk axis & side drop
def test_catwalk_axis_and_drop(static_graph):
    surfs = static_graph._decode_zone_surfaces(457)
    catwalk_tiles = [
        s for s in surfs
        if s["surface"].get("tile_class") in (0xBE, 0xBF) or
        s["surface"].get("material", {}).get("kind") in ("catwalk", "catwalk_entry")
    ]
    assert len(catwalk_tiles) > 0, "Zone 457 must have catwalk tiles"

    for c in catwalk_tiles[:5]:
        mat = c["surface"].get("material", {})
        assert mat.get("kind") in ("catwalk", "catwalk_entry")
        y_val = c["surface"].get("height", {}).get("chunk_relative_world_y") or 0.0
        assert abs(y_val - 32.0) < 0.1, f"Catwalk world_y {y_val} must be ~32.0"


# TC-02: Multi-width door extents in ROM entities
def test_multi_width_door_extents(static_graph):
    multi_warps = []
    for zid in range(static_graph.rom.zone_count):
        try:
            ent = static_graph.rom.entities(zid)
            for w in ent.get("warps", []):
                ex = w.get("x_extent_raw", 1)
                ey = w.get("y_extent_raw", 1)
                if ex > 1 or ey > 1:
                    multi_warps.append((zid, w["id"], ex, ey))
        except Exception:
            pass

    assert len(multi_warps) >= 400, f"Expected >= 400 multi-extent warps across ROM, got {len(multi_warps)}"

    widths = {ex for _, _, ex, _ in multi_warps}
    assert 4 in widths, "Must contain 4-wide doors"
    assert 3 in widths, "Must contain 3-wide doors"
    assert 2 in widths, "Must contain 2-wide doors"


# TC-03: Door approach vector resolution
def test_door_approach_vector(static_graph):
    geom4 = static_graph.resolve_door_geometry(0, 50, 9, 4, 1)
    assert geom4["type"] == "building_portal"
    assert geom4["width"] == 4
    assert geom4["width_category"] == "4_wide"
    assert geom4["facing"] == "South"
    assert geom4["entry_direction"] == "North"
    assert geom4["entry_vector"] == {"dx": 0, "dz": -1}
    assert len(geom4["doorsteps"]) == 4
    assert geom4["doorsteps"] == [(50, 10), (51, 10), (52, 10), (53, 10)]

    geom1x4 = static_graph.resolve_door_geometry(0, 9, 50, 1, 4)
    assert geom1x4["width"] == 1
    assert geom1x4["height"] == 4
    assert geom1x4["width_category"] == "4_high"
    assert geom1x4["facing"] == "East"
    assert geom1x4["entry_direction"] == "West"
    assert geom1x4["entry_vector"] == {"dx": -1, "dz": 0}
    assert len(geom1x4["doorsteps"]) == 4

    geom_mat = static_graph.resolve_door_geometry(350, 11, 15, 1, 1)
    assert geom_mat["type"] == "walkable_mat"
    assert geom_mat["entry_direction"] == "directly"


# TC-04: Multi-wide door event overlay recognition
def test_multi_wide_door_event_overlay(static_graph):
    for x in range(50, 54):
        ov = static_graph.event_overlay_at(0, x, 10)
        warp_items = [item for item in ov if item.get("kind") == "warp"]
        assert len(warp_items) >= 1, f"Tile ({x}, 10) must be recognized as part of Warp 0 doorstep"
        w = warp_items[0]
        assert w["extent"] == {"x": 4, "z": 1}
        assert w["approach_direction"] == "South"
        assert w["entry_direction"] == "North"
        assert len(w["doorstep_candidates"]) == 4


# TC-05: Bidirectional 4-level staircase path planning
def test_staircase_bidirectional_plan(planner):
    dest_up = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 10, "y": 2, "z": 46}
    start_up = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 12, "y": 0, "z": 46}
    plan_up = planner.create_plan(dest_up, start_position=start_up)
    assert plan_up["status"] == "ready"
    assert len(plan_up["route_detail"]["nodes"]) >= 2
    actions_up = plan_up["route_detail"]["actions"]
    assert any(a["direction"] == "West" for a in actions_up)

    dest_down = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 13, "y": 0, "z": 46}
    start_down = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 12, "y": 0, "z": 46}
    plan_down = planner.create_plan(dest_down, start_position=start_down)
    assert plan_down["status"] == "ready"
    actions_down = plan_down["route_detail"]["actions"]
    assert any(a["direction"] == "East" for a in actions_down)


# TC-06: Staircase AI semantics validation
def test_staircase_ai_semantics(static_graph):
    s13 = static_graph.surface_at(457, 13, 46, 0, allow_unverified_terrain=True)
    s12 = static_graph.surface_at(457, 12, 46, 0, allow_unverified_terrain=True)
    s11 = static_graph.surface_at(457, 11, 46, 0, allow_unverified_terrain=True)
    s10 = static_graph.surface_at(457, 10, 46, 0, allow_unverified_terrain=True)

    y13 = s13["surfaces"][0]["height"]["chunk_relative_world_y"] or 0.0
    y12 = s12["surfaces"][0]["height"]["chunk_relative_world_y"] or 0.0
    y11 = s11["surfaces"][0]["height"]["chunk_relative_world_y"] or 0.0
    y10 = s10["surfaces"][0]["height"]["chunk_relative_world_y"] or 0.0

    assert abs(y13 - 0.0) < 0.1, "Ground tile X=13 must be at 0.0"
    assert abs(y12 - 8.0) < 0.1, "Step 1 tile X=12 must be at 8.0"
    assert abs(y11 - 24.0) < 0.1, "Step 2 tile X=11 must be at 24.0"
    assert abs(y10 - 32.0) < 0.1, "Top platform tile X=10 must be at 32.0"

    assert s12["surfaces"][0]["height"]["slope_index"] == 16, "Step 1 must be slope 16"
    assert s11["surfaces"][0]["height"]["slope_index"] == 16, "Step 2 must be slope 16"
    assert s13["surfaces"][0]["height"]["slope_index"] == 0, "Ground must be flat (slope 0)"
    assert s10["surfaces"][0]["height"]["slope_index"] == 0, "Platform must be flat (slope 0)"


# TC-07: Surf shoreline transitions and height delta threshold
def test_surf_shoreline_transitions():
    res_448 = surf_transition_service.analyze_zone(448)
    assert res_448["total_mount_points"] > 0, "Zone 448 must have legal surf mount points"
    assert res_448["total_dismount_points"] > 0, "Zone 448 must have legal surf dismount points"

    for mp in res_448["mount_points"]:
        assert mp["height_delta"] <= 8.0, f"Surf jump point dy {mp['height_delta']} exceeds 8.0"
        assert mp["requires"] == "move:surf"

    res_446 = surf_transition_service.analyze_zone(446)
    assert res_446["total_mount_points"] > 100, "Zone 446 (Route 20) must have extensive water shores"


# TC-08: Surf dismount to land edge detection
def test_surf_dismount_edge():
    res_448 = surf_transition_service.analyze_zone(448)
    dismounts = res_448["dismount_points"]
    assert len(dismounts) > 0
    sample = dismounts[0]
    assert sample["type"] == "surf_dismount"
    assert sample["status"] == "legal_dismount_edge"
    assert sample["dismount_direction"] in ("North", "South", "East", "West")


# TC-09: Fast travel (Fly) capabilities & ZoneHeader landing targets
def test_fly_capabilities_and_targets():
    dests = fast_travel_service.get_destinations()
    assert len(dests) >= 50, f"Expected >= 50 fly destinations across Gen-5 Unova, got {len(dests)}"

    eval_outdoor = fast_travel_service.evaluate_fly(448, {19})
    assert eval_outdoor["legal"] is True
    assert eval_outdoor["current_zone_allows_fly"] is True

    eval_indoor = fast_travel_service.evaluate_fly(350, {19})
    assert eval_indoor["legal"] is False
    assert eval_indoor["current_zone_allows_fly"] is False

    eval_no_move = fast_travel_service.evaluate_fly(448, set())
    assert eval_no_move["legal"] is False
    assert eval_no_move["has_move_fly"] is False


# TC-10: General N-step staircase corridor clustering & flattening
def test_staircase_corridor_clustering():
    corridors = staircase_corridor_service.analyze_zone(457)
    assert len(corridors) == 4, f"Zone 457 must cluster into exactly 4 corridors, got {len(corridors)}"

    # Check 2-step stairs
    c_2step = next((c for c in corridors if c.corridor_id == "stair:457:12_11_46"), None)
    assert c_2step is not None
    assert c_2step.total_steps == 2
    assert c_2step.axis == "east_west"
    assert c_2step.rising_direction == "West"
    assert c_2step.lower_portal["x"] == 13 and c_2step.lower_portal["z"] == 46
    assert c_2step.upper_portal["x"] == 10 and c_2step.upper_portal["z"] == 46
    assert c_2step.flattened_slice_y == 1

    # Check 3-step trench stairs
    c_3step = next((c for c in corridors if c.total_steps == 3), None)
    assert c_3step is not None
    assert len(c_3step.steps) == 3

    # Check 5-step gentle ramp
    c_5step = next((c for c in corridors if c.total_steps == 5), None)
    assert c_5step is not None
    assert len(c_5step.steps) == 5


# TC-11: Player step evaluation within corridor
def test_player_step_evaluation():
    # Step 1
    s1 = staircase_corridor_service.evaluate_player_step(457, 12, 46, 7.99)
    assert s1 is not None
    assert s1["in_staircase_corridor"] is True
    assert s1["current_step"] == 1
    assert s1["total_steps"] == 2
    assert s1["flattened_slice_y"] == 1

    # Step 2
    s2 = staircase_corridor_service.evaluate_player_step(457, 11, 46, 23.99)
    assert s2 is not None
    assert s2["current_step"] == 2
    assert s2["total_steps"] == 2

    # Non-stair tile returns None
    s_none = staircase_corridor_service.evaluate_player_step(457, 13, 46, 0.0)
    assert s_none is None
