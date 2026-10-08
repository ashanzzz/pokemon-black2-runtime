import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from backend.black2.state.engine import SemanticStateEngine, GameScreenType
from backend.black2.decoders.dialogue import DialogueState
from backend.black2.decoders.title_login import TitleLoginState


class DummyMemoryReader:
    async def read_memory(self, addr, length):
        return bytearray(length)

    async def read_batch_snapshot(self, specs):
        return {
            "frame": 100,
            "results": {
                spec["id"]: {"bytes": [0] * spec.get("length", 4)} for spec in specs
            },
        }


def test_battle_printer_activity_is_separate_unresolved_overlay_not_field_dialogue():
    reader = DummyMemoryReader()
    engine = SemanticStateEngine(reader)

    fake_battle_evidence = {
        "active": True,
        "active_status": "candidate",
        "confidence": 0.95,
        "field_busy": {"raw": 1, "name": "battle"},
    }

    printer_active = DialogueState(active=True, current_text="not a verified battle message")
    with patch.object(engine.battle_runtime, "sample", AsyncMock(return_value=fake_battle_evidence)), \
         patch.object(engine.dialogue_decoder, "decode", return_value=printer_active), \
         patch.object(engine.title_login_decoder, "decode", return_value=TitleLoginState(is_main_menu=False)):
        state = asyncio.run(engine.sample_once())
        assert state.context.screen_type == GameScreenType.BATTLE
        assert state.context.can_move_player is False
        assert state.context.is_dialogue_active is False
        assert state.context.dialogue_text == ""
        assert state.context.battle_message_overlay["status"] == "unresolved"
        assert state.context.battle_message_overlay["printer_activity"] == {
            "status": "candidate",
            "source": "shared RuntimeDialogueDecoder hardware-printer sample",
            "active": True,
            "source_frame": 100,
        }
        assert state.context.battle_message_overlay["current_text"]["value"] is None
        assert state.map_loaded is False
        assert state.field_runtime["status"] == "not_applicable"


def test_busy_loading_does_not_fall_through_to_overworld():
    reader = DummyMemoryReader()
    engine = SemanticStateEngine(reader)

    fake_battle_evidence = {
        "active": None,
        "active_status": "transition_candidate",
        "confidence": 0.8,
        "field_busy": {"raw": 2, "name": "loading"},
    }

    with patch.object(engine.battle_runtime, "sample", AsyncMock(return_value=fake_battle_evidence)), \
         patch.object(engine.title_login_decoder, "decode", return_value=TitleLoginState(is_main_menu=False)):
        state = asyncio.run(engine.sample_once())
        assert state.context.screen_type == GameScreenType.LOADING
        assert state.context.screen_type != GameScreenType.OVERWORLD
        assert state.map_loaded is False


def test_busy_loading_remains_primary_state_when_dialogue_batch_fails():
    reader = DummyMemoryReader()
    engine = SemanticStateEngine(reader)

    fake_battle_evidence = {
        "active": None,
        "active_status": "transition_candidate",
        "confidence": 0.8,
        "field_busy": {"raw": 2, "name": "loading"},
    }

    with patch.object(engine.battle_runtime, "sample", AsyncMock(return_value=fake_battle_evidence)), \
         patch.object(engine, "_sample_dialogue_batch", AsyncMock(side_effect=RuntimeError("transient read"))), \
         patch.object(engine.title_login_decoder, "decode", return_value=TitleLoginState(is_main_menu=False)):
        state = asyncio.run(engine.sample_once())
        assert state.context.screen_type == GameScreenType.LOADING
        assert state.context.can_move_player is False
        assert state.map_loaded is False


def test_battle_does_not_claim_current_map_loaded():
    reader = DummyMemoryReader()
    engine = SemanticStateEngine(reader)

    fake_battle_evidence = {
        "active": True,
        "active_status": "candidate",
        "confidence": 0.95,
        "field_busy": {"raw": 1, "name": "battle"},
    }

    with patch.object(engine.battle_runtime, "sample", AsyncMock(return_value=fake_battle_evidence)), \
         patch.object(engine.title_login_decoder, "decode", return_value=TitleLoginState(is_main_menu=False)):
        state = asyncio.run(engine.sample_once())
        assert state.map_loaded is False
        assert state.battle["active"] is True
