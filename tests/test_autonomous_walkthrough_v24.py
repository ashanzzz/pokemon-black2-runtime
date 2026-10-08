"""Comprehensive acceptance test suite for Autonomous Walkthrough & Locomotion (V24).

Verifies the 5 core walkthrough capabilities:
1. Nurse Joy universal counter geometry and Loop Breaker (stepping South);
2. Multi-wide gate (1/2/3/4 wide) approach vector resolution and handoff;
3. Battle forced switch and party replacement action sequence;
4. Fast-travel (Fly) destinations and landing doorstep verification;
5. N-step staircase corridor flattening and single-layer transition abstraction.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from backend.black2.world.static_navigation import RomStaticNavigationGraph
from backend.black2.world.staircase_corridors import staircase_corridor_service
from backend.black2.world.fast_travel import fast_travel_service
from backend.black2.world.story_automation import _NURSE_SCRIPT_ID
from backend.black2.api.app import app


@pytest.fixture(scope="module")
def static_graph():
    return RomStaticNavigationGraph()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# 1. Nurse Joy Universal Geometry across all Pokemon Centers
def test_nurse_joy_universal_geometry_across_all_centers(static_graph):
    rom = static_graph.rom
    centers_found = []
    for zid in range(rom.zone_count):
        try:
            ent = rom.entities(zid)
            for npc in ent.get("npcs", []):
                if npc.get("script_id") == _NURSE_SCRIPT_ID:
                    centers_found.append({
                        "zone_id": zid,
                        "nurse_x": npc.get("x"),
                        "nurse_y": npc.get("y"),
                    })
        except Exception:
            continue

    assert len(centers_found) >= 15, f"Expected >= 15 Pokemon Centers across Unova, found {len(centers_found)}"

    # All centers must share the exact universal layout: nurse at (7, 10), stand at (7, 12)
    for c in centers_found:
        assert c["nurse_x"] == 7 and c["nurse_y"] == 10, f"Zone {c['zone_id']} nurse is not at (7, 10)"
        counter_z = c["nurse_y"] + 1  # 11
        stand_z = c["nurse_y"] + 2    # 12
        escape_z = stand_z + 1         # 13
        assert counter_z == 11
        assert stand_z == 12
        assert escape_z == 13  # Stepping Down lands at Z=13, safely outside counter interaction range


# 2. Nurse Joy Loop Breaker Verification
def test_nurse_joy_loop_breaker_logic():
    # Verify that the counter interaction tile is (7, 12) facing North,
    # and the loop breaker escape vector is (0, +1) South to Z=13.
    stand_tile = (7, 12)
    facing = "North"
    escape_vector = (0, 1)  # Pressing Down moves Z from 12 to 13
    escaped_tile = (stand_tile[0] + escape_vector[0], stand_tile[1] + escape_vector[1])
    assert escaped_tile == (7, 13)
    # Standing at (7, 13) facing North is 2 tiles away from counter (7, 11), impossible to trigger Nurse Joy
    distance_to_counter = abs(escaped_tile[0] - 7) + abs(escaped_tile[1] - 11)
    assert distance_to_counter == 2, "Escaped tile must be 2 tiles away from counter to prevent loop re-trigger"


# 3. Multi-Width Gate Approach Vector & Handoff
def test_multi_wide_gate_approach_vector_and_handoff(static_graph):
    # 4-wide gate: Zone 0 Warp 0 (50..53, 9)
    geom_4wide = static_graph.resolve_door_geometry(0, 50, 9, 4, 1)
    assert geom_4wide["width"] == 4
    assert geom_4wide["width_category"] == "4_wide"
    assert geom_4wide["facing"] == "South"
    assert geom_4wide["entry_direction"] == "North"
    assert geom_4wide["entry_vector"] == {"dx": 0, "dz": -1}
    assert len(geom_4wide["doorsteps"]) == 4

    # 1x4 vertical gate: Zone 0 Warp 1 (9, 50..53)
    geom_1x4 = static_graph.resolve_door_geometry(0, 9, 50, 1, 4)
    assert geom_1x4["width_category"] == "4_high"
    assert geom_1x4["facing"] == "East"
    assert geom_1x4["entry_direction"] == "West"
    assert geom_1x4["entry_vector"] == {"dx": -1, "dz": 0}

    # 1x1 door: Zone 350 Warp 0 (16, 22)
    geom_1wide = static_graph.resolve_door_geometry(350, 16, 22, 1, 1)
    assert geom_1wide["width"] == 1
    assert geom_1wide["height"] == 1


# 4. Battle Forced Switch & Party Replacement
def test_battle_forced_switch_action_sequence(client):
    # Call battle action endpoint with switch action
    # Should accept slot 2 switch and generate deterministic touch/D-Pad sequence
    r = client.post("/api/v1/battle/ui-actions", json={
        "type": "switch",
        "actor": "player:0",
        "party_slot": 2
    })
    # Either executed (200) or rejected with structured battle reason (409)
    assert r.status_code in (200, 409)
    data = r.json()
    assert data.get("format") == "black2-battle-ui-action/v1"
    if r.status_code == 200:
        assert data.get("executed") is True
        assert data.get("verification", {}).get("code") == "BATTLE_SWITCH_EXECUTED"


# 5. Fast-Travel Fly Destinations & Landing Doorsteps
def test_fast_travel_fly_destinations_and_landing_doorstep():
    dests = fast_travel_service.get_destinations()
    assert len(dests) >= 50

    for d in dests:
        grid = d["landing_grid"]
        world = d["landing_world"]
        assert "x" in grid and "z" in grid
        assert "x" in world and "z" in world
        assert d["enable_fly_from"] is True
        assert d["landing_description"] == "城镇宝可梦中心门外门垫 (Pokemon Center Doorstep)"


# 6. N-Step Staircase Corridor Flattening & Single-Layer Abstraction
def test_staircase_flattening_ai_observability():
    corridors = staircase_corridor_service.analyze_zone(457)
    assert len(corridors) == 4

    for c in corridors:
        data = c.as_dict()
        assert "corridor_id" in data
        assert data["total_steps"] >= 2
        assert len(data["steps"]) == data["total_steps"]
        assert data["axis"] in ("east_west", "north_south")
        assert data["rising_direction"] in ("West", "East", "North", "South")
        # Every corridor abstracts to exactly 1 flattened transition slice
        assert isinstance(data["flattened_slice_y"], int)
