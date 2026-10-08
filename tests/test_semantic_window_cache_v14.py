"""Offline regressions for bounded static map-window reads."""
from __future__ import annotations

from dataclasses import dataclass

from backend.black2.api import semantic_routes as routes
from backend.black2.world.semantic_world import SemanticWorldService, TERRAIN_GRID_CACHE_CAP
from backend.black2.world import tile_semantics


@dataclass(frozen=True)
class _Header:
    matrix_id: int
    entities_id: int


class _Matrix:
    width = 1
    height = 1

    def __init__(self, matrix_id: int) -> None:
        self.matrix_id = matrix_id

    def cell(self, x: int, z: int) -> dict[str, int]:
        assert (x, z) == (0, 0)
        return {"chunk_id": self.matrix_id, "zone_id": self.matrix_id}


class _Grid:
    width = 32
    height = 32

    def tile(self, x: int, z: int) -> dict:
        return {
            "x": x,
            "z": z,
            "layer_index": 0,
            "material": {"kind": "ground"},
            "collision": {"static_blocked": False, "can_walk": None},
            "height": {"chunk_relative_world_y": 0.0},
            "raw": {"flags": 0},
        }


class _Rom:
    def __init__(self) -> None:
        self.chunk_reads: list[int] = []

    def zone(self, zone_id: int) -> _Header:
        if not 0 <= zone_id < 40:
            raise IndexError(zone_id)
        return _Header(matrix_id=zone_id, entities_id=zone_id)

    def matrix(self, matrix_id: int) -> _Matrix:
        return _Matrix(matrix_id)

    def chunk(self, chunk_id: int) -> object:
        self.chunk_reads.append(chunk_id)
        return object()

    @staticmethod
    def cache_status() -> dict:
        return {"fixture": True}


class _LocalConnectors:
    def __init__(self) -> None:
        self.window_calls: list[int] = []
        self.global_calls = 0

    def window_warps(self, zone_id: int) -> list[dict]:
        self.window_calls.append(zone_id)
        return []

    def query(self, *args, **kwargs):
        self.global_calls += 1
        raise AssertionError("map-window rendering must not request the global connector catalog")


def test_many_zone_windows_have_a_bounded_terrain_lru_and_no_global_connector_load(monkeypatch):
    rom = _Rom()
    world = SemanticWorldService(rom)
    connectors = _LocalConnectors()
    monkeypatch.setattr(tile_semantics, "decode_chunk_terrain", lambda _chunk: (_Grid(),))
    monkeypatch.setattr(routes, "_world_service", world)
    monkeypatch.setattr(routes, "_connectors", lambda: connectors)

    for zone_id in range(40):
        data = routes._window({}, zone_id, 0, 0, 0, radius=0)
        assert data["view"]["kind"] == "static_rom_window"
        assert data["view"]["is_current_nds_view"] is False
        assert data["view"]["memory_backed"] is False

    # The workbench requests tile and window in parallel.  A single-tile
    # interaction lookup must retain the same local-only connector policy.
    routes._tile({}, 0, 0, 0, 0)

    status = world.cache_status()
    assert status["terrain_grids"]["entries"] == TERRAIN_GRID_CACHE_CAP
    assert status["terrain_grids"]["chunk_ids"] == list(range(9, 40)) + [0]
    assert rom.chunk_reads == list(range(40)) + [0]
    assert connectors.window_calls == list(range(40)) + [0]
    assert connectors.global_calls == 0
