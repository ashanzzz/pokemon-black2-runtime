"""Tests for manual input evidence reporting."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.black2.dev.tester import DeveloperTestWorkbench
from backend.black2.observer.presentation import build_observer_presentation


def _state(name: str):
    return SimpleNamespace(context=SimpleNamespace(screen_type=SimpleNamespace(value=name)))


class FakeClient:
    def __init__(self):
        self.frames = iter((120, 144))

    async def get_emu_state(self):
        return {"frame": next(self.frames)}

    async def press_buttons(self, _buttons, frames):
        return {"queued": True, "frames": frames}


class FakeStateEngine:
    def __init__(self):
        self.current_state = _state("DIALOGUE_ACTIVE")

    async def sample_once(self):
        return _state("OVERWORLD")


class TestDeveloperWorkbench(unittest.TestCase):
    def test_input_record_uses_context_screen_type_and_bridge_evidence(self):
        workbench = DeveloperTestWorkbench(FakeClient(), FakeStateEngine())
        workbench._queue_length = AsyncMock(side_effect=(0, 0, 0))
        workbench._capture = AsyncMock(side_effect=(None, None))

        record = asyncio.run(workbench.execute_input_test("A", frames=8))

        self.assertEqual(record.state_before, "DIALOGUE_ACTIVE")
        self.assertEqual(record.state_after, "OVERWORLD")
        self.assertTrue(record.bridge_accepted)
        self.assertEqual(record.result, "PASS")

    def test_unproven_delivery_never_reports_pass(self):
        result, _status = DeveloperTestWorkbench._test_outcome(
            completed=False,
            screen_changed=None,
            state_before="DIALOGUE_ACTIVE",
            state_after="DIALOGUE_ACTIVE",
        )
        self.assertEqual(result, "FAIL")

    def test_dialogue_inference_does_not_claim_that_controller_is_locked(self):
        presentation = build_observer_presentation({
            "frame": 1,
            "context": {"is_dialogue_active": True, "dialogue_text": "test", "choices": []},
            "location": "实时 ARM9 地图（Map Section 未验证）",
            "player_world_pos": {"x": 54, "y": 733, "z": 12},
            "player_position_verified": True,
        })
        self.assertTrue(presentation.input_state.startswith("INJECTABLE"))
        self.assertTrue(presentation.player_control.startswith("UNVERIFIED"))
        self.assertEqual(presentation.world_pos, {"x": 54, "y": 733, "z": 12})
        self.assertEqual(presentation.map_id_hex, "UNVERIFIED")
        self.assertNotIn("主角家", presentation.location_zh)
