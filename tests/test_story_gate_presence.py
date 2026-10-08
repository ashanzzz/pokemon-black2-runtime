from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from backend.black2.api.app import app
from backend.black2.api import navigation_routes


@pytest.fixture
def client():
    from backend.black2.world.static_navigation import RomStaticNavigationGraph
    from backend.black2.world.navigation_planning import NavigationPlanService
    from backend.black2.world.observed_navigation import ObservedNavigationGraph
    provider = RomStaticNavigationGraph()
    navigation_routes.configure_navigation_routes(
        NavigationPlanService(ObservedNavigationGraph(), lambda: None, static_provider=provider),
        static_provider=provider,
        runtime_reader=None,
    )
    return TestClient(app)


def test_story_gate_presence_filters_vacated_static_spawn(client: TestClient):
    """Ensure vacated spawn points (like Alder at 110,695) do not retain ghost [!] gates."""
    # Query Zone 439 at Alder's old birth point (110, 695)
    response = client.get("/api/v1/navigation/radar/grid?zone_id=439&x=110&z=695&y=1&radius=1")
    assert response.status_code == 200
    data = response.json()
    cell = next((c for row in data["grid"] for c in row if c.get("x") == 110 and c.get("z") == 695), None)
    assert cell is not None
    # Must NOT be marked as story gate [!]
    assert cell.get("symbol") != "!"
    assert cell.get("story_gate") is None
    assert cell.get("walkable") is True


def test_indoor_pokemon_center_has_zero_false_story_gates(client: TestClient):
    """Ensure ordinary NPCs in Pokemon Center (like patron at 5,18) are never stamped with [!]."""
    response = client.get("/api/v1/navigation/radar/grid?zone_id=443&x=7&z=10&y=0&radius=9")
    assert response.status_code == 200
    data = response.json()
    all_cells = [c for row in data["grid"] for c in row]
    false_gates = [c for c in all_cells if c.get("symbol") == "!"]
    assert len(false_gates) == 0, f"Expected 0 false story gates, found: {false_gates}"

    # Verify patron at (5, 18) is ordinary NPC or ground, never story gate
    patron = next((c for c in all_cells if c.get("x") == 5 and c.get("z") == 18), None)
    assert patron is not None
    assert patron.get("story_gate") is None


def test_doorstep_stand_tile_standard_across_zones(client: TestClient):
    """Ensure [D] is always a walkable ground doorstep (Z=694 outdoor, Z=19 indoor)."""
    # Outdoor Pokemon Center
    r_out = client.get("/api/v1/navigation/radar/grid?zone_id=439&x=105&z=694&y=1&radius=1")
    assert r_out.status_code == 200
    grid_out = [c for row in r_out.json()["grid"] for c in row]
    out_door = next((c for c in grid_out if c.get("x") == 105 and c.get("z") == 694), None)
    out_wall = next((c for c in grid_out if c.get("x") == 105 and c.get("z") == 693), None)
    assert out_door is not None and out_door.get("symbol") == "D" and out_door.get("walkable") is True
    assert out_wall is not None and out_wall.get("symbol") == "#" and out_wall.get("walkable") is False

    # Indoor Pokemon Center
    r_in = client.get("/api/v1/navigation/radar/grid?zone_id=443&x=7&z=19&y=0&radius=1")
    assert r_in.status_code == 200
    grid_in = [c for row in r_in.json()["grid"] for c in row]
    in_door = next((c for c in grid_in if c.get("x") == 7 and c.get("z") == 19), None)
    assert in_door is not None and in_door.get("symbol") == "D" and in_door.get("walkable") is True


def test_alder_empirical_trigger_tiles_only_mark_left_and_right_hand_tiles(client: TestClient):
    """Live playtest established (112,668) and (113,668) are passable; (111,668) is the trigger."""
    response = client.get("/api/v1/navigation/radar/grid?zone_id=439&x=112&z=668&y=2&radius=2")
    assert response.status_code == 200
    cells = {(
        c.get("x"), c.get("z")
    ): c for row in response.json()["grid"] for c in row}

    assert cells[(111, 669)]["symbol"] in {"N", "."}  # offline provider has no live actor
    if cells[(111, 669)]["symbol"] == "N":
        assert cells[(111, 669)]["walkable"] is False
        assert cells[(111, 669)]["story_gate"] is not None
    assert cells[(111, 668)]["symbol"] == "!"
    assert cells[(111, 668)]["walkable"] is False
    assert cells[(112, 668)]["symbol"] != "!"
    assert cells[(112, 668)]["walkable"] is True
    assert cells[(113, 668)]["symbol"] != "!"
    assert cells[(113, 668)]["walkable"] is True
    assert cells[(111, 670)]["symbol"] == "!"
    assert cells[(111, 670)]["walkable"] is False


def test_hiker_empirical_flank_trigger_tiles_only_mark_left_and_right_hand_tiles(client: TestClient):
    """Live RAM playtest established Hiker at (159, 645) facing West blocks his left & right flanks.

    - Right flank (North): (159, 644) triggers intercept [!]
    - Left flank (South): (159, 646) triggers intercept [!]
    - Corridors (158, 644), (160, 644), (158, 646), (160, 646) are completely passable [.].
    - Row Z=644 must NOT have 3 consecutive [!] blocks; only (159, 644) is [!].
    """
    response = client.get("/api/v1/navigation/radar/grid?zone_id=446&x=159&z=645&y=2&radius=2")
    assert response.status_code == 200
    cells = {(c.get("x"), c.get("z")): c for row in response.json()["grid"] for c in row}

    # Flank triggers
    assert cells[(159, 644)]["symbol"] == "!"
    assert cells[(159, 644)]["walkable"] is False
    assert cells[(159, 646)]["symbol"] == "!"
    assert cells[(159, 646)]["walkable"] is False

    # Corridors around flanks must be passable and NOT [!]
    assert cells[(158, 644)]["symbol"] != "!"
    assert cells[(158, 644)]["walkable"] is True
    assert cells[(160, 644)]["symbol"] != "!"
    assert cells[(160, 644)]["walkable"] is True
    assert cells[(158, 646)]["symbol"] != "!"
    assert cells[(158, 646)]["walkable"] is True
    assert cells[(160, 646)]["symbol"] != "!"
    assert cells[(160, 646)]["walkable"] is True
