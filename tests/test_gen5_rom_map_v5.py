from __future__ import annotations

import struct
import unittest

from backend.black2.world.gen5_rom_map import (
    AreaBuildingBundle,
    AreaHeader,
    ChunkBuilding,
    GFContainer,
    MapMatrix,
    ZoneHeader,
)


def gf_container(magic: bytes, files: list[bytes]) -> bytes:
    count = len(files)
    header_size = 4 + (count + 1) * 4
    base = (header_size + 3) & ~3
    offsets = [base]
    cursor = base
    for payload in files:
        cursor += len(payload)
        offsets.append(cursor)
    out = bytearray(cursor)
    out[:2] = magic
    struct.pack_into("<H", out, 2, count)
    struct.pack_into(f"<{count + 1}I", out, 4, *offsets)
    for index, payload in enumerate(files):
        out[offsets[index]:offsets[index + 1]] = payload
    return bytes(out)


class TestGen5V5Formats(unittest.TestCase):
    def test_zone_header_offsets(self):
        raw = bytearray(0x30)
        raw[0] = 7
        raw[1] = 9
        struct.pack_into("<H", raw, 0x02, 0x1234)  # area
        struct.pack_into("<H", raw, 0x04, 0x2345)  # matrix
        struct.pack_into("<H", raw, 0x16, 0x3456)  # entities
        z = ZoneHeader.parse(bytes(raw), 88)
        self.assertEqual(z.zone_id, 88)
        self.assertEqual(z.area_id, 0x1234)
        self.assertEqual(z.matrix_id, 0x2345)
        self.assertEqual(z.entities_id, 0x3456)

    def test_area_header(self):
        raw = struct.pack("<HHBBBBBB", 21, 31, 2, 3, 1, 4, 5, 6)
        a = AreaHeader.parse(raw, 11)
        self.assertEqual(a.buildings_id, 21)
        self.assertEqual(a.textures_id, 31)
        self.assertTrue(a.is_exterior)

    def test_matrix_has_zone_table(self):
        chunks = (10, 11, 12, 13)
        zones = (40, 40, 41, 41)
        raw = struct.pack("<IHH4I4I", 1, 2, 2, *chunks, *zones)
        m = MapMatrix.parse(raw, 5)
        self.assertTrue(m.has_zones)
        self.assertEqual(m.chunk_ids, chunks)
        self.assertEqual(m.zone_ids, zones)
        self.assertEqual(m.cell(1, 1)["zone_id"], 41)

    def test_gf_container_and_chunk_buildings(self):
        b = struct.pack("<IiiiHH", 1, 4096, -8192, 12288, 0x4000, 77)
        raw = gf_container(b"WB", [b"BMD0fake", b"perm", b])
        c = GFContainer.parse(raw, expected_magic="WB")
        self.assertEqual(c.file_count, 3)
        placements = ChunkBuilding.parse_many(c.files[-1])
        self.assertEqual(len(placements), 1)
        self.assertEqual(placements[0].model_uid, 77)
        self.assertEqual(placements[0].local_x, 1.0)
        self.assertEqual(placements[0].local_y, -2.0)
        self.assertAlmostEqual(placements[0].rotation_degrees, 90.0)

    def test_area_building_bundle_halves(self):
        meta0 = struct.pack("<HHHhhhHHBBBB", 100, 2, 9, 1, 2, 3, 0, 0, 0, 0, 0, 0)
        meta1 = struct.pack("<HHHhhhHHBBBB", 101, 3, 0xFFFF, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        raw = gf_container(b"AB", [meta0, meta1, b"BMD0-model-100", b"BMD0-model-101"])
        bundle = AreaBuildingBundle.parse(raw, 4, True)
        self.assertEqual(len(bundle.resources), 2)
        self.assertEqual(bundle.by_uid(100).door_uid, 9)
        self.assertIsNone(bundle.by_uid(101).door_uid)
        self.assertEqual(bundle.by_uid(100).model, b"BMD0-model-100")


if __name__ == "__main__":
    unittest.main()
