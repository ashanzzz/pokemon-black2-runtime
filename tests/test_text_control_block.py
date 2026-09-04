"""Regression tests for the EXP_013 control-flow candidate evidence.

The fixture proves a useful candidate trace, not a TextPrinter-to-screen
binding.  These tests deliberately ensure it cannot leak into visible lines.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from backend.black2.decoders.text_control_block import decode_text_control_block


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "reverse_engineering" / "experiments" / "EXP_013_dialogue_render_surface"


def _batch(capture_name: str, sample_index: int) -> dict:
    capture = json.loads((EVIDENCE / capture_name).read_text(encoding="utf-8"))
    entries = {item["id"]: item for item in capture["capture"]["samples"][sample_index]["ranges"]}
    control = entries["dialogue_control_allocations"]
    control_raw = bytes.fromhex(control["hex"])
    control_base = 0x02000000 + control["offset"]
    tcb_start = 0x02332C20 - control_base
    return {
        "msg_printer_buffer": entries["msg_buffer"],
        "dialogue_tcb": {"offset": 0x332C20, "bytes": list(control_raw[tcb_start:tcb_start + 0x80])},
        "dialogue_bitmap_surface": entries["candidate_bitmap_surface"],
    }


@unittest.skipUnless(EVIDENCE.is_dir(), "historical EXP_013 evidence is not bundled in the clean source release")
class TestTextControlBlockEvidence(unittest.TestCase):
    def test_clear_wait_retains_first_page_even_after_continuation_advanced(self):
        render = decode_text_control_block(_batch("a_edge_capture_page1_clear_to_page2.json", 0))
        self.assertTrue(render.resolved)
        self.assertEqual(render.current_char, 0x022490C8)
        self.assertEqual(render.pending_command, "clear")
        self.assertEqual(render.candidate_lines, ["科学的力量真是惊人！"])
        self.assertEqual(render.visible_lines, [])

    def test_clear_then_explicit_lf_restores_two_line_page(self):
        render = decode_text_control_block(_batch("a_edge_capture_page1_clear_to_page2.json", 52))
        self.assertTrue(render.resolved)
        self.assertEqual(render.phase, 1)
        self.assertEqual(render.current_char, 0x022490EC)
        self.assertEqual(render.pending_command, "scroll")
        self.assertEqual(render.candidate_lines, ["现在可以用通信", "和１００个人"])
        self.assertEqual(render.visible_lines, [])

    def test_scroll_keeps_old_second_line_and_draws_new_second_line(self):
        capture = "a_edge_capture_page2_scroll_to_overlap.json"
        before = decode_text_control_block(_batch(capture, 0))
        after_scroll = decode_text_control_block(_batch(capture, 7))
        settled = decode_text_control_block(_batch(capture, 24))

        self.assertEqual(before.candidate_lines, ["现在可以用通信", "和１００个人"])
        self.assertEqual(after_scroll.scroll_distance, 16)
        self.assertEqual(after_scroll.candidate_lines, ["和１００个人"])
        self.assertEqual(settled.phase, 2)
        self.assertEqual(settled.current_char, 0x022490F6)
        self.assertEqual(settled.candidate_lines, ["和１００个人", "同时游戏！"])
        self.assertEqual(settled.visible_lines, [])

    def test_odd_continuation_cursor_is_rejected(self):
        batch = _batch("a_edge_capture_page1_clear_to_page2.json", 0)
        raw = bytearray(batch["dialogue_tcb"]["bytes"])
        raw[0x2C:0x30] = (0x022490C9).to_bytes(4, "little")
        batch["dialogue_tcb"]["bytes"] = list(raw)

        render = decode_text_control_block(batch)

        self.assertFalse(render.resolved)
        self.assertEqual(render.reason, "tcb_structural_guard_failed")
        self.assertEqual(render.visible_lines, [])

    def test_continuation_cursor_inside_command_is_rejected(self):
        batch = _batch("a_edge_capture_page1_clear_to_page2.json", 0)
        raw = bytearray(batch["dialogue_tcb"]["bytes"])
        raw[0x2C:0x30] = (0x022490C2).to_bytes(4, "little")
        batch["dialogue_tcb"]["bytes"] = list(raw)

        render = decode_text_control_block(batch)

        self.assertFalse(render.resolved)
        self.assertEqual(render.reason, "tcb_structural_guard_failed")


if __name__ == "__main__":
    unittest.main()
