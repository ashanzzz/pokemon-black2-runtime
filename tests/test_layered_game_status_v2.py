from backend.black2.runtime.layered_status import project_layered_state


def snap(*, dialogue: bool, screen: str = "DIALOGUE_ACTIVE"):
    return {
        "age_seconds": 0.1,
        "transport": {"bridge_connected": True},
        "runtime": {"semantic_status": "ready"},
        "player": {"zone_id": 19, "position": {"grid": {"x": 5, "y": 0, "z": 7}}},
        "semantic": {
            "frame": 12345,
            "ready_for_input": True,
            "context": {
                "screen_type": screen,
                "is_dialogue_active": dialogue,
                "dialogue_text": "测试文本",
                "speaker": "NPC",
                "choices": [],
                "can_move_player": not dialogue,
                "recommended_action": "按 A 键继续" if dialogue else "自由探索",
            },
        },
    }


def battle(active):
    return {
        "active": active,
        "active_status": "candidate",
        "field_busy": {"raw": 1 if active else 0, "name": "battle" if active else "none"},
        "party_header": {"status": "candidate", "capacity": 6, "count": 1},
        "frame": 12345,
    }


def test_battle_and_dialogue_are_simultaneous_layers_not_mutually_exclusive():
    payload = project_layered_state(snap(dialogue=True), battle(True))
    assert payload["primary_context"] == "battle"
    assert "battle" in payload["active_layers"]
    assert "dialogue" in payload["active_layers"]
    assert "dialogue" in payload["overlays"]
    assert payload["input"]["owner"] == "dialogue"
    assert payload["battle"]["phase"] == "message"
    assert payload["exploration"]["active"] is False


def test_exploration_remains_active_under_dialogue_overlay():
    payload = project_layered_state(snap(dialogue=True), battle(False))
    assert payload["primary_context"] == "exploration"
    assert set(payload["active_layers"]) >= {"exploration", "dialogue"}
    assert payload["input"]["owner"] == "dialogue"
    assert payload["exploration"]["active"] is True


def test_plain_overworld_gives_exploration_input_ownership():
    payload = project_layered_state(snap(dialogue=False, screen="OVERWORLD"), battle(False))
    assert payload["active_layers"] == ["exploration"]
    assert payload["input"]["owner"] == "exploration"
    assert payload["input"]["kind"] == "navigation_or_interaction"
