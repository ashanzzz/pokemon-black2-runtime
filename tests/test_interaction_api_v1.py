"""Normalized static interaction API contracts for autonomous play."""

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.black2.api import semantic_routes as routes


class Hub:
    def __init__(self):
        self.value = {
            "age_seconds": 0.1,
            "sampled_at": 123,
            "transport": {"bridge_connected": True, "session_id": "test"},
            "runtime": {"status": "ready", "semantic_status": "ready"},
            "semantic": {"context": {"screen_type": "OVERWORLD", "can_move_player": True}},
            "profile": {},
            "player": {
                "status": "resolved",
                "confidence": "candidate",
                "zone_id": 10,
                "frame": 88,
                "position": {"grid": {"x": 2, "y": 0, "z": 3}},
            },
        }

    def snapshot(self):
        return deepcopy(self.value)


class World:
    def zone(self, zone_id):
        if zone_id != 10:
            raise IndexError("unknown zone")
        return {
            "events": {
                "warps": [{
                    "id": 0,
                    "target_zone_or_map_raw": 11,
                    "target_warp_id": 0,
                    "x_world": 32,
                    "y_world": 64,
                    "z": 0,
                    "width": 1,
                    "height": 1,
                    "coordinate_units": "map_world_units_16_per_tile_candidate",
                }],
                "npcs": [{
                    "record_index": 2,
                    "id": 2,
                    "sprite_id": 7,
                    "movement_id": 0,
                    "flag_id": 42,
                    "script_id": 9,
                    "facing_id": 1,
                    "x": 3,
                    "y": 4,
                    "z": 0,
                    "coordinate_units": "map_local_tiles_candidate",
                }],
                "furniture": [{
                    "id": 1,
                    "script_id": 13,
                    "x": 2,
                    "y": 3,
                    "z": 0,
                    "coordinate_units": "map_local_tiles_candidate",
                }],
                "triggers": [{
                    "id": 0,
                    "entity_id": 3,
                    "constant": 1,
                    "reference": 99,
                    "x": 8,
                    "y": 8,
                    "z": 0,
                    "coordinate_units": "map_local_tiles_candidate",
                }],
            }
        }


class Connectors:
    def query(self, zone_id=None, **kwargs):
        return {
            "format": "black2-ai-warps/v1",
            "warps": [{
                "id": "zone:10:warp:0",
                "source": {
                    "zone_id": 10,
                    "warp_id": 0,
                    "grid_candidate": {"space": "gen5-field-grid-v1", "zone_id": 10, "x": 2, "y": None, "z": 4},
                },
                "destination": {"zone_id": 11, "warp_id": 0, "landing_status": "not_observed"},
                "role": {"kind": "entrance", "status": "candidate"},
            }],
            "count": 1,
        }


@pytest.fixture
def api(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(routes, "_hub", hub)
    monkeypatch.setattr(routes, "_world_service", World())
    monkeypatch.setattr(routes, "_connectors", lambda: Connectors())
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), hub


def test_interactions_normalize_entities_and_preserve_unknown_execution(api):
    client, _ = api
    response = client.get(
        "/api/v1/game/interactions",
        params={"zone_id": 10, "x": 2, "y": 0, "z": 3, "radius": 2},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "black2-ai-interactions/v1"
    assert body["query"]["origin_source"] == "query"
    assert body["counts"] == {"warp": 1, "npc": 1, "furniture": 1, "trigger": 0}
    by_kind = {row["kind"]: row for row in body["interactions"]}
    assert by_kind["npc"]["coordinate"] == {
        "space": "gen5-field-grid-v1", "zone_id": 10, "x": 3, "y": 0, "z": 4,
        "status": "candidate", "source_units": "map_local_tiles_candidate",
        "axis_mapping": "event.x->grid.x; event.y->grid.z; event.z->grid.y",
    }
    assert by_kind["npc"]["affordance"]["action"] == "talk"
    assert by_kind["npc"]["availability"]["can_interact"] is None
    assert by_kind["warp"]["destination"]["landing_status"] == "not_observed"


def test_interactions_can_use_fresh_player_origin_and_alias(api):
    client, _ = api
    body = client.get("/api/v1/ai/map/interactions", params={"radius": 0}).json()
    assert body["query"]["origin_source"] == "runtime_player"
    assert body["count"] == 1
    assert body["interactions"][0]["kind"] == "furniture"


@pytest.mark.parametrize("params", [{"x": 1}, {"z": 1}, {"x": 1, "y": 0}])
def test_interactions_require_horizontal_pair(api, params):
    client, _ = api
    assert client.get("/api/v1/game/interactions", params=params).status_code == 422


def test_capabilities_publish_interaction_contract(api):
    client, _ = api
    caps = client.get("/api/v1/game/capabilities").json()
    assert caps["read"]["map_interactions"] == "/api/v1/ai/map/interactions"
    assert caps["read"]["interactions"] == "/api/v1/game/interactions"
    assert caps["support"]["interaction_index"] == "ROM static candidates"
