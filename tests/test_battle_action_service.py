import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.black2.battle.battle_action_service import BattleActionService


@pytest.mark.anyio
async def test_battle_action_service_rejects_when_not_configured():
    svc = BattleActionService()
    res = await svc.execute_decision({"type": "use_move", "move_slot": 1})
    assert res["status"] == "rejected"
    assert res["reason"]["code"] == "ACTION_ENGINE_NOT_CONFIGURED"


@pytest.mark.anyio
async def test_battle_action_service_rejects_empty_commands():
    engine = MagicMock()
    svc = BattleActionService(action_engine=engine)
    res = await svc.execute_decision({"commands": []})
    assert res["status"] == "rejected"
    assert res["reason"]["code"] == "EMPTY_COMMANDS"


@pytest.mark.anyio
async def test_battle_action_service_rejects_when_battle_inactive(monkeypatch):
    engine = MagicMock()
    svc = BattleActionService(action_engine=engine)

    from backend.black2.battle.battle_state_machine import battle_state_machine
    monkeypatch.setattr(battle_state_machine, "sample", AsyncMock(return_value={"active": False}))

    res = await svc.execute_decision({"type": "use_move", "move_slot": 1})
    assert res["status"] == "rejected"
    assert res["reason"]["code"] == "BATTLE_NOT_ACTIVE"


@pytest.mark.anyio
async def test_battle_action_service_executes_use_move_and_verifies_pp(monkeypatch):
    engine = MagicMock()
    engine.touch_screen = AsyncMock(return_value={"ok": True})
    engine.press_button = AsyncMock(return_value={"ok": True})
    svc = BattleActionService(action_engine=engine)

    sample_before = {
        "active": True,
        "phase": "command_menu",
        "player": {
            "active": {
                "moves": [
                    {"slot": 1, "move_id": 33, "current_pp": 15, "max_pp": 15},
                    {"slot": 2, "move_id": 98, "current_pp": 20, "max_pp": 20},
                ]
            }
        },
        "opponent": {
            "active": {"species_id": 504, "current_hp": 20}
        }
    }
    sample_after = {
        "active": True,
        "phase": "command_menu",
        "player": {
            "active": {
                "moves": [
                    {"slot": 1, "move_id": 33, "current_pp": 14, "max_pp": 15},
                    {"slot": 2, "move_id": 98, "current_pp": 20, "max_pp": 20},
                ]
            }
        },
        "opponent": {
            "active": {"species_id": 504, "current_hp": 12}
        }
    }

    mock_sample = AsyncMock(side_effect=[sample_before, sample_before, sample_after, sample_after, sample_after])
    from backend.black2.battle.battle_state_machine import battle_state_machine
    monkeypatch.setattr(battle_state_machine, "sample", mock_sample)

    res = await svc.execute_decision({"type": "use_move", "move_slot": 1})
    assert res["status"] == "executed"
    assert res["executed"] is True
    assert res["verification"]["pp_decreased"] is True
    assert res["verification"]["damage_dealt"] is True


@pytest.mark.anyio
async def test_battle_action_service_executes_run_and_verifies_escape(monkeypatch):
    engine = MagicMock()
    engine.touch_screen = AsyncMock(return_value={"ok": True})
    engine.press_button = AsyncMock(return_value={"ok": True})
    svc = BattleActionService(action_engine=engine)

    sample_active = {"active": True, "battle_kind": "wild", "phase": "command_menu"}
    sample_escaped = {"active": False}

    mock_sample = AsyncMock(side_effect=[sample_active, sample_active, sample_escaped, sample_escaped])
    from backend.black2.battle.battle_state_machine import battle_state_machine
    monkeypatch.setattr(battle_state_machine, "sample", mock_sample)

    res = await svc.execute_decision({"type": "run"})
    assert res["status"] == "executed"
    assert res["executed"] is True
    assert res["verification"]["escaped"] is True


@pytest.mark.anyio
async def test_battle_action_service_rejects_run_from_trainer(monkeypatch):
    engine = MagicMock()
    svc = BattleActionService(action_engine=engine)

    sample_trainer = {"active": True, "battle_kind": "trainer", "phase": "command_menu"}
    from backend.black2.battle.battle_state_machine import battle_state_machine
    monkeypatch.setattr(battle_state_machine, "sample", AsyncMock(return_value=sample_trainer))

    res = await svc.execute_decision({"type": "run"})
    assert res["status"] == "rejected"
    assert res["reason"]["code"] == "CANNOT_RUN_FROM_TRAINER"


@pytest.mark.anyio
async def test_battle_action_service_rejects_use_move_when_pp_exhausted(monkeypatch):
    engine = MagicMock()
    svc = BattleActionService(action_engine=engine)

    sample_no_pp = {
        "active": True,
        "phase": "command_menu",
        "player": {
            "active": {
                "moves": [
                    {"slot": 1, "move_id": 33, "name": "撞击", "current_pp": 0, "max_pp": 35},
                ]
            }
        },
        "opponent": {
            "active": {"species_id": 504, "current_hp": 20}
        }
    }
    from backend.black2.battle.battle_state_machine import battle_state_machine
    monkeypatch.setattr(battle_state_machine, "sample", AsyncMock(return_value=sample_no_pp))

    res = await svc.execute_decision({"type": "use_move", "move_slot": 1})
    assert res["status"] == "rejected"
    assert res["executed"] is False
    assert res["reason"]["code"] == "MOVE_PP_EXHAUSTED"
    assert res["current_pp"] == 0
