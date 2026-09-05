import unittest

from backend.black2.world.runtime_actor_overlay import RuntimeActorOverlayService


class TestRuntimeActorOverlayV10(unittest.TestCase):
    def test_raw_zone_match_is_probable_current_scene(self):
        latest = {
            "zone_id": 428,
            "mapper": {"chunk_tile_size": 32, "matrix_width": 1, "matrix_height": 1},
        }
        result = RuntimeActorOverlayService._scene_membership(428, {"x": 4, "y": 0, "z": 5}, latest)
        self.assertTrue(result["same_current_scene"])
        self.assertEqual(result["confidence"], "probable")
        self.assertEqual(result["effective_zone_id"], 428)

    def test_zero_raw_zone_inside_mapper_is_candidate_only(self):
        latest = {
            "zone_id": 428,
            "mapper": {"chunk_tile_size": 32, "matrix_width": 1, "matrix_height": 1},
        }
        result = RuntimeActorOverlayService._scene_membership(0, {"x": 5, "y": 0, "z": 6}, latest)
        self.assertTrue(result["same_current_scene"])
        self.assertEqual(result["confidence"], "candidate")
        self.assertEqual(result["effective_zone_id"], 428)

    def test_zero_raw_zone_outside_mapper_stays_unresolved(self):
        latest = {
            "zone_id": 428,
            "mapper": {"chunk_tile_size": 32, "matrix_width": 1, "matrix_height": 1},
        }
        result = RuntimeActorOverlayService._scene_membership(0, {"x": 50, "y": 0, "z": 50}, latest)
        self.assertFalse(result["same_current_scene"])
        self.assertEqual(result["confidence"], "unresolved")
        self.assertIsNone(result["effective_zone_id"])


if __name__ == "__main__":
    unittest.main()
