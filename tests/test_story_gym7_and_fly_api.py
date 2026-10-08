from fastapi.testclient import TestClient
import pytest
from unittest.mock import AsyncMock, patch

from backend.black2.api.app import app
from backend.black2.world.runtime_player_state import player_runtime_service
from backend.black2.progression.state import progression_state_service


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_fast_travel_destinations_contains_opelucid(client):
    res = client.get("/api/v1/navigation/fast-travel/destinations")
    assert res.status_code == 200
    dests = res.json()
    assert isinstance(dests, list)
    opelucid = next((d for d in dests if d["zone_id"] == 120), None)
    assert opelucid is not None
    assert "双龙" in opelucid["name"] or "Opelucid" in opelucid["name"]
    assert opelucid["landing_grid"] == {"x": 26, "y": 0, "z": 10}


def test_fast_travel_evaluate_endpoint(client):
    mock_p = {
        "status": "resolved",
        "zone_id": 448,
        "position": {"grid": {"x": 210, "y": 0, "z": 649}},
        "locomotion": {"phase": "Idle"},
    }
    with patch.object(player_runtime_service, "latest", mock_p), \
         patch("backend.black2.api.battle_routes._party_decoder.sample", new_callable=AsyncMock) as mock_party:
        mock_party.return_value = {
            "slots": [{"slot": 1, "moves": [{"move_id": 19}]}]
        }
        res = client.get("/api/v1/navigation/fast-travel/evaluate?zone_id=448")
        assert res.status_code == 200
        body = res.json()
        assert body["legal"] is True
        assert body["has_move_fly"] is True
        assert body["current_zone_allows_fly"] is True


def test_fast_travel_fly_endpoint_validates_and_dispatches(client):
    mock_p = {
        "status": "resolved",
        "zone_id": 448,
        "position": {"grid": {"x": 210, "y": 0, "z": 649}},
        "locomotion": {"phase": "Idle"},
    }
    with patch.object(player_runtime_service, "latest", mock_p), \
         patch("backend.black2.api.battle_routes._party_decoder.sample", new_callable=AsyncMock) as mock_party:
        mock_party.return_value = {
            "slots": [{"slot": 6, "species": 169, "species_name": "Crobat", "level": 41, "moves": [{"move_id": 19}]}]
        }
        res = client.post("/api/v1/navigation/fast-travel/fly", json={"destination_zone": 120})
        assert res.status_code == 200
        body = res.json()
        assert body["ok"] is True
        assert body["action"] == "fast_travel_fly"
        assert body["destination"]["zone_id"] == 120
        assert body["departure"]["zone_id"] == 448
        assert body["evaluation"]["legal"] is True
        assert body["fly_pokemon"]["slot"] == 6


def test_gym7_catalog_has_all_five_subordinates_and_leader(client):
    res = client.get("/api/v1/ai/gym/overview")
    assert res.status_code == 200
    gyms = res.json().get("gyms", [])
    gym7 = next((g for g in gyms if g["gym_index"] == 7), None)
    assert gym7 is not None
    assert gym7["zone_id"] == 121
    assert gym7["leader_name_zh"] == "夏卡"
    assert gym7["badge_name_zh"] == "传说徽章"
    assert gym7["type_specialty"] == "Dragon"
    assert gym7["total_battles_in_gym"] == 6
    assert len(gym7["subordinate_trainers"]) == 5
    sub_ids = [t["trainer_id"] for t in gym7["subordinate_trainers"]]
    assert sub_ids == [381, 382, 383, 384, 385]


def test_story_progression_plan_targets_gym7(client):
    mock_p = {
        "status": "resolved",
        "zone_id": 448,
        "position": {"grid": {"x": 210, "y": 0, "z": 649}},
        "locomotion": {"phase": "Idle"},
    }
    mock_prog = {
        "status": "verified",
        "badges": {"count": 6, "mask": 63},
    }
    with patch.object(player_runtime_service, "latest", mock_p), \
         patch.object(progression_state_service, "latest", mock_prog), \
         patch.object(progression_state_service, "sample", new_callable=AsyncMock) as mock_sample:
        mock_sample.return_value = mock_prog
        res = client.get("/api/v1/agent/story/plan")
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "ready"
        assert body["milestone"]["id"] == "M8_DRAYDEN_FREEZE_BADGE"
        assert body["location"]["target_zone"] == 121
        assert body["world_route"]["traversable"] is True


def test_flyable_regions_full_catalog_and_filtering(client):
    mock_p = {
        "status": "resolved",
        "zone_id": 406,
        "position": {"grid": {"x": 660, "y": 0, "z": 186}},
        "locomotion": {"phase": "Idle"},
    }
    with patch.object(player_runtime_service, "latest", mock_p),          patch("backend.black2.api.battle_routes._party_decoder.sample", new_callable=AsyncMock) as mock_party:
        mock_party.return_value = {
            "slots": [{"slot": 6, "species": 169, "species_name": "Crobat", "level": 41, "moves": [{"move_id": 19}]}]
        }
        res = client.get("/api/v1/player/fly/regions")
        assert res.status_code == 200
        body = res.json()
        assert body["format"] == "black2-flyable-regions/v1"
        assert body["total_destinations"] == 58
        assert body["current_flight_status"]["can_fly_now"] is True
        assert body["current_flight_status"]["flying_mount"]["slot"] == 6
        assert "gym_cities" in body["categories_summary"]

        # Test filtering by category=gym_cities
        res_gyms = client.get("/api/v1/player/fly/destinations?category=gym_cities&format=flat")
        assert res_gyms.status_code == 200
        gym_list = res_gyms.json()
        assert len(gym_list) >= 8
        assert all(g["has_gym"] is True for g in gym_list)

        # Test search query
        res_search = client.get("/api/v1/player/fly/destinations?search=120&format=flat")
        assert res_search.status_code == 200
        assert len(res_search.json()) >= 1
        assert res_search.json()[0]["zone_id"] == 120
