"""Contracts for the read-only AI current-view foundation."""
from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.black2.api import current_view_routes as routes


class _Hub:
    def __init__(self) -> None:
        self.snapshot_calls = 0
        self.bridge_calls = 0
        self.value = {
            "sampled_at": 10.0,
            "age_seconds": 0.2,
            "runtime": {"status": "ready"},
            "player": {
                "status": "resolved", "frame": 77, "zone_id": 12,
                "position": {"grid": {"x": 40, "y": 2, "z": 50}},
            },
            "capture": {"reference": "/cached/frame.png", "frame": 77},
        }

    def snapshot(self) -> dict:
        self.snapshot_calls += 1
        return deepcopy(self.value)

    def bridge_command(self) -> None:
        self.bridge_calls += 1
        raise AssertionError("GET current view must not issue a bridge command")


class _Projection:
    def __init__(self) -> None:
        self.tile_calls: list[tuple[int, int, int, int]] = []
        self.window_calls = 0
        self.connector_calls = 0

    def window(self, *args, **kwargs):
        self.window_calls += 1
        raise AssertionError("current view must not reuse the map-window route")

    def connectors(self, *args, **kwargs):
        self.connector_calls += 1
        raise AssertionError("current view must not build a connector catalog")

    def tile(self, zone_id: int, x: int, y: int, z: int, *, include_raw: bool = False) -> dict:
        assert include_raw is False
        self.tile_calls.append((zone_id, x, y, z))
        return {
            "status": "decoded",
            "coordinate": {"zone_id": zone_id, "x": x, "y": y, "z": z},
            "rom": {"matrix_id": 8, "owner_zone_id": 12},
            "surfaces": [{
                "layer_index": 0,
                "material": {"kind": "ground"},
                "collision": {"static_blocked": False, "can_walk": None},
                "height": {"status": "unresolved"},
            }],
        }


@pytest.fixture
def api(monkeypatch):
    hub = _Hub()
    projection = _Projection()
    monkeypatch.setattr(routes, "_hub", hub)
    monkeypatch.setattr(routes, "_static_world", projection)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), hub, projection


def test_strict_is_cached_only_and_has_no_fabricated_neighbours(api):
    client, hub, projection = api
    body = client.get("/api/v1/ai/view/current?profile=strict").json()

    assert body["profile"] == "strict"
    assert body["mode"] == "strict_runtime_cache"
    assert body["tiles"] == []
    assert body["address"]["status"] == "unresolved"
    assert body["capture"]["alignment"] == "not_frame_verified"
    assert hub.snapshot_calls == 1
    assert hub.bridge_calls == 0
    assert projection.tile_calls == []
    assert projection.window_calls == projection.connector_calls == 0


def test_strict_uses_cached_matrix_in_its_spatial_key_without_rom(api):
    client, hub, projection = api
    hub.value["player"]["matrix_id"] = 8

    body = client.get("/api/v1/ai/view/current?profile=strict").json()

    assert body["address"]["spatial_key"] == "matrix:8:gpos:40:2:50"
    assert body["context"]["runtime_zone_id"] == 12
    assert body["context"]["rom_owner_zone_id"] is None
    assert projection.tile_calls == []


def test_strict_rejects_conflicting_cached_matrix_provenance(api):
    client, hub, projection = api
    hub.value["player"]["matrix_id"] = 8
    hub.value["player"]["global"] = {"matrix_id": 9}

    body = client.get("/api/v1/ai/view/current?profile=strict").json()

    assert body["address"]["status"] == "unresolved"
    assert body["runtime_player"]["matrix_provenance"] == {
        "status": "conflict",
        "selected_matrix_id": None,
        "sources": {
            "runtime_player.matrix_id": 8,
            "runtime_player.global.matrix_id": 9,
        },
        "reason": "cached runtime matrix identifiers disagree; no precedence is assumed",
    }
    assert projection.tile_calls == []


def test_strict_never_constructs_a_rom_reader(api, monkeypatch):
    client, _hub, _projection = api
    monkeypatch.setattr(routes, "_static_world", None)

    def fail_if_constructed():
        raise AssertionError("strict current-view must not construct Gen5RomMap")

    monkeypatch.setattr(routes, "Gen5RomMap", fail_if_constructed)
    body = client.get("/api/v1/ai/view/current?profile=strict").json()

    assert body["mode"] == "strict_runtime_cache"


def test_assisted_local_is_nine_by_seven_and_explicitly_static(api):
    client, _hub, projection = api
    body = client.get("/api/v1/ai/view/current?profile=assisted_local").json()

    assert body["address"]["spatial_key"] == "matrix:8:gpos:40:2:50"
    assert body["context"] == {
        "runtime_zone_id": 12,
        "rom_owner_zone_id": 12,
        "zone_policy": "runtime and ROM owner zones are context only; they are not spatial identity",
    }
    assert body["coverage"]["bounds"] == {"width": 9, "height": 7, "max_tiles": 63}
    assert len(body["tiles"]) == 63
    assert len(projection.tile_calls) == 63
    assert {(tile["knowledge_state"], tile["source_kind"], tile["screen_visibility"])
            for tile in body["tiles"]} == {("static_candidate", "rom_static", "not_applicable")}
    assert projection.window_calls == projection.connector_calls == 0


def test_assisted_local_never_flattens_matrix_identity(api):
    client, _hub, projection = api
    original_tile = projection.tile

    def mixed_matrix_tile(zone_id, x, y, z, *, include_raw=False):
        tile = original_tile(zone_id, x, y, z, include_raw=include_raw)
        if (x, z) == (36, 47):
            tile["rom"]["matrix_id"] = 9
            tile["rom"]["owner_zone_id"] = 99
        return tile

    projection.tile = mixed_matrix_tile
    body = client.get("/api/v1/ai/view/current?profile=assisted_local").json()
    exceptional = next(tile for tile in body["tiles"] if tile["context"]["rom_owner_zone_id"] == 99)

    assert exceptional["address"]["matrix_id"] == 9
    assert exceptional["address"]["spatial_key"] == "matrix:9:gpos:36:2:47"
    assert exceptional["context"]["runtime_zone_id"] == 12


def test_unknown_cached_location_stays_unresolved(api):
    client, hub, projection = api
    hub.value["player"]["position"]["grid"] = {"x": None, "y": None, "z": None}
    body = client.get("/api/v1/ai/view/current?profile=assisted_local").json()

    assert body["mode"] == "assisted_local_unresolved"
    assert body["address"]["status"] == "unresolved"
    assert body["tiles"] == []
    assert projection.tile_calls == []
