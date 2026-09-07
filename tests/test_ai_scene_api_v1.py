"""Normalized AI scene envelope contract."""

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
            "transport": {"bridge_connected": True, "session_id": "scene-test"},
            "runtime": {"status": "ready", "semantic_status": "ready"},
            "semantic": {"context": {"screen_type": "OVERWORLD", "can_move_player": True}},
            "profile": {},
            "player": {
                "status": "resolved",
                "confidence": "candidate",
                "zone_id": 10,
                "frame": 90,
                "position": {"grid": {"x": 0, "y": 0, "z": 0}},
            },
        }

    def snapshot(self):
        return deepcopy(self.value)


class World:
    def zone(self, zone_id):
        assert zone_id == 10
        return {
            "matrix": {
                "id": 4,
                "width": 1,
                "height": 1,
                "cells": [{"x": 0, "z": 0, "chunk_id": 77}],
            },
            "rules": {"running": True},
            "events": {
                "warps": [],
                "npcs": [{
                    "record_index": 3,
                    "id": 3,
                    "sprite_id": 12,
                    "movement_id": 0,
                    "facing_id": 1,
                    "script_id": 8,
                    "flag_id": 0,
                    "x": 1,
                    "y": 2,
                    "z": 0,
                }],
                "furniture": [],
                "triggers": [],
            },
        }

    def tile(self, zone_id, x, y, z, include_raw=False):
        return {
            "coordinate": {"zone_id": zone_id, "x": x, "y": y, "z": z},
            "status": "decoded",
            "terrain": {"kind": "ground"},
            "collision": {"can_walk": True, "static_blocked": False},
            "surfaces": [],
        }

    def window(self, zone_id, x, y, z, radius, include_raw=False):
        return {
            "tiles": [self.tile(zone_id, x, y, z, include_raw=include_raw)],
            "width": 1,
            "height": 1,
            "bounds": {"min_x": x, "max_x": x, "min_z": z, "max_z": z},
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
                    "world": {"x": 16.0, "y": 0.0, "z": 16.0},
                    "grid_candidate": {"space": "gen5-field-grid-v1", "zone_id": 10, "x": 1, "y": None, "z": 1},
                },
                "destination": {"zone_id": 11, "warp_id": 0, "landing_status": "not_observed"},
                "role": {"kind": "entrance", "status": "candidate"},
                "traversal": {"can_traverse": None, "status": "unverified"},
            }],
            "count": 1,
        }


@pytest.fixture
def api(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(routes, "_hub", hub)
    monkeypatch.setattr(routes, "_world_service", World())
    monkeypatch.setattr(routes, "_connectors", lambda: Connectors())
    monkeypatch.setattr(routes, "_door_catalog", lambda zone_id, include_raw=False: [{
        "id": "door-1",
        "zone_id": zone_id,
        "door_uid": 9,
        "building": {"instance_id": "building-1", "model_uid": 2, "chunk_id": 77, "placement_index": 0,
                      "rotation_degrees": 0},
        "coordinate": {"space": "gen5-field-world-v1", "world": {"x": 16.0, "y": 0.0, "z": 16.0}},
        "resource": {"asset_url": "/api/v1/map/v5/building/10/2/model.glb"},
    }])
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_scene_composes_normalized_portals_doors_actors_geometry_collision(api):
    response = api.get("/api/v1/ai/map/scene?zone_id=10&radius=0")
    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "black2-ai-scene/v1"
    assert body["status"] == "decoded"
    assert set(body["normalized"]) == {"portals", "doors", "actors", "geometry", "collision"}
    assert body["normalized"]["portals"][0]["destination"]["landing_status"] == "not_observed"
    assert body["normalized"]["doors"][0]["door_uid"] == 9
    assert body["normalized"]["actors"]["rom_static_npcs"][0]["coordinate"]["z"] == 2
    assert body["normalized"]["geometry"]["terrain_cells"][0]["asset_url"].endswith("/10/0/0/model.glb")
    assert body["normalized"]["collision"]["tiles"][0]["collision"]["can_walk"] is True
    # Top-level aliases are intentionally kept for the workbench and older clients.
    assert body["portals"] == body["normalized"]["portals"]
    assert body["doors"] == body["normalized"]["doors"]
    assert body["actors"] == body["normalized"]["actors"]


def test_scene_documents_static_and_dynamic_evidence_boundaries(api):
    body = api.get("/api/v1/ai/map/scene?zone_id=10&radius=0").json()
    assert body["collision"]["executable"] is False
    assert body["actors"]["runtime_positions_status"] == "not_sampled"
    assert body["evidence"]["verified"] is False
    assert body["doors_status"]["status"] == "decoded"
