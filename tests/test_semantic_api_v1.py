import asyncio

from backend.black2.api import semantic_routes
from backend.black2.world.runtime_player_state import player_runtime_service


class DummyReader:
    pass


class FakeHub:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def snapshot(self):
        return self._snapshot


class FakeSchematic:
    async def current(self, reader, include_raw=False):
        return {
            "status": "aligned",
            "map_header_id": 12,
            "matrix": {"id": 3, "width": 2, "height": 2, "cells": []},
            "runtime_actors": [{"kind": "player", "x": 2, "z": 3}],
            "events": {
                "warps": [{"tile_x": 5, "tile_y": 6, "target_map_header_id": 13, "kind": "door"}],
                "npcs": [], "furniture": [], "triggers": [],
            },
            "collision": {"status": "unverified", "known_walkable_tiles": []},
            "semantic_policy": {"raw_permissions": "raw bytes"},
            "ai_text": "BLACK2_MAP_SCHEMATIC/v1\n",
        }

    async def tile(self, reader, x, y):
        return {
            "status": "aligned",
            "model_id": 7,
            "permission_planes": {"P00": 1},
            "global": {"x": x, "y": y},
        }


def _setup(snapshot=None, latest=None, schematic=None):
    snapshot = snapshot or {
        "transport": {"frame": 100},
        "runtime": {"status": "ready"},
        "semantic": {"context": {"screen_type": "OVERWORLD", "can_move_player": True}},
        "profile": {"party_count": 1},
    }
    latest = latest or {
        "status": "resolved", "zone_id": 10,
        "position": {"grid": {"x": 2, "y": 0, "z": 3}, "world": {"x": 40, "y": 0, "z": 56}},
        "orientation": {"facing": "N"},
        "environment": {"tile_under": 4},
    }
    old_reader, old_hub = semantic_routes._reader, semantic_routes._hub
    semantic_routes.configure_semantic_routes(DummyReader(), FakeHub(snapshot))
    old_schematic = semantic_routes._schematic
    old_player = player_runtime_service.latest
    semantic_routes._schematic = schematic or FakeSchematic()
    player_runtime_service.latest = latest
    return old_schematic, old_player, old_reader, old_hub


def _restore(old_schematic, old_player, old_reader, old_hub):
    semantic_routes._schematic = old_schematic
    player_runtime_service.latest = old_player
    semantic_routes._reader, semantic_routes._hub = old_reader, old_hub


def test_capabilities_publish_ai_coordinate_contract_and_endpoints():
    old_schematic, old_player, old_reader, old_hub = _setup()
    try:
        body = asyncio.run(semantic_routes.game_capabilities())
        assert body["format"] == "black2-game-capabilities/v1"
        assert body["coordinate_contract"]["space"] == "gen5-field-grid-v1"
        assert body["coordinate_contract"]["world_units_per_tile"] == 16
        assert body["read"]["tile"].startswith("/api/v1/ai/map/tile")
        assert "evidence_policy" in body
    finally:
        _restore(old_schematic, old_player, old_reader, old_hub)


def test_ai_map_preserves_warp_destination_and_collision_evidence():
    old_schematic, old_player, old_reader, old_hub = _setup()
    try:
        body = asyncio.run(semantic_routes.ai_map(reader=DummyReader()))
        assert body["format"] == "black2-ai-map/v1"
        warp = body["events"]["warps"][0]
        assert warp["target_map_header_id"] == 13
        assert body["collision"]["status"] in {"unverified", "candidate"}
        assert body["coordinate_space"] == "gen5-field-grid-v1"
    finally:
        _restore(old_schematic, old_player, old_reader, old_hub)


def test_tile_cross_zone_is_explicitly_unknown_and_never_claims_walkability():
    old_schematic, old_player, old_reader, old_hub = _setup()
    try:
        body = asyncio.run(semantic_routes.ai_map_tile(zone_id=99, x=1, y=0, z=2, reader=DummyReader()))
        assert body["status"] == "outside_loaded_zone"
        assert body["terrain"] == {"kind": "unknown", "label": None}
        assert body["collision"]["can_walk"] is None
        assert body["evidence"]["verified"] is False
    finally:
        _restore(old_schematic, old_player, old_reader, old_hub)


def test_tile_same_zone_exposes_raw_model_and_unverified_semantics():
    old_schematic, old_player, old_reader, old_hub = _setup()
    try:
        body = asyncio.run(semantic_routes.ai_map_tile(zone_id=10, x=1, y=0, z=2, reader=DummyReader()))
        assert body["rom"]["model_id"] == 7
        assert body["terrain"]["kind"] == "unknown"
        assert body["collision"]["can_walk"] is None
        assert body["collision"]["status"] in {"unverified", "candidate"}
    finally:
        _restore(old_schematic, old_player, old_reader, old_hub)


def test_context_aggregates_state_party_inventory_and_map_for_external_ai():
    old_schematic, old_player, old_reader, old_hub = _setup()
    try:
        body = asyncio.run(semantic_routes.ai_context(reader=DummyReader()))
        assert body["format"] == "black2-ai-context/v1"
        assert body["state"]["map"]["zone_id"] == 10
        assert body["party"]["decode_status"] == "unverified"
        assert body["inventory"]["decode_status"] == "unverified"
        assert body["map"]["events"]["warps"][0]["target_map_header_id"] == 13
        assert body["navigation"]["plan"].endswith("/plans")
        assert body["limitations"]
    finally:
        _restore(old_schematic, old_player, old_reader, old_hub)

