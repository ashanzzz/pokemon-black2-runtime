from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.world.world_graph import world_graph_service
from backend.black2.api.navigation_routes import router as nav_router


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(nav_router)
    return TestClient(app)


def test_world_graph_builds_and_resolves_nodes():
    world_graph_service.build()
    assert len(world_graph_service._zone_meta) > 400
    assert len(world_graph_service._adj) > 400


def test_world_graph_routes_aspertia_to_virbank():
    res = world_graph_service.find_route(427, 448, badge_mask=0x3F, badge_count=6)
    assert res["status"] == "traversable"
    assert res["traversable"] is True
    assert res["start_zone"] == 427
    assert res["goal_zone"] == 448
    assert res["zone_path"] == [427, 437, 439, 446, 448]
    assert len(res["steps"]) == 4


def test_world_graph_blocks_humilau_without_badge7():
    # 6 badges (mask 0x3F): Opelucid is open, but Humilau is blocked
    res = world_graph_service.find_route(448, 472, badge_mask=0x3F, badge_count=6)
    assert res["status"] == "blocked_by_story_gate"
    assert res["traversable"] is False
    assert res["blocked_by_gate"] is not None
    assert 7 in res["blocked_by_gate"]["missing_badges"]

    # 7 badges (mask 0x7F): Humilau is unlocked!
    res_unlocked = world_graph_service.find_route(448, 472, badge_mask=0x7F, badge_count=7)
    assert res_unlocked["status"] == "traversable"
    assert res_unlocked["traversable"] is True
    assert res_unlocked["blocked_by_gate"] is None


def test_global_route_api_endpoint(api: TestClient):
    res = api.get("/api/v1/navigation/global/route?from_zone=448&to_zone=120")
    assert res.status_code == 200
    data = res.json()
    assert data["format"] == "black2-world-route/v1"
    assert data["start_zone"] == 448
    assert data["goal_zone"] == 120
    assert len(data["zone_path"]) > 1
