"""Unified read-only observation contract for the future Pokémon Agent."""
from __future__ import annotations

from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.black2.api import semantic_routes as routes


class Hub:
    def __init__(self) -> None:
        self.calls = 0
        self.value = {
            "age_seconds": 0.2,
            "sampled_at": 123,
            "transport": {
                "bridge_connected": True,
                "frame": 105,
                "session_id": "test",
            },
            "runtime": {"status": "ready", "semantic_status": "ready"},
            "semantic": {
                "frame": 100,
                "context": {
                    "screen_type": "OVERWORLD",
                    "can_move_player": True,
                    "is_dialogue_active": False,
                },
            },
            "profile": {"party_count": 1},
            "player": {
                "status": "resolved",
                "confidence": "candidate",
                "zone_id": 10,
                "frame": 100,
                "position": {"grid": {"x": 2, "y": 0, "z": 3}},
            },
            "battle": {"active": False, "active_status": "inactive"},
        }

    def snapshot(self) -> dict:
        self.calls += 1
        return deepcopy(self.value)


class World:
    def zone(self, zone_id: int) -> dict:
        return {
            "zone_id": zone_id,
            "header": {
                "area_id": 1,
                "matrix_id": 2,
                "entities_id": 3,
                "map_type": 0,
                "parent_zone_id": 0,
                "location_name_id": 0,
            },
            "matrix": {"id": 2, "width": 1, "height": 1, "cells": []},
            "coordinate_space": "gen5-field-grid-v1",
            "rules": {"cycling": True, "running": True},
            "events": {"npcs": [], "warps": [], "furniture": [], "triggers": []},
        }


class Connectors:
    def query(self, zone_id=None, **kwargs):
        return {"format": "black2-ai-warps/v1", "warps": [], "count": 0}


@pytest.fixture
def api(monkeypatch):
    hub = Hub()
    monkeypatch.setattr(routes, "_hub", hub)
    monkeypatch.setattr(routes, "_reader", None)
    monkeypatch.setattr(routes, "_world_service", World())
    monkeypatch.setattr(routes, "_connectors", lambda: Connectors())
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app), hub


def test_observe_is_one_snapshot_read_only_and_tri_state(api):
    client, hub = api
    response = client.get("/api/v1/agent/observe?radius=0")
    assert response.status_code == 200
    body = response.json()

    assert body["format"] == "black2-agent-observation/v1"
    assert body["status"] == "current"
    assert body["read_only"] is True
    assert body["writes_performed"] is False
    assert body["input_sent"] is False
    assert hub.calls == 1
    assert body["player"]["position"]["grid"] == {"x": 2, "y": 0, "z": 3}
    assert body["world"]["zone_id"] == 10
    assert body["world"]["interactions"]["count"] == 0
    assert body["party"]["contents_known"] is False
    assert body["inventory"]["contents_known"] is False
    assert body["state"]["primary_context"] == "exploration"
    assert body["actions"][0]["type"] == "navigate_to"
    assert body["actions"][0]["can_execute"] is True
    assert body["actions"][4]["type"] == "battle_select_move"
    assert body["actions"][4]["can_execute"] is False
    assert body["events"]["cursor"] >= 0
    assert any(item["id"] == "inventory_contents_decoder" for item in body["missing_api"])


def test_observe_keeps_runtime_unknown_when_stale(api):
    client, hub = api
    hub.value["age_seconds"] = 30
    body = client.get("/api/v1/agent/observe").json()

    assert body["status"] == "partial"
    assert body["observation"]["freshness"]["fresh"] is False
    assert body["state"]["screen_type"] == "RUNTIME_UNRESOLVED"
    assert body["player"]["evidence"]["verified"] is False
    assert body["inventory"]["contents_known"] is False

