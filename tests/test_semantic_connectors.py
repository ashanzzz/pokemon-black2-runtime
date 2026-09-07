from __future__ import annotations

import struct

import pytest

from backend.black2.world.gen5_rom_map import AreaHeader, MapMatrix, ZoneHeader, decode_entities
from backend.black2.world.map_graph import RomMapGraphService
from backend.black2.world.semantic_connectors import SemanticConnectorService


def _warp(target_zone: int, target_warp: int, *, x_world: int = 8, z_world: int = 8, height: int = 0) -> bytes:
    raw = bytearray(20)
    struct.pack_into("<HHH", raw, 0, target_zone, target_warp, 770)
    struct.pack_into("<h", raw, 8, x_world)
    struct.pack_into("<hHHh", raw, 12, z_world, 1, 1, height)
    return bytes(raw)


def _entities(entity_id: int, records: list[bytes]) -> dict:
    header = struct.pack("<IBBBB", 8 + 20 * len(records), 0, 0, len(records), 0)
    return decode_entities(header + b"".join(records), entity_id)


class FixtureRom:
    zone_count = 4

    def __init__(self):
        self.zone_headers = {}
        for zone_id in range(self.zone_count):
            raw = bytearray(0x30)
            raw[0] = 0 if zone_id in (0, 3) else 0x10
            struct.pack_into("<HH", raw, 2, zone_id, 0 if zone_id in (0, 3) else zone_id)
            struct.pack_into("<H", raw, 0x14, 900 + zone_id)
            struct.pack_into("<H", raw, 0x16, 200 + zone_id)
            self.zone_headers[zone_id] = ZoneHeader.parse(bytes(raw), zone_id)
        self.events = {
            200: _entities(200, [
                _warp(1, 0),
                _warp(1, 1),
                _warp(0xFFFF, 0xFFFF),
                _warp(9, 0),
                _warp(1, 99),
                _warp(0, 5),
            ]),
            201: _entities(201, [_warp(0, 0, x_world=104, z_world=312), _warp(2, 0), _warp(0, 1)]),
            202: _entities(202, [_warp(1, 1)]),
            203: _entities(203, []),
        }

    def zone(self, zone_id):
        if zone_id not in self.zone_headers:
            raise IndexError(f"Zone {zone_id} outside fixture")
        return self.zone_headers[zone_id]

    def area(self, area_id):
        return AreaHeader.parse(struct.pack("<HHBBBBBB", 0, 0, 0, 0, int(area_id in (0, 3)), 0, 0, 0), area_id)

    def entities(self, entities_id):
        if entities_id not in self.events:
            raise IndexError(f"Entities {entities_id} missing")
        return self.events[entities_id]

    def matrix(self, matrix_id):
        if matrix_id == 0:
            return MapMatrix.parse(struct.pack("<IHH4I", 1, 2, 1, 100, 101, 0, 3), 0)
        return MapMatrix.parse(struct.pack("<IHHI", 0, 1, 1, 102), matrix_id)


def _service(rom: FixtureRom | None = None) -> SemanticConnectorService:
    return SemanticConnectorService(RomMapGraphService(rom or FixtureRom()))


def test_target_record_coordinates_and_reciprocal_pair_are_resolved():
    result = _service().query(0)
    warp = result["warps"][0]
    assert warp["source"]["entities_id"] == 200  # ZoneData +0x16, not encounterID at +0x14.
    assert warp["destination"]["resolution"] == "rom_target_warp_resolved"
    assert warp["destination"]["target_connector_id"] == "zone:1:warp:0"
    assert warp["destination"]["target_tile_candidate"] == {
        "space": "gen5-field-grid-v1", "zone_id": 1, "x": 6, "y": None, "z": 19,
    }
    assert warp["destination"]["landing_tile"] is None
    assert warp["destination"]["landing_status"] == "not_observed"
    assert warp["reverse_edges"] == ["zone:1:warp:0"]
    assert warp["role"]["kind"] == "entrance"
    assert warp["traversal"]["can_traverse"] is None
    assert warp["return_path"]["can_return"] is None
    assert warp["verification"]["runtime_observed"] is False


def test_incoming_to_source_is_not_a_reverse_without_matching_destination_pair():
    warp = _service().query(0)["warps"][1]
    # Zone 1 Warp 2 points at Zone 0 Warp 1, but the forward endpoint is
    # Zone 1 Warp 1. Returning through Warp 2 is a different connector.
    assert warp["destination"]["target_connector_id"] == "zone:1:warp:1"
    assert warp["reverse_edges"] == []
    assert warp["return_path"]["status"] == "unverified"


