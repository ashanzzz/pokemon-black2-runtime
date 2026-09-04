import unittest
from backend.black2.world.world3d_scene import canonical_player, scene_origin


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
