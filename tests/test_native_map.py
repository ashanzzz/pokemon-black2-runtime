"""Unit tests for ROM-native map helpers."""

import asyncio
import struct
import unittest
from types import SimpleNamespace

from backend.black2.world.native_map import (
    NativeMapService,
    _candidate_gallery,
    _live_state_ranges,
    _match_headers,
    _matrix_tables,
    _select_texture_for_model,
    read_live_map_state,
)
from backend.black2.world.map_knowledge import (
    MapKnowledgeService,
    _decode_events,
    _decode_matrix,
    _visual_definition_ids,
    format_current_text,
)
from backend.black2.world.map_schematic import (
    MapSchematicService,
    build_machine_map,
    format_schematic_text,
)


class FakeMemoryReader:
    async def read_batch_ranges(self, _ranges):
        position = bytearray(0x20)
        struct.pack_into("<H", position, 0x0E, 17)
        struct.pack_into("<H", position, 0x12, 2)
        struct.pack_into("<H", position, 0x16, 29)
        return {
            "map_0": {"bytes": [0x61, 0x01]},
            "map_1": {"bytes": [0x61, 0x01]},
            "map_2": {"bytes": [0x61, 0x01]},
            "map_3": {"bytes": [0x00, 0x00]},
            "position_0": {"bytes": list(position)},
            "position_1": {"bytes": list(position)},
            "position_2": {"bytes": list(position)},
        }


class FlakyMemoryReader(FakeMemoryReader):
    def __init__(self) -> None:
        self.read_count = 0

    async def read_batch_ranges(self, ranges):
        self.read_count += 1
        if self.read_count == 1:
            return {}
        return await super().read_batch_ranges(ranges)


