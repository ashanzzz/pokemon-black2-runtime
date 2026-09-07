"""Semantic HTTP contracts without an emulator or proprietary ROM."""
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.black2.api import semantic_routes as routes


class Hub:
    def __init__(self):
        self.calls = 0
        self.value = {
            "age_seconds": 0.2, "sampled_at": 123,
            "transport": {"bridge_connected": True, "frame": 105, "session_id": "test"},
            "runtime": {"status": "ready", "semantic_status": "ready"},
            "semantic": {"context": {"screen_type": "OVERWORLD", "can_move_player": True}},
            "profile": {"party_count": 1},
            "player": {"status": "resolved", "confidence": "candidate", "zone_id": 10, "frame": 100,
                       "position": {"grid": {"x": 2, "y": 0, "z": 3}},
                       "environment": {"tile_under": {"class": 0, "flags": 128}}},
        }

    def snapshot(self):
        self.calls += 1
        return deepcopy(self.value)


class World:
    def zone(self, zone):
        if zone >= 20:
            raise IndexError("Zone outside ROM")
        return {"matrix": {"id": 1}, "rules": {"cycling": True}, "events": {"npcs": [], "warps": []}}

    def tile(self, zone, x, y, z, include_raw=False):
        self.zone(zone)
        return {"coordinate": {"zone_id": zone, "x": x, "y": y, "z": z}, "status": "decoded",
                "terrain": {"kind": "unknown"}, "collision": {"can_walk": None}, "surfaces": []}

    def window(self, zone, x, y, z, radius, include_raw=False):
        return {"tiles": [self.tile(zone, tx, y, tz) for tz in range(z-radius, z+radius+1) for tx in range(x-radius, x+radius+1)],
                "width": 2*radius+1, "height": 2*radius+1, "bounds": {"min_x": x-radius, "min_z": z-radius}}


class Connectors:
    def query(self, zone_id=None, **kwargs):
        return {"format": "black2-ai-warps/v1", "warps": [], "count": 0}


@pytest.fixture
def api(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(routes, "_hub", hub)
    monkeypatch.setattr(routes, "_world_service", World())
    monkeypatch.setattr(routes, "_connectors", lambda: Connectors())
    monkeypatch.setattr(routes.observed_navigation_graph, "tile_evidence", lambda node: {"occupied_observations": 0})
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), hub


def test_state_preserves_candidate_and_source_frame(api):
    client, hub = api
    body = client.get("/api/v1/game/state").json()
    assert body["frame"] == 100
    assert body["can_act"] is True
    assert body["evidence"]["verified"] is False
    hub.value["age_seconds"] = 30
    stale = client.get("/api/v1/game/state").json()
    assert stale["can_act"] is None
    assert stale["execution_available"] is False
    assert stale["freshness"]["fresh"] is False


def test_static_other_zone_query_works_without_live_ram(api):
    client, hub = api
    hub.value["transport"]["bridge_connected"] = False
    response = client.get("/api/v1/ai/map/tile?zone_id=11&x=2&y=0&z=3")
    assert response.status_code == 200
    assert response.json()["coordinate"]["zone_id"] == 11
    assert response.json()["runtime_tile_type"] is None
    assert response.json()["collision"]["can_walk"] is None


def test_runtime_tile_only_attached_to_exact_fresh_layer(api):
    client, hub = api
    url = "/api/v1/ai/map/tile?zone_id=10&x=2&z=3&y="
    assert client.get(url + "0").json()["runtime_tile_type"] == {"class": 0, "flags": 128}
    assert client.get(url + "1").json()["runtime_tile_type"] is None
    hub.value["age_seconds"] = 5
    assert client.get(url + "0").json()["runtime_tile_type"] is None


@pytest.mark.parametrize("query", ["radius=17", "radius=-1", "x=1.4", "y=32768", "zone_id=-1"])
def test_window_rejects_unbounded_and_invalid_coordinates(api, query):
    client, _ = api
    params = {"zone_id": 10, "x": 2, "y": 0, "z": 3}
    key, value = query.split("=")
    params[key] = value
    assert client.get("/api/v1/ai/map/window", params=params).status_code == 422


def test_window_orientation_and_player_marker(api):
    client, _ = api
    body = client.get("/api/v1/ai/map/window?zone_id=10&x=2&y=0&z=3&radius=1").json()
    assert body["rows"] == ["???", "?@?", "???"]
    assert body["origin"] == {"zone_id": 10, "x": 1, "y": 0, "z": 2}
    assert body["legend"]["?"]


def test_context_reads_runtime_once_and_bag_unknown(api):
    client, hub = api
    body = client.get("/api/v1/ai/context?radius=0").json()
    assert hub.calls == 1
    assert body["map"]["map_header_id"] == body["state"]["map"]["zone_id"] == 10
    assert body["party"]["count"] == 1
    assert body["party"]["contents_known"] is False
    assert body["inventory"]["contents_known"] is False
    assert body["inventory"]["evidence"]["confidence"] == "unverified"
    assert body["inventory"]["evidence"]["runtime_observation"]["current"] is True
    assert "doors" in body
    assert body["scene_url"] == "/api/v1/ai/map/scene"


