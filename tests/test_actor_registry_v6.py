import unittest
from backend.black2.world.original_actor_assets import normalize_bw2_objcode


class TestActorRegistryV6(unittest.TestCase):
    def test_player_objcodes_are_direct_registry_indices(self):
        self.assertEqual(normalize_bw2_objcode(231), 231)
        self.assertEqual(normalize_bw2_objcode(240), 240)

    def test_bw2_extended_objcode_ranges_follow_ctrmapv_normalizer(self):
        self.assertEqual(normalize_bw2_objcode(4096), 377)
        self.assertEqual(normalize_bw2_objcode(8192), 997)
        self.assertEqual(normalize_bw2_objcode(5000), 10)
