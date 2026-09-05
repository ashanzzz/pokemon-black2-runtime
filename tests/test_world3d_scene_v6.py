import asyncio
import unittest
from backend.black2.world.runtime_player_state import player_runtime_service
from backend.black2.world.world3d_scene import World3DSceneService, canonical_player, scene_origin


def _sample(phase="Idle"):
    return {
        "status": "resolved",
        "confidence": "probable",
        "frame": 100,
        "zone_id": 427,
        "position": {
            "grid": {"x": 37, "y": 9, "z": 714},
            "world": {"x": 600.0, "y": 144.035, "z": 11432.0},
        },
        "orientation": {
            "face_dir_raw": 1,
            "facing": "South",
            "facing_zh": "下",
            "verified": True,
            "sources_agree": True,
        },
        "locomotion": {"phase": phase, "semantic_state": "Idle", "transport_mode": "OnFoot"},
        "mapper": {
            "player_chunk": {"index": 639, "x": 1, "y": 22},
            "chunk_tile_size": 32,
            "chunk_matches_gpos": True,
        },
    }


class TestWorld3DSceneV6(unittest.TestCase):
    def test_canonical_player_keeps_runtime_wpos(self):
        p = canonical_player(_sample())
        self.assertEqual(p["world"]["x"], 600.0)
        self.assertEqual(p["world"]["z"], 11432.0)
        self.assertIs(p["validation"]["grid_world_consistent"], True)
        self.assertEqual(p["chunk"]["x"], 1)
        self.assertEqual(p["chunk"]["z"], 22)

    def test_moving_player_does_not_snap_wpos_to_tile_center(self):
        s = _sample("Moving")
        s["position"]["world"]["x"] = 604.0
        p = canonical_player(s)
        self.assertEqual(p["world"]["x"], 604.0)
        self.assertIs(p["validation"]["grid_centre_check_applicable"], False)
        self.assertIsNone(p["validation"]["grid_world_consistent"])

    def test_scene_origin_is_display_only_live_player_anchor(self):
        p = canonical_player(_sample())
        o = scene_origin(p, {"matrix": {"width": 29, "height": 27}, "render_coordinate_system": {"chunk_span_world": 512}})
        self.assertEqual(o["x"], 600.0)
        self.assertEqual(o["z"], 11432.0)
        self.assertEqual(o["source"], "live_player_wpos")

    def test_unresolved_player_returns_without_ram_discovery(self):
        class ReaderThatMustNotBeCalled:
            async def read_bytes(self, *_args, **_kwargs):
                raise AssertionError("normal scene polling must not start RAM-wide discovery")

        original_latest = player_runtime_service.latest
        try:
            player_runtime_service.latest = {"status": "unresolved", "reason": "no cached player"}
            service = World3DSceneService(original=None, truth=None, exported=object())
            result = asyncio.run(service.current_scene(ReaderThatMustNotBeCalled()))
        finally:
            player_runtime_service.latest = original_latest

        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["reason"], "no runtime ZoneID is available")

    def test_loaded_visual_replaces_only_verified_matching_terrain_cells(self):
        service = World3DSceneService(original=None, truth=None, exported=object())
        static = {
            "zone_id": 427,
            "matrix": {"matrix_id": 0},
            "terrains": [
                {"cell": {"x": 1, "z": 23}, "asset_url": "/fallback.glb"},
                {"cell": {"x": 1, "z": 22}, "asset_url": "/other-fallback.glb"},
            ],
        }
        visual = {
            "verified": True,
            "map_definition_id": 427,
            "matrix_id": 0,
            "cache_key": "live-bmd-btx",
            "texture_id": 210,
            "player_alignment": {"verified": True},
            "models": [{
                "model_id": 279,
                "texture_id": 210,
                "texture_match": "78/78",
                "texture_candidate": False,
                "cell": {"x": 1, "y": 23},
                "asset_url": "/verified-279.glb",
            }],
        }

        result = service._bind_loaded_visual(static, visual)

        self.assertEqual(result["terrains"][0]["asset_url"], "/verified-279.glb")
        self.assertEqual(result["terrains"][1]["asset_url"], "/other-fallback.glb")
        self.assertEqual(result["terrains"][0]["texture_binding"]["status"], "verified")
        self.assertEqual(result["visual_binding"]["status"], "verified")

    def test_mismatched_loaded_visual_is_not_applied(self):
        service = World3DSceneService(original=None, truth=None, exported=object())
        static = {"zone_id": 427, "matrix": {"matrix_id": 0}, "terrains": []}
        result = service._bind_loaded_visual(static, {
            "verified": True,
            "map_definition_id": 426,
            "matrix_id": 0,
            "player_alignment": {"verified": True},
        })
        self.assertEqual(result["visual_binding"]["status"], "rejected")

    def test_runtime_matched_matrix_replaces_wrong_zone_header_matrix(self):
        class Matrix:
            matrix_id = 257
            has_zones = True
            width = height = 1
            cell_count = 1
            trailing_bytes = 0

            @staticmethod
            def cells():
                return iter([{"x": 0, "y": 0, "chunk_id": 77}])

        class Original:
            class rom:
                @staticmethod
                def matrix(matrix_id):
                    assert matrix_id == 257
                    return Matrix()

        class Exported:
            @staticmethod
            def zone(_zone_id):
                return {
                    "matrix": {"matrix_id": 0},
                    "render_coordinate_system": {"chunk_span_world": 512},
                    "cells": [], "buildings": [], "entities": {}, "zone": {}, "area": {},
                }

        static = World3DSceneService(original=Original(), truth=None, exported=Exported()).static_scene(
            428, runtime_matrix_id=257,
        )

        self.assertEqual(static["matrix"]["matrix_id"], 257)
        self.assertEqual(static["terrains"][0]["chunk_id"], 77)
        # A runtime-matched matrix is now allowed to use the lazy original
        # ROM converter immediately; the previous null URL caused an empty
        # coordinate grid even when the BMD0/BTX0 pair was valid.
        self.assertEqual(static["terrains"][0]["asset_url"], "/api/v1/map/v5/terrain/428/0/0/model.glb")
        self.assertEqual(static["runtime_matrix_binding"]["status"], "probable")