def test_scene_is_one_normalized_document_with_geometry_collision_and_interactions(api, monkeypatch):
    client, _ = api

    class DoorWorld:
        def zone(self, zone_id):
            return {
                "area": {"is_exterior": True},
                "render_coordinate_system": {"chunk_span_world": 512},
                "buildings": [{
                    "instance_id": "z10-b1", "belongs_to_zone": True, "model_uid": 1,
                    "chunk_id": 4, "placement_index": 0,
                    "world_position_candidate": {"x": 16, "y": 0, "z": 16},
                    "rotation_degrees": 0,
                    "resource": {"door_uid": 11, "door_offset": {"x": 0, "y": 0, "z": 15}},
                }],
                "permission_models": [{"chunk_id": 4, "semantic_status": "raw"}],
            }

    class SceneWorld(World):
        def zone(self, zone):
            value = super().zone(zone)
            value["matrix"] = {"id": 1, "cells": [{"x": 0, "z": 0, "chunk_id": 4}]}
            return value

    monkeypatch.setattr(routes, "_world_service", SceneWorld())
    monkeypatch.setattr(routes, "_door_world_service", DoorWorld())
    body = client.get("/api/v1/ai/map/scene?zone_id=10&radius=0").json()
    assert body["format"] == "black2-ai-scene/v1"
    assert body["status"] == "decoded"
    for key in ("normalized", "portals", "doors", "actors", "geometry", "collision"):
        assert key in body
    assert body["normalized"]["portals"] == body["portals"]
    assert body["normalized"]["doors"] == body["doors"]
    assert body["doors"][0]["door_uid"] == 11
    assert body["geometry"]["terrain_cells"][0]["chunk_id"] == 4
    assert body["collision"]["executable"] is False
    assert body["evidence"]["verified"] is False


def test_rom_failure_keeps_state_available(api, monkeypatch):
    client, _ = api
    def unavailable():
        raise FileNotFoundError("test ROM absent")
    monkeypatch.setattr(routes, "_world", unavailable)
    body = client.get("/api/v1/ai/context").json()
    assert body["map"]["status"] == "unavailable"
    assert body["state"]["map"]["zone_id"] == 10
    assert client.get("/api/v1/ai/map/tile?zone_id=10&x=0&y=0&z=0").status_code == 503


def test_bad_zone_is_404_and_schemas_are_real(api):
    client, _ = api
    assert client.get("/api/v1/ai/map/tile?zone_id=99&x=0&y=0&z=0").status_code == 404
    caps = client.get("/api/v1/game/capabilities").json()
    assert caps["support"]["cross_zone_execution"] is False
    actions = client.get("/api/v1/game/actions").json()
    assert "schemas" in actions


def test_doors_are_stable_paginated_candidates_with_rotated_coordinates(api, monkeypatch):
    client, _ = api

    class DoorWorld:
        def zone(self, zone_id):
            assert zone_id == 10
            return {
                "area": {"is_exterior": True},
                "render_coordinate_system": {"chunk_span_world": 512},
                "buildings": [
                    {"instance_id": "z10-b2", "belongs_to_zone": True, "model_uid": 2,
                     "chunk_id": 4, "placement_index": 1,
                     "world_position_candidate": {"x": 100, "y": 5, "z": 200}, "rotation_degrees": 90,
                     "resource": {"door_uid": 22, "door_offset": {"x": 16, "y": 1, "z": 0},
                                   "model_source": "rom:/building/2"}},
                    {"instance_id": "z10-b1", "belongs_to_zone": True, "model_uid": 1,
                     "world_position_candidate": {"x": 16, "y": 0, "z": 16}, "rotation_degrees": 0,
                     "resource": {"door_uid": 11, "door_offset": {"x": 0, "y": 0, "z": 15}}},
                    {"instance_id": "z10-other", "belongs_to_zone": False, "model_uid": 3,
                     "world_position_candidate": {"x": 0, "y": 0, "z": 0}, "resource": {"door_uid": 33}},
                    {"instance_id": "z10-no-door", "belongs_to_zone": True, "model_uid": 4,
                     "world_position_candidate": {"x": 0, "y": 0, "z": 0}, "resource": {"door_uid": None}},
                ],
            }

    monkeypatch.setattr(routes, "_door_world_service", DoorWorld())
    body = client.get("/api/v1/ai/map/doors?zone_id=10&offset=0&limit=1").json()
    assert body["format"] == "black2-ai-doors/v1"
    assert body["count"] == 1 and body["total_count"] == 2
    assert body["next_offset"] == 1
    assert body["doors"][0]["id"] == "z10-b1"
    assert body["doors"][0]["coordinate"]["world"] == {"x": 16.0, "y": 0.0, "z": 31.0}
    assert body["doors"][0]["resource"]["independent_asset"]["available"] is False

    second = client.get("/api/v1/ai/map/doors?zone_id=10&offset=1&limit=1&include_raw=true").json()
    assert second["doors"][0]["id"] == "z10-b2"
    assert second["doors"][0]["coordinate"]["world"]["z"] == 216.0
    assert second["doors"][0]["raw"]["resource"]["door_uid"] == 22
