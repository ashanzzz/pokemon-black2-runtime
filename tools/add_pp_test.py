with open("tests/test_battle_action_service.py", "r", encoding="utf-8") as f:
    text = f.read()

new_test = """

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
"""

if "test_battle_action_service_rejects_use_move_when_pp_exhausted" not in text:
    text += new_test
    with open("tests/test_battle_action_service.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Added test_battle_action_service_rejects_use_move_when_pp_exhausted!")