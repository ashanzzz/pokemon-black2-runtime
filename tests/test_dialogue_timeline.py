"""Tests for dialogue speaker identification, active/idle state detection, and timeline logging."""

import unittest
from backend.black2.decoders.dialogue import (
    DialogueDecoder,
    DialogueTimelineManager,
    infer_speaker,
)


class TestDialogueTimeline(unittest.TestCase):
    def test_infer_speaker_signpost(self):
        speaker, category = infer_speaker("20号公路 只要沿着路往前走就到了！1", "20号公路 (Route 20)")
        self.assertEqual(category, "SIGNPOST")
        self.assertIn("20号公路", speaker)

    def test_infer_speaker_mother(self):
        speaker, category = infer_speaker("零！穿上这双跑鞋，路上要小心啊。", "桧扇市 主角家 (Player's House 1F)")
        self.assertEqual(category, "MAIN_NPC")
        self.assertIn("妈妈", speaker)

    def test_infer_speaker_bianca(self):
        speaker, category = infer_speaker("我是白露！受红豆杉博士的委托来给你送宝可梦图鉴的。", "桧扇市 展望台 (Lookout Point)")
        self.assertEqual(category, "MAIN_NPC")
        self.assertIn("白露", speaker)

    def test_infer_speaker_system_item(self):
        speaker, category = infer_speaker("获得了 跑鞋！放入了重要物品口袋。", "桧扇市 主角家")
        self.assertEqual(category, "SYSTEM")
        self.assertIn("系统提示", speaker)

    def test_timeline_transition_moving_player_keeps_speaker_unresolved(self):
        mgr = DialogueTimelineManager(log_dir="test_logs_tmp")
        mgr.clear_history()

        # Step 1: Dialogue appears while player is stationary
        is_active, entry = mgr.record_transition(
            text="20号公路 只要沿着路往前走就到了！",
            frame=100,
            location="20号公路",
            is_player_moving=False,
        )
        self.assertTrue(is_active)
        self.assertIsNotNone(entry)
        # Runtime timeline entries must not promote a speaker from text/location
        # heuristics.  `infer_speaker` remains a separate legacy helper only.
        self.assertEqual(entry.speaker_category, "UNRESOLVED")

        # Step 2: Player starts walking/moving
        is_active_moving, entry_moving = mgr.record_transition(
            text="20号公路 只要沿着路往前走就到了！",
            frame=120,
            location="20号公路",
            is_player_moving=True,
        )
        self.assertFalse(is_active_moving)
        self.assertFalse(entry_moving.is_active)
        self.assertEqual(len(mgr.history), 1)

    def test_timeline_does_not_infer_speaker_order_from_text(self):
        mgr = DialogueTimelineManager(log_dir="test_logs_tmp")
        mgr.clear_history()

        # Utterance 1: Mother
        mgr.record_transition("妈妈给你准备了跑鞋", frame=100, location="主角家", is_player_moving=False)
        # Utterance 2: Player receives item
        mgr.record_transition("获得了 跑鞋！", frame=200, location="主角家", is_player_moving=False)
        # Utterance 3: Mother speaks again
        mgr.record_transition("路上小心哦！", frame=300, location="主角家", is_player_moving=False)

        history = mgr.get_history(limit=10)
        self.assertEqual(len(history), 3)
        # History is returned in reverse chronological order (latest first)
        self.assertIn("路上小心", history[0].text)
        self.assertEqual(history[1].speaker_category, "UNRESOLVED")
        self.assertIn("跑鞋", history[2].text)


if __name__ == "__main__":
    unittest.main()
