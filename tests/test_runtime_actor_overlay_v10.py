import unittest

from backend.black2.world.runtime_actor_overlay import RuntimeActorOverlayService
from backend.black2.world.runtime_field_resolver import ACTOR, ACTOR_SYSTEM, ARM9_BASE, MAIN_RAM_SIZE, MAPPER


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

    def test_battle_recovery_finds_actor_system_without_player_field_chain(self):
        raw = bytearray(MAIN_RAM_SIZE)
        system = ARM9_BASE + 0x100
        heap = ARM9_BASE + 0x200
        mapper = ARM9_BASE + 0x1000
        field = ARM9_BASE + 0x1200

        def w16(address, value):
            raw[address - ARM9_BASE:address - ARM9_BASE + 2] = int(value).to_bytes(2, "little", signed=False)

        def w32(address, value):
            raw[address - ARM9_BASE:address - ARM9_BASE + 4] = int(value).to_bytes(4, "little", signed=False)

        w16(system + ACTOR_SYSTEM["capacity"], 2)
        w16(system + ACTOR_SYSTEM["count"], 2)
        w32(system + ACTOR_SYSTEM["actor_heap"], heap)
        w32(system + ACTOR_SYSTEM["g3d_mapper"], mapper)
        w32(system + ACTOR_SYSTEM["field"], field)
        w16(mapper + MAPPER["matrix_width"], 1)
        w16(mapper + MAPPER["matrix_height"], 1)
        w32(mapper + MAPPER["chunk_id_count"], 1)

        player = heap
        npc = heap + ACTOR["stride"]
        for actor, uid, zone, model, script, gx, gz in (
            (player, 0xFF, 0, 231, 0, 140, 664),
            (npc, 7, 446, 11, 3164, 141, 664),
        ):
            w32(actor + ACTOR["actor_system"], system)
            w16(actor + ACTOR["uid"], uid)
            w16(actor + ACTOR["zone_id"], zone)
            w16(actor + ACTOR["model_id"], model)
            w16(actor + ACTOR["script_id"], script)
            w16(actor + ACTOR["event_type"], 1 if uid != 0xFF else 0)
            w16(actor + ACTOR["gpos_x"], gx)
            w16(actor + ACTOR["gpos_y"], 2)
            w16(actor + ACTOR["gpos_z"], gz)

        result = RuntimeActorOverlayService._discover_actor_system_from_ram(bytes(raw))
        self.assertIsNotNone(result)
        self.assertEqual(result["zone_id"], 446)
        self.assertEqual(result["player_grid"], {"x": 140, "y": 2, "z": 664})
        self.assertEqual(result["addresses"]["actor_system"], system)
        self.assertEqual(result["addresses"]["player_actor"], player)
        self.assertEqual(result["actor_count"], 2)


if __name__ == "__main__":
    unittest.main()
