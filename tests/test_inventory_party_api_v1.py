"""Tests for Gen V player inventory and party runtime APIs."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api import semantic_routes as routes
from backend.black2.decoders.inventory_runtime import (
    decode_player_inventory_from_bytes,
    decode_player_inventory_from_ram,
    POCKET_DEFINITIONS,
)


def test_inventory_bytes_decoder_empty():
    empty_bytes = b"\x00" * 0x998
    result = decode_player_inventory_from_bytes(empty_bytes, frame=100)
    assert result["format"] == "black2-inventory/v1"
    assert result["status"] == "ready"
    assert result["decode_status"] == "verified"
    assert result["contents_known"] is True
    assert result["item_count"] == 0
    assert result["pocket_count"] == 5
    assert len(result["items"]) == 0
    assert len(result["pockets"]) == 5


def test_inventory_bytes_decoder_with_items():
    raw = bytearray(0x998)
    # Put 10 Poke Balls (id 4, qty 10) in pocket 0 (items at 0x000)
    raw[0:2] = (4).to_bytes(2, "little")
    raw[2:4] = (10).to_bytes(2, "little")

    # Put 2 Potions (id 17, qty 2) in pocket 3 (medicine at 0x7D8)
    med_off = 0x7D8
    raw[med_off:med_off + 2] = (17).to_bytes(2, "little")
    raw[med_off + 2:med_off + 4] = (2).to_bytes(2, "little")

    # Put 1 Key item (id 437, qty 1) in pocket 1 (key_items at 0x4D8)
    key_off = 0x4D8
    raw[key_off:key_off + 2] = (437).to_bytes(2, "little")
    raw[key_off + 2:key_off + 4] = (1).to_bytes(2, "little")

    result = decode_player_inventory_from_bytes(bytes(raw), frame=200)
    assert result["status"] == "ready"
    assert result["item_count"] == 3
    assert result["contents_known"] is True

    items_pocket = next(p for p in result["pockets"] if p["pocket_id"] == "items")
    assert items_pocket["count"] == 1
    assert items_pocket["items"][0]["item_id"] == 4
    assert items_pocket["items"][0]["quantity"] == 10
    assert "精灵球" in items_pocket["items"][0]["name"]

    med_pocket = next(p for p in result["pockets"] if p["pocket_id"] == "medicine")
    assert med_pocket["count"] == 1
    assert med_pocket["items"][0]["item_id"] == 17
    assert med_pocket["items"][0]["quantity"] == 2
    assert "伤药" in med_pocket["items"][0]["name"]

    key_pocket = next(p for p in result["pockets"] if p["pocket_id"] == "key_items")
    assert key_pocket["count"] == 1
    assert key_pocket["items"][0]["item_id"] == 437
    assert key_pocket["items"][0]["key_item"] is True


def test_inventory_ram_bounds_check():
    short_ram = b"\x00" * 1024
    result = decode_player_inventory_from_ram(short_ram)
    assert result["status"] == "unresolved"
    assert result["contents_known"] is False


def test_semantic_routes_party_and_inventory_fallback(monkeypatch):
    class MockHub:
        def snapshot(self):
            return {
                "age_seconds": 0.1,
                "transport": {"bridge_connected": True, "session_id": "test"},
                "runtime": {"status": "ready", "semantic_status": "ready"},
                "semantic": {
                    "frame": 50,
                    "context": {
                        "screen_type": "OVERWORLD",
                        "can_move_player": True,
                        "dialogue_text": "",
                        "loaded_dialogue_text": "测试对话文本",
                    },
                },
                "profile": {"party_count": 2, "badges": 1},
                "player": {"status": "resolved"},
            }

    monkeypatch.setattr(routes, "_hub", MockHub())
    monkeypatch.setattr(routes, "_reader", None)

    app = FastAPI()
    app.include_router(routes.router)
    client = TestClient(app)

    # Party fallback when no reader
    party_resp = client.get("/api/v1/game/party").json()
    assert party_resp["format"] == "black2-party/v1"
    assert party_resp["count"] == 2

    # Inventory fallback when no reader
    inv_resp = client.get("/api/v1/game/inventory").json()
    assert inv_resp["format"] == "black2-inventory/v1"
    assert inv_resp["contents_known"] is False

    # Cutscene fallback to loaded_dialogue_text
    cutscene_resp = client.get("/api/v1/game/cutscene").json()
    assert cutscene_resp["dialogue"]["text"] == "测试对话文本"
    assert cutscene_resp["dialogue"]["loaded_text"] == "测试对话文本"
