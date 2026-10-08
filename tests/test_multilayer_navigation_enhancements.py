from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from backend.black2.api.app import app
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode
from backend.black2.world.navigation_constraints import ConstraintEvaluator, DEFAULT_AGENT_POLICY
from backend.black2.world.navigation_planning import _path_action_segments


def test_encounter_grass_soft_cost_reroutes_around_tall_grass():
    """Verify A* avoids tall_grass shortcut to prevent wild battle interruptions."""
    g = RomStaticNavigationGraph()
    # Path from (21, 0, 45) to (13, 0, 46) on Floor 0 in Zone 457
    # Default policy (encounter_grass='soft_avoid') must step West onto paved_road, avoiding (21, 46) and (20, 46)
    res_avoid = g.find_path(NavNode(457, 21, 0, 45), NavNode(457, 13, 0, 46))
    assert res_avoid['reachable'] is True
    path = [(p['x'], p['z']) for p in res_avoid['path']]
    assert (21, 46) not in path, "Path should avoid stepping into tall grass at (21, 46)"
    assert (20, 46) not in path, "Path should avoid stepping into tall grass at (20, 46)"
    assert path[1] == (20, 45), "Path should exit grass onto paved road at (20, 45)"

    # With encounter_grass='allow', A* takes the grass shortcut to save steps
    evaluator_allow = ConstraintEvaluator([], policy={'encounter_grass': 'allow'})
    res_allow = g.find_path(NavNode(457, 21, 0, 45), NavNode(457, 13, 0, 46), constraint_evaluator=evaluator_allow)
    assert res_allow['reachable'] is True
    path_allow = [(p['x'], p['z']) for p in res_allow['path']]
    assert path_allow[1] == (21, 46), "Path should take direct grass route when encounter_grass is allowed"


def test_stair_action_segment_partitioning_at_elevation_boundary():
    """Verify cardinal action compression breaks continuous hold at elevation changes."""
    test_path = [
        {'x': 15, 'y': 0, 'z': 46},
        {'x': 14, 'y': 0, 'z': 46},
        {'x': 13, 'y': 0, 'z': 46},  # Lower portal on Floor 0
        {'x': 12, 'y': 1, 'z': 46},  # Step 1
        {'x': 11, 'y': 1, 'z': 46},  # Step 2
        {'x': 10, 'y': 2, 'z': 46},  # Upper portal on Floor 2
        {'x': 9, 'y': 2, 'z': 46},
    ]
    segments = _path_action_segments(test_path)
    # Must NOT be compressed into 1 single 6-step segment
    assert len(segments) >= 4
    # Segment 1 must arrive at the lower portal (13, 0, 46)
    assert segments[0]['to']['x'] == 13
    assert segments[0]['to']['y'] == 0
    # Steps that transition elevation must have elevation_transition=True
    transit_segments = [s for s in segments if s.get('elevation_transition')]
    assert len(transit_segments) >= 2


def test_real_stairs_vs_illusory_dropoffs_distinction():
    """Verify system distinguishes between true staircases and non-stair dropoffs/cliffs."""
    client = TestClient(app)
    # 1. Real staircase: (14, 53) in Zone 457 belongs to stair:457:13_17_53
    res_stair = client.get("/api/v1/navigation/radar/grid?zone_id=457&x=14&z=53&y=0&radius=1").json()
    stair_cell = next(c for row in res_stair["grid"] for c in row if c["x"] == 14 and c["z"] == 53)
    assert stair_cell["blocked"] is False, "True stair must not be blocked"
    assert stair_cell["can_traverse"] is True, "True stair must be traversable"
    assert stair_cell["elevation_transition"] is not None
    assert stair_cell["elevation_transition"]["type"] == "slope"

    # 2. Illusory dropoff: (14, 54) has Y=-1 ground beneath, but NO staircase!
    res_cliff = client.get("/api/v1/navigation/radar/grid?zone_id=457&x=14&z=54&y=0&radius=1").json()
    cliff_cell = next(c for row in res_cliff["grid"] for c in row if c["x"] == 14 and c["z"] == 54)
    # Must be marked with ↕ (cliff dropoff) rather than a fake ▼ staircase
    assert cliff_cell["symbol"] == chr(0x2195), "Non-stair pit tile must display ↕ cliff dropoff"
    assert cliff_cell["is_stair_transit"] is False, "Non-stair pit tile must not be stair transit"
    assert cliff_cell["blocked"] is True, "Non-stair cliff tile must be blocked"
    assert cliff_cell["can_traverse"] is False, "Non-stair cliff tile must not be traversable"


def test_navigation_stairs_api_endpoint():
    """Verify /api/v1/navigation/stairs exposes all corridors for Zone 457."""
    client = TestClient(app)
    response = client.get("/api/v1/navigation/stairs?zone_id=457")
    assert response.status_code == 200
    data = response.json()
    assert data["format"] == "black2-navigation-stairs/v1"
    assert data["zone_id"] == 457
    assert data["total_corridors"] == 4
    corridors = data["corridors"]
    # Check corridor between Floor 0 and Floor 2
    c0_2 = next((c for c in corridors if c["corridor_id"] == "stair:457:12_11_46"), None)
    assert c0_2 is not None
    assert c0_2["lower_portal"]["floor_y"] == 0
    assert c0_2["upper_portal"]["floor_y"] == 2
    assert c0_2["axis"] == "east_west"
    assert c0_2["rising_direction"] == "West"


def test_navigation_topology_api_endpoint():
    """Verify /api/v1/navigation/topology exposes compact graph view for AI."""
    client = TestClient(app)
    response = client.get("/api/v1/navigation/topology?zone_id=457")
    assert response.status_code == 200
    data = response.json()
    assert data["format"] == "black2-navigation-topology/v1"
    assert data["zone_id"] == 457
    assert -3 in data["available_floors"]
    assert -1 in data["available_floors"]
    assert 0 in data["available_floors"]
    assert 2 in data["available_floors"]
    assert len(data["stairs"]) == 4
    assert "policy_recommendation" in data


def test_navigation_context_includes_staircase_corridors():
    """Verify /api/v1/navigation/context contains staircase_corridors for AI."""
    client = TestClient(app)
    response = client.get("/api/v1/navigation/context")
    assert response.status_code == 200
    data = response.json()
    assert "staircase_corridors" in data
    assert isinstance(data["staircase_corridors"], list)
