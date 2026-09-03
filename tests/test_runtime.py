"""Unit tests for Black 2 Semantic Runtime."""

import sys
import os
import unittest
import asyncio
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from backend.black2.decoders.text import decode_gen5_string, extract_printable_strings
from backend.black2.decoders.title_login import TitleLoginDecoder, GameScene
from backend.black2.bizhawk.process_probe import probe_bizhawk_process
from backend.black2.state.engine import SemanticStateEngine
from backend.black2.world.native_map import LiveMapState


class FakeEngineReader:
    async def read_batch_snapshot(self, _specs):
        return {
            "frame": 123,
            "results": {
                "script_message_active": {"bytes": [0]},
                "msg_printer_buffer": {"bytes": []},
                "main_menu_struct": {"bytes": [0] * 16},
            },
        }


class TestRuntime(unittest.TestCase):
    def test_process_probe(self):
        proc = probe_bizhawk_process()
        self.assertIsInstance(proc.running, bool)

    def test_gen5_text_decoder(self):
        # Test ASCII / UTF-16 decoding
        ascii_words = [ord(c) for c in "POKEMON"] + [0xFFFF]
        self.assertEqual(decode_gen5_string(ascii_words), "POKEMON")

        # Test newline control code 0xFFFE 0x0001
        newline_words = [ord('H'), ord('i'), 0xFFFE, 0x0001, 0, ord('!'), 0xFFFF]
        res = decode_gen5_string(newline_words)
        self.assertTrue("Hi" in res)

    def test_title_login_decoder(self):
        decoder = TitleLoginDecoder()
        batch = {
            "overlay_id": {"hex": "00000000", "bytes": [0, 0, 0, 0]},
            "ui_mode": {"hex": "01", "bytes": [1]}
        }
        state = decoder.decode(batch)
        self.assertIsNotNone(state)

    def test_state_engine_does_not_publish_unverified_live_coordinates(self):
        candidate = LiveMapState(
            map_id=0x0161,
            x=17,
            y=29,
            elevation=2,
            verified=False,
            facing="South",
            movement_state="Walking (行走中)",
        )
        with patch(
            "backend.black2.state.engine.read_live_map_state",
            new=AsyncMock(return_value=candidate),
        ):
            state = asyncio.run(SemanticStateEngine(FakeEngineReader()).sample_once())

        self.assertEqual(state.map_section_id, 0x0161)
        self.assertEqual(state.player_world_pos, {"x": None, "y": None, "z": None})
        self.assertFalse(state.player_position_verified)
        self.assertEqual(state.player_facing, "Unresolved")
        self.assertEqual(state.movement_state, "Unresolved")


if __name__ == "__main__":
    unittest.main()
