import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from starlette.testclient import TestClient

from backend.black2.api.app import app
from backend.black2.battle.battle_planner import (
    compute_gen5_damage,
    compute_turn_speed,
    evaluate_move,
    stage_multiplier,
)
from backend.black2.actions.command_bus import CommandBus, TransactionResult


@pytest.fixture
def client():
    return TestClient(app)


def test_gen5_damage_formula_physical_and_special():
    dmg = compute_gen5_damage(
        attacker_level=50,
        base_power=90,
        attacker_stat=100,
        defender_stat=100,
        is_stab=False,
        type_mult=1.0,
        damage_class="physical",
    )
    assert 30 <= dmg["min"] <= 45
    assert dmg["min"] <= dmg["avg"] <= dmg["max"]

    dmg_burned = compute_gen5_damage(
        attacker_level=50,
        base_power=90,
        attacker_stat=100,
        defender_stat=100,
        is_stab=False,
        type_mult=1.0,
        is_burned=True,
        damage_class="physical",
    )
    assert dmg_burned["burn_factor"] == 0.5
    assert dmg_burned["max"] < dmg["max"]


def test_gen5_weather_damage_modifiers():
    water_dmg_rain = compute_gen5_damage(
        attacker_level=50, base_power=90, attacker_stat=100, defender_stat=100,
        weather="rain", move_type_id=11, damage_class="special"
    )
    water_dmg_none = compute_gen5_damage(
        attacker_level=50, base_power=90, attacker_stat=100, defender_stat=100,
        weather=None, move_type_id=11, damage_class="special"
    )
    assert water_dmg_rain["weather_factor"] == 1.5
    assert water_dmg_rain["max"] > water_dmg_none["max"]

    fire_dmg_rain = compute_gen5_damage(
        attacker_level=50, base_power=90, attacker_stat=100, defender_stat=100,
        weather="rain", move_type_id=10, damage_class="special"
    )
    assert fire_dmg_rain["weather_factor"] == 0.5
    assert fire_dmg_rain["max"] < water_dmg_none["max"]


def test_turn_speed_and_priority():
    prio_res = compute_turn_speed(
        user_base_speed=50, user_speed_stage=0, user_status=None,
        opp_base_speed=120, opp_speed_stage=0, opp_status=None,
        move_priority=1
    )
    assert prio_res["user_moves_first"] is True

    neg_prio = compute_turn_speed(
        user_base_speed=150, user_speed_stage=0, user_status=None,
        opp_base_speed=40, opp_speed_stage=0, opp_status=None,
        move_priority=-6
    )
    assert neg_prio["user_moves_first"] is False

    para_res = compute_turn_speed(
        user_base_speed=100, user_speed_stage=0, user_status="paralysis",
        opp_base_speed=50, opp_speed_stage=0, opp_status=None,
        move_priority=0
    )
    assert para_res["user_speed"] == 25.0
    assert para_res["user_moves_first"] is False


def test_command_bus_rollback_on_post_check_failure():
    async def _run_test():
        bus = CommandBus()
        ram_memory = bytearray(b"ORIGINAL_STATE")

        async def _capture():
            return (0x02200000, bytes(ram_memory), {})

        async def _action(_meta):
            ram_memory[:14] = b"CORRUPTED_DATA"
            return {"written": True}

        async def _verify(_action_res):
            return False, {}, "Checksum error simulation"

        async def _write_ram(_offset, data):
            ram_memory[:len(data)] = data

        result = await bus.execute_transaction(
            "test_operation",
            owner_id="test_unit",
            capture_pre_state=_capture,
            execute_action=_action,
            verify_post_state=_verify,
            write_ram=_write_ram,
        )

        assert result.ok is False
        assert result.rolled_back is True
        assert ram_memory == b"ORIGINAL_STATE"

    asyncio.run(_run_test())


def test_battle_request_returns_unresolved_when_evidence_missing(client):
    with patch("backend.black2.api.battle_routes._evidence", new_callable=AsyncMock) as mock_ev, \
         patch("backend.black2.api.battle_routes._battle_identity", new_callable=AsyncMock) as mock_ident:
        mock_ev.return_value = {"active": True, "frame": 1234}
        mock_ident.return_value = {
            "status": "candidate",
            "battle_kind": {"status": "unresolved", "value": None},
            "player": {"active": {"species_id": 501, "moves": []}},
            "opponent": {"active": {"species_id": 114, "moves": []}},
        }

        res = client.get("/api/v1/battle/request")
        assert res.status_code == 200
        d = res.json()
        assert d["battle_kind"] == {"status": "unresolved", "value": None}
        assert d["turn"] == {"status": "unresolved", "value": None, "reason": "RAM turn counter is not yet verified"}


def test_battle_state_includes_menu_phase_and_legal_targets(client):
    with patch("backend.black2.api.battle_routes._evidence", new_callable=AsyncMock) as mock_ev, \
         patch("backend.black2.api.battle_routes._battle_identity", new_callable=AsyncMock) as mock_ident, \
         patch("backend.black2.api.battle_routes._ui_sample", new_callable=AsyncMock) as mock_ui, \
         patch("backend.black2.api.battle_routes._ui_cursor_sample", new_callable=AsyncMock) as mock_cursor:

        mock_ev.return_value = {"active": True, "active_status": "candidate"}
        mock_ident.return_value = {
            "status": "candidate",
            "battle_kind": {"status": "candidate", "value": "wild"},
            "player": {"active": {"species_id": 638, "species_name": "勾帕路翁", "moves": [{"slot": 1, "name": "圣剑", "name_en": "Sacred Sword"}]}},
            "opponent": {"active": {"species_id": 114, "species_name": "蔓藤怪", "hp": {"current": 28, "max": 88}}},
        }
        mock_ui.return_value = {"active": True}
        mock_cursor.return_value = {
            "status": "candidate",
            "phase": "command_menu",
            "cursor": {"slot": 1, "grid": "top_left"}
        }

        res = client.get("/api/v1/battle/state")
        assert res.status_code == 200
        d = res.json()
        assert d["phase"] == "command_selection"
        assert d["waiting_for_input"] is True
        assert d["menu"]["status"] == "resolved"
        assert d["menu"]["kind"] == "command_menu"
        assert len(d["menu"]["legal_targets"]) == 1
        assert d["menu"]["legal_targets"][0]["target_id"] == "opponent:0"
        assert d["menu"]["legal_targets"][0]["species_name"] == "蔓藤怪"
        assert "move:1:Sacred Sword" in d["available_actions"]