class TestNativeMap(unittest.TestCase):
    def test_live_ranges_exclude_rejected_pseudo_player(self):
        range_ids = {str(item["id"]) for item in _live_state_ranges()}
        self.assertEqual(range_ids, {"map_0", "map_1", "map_2", "map_3"})

    def test_live_state_does_not_publish_legacy_coordinate_mirrors(self):
        live = asyncio.run(read_live_map_state(FakeMemoryReader()))
        self.assertEqual(live.map_id, 0x0161)
        self.assertEqual((live.x, live.y, live.elevation), (None, None, None))
        self.assertFalse(live.verified)
        self.assertEqual(live.facing, "Unresolved")
        self.assertEqual(live.movement_state, "Unresolved")

    def test_live_state_retries_a_transient_empty_batch(self):
        reader = FlakyMemoryReader()
        live = asyncio.run(read_live_map_state(reader))
        self.assertEqual(reader.read_count, 2)
        self.assertEqual((live.x, live.y, live.elevation), (None, None, None))
        self.assertFalse(live.verified)

    def test_header_matching_uses_prefix_and_kind(self):
        prefix = bytes(range(16))
        matches = [
            {"kind": "BMD0", "offset": 12, "prefix_hex": prefix.hex()},
            {"kind": "BTX0", "offset": 24, "prefix_hex": prefix.hex()},
        ]
        self.assertEqual(_match_headers(matches, "BMD0", {prefix: 7}), {7: [12]})

    def test_matrix_tables_decode_models_and_definitions(self):
        raw = b"MAP0" + struct.pack("<HH", 2, 1)
        raw += struct.pack("<2I", 5, 6)
        raw += struct.pack("<2I", 9, 9)
        self.assertEqual(_matrix_tables(raw), (2, 1, (5, 6), (9, 9)))

    def test_texture_selection_prefers_model_material_match(self):
        bmd = b"BMD0\0m_h02_00_00\0m_h02_01\0m_h02_01_pl\0"
        payload = struct.pack("<4I", 0, 16, 16 + len(bmd), 16 + len(bmd)) + bmd
        textures = (b"BTX0 unrelated", b"BTX0 m_h02_01 m_h02_01_pl")
        self.assertEqual(_select_texture_for_model(0, (payload,), textures, 0), (1, 2, 2))

    def test_candidate_gallery_deduplicates_equivalent_model_previews(self):
        candidates = [
            (1, 4, 1, 1, (7,), None),
            (1, 5, 1, 1, (7,), None),
        ]
        gallery = _candidate_gallery(candidates, {7: [123]}, {9: [456]})
        self.assertEqual(len(gallery["active_cells"]), 1)
        self.assertEqual(gallery["active_cells"][0]["candidate_matrix_ids"], [4, 5])

    def test_map_knowledge_preserves_matrix_and_event_coordinates(self):
        matrix = b"MAP0" + struct.pack("<HH", 2, 1) + struct.pack("<2I", 7, 8)
        matrix += struct.pack("<2I", 426, 426)
        decoded_matrix = _decode_matrix(matrix, 45)
        self.assertEqual(decoded_matrix["model_ids"], [7, 8])
        self.assertEqual(decoded_matrix["definition_counts"]["426"], 2)

        warp = bytearray(0x14)
        struct.pack_into("<HHH", warp, 0, 13, 2, 1)
        struct.pack_into("<hhHHh", warp, 8, 32, 48, 1, 1, 4)
        event = struct.pack("<I4B", 0x40, 0, 0, 1, 0) + bytes(warp)
        decoded_event = _decode_events(event, 13)
        self.assertEqual(decoded_event["warps"][0]["target_map_id"], 13)
        self.assertEqual(decoded_event["warps"][0]["tile_x"], 2.0)

    def test_current_text_marks_unverified_semantics(self):
        text = format_current_text({
            "live_player": {"x": 1, "y": 2, "elevation": 3, "map_section_id": None, "verified": True},
            "candidate_matrix_ids": [45],
            "candidate_map_header_count": 0,
            "candidate_map_headers": [],
        })
        self.assertIn("碰撞: 输出 ROM 原始 permission byte 平面", text)
        self.assertIn("目标落点要以实际切图后的 Map Header 验证", text)

    def test_live_visual_only_uses_resident_definition_cells(self):
        matrix = b"MAP0" + struct.pack("<HH", 3, 1)
        matrix += struct.pack("<3I", 7, 8, 9)
        matrix += struct.pack("<3I", 100, 200, 300)
        engine = SimpleNamespace(matrix_narc=SimpleNamespace(files=(matrix,)))
        visual = {
            "candidate_scenes": [{
                "matrix_id": 0,
                "resident_cells": [{"x": 1, "y": 0, "model_id": 8}],
            }],
        }
        self.assertEqual(_visual_definition_ids(visual, engine), {200})

    def test_live_observation_records_only_verified_coordinate_change(self):
        service = MapKnowledgeService()
        first = {"live_player": {"x": 1, "y": 2, "elevation": 0, "verified": True}}
        self.assertEqual(service._record_live_observation(first)["kind"], "initial_sample")
        service._last_live = first
        unchanged = {"live_player": dict(first["live_player"])}
        self.assertIsNone(service._record_live_observation(unchanged))
        moved = {"live_player": {"x": 2, "y": 2, "elevation": 0, "verified": True}}
        observation = service._record_live_observation(moved)
        self.assertEqual(observation["kind"], "position_change")
        self.assertTrue(observation["verified"])

    def test_schematic_text_keeps_model_codes_and_semantic_boundaries(self):
        text = format_schematic_text({
            "status": "aligned",
            "message": "verified player anchor",
            "live_player": {"x": 64, "y": 96, "elevation": 12, "verified": True},
            "map_header_id": 426,
            "player_chunk": {"x": 2, "y": 3},
            "player_local": {"x": 0, "y": 0},
            "matrix": {
                "id": 45,
                "width": 3,
                "height": 4,
                "cells": [{
                    "x": 2, "y": 3, "code": "M847", "resident": True,
                    "tile_size": {"width": 32, "height": 32},
                }],
            },
            "events": {"counts": {"warps": 2, "npcs": 4, "furniture": 0, "triggers": 0}},
            "semantic_policy": {
                "model_codes": "M<number> is a ROM model identifier, not a terrain name",
                "raw_permissions": "Pxx values are raw bytes",
            },
        })
        self.assertIn("CELL x=2 y=3 code=M847", text)
        self.assertIn("EVENTS warps=2 npcs=4", text)
        self.assertIn("not a terrain name", text)

    def test_machine_map_flattens_raw_tiles_without_claiming_walkability(self):
        machine = build_machine_map({
            "status": "aligned",
            "message": "verified",
            "live_player": {"x": 0, "y": 0, "verified": True},
            "map_header_id": 7,
            "map_header_resolution": {"verified": True},
            "chunk_tile_size": {"width": 2, "height": 1},
            "map_definition_bounds": {"min_chunk_x": 1, "max_chunk_x": 1},
            "matrix": {
                "id": 3,
                "resident_cells": [{
                    "x": 1,
                    "y": 2,
                    "model_id": 9,
                    "tile_size": {"width": 2, "height": 1},
                    "raw_permission_planes": {"0": [[1, 2]], "1": [[3, 4]]},
                }],
            },
            "events": {},
            "semantic_policy": {"raw_permissions": "raw only"},
        })
        self.assertEqual(machine["tile_record_schema"], ["global_x", "global_y", "model_id", "P00", "P01"])
        self.assertEqual(machine["tile_records"], [[2, 2, 9, 1, 3], [3, 2, 9, 2, 4]])
        self.assertFalse(machine["navigation"]["route_planning_ready"])

    def test_standalone_interior_requires_unique_primary_matrix_header(self):
        zone_data = bytearray(0x60)
        struct.pack_into("<HH", zone_data, 0, 5, 0)
        engine = SimpleNamespace(zone_data=bytes(zone_data))

        class InteriorService(MapSchematicService):
            @property
            def engine(self):
                return engine

            def _events_for_header(self, map_header_id):
                return {
                    "event_archive_id": 11,
                    "furniture": [],
                    "npcs": [{"tile_x": 3, "tile_y": 4}],
                    "warps": [],
                    "triggers": [],
                    "counts": {"furniture": 0, "npcs": 1, "warps": 0, "triggers": 0},
                    "coordinate_space": "map_definition_local_map_plane",
                }

        header_id, _events, evidence = InteriorService()._standalone_interior_header(
            5,
            {
                "map_definition_bounds": {"width": 1, "height": 1},
                "chunk_tile_size": {"width": 32, "height": 32},
            },
        )
        self.assertEqual(header_id, 0)
        self.assertTrue(evidence["verified"])

    def test_visual_context_never_invents_a_header_without_a_live_player(self):
        context = MapSchematicService().visual_context({
            "player_alignment": {"verified": True},
            "live_player": {"verified": False},
        })
        self.assertIsNone(context["map_header_id"])
        self.assertFalse(context["map_header_resolution"]["verified"])

    def test_scene_descriptor_keeps_models_and_memory_offsets_as_evidence(self):
        visual = {
            "player_alignment": {"verified": True},
            "matrix_id": 45,
            "map_definition_id": None,
            "loaded_model_ids": [847],
            "active_cells": [{"x": 0, "y": 0, "model_id": 847}],
            "texture_id": 349,
            "verification": {"loaded_bmd0_offsets": {"847": [1234]}},
        }
        live = SimpleNamespace(x=6, y=15, elevation=11, verified=True)
        scene = NativeMapService._scene_descriptor(visual, live, "scene_test")
        self.assertEqual(scene["id"], "scene_test")
        self.assertEqual(scene["active_model_ids"], [847])
        self.assertEqual(scene["loaded_model_ids"], [847])
        self.assertEqual(scene["memory_offsets"], {"847": [1234]})

    def test_cache_identity_ignores_stale_non_active_models(self):
        service = NativeMapService.__new__(NativeMapService)
        base = {
            "display_mode": "aligned-map",
            "matrix_id": 45,
            "equivalent_matrix_ids": [45],
            "map_definition_id": None,
            "active_cells": [{"x": 0, "y": 0, "model_id": 847}],
            "texture_id": 349,
        }
        first = {**base, "loaded_model_ids": [847]}
        second = {**base, "loaded_model_ids": [847, 962, 1049]}
        self.assertEqual(service._cache_key(first, None), service._cache_key(second, None))
        next_interior = {
            **base,
            "matrix_id": 46,
            "active_cells": [{"x": 0, "y": 0, "model_id": 848}],
        }
        self.assertNotEqual(service._cache_key(first, None), service._cache_key(next_interior, None))


if __name__ == "__main__":
    unittest.main()
