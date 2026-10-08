import pytest
from fastapi.testclient import TestClient
from backend.black2.api.app import app
from backend.black2.api.navigation_routes import (
    _scan_active_story_triggers,
    navigation_static_provider,
)

@pytest.fixture
def client():
    return TestClient(app)

def test_alder_story_roadblock_dynamic_evaluation():
    provider = navigation_static_provider()
    
    # 1. When EventWork 0x40A5 == 2 (Alder actively blocking Floccesy Town)
    simulated_works = [0] * 431
    simulated_works[0x00A5] = 2  # Var 0x40A5 = 2
    
    trig_map, roadblocks, impassable = _scan_active_story_triggers(
        provider, 439, works_u16=simulated_works
    )
    
    assert len(roadblocks) >= 1
    alder_rb = next((rb for rb in roadblocks if "阿戴克" in rb["name"] or rb["script_id"] == 5), None)
    assert alder_rb is not None
    assert alder_rb["var_id"] == "0x40A5"
    assert alder_rb["live_value"] == 2
    assert alder_rb["expected_value"] == 2
    
    # Verify exact blocked coordinates: (111, 668, 2), (111, 669, 2), (111, 670, 2)
    blocked_tiles = [(t["x"], t["z"], t["y"]) for t in alder_rb["blocked_tiles"]]
    assert (111, 668, 2) in blocked_tiles
    assert (111, 669, 2) in blocked_tiles
    assert (111, 670, 2) in blocked_tiles
    
    # Verify 2D map lookup contains the blocked tiles
    assert (111, 668) in trig_map
    assert (111, 669) in trig_map
    assert (111, 670) in trig_map

    # 2. When EventWork 0x40A5 == 6 (Task completed / Player passed)
    simulated_works[0x00A5] = 6
    trig_map_passed, roadblocks_passed, impassable_passed = _scan_active_story_triggers(
        provider, 439, works_u16=simulated_works
    )
    assert len(roadblocks_passed) == 0
    assert (111, 668) not in trig_map_passed
    assert (111, 669) not in trig_map_passed

def test_universal_unregistered_npc_roadblock_detection():
    """Verify that ANY arbitrary story trigger outside the registry is 100% detected without hardcoding."""
    provider = navigation_static_provider()
    
    # Test Route 19 (Zone 437) where Bianca / Alder guides player at game start: Var 0x40A3 == 0 (Script 1)
    sim_works = [0] * 431
    sim_works[0x00A3] = 0  # Var 0x40A3 = 0
    
    trig_map, roadblocks, impassable = _scan_active_story_triggers(
        provider, 437, works_u16=sim_works
    )
    assert len(roadblocks) >= 1
    r19_rb = roadblocks[0]
    assert r19_rb["var_id"] == "0x40A3"
    assert r19_rb["script_id"] == 1
    # Check that width and height were expanded: (51..55, 699, 1)
    assert len(r19_rb["blocked_tiles"]) == 5

def test_live_actor_occupancy_blocks_tile():
    provider = navigation_static_provider()
    mock_actors = [
        {
            "is_player": False,
            "zone_id": 439,
            "model_id": 97,
            "facing": "North",
            "grid": {"x": 111, "y": 2, "z": 669},
        }
    ]
    _, _, impassable = _scan_active_story_triggers(
        provider, 439, works_u16=[0]*431, runtime_actors=mock_actors
    )
    
    npc_blocked = next((c for c in impassable if c["reason"] == "live_npc_body_occupancy" and c["x"] == 111 and c["z"] == 669), None)
    assert npc_blocked is not None
    assert npc_blocked["model_id"] == 97

def test_story_roadblocks_endpoint(client):
    res = client.get("/api/v1/navigation/story-roadblocks?zone_id=457")
    assert res.status_code == 200
    data = res.json()
    assert data["format"] == "black2-story-roadblocks/v1"
    assert data["zone_id"] == 457
    assert "impassable_coordinates" in data
