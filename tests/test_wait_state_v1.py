from backend.black2.runtime.wait_state import derive_wait_state


def _snapshot(*, context=None, battle=None, battle_ui=None, ready_for_input=True):
    return {
        "transport": {"bridge_connected": True, "frame": 100, "session_id": "s1"},
        "runtime": {"status": "ready", "semantic_status": "ready"},
        "semantic": {
            "ready_for_input": ready_for_input,
            "context": {
                "screen_type": "OVERWORLD",
                "is_dialogue_active": False,
                "choices": [],
                **(context or {}),
            },
        },
        "player": {"zone_id": 446, "position": {"grid": {"x": 1, "y": 0, "z": 2}}},
        "battle": {"active": False, "field_busy": {}, **(battle or {})},
        "battle_ui": battle_ui,
    }


def test_dialogue_page_is_one_shot_automatic_wait():
    state = derive_wait_state(_snapshot(context={
        "screen_type": "DIALOGUE_ACTIVE",
        "is_dialogue_active": True,
        "active_pointer": "0x1234",
        "dialogue_text": "下一页",
    }))
    assert state["kind"] == "auto_transition"
    assert state["reason"] == "dialogue_page"
    assert state["auto_policy"] == "press_A_once"
    assert state["allowed_actions"] == ["dialogue.advance"]
    assert state["wait_id"] == state["boundary_key"]


def test_dialogue_choice_pauses_automatic_progression():
    state = derive_wait_state(_snapshot(context={
        "screen_type": "DIALOGUE_CHOICE",
        "is_dialogue_active": True,
        "active_pointer": "0x1234",
        "choices": [{"index": 0, "label": "是", "selected": True}, {"index": 1, "label": "否"}],
    }))
    assert state["kind"] == "decision"
    assert state["reason"] == "dialogue_choice"
    assert state["decision_required"] is True
    assert state["auto_policy"] == "pause"


def test_calibrated_loaded_dialogue_stream_authorizes_one_a_without_global_fallback():
    state = derive_wait_state(_snapshot(
        ready_for_input=False,
        context={
            "screen_type": "DIALOGUE_ACTIVE",
            "is_dialogue_active": True,
            "active_pointer": None,
            "loaded_dialogue_text": (
                "喂喂！一个道馆徽章\n"
                "也没有的小孩子\n"
                "[SCROLL]\n\n"
                "也想过去吗！？\n"
                "[CLEAR]\n\n"
                "跟旁边的训练师以及宝可梦\n"
                "多战斗战斗吧！\n"
                "[CLEAR]"
            ),
        },
    ))
    assert state["reason"] == "dialogue_page_calibrated"
    assert state["auto_policy"] == "press_A_once"
    assert state["context"]["auto_advance_contract"]["contract_id"] == "EXP_012_zone446_gate_no_badge_dialogue"


def test_ranch_story_loaded_stream_is_narrowly_calibrated():
    state = derive_wait_state(_snapshot(
        ready_for_input=False,
        context={
            "screen_type": "DIALOGUE_ACTIVE",
            "is_dialogue_active": True,
            "active_pointer": None,
            "loaded_dialogue_text": (
                "算木牧场\n"
                "也有野生宝可梦！\n"
                "[CLEAR]\n\n"
                "真是只有自己的宝可梦\n"
                "才靠得住啊！"
            ),
        },
    ))
    assert state["reason"] == "dialogue_page_calibrated"
    assert state["auto_policy"] == "press_A_once"
    assert state["context"]["auto_advance_contract"]["contract_id"] == "EXP_020_zone439_ranch_story_npc"


def test_unknown_unresolved_dialogue_stream_remains_frozen():
    state = derive_wait_state(_snapshot(
        ready_for_input=False,
        context={
            "screen_type": "DIALOGUE_ACTIVE",
            "is_dialogue_active": True,
            "loaded_dialogue_text": "未知 NPC：真的要继续吗？",
        },
    ))
    assert state["reason"] == "dialogue_text_printing"
    assert state["auto_policy"] == "wait_for_state_change"
    assert state["allowed_actions"] == []


def test_battle_move_menu_is_decision_and_exposes_semantic_slots():
    state = derive_wait_state(_snapshot(
        battle={"active": True, "field_busy": {"raw": 1}},
        battle_ui={
            "phase": {"value": "move_menu", "raw_u32": 2},
            "cursor": {"status": "candidate", "slot": 1, "raw_u32": 0x4200},
        },
    ))
    assert state["kind"] == "decision"
    assert state["reason"] == "battle_move_choice"
    assert state["decision_required"] is True
    assert [item["move_slot"] for item in state["actions"]] == [1, 2, 3]


def test_unresolved_battle_never_authorizes_input():
    state = derive_wait_state(_snapshot(battle={"active": True, "field_busy": {"raw": 1}}))
    assert state["status"] == "unresolved"
    assert state["kind"] == "hold"
    assert state["allowed_actions"] == []


def test_overworld_without_verified_player_position_is_hold():
    state = derive_wait_state(_snapshot())
    assert state["reason"] == "player_position_unresolved"
    assert state["allowed_actions"] == []
