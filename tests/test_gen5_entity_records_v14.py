from __future__ import annotations

import struct

from backend.black2.world.gen5_rom_map import decode_entities
from backend.black2.world.map_graph import RomMapGraphService
from backend.black2.world.semantic_connectors import SemanticConnectorService


def _entities(furniture=b"", npc=b"", warp=b"", trigger=b""):
    header = struct.pack("<IBBBB", 8 + len(furniture) + len(npc) + len(warp) + len(trigger),
                         1 if furniture else 0, 1 if npc else 0, 1 if warp else 0, 1 if trigger else 0)
    return header + furniture + npc + warp + trigger


def test_all_entity_records_keep_raw_hex_and_original_widths():
    furniture = bytearray(0x14)
    struct.pack_into("<HHHHiii", furniture, 0, 1, 2, 3, 4, -5, 6, 7)
    npc = bytearray(0x24)
    struct.pack_into("<18H", npc, 0, *range(18))
    warp = bytearray(0x14)
    struct.pack_into("<HH", warp, 0, 41, 42)
    warp[4:6] = b"\x12\x34"
    trigger = bytearray(0x16)
    struct.pack_into("<11H", trigger, 0, *range(100, 111))

    decoded = decode_entities(_entities(furniture, npc, warp, trigger), 7)
    assert decoded["furniture"][0]["arg4_raw"] == 4
    assert decoded["furniture"][0]["raw_hex"] == bytes(furniture).hex()
    assert decoded["npcs"][0]["movement2_raw"] == 3
    assert decoded["npcs"][0]["sight_raw"] == 7
    assert decoded["npcs"][0]["leash_lr_raw"] == 10
    assert decoded["triggers"][0]["arg9_raw"] == 108
    assert decoded["triggers"][0]["arg11_raw"] == 110
    assert decoded["warps"][0]["arg3_raw"] == 0x12
    assert decoded["warps"][0]["arg4_raw"] == 0x34
    assert "kind" not in decoded["warps"][0]
    assert "target_warp_id" not in decoded["warps"][0]
    assert decoded["warps"][0]["arg11_raw"] == 0


def test_unverified_warp_arg2_does_not_create_connector_or_height():
    class Rom:
        zone_count = 1

        def zone(self, _zone):
            from backend.black2.world.gen5_rom_map import ZoneHeader
            raw = bytearray(0x30)
            struct.pack_into("<HH", raw, 2, 0, 0)
            struct.pack_into("<H", raw, 0x16, 1)
            return ZoneHeader.parse(bytes(raw), 0)

        def area(self, _area):
            from backend.black2.world.gen5_rom_map import AreaHeader
            return AreaHeader.parse(struct.pack("<HHBBBBBB", 0, 0, 0, 0, 1, 0, 0, 0), 0)

        def entities(self, _entity_id):
            warp = bytearray(0x14)
            struct.pack_into("<HH", warp, 0, 0, 0)
            struct.pack_into("<hhHHH", warp, 8, 16, 32, 1, 1, 0x7777)
            return decode_entities(_entities(warp=bytes(warp)), 1)

        def matrix(self, _matrix):
            from backend.black2.world.gen5_rom_map import MapMatrix
            return MapMatrix.parse(struct.pack("<IHHI", 0, 1, 1, 1), 0)

    edge = SemanticConnectorService(RomMapGraphService(Rom())).query(0)["warps"][0]
    assert edge["source"]["world"]["y"] is None
    assert edge["destination"]["resolution"] == "target_warp_index_unresolved"
    assert edge["destination"]["target_connector_id"] is None
    assert edge["return_path"]["can_return"] is None
