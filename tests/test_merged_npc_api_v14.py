from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api import map_v5_routes


def test_merged_npc_endpoint_uses_explicit_scene_zone_ids_without_scene_rebuild():
    class Scene:
        async def merged_npcs(self, reader, *, zone_ids, max_zones=8):
            assert reader is sentinel_reader
            assert zone_ids == [439, 446]
            return {
                "format": "black2-scene-npc-merge/v1",
                "zone_ids": zone_ids,
                "actors": [],
                "npcs": [],
                "suppressed_static_entity_ids": [],
                "read_policy": "bounded",
            }

    sentinel_reader = object()
    original_reader = map_v5_routes._reader
    original_services = map_v5_routes._services
    map_v5_routes._reader = sentinel_reader
    map_v5_routes._services = lambda: (None, None, None, None, Scene())
    app = FastAPI()
    app.include_router(map_v5_routes.router)
    try:
        response = TestClient(app).get("/api/v1/map/v6/npcs/merged?zone_ids=439,446,439")
        assert response.status_code == 200, response.text
        assert response.json()["zone_ids"] == [439, 446]
    finally:
        map_v5_routes._reader = original_reader
        map_v5_routes._services = original_services


def test_merged_npc_endpoint_requires_at_least_one_zone_id():
    original_reader = map_v5_routes._reader
    map_v5_routes._reader = object()
    app = FastAPI()
    app.include_router(map_v5_routes.router)
    try:
        response = TestClient(app).get("/api/v1/map/v6/npcs/merged?zone_ids=not-a-zone")
        assert response.status_code == 422
    finally:
        map_v5_routes._reader = original_reader