def test_sentinel_invalid_zone_and_missing_target_warp_are_distinct():
    warps = _service().query(0)["warps"]
    assert warps[2]["destination"]["resolution"] == "dynamic_or_sentinel"
    assert warps[2]["destination"]["target_zone_or_map_raw"] == 65535
    assert warps[2]["destination"]["target_tile_candidate"] is None
    assert warps[3]["destination"]["resolution"] == "invalid_or_unresolved_zone"
    assert warps[3]["destination"]["target_zone_or_map_raw"] == 9
    assert warps[4]["destination"]["resolution"] == "missing_target_warp"
    assert warps[4]["destination"]["zone_id"] == 1
    assert warps[4]["destination"]["warp_id"] == 99
    assert warps[5]["role"]["kind"] == "same_zone_link"
    assert warps[5]["reverse_edges"] == []


def test_world_tile_floor_and_matrix_reference_do_not_add_live_chunk_origins():
    rom = FixtureRom()
    rom.events[200] = _entities(200, [_warp(1, 0, x_world=24, z_world=40, height=2)])
    source = _service(rom).query(0)["warps"][0]["source"]
    assert source["tile"]["x"] == 1.5  # Preserve graph wire format.
    assert source["grid_candidate"]["x"] == 1
    assert source["grid_candidate"]["z"] == 2
    assert source["grid_candidate"]["y"] is None
    assert source["coordinate_resolution"]["height"]["raw"] == 2
    assert source["coordinate_resolution"]["horizontal_reference"] == "matrix_absolute_candidate"
    assert source["coordinate_resolution"]["matrix_cell_candidate"]["zone_id"] == 0
    destination = _service().query(0)["warps"][0]["destination"]
    assert destination["target_coordinate_resolution"]["horizontal_reference"] == "standalone_matrix_local_candidate"


@pytest.mark.parametrize("x_world,reason", [(-8, "outside"), (536, "different Zone")])
def test_outside_or_other_zone_coordinates_are_not_aligned(x_world, reason):
    rom = FixtureRom()
    rom.events[200] = _entities(200, [_warp(1, 0, x_world=x_world)])
    coordinates = _service(rom).query(0)["warps"][0]["source"]["coordinate_resolution"]
    assert coordinates["status"] == "unresolved"
    assert coordinates["horizontal_reference"] == "unresolved"
    assert reason in coordinates["reason"]


def test_roles_and_catalog_coverage_include_warpless_zones():
    service = _service()
    assert service.query(1)["warps"][0]["role"]["kind"] == "exit"
    assert service.query(1)["warps"][1]["role"]["kind"] == "interior_link"
    assert service.query(3)["count"] == 0
    assert service.coverage() == service.query()["coverage"]
    coverage = service.coverage()
    assert coverage["status"] == "complete_rom_catalog"
    assert coverage["zone_count_expected"] == coverage["zone_count_decoded"] == 4
    assert coverage["entities_zone_count_decoded"] == 4
    assert coverage["warp_count_expected"] == coverage["warp_count_decoded"] == 10
    assert coverage["runtime_verified_count"] == 0


def test_partial_rom_failures_are_visible_in_coverage():
    rom = FixtureRom()
    del rom.events[203]
    coverage = _service(rom).coverage()
    assert coverage["status"] == "partial_or_unverified"
    assert coverage["entities_zone_count_decoded"] == 3
    assert any(error["zone_id"] == 3 and error["stage"] == "zone_entities" for error in coverage["decode_errors"])


def test_pagination_raw_opt_in_and_cache_defensive_copy():
    service = _service()
    first = service.query(0, limit=2)
    assert first["count"] == 2
    assert first["total_count"] == 6
    assert first["next_offset"] == 2
    assert "raw" not in first["warps"][0]
    first["warps"][0]["destination"]["zone_id"] = 99
    assert service.query(0)["warps"][0]["destination"]["zone_id"] == 1
    second = service.query(0, offset=4, limit=2, include_raw=True)
    assert second["next_offset"] is None
    assert second["warps"][0]["raw"]["id"] == 4
    assert service.query(offset=999)["count"] == 0


@pytest.mark.parametrize("kwargs", [{"offset": -1}, {"offset": True}, {"limit": 0}, {"limit": 2049}, {"limit": 1.5}])
def test_invalid_pagination_is_rejected(kwargs):
    with pytest.raises(ValueError):
        _service().query(**kwargs)


@pytest.mark.parametrize("zone_id", [-1, 4, True, 1.5])
def test_invalid_zone_is_rejected(zone_id):
    with pytest.raises(IndexError):
        _service().query(zone_id)
