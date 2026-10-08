from backend.black2.runtime.layered_status import normalize_screen_type, project_layered_state


def test_screen_type_normalization_accepts_enum_style_legacy_labels():
    assert normalize_screen_type("GameScreenType.MAIN_MENU") == "MAIN_MENU"
    assert normalize_screen_type("GAMESCREENTYPE.MAIN_MENU") == "MAIN_MENU"
    assert normalize_screen_type(None) == "RUNTIME_UNRESOLVED"


def test_main_menu_is_a_blocking_menu_layer_when_legacy_prefix_is_present():
    snapshot = {
        "age_seconds": 0.1,
        "transport": {"bridge_connected": True},
        "runtime": {"semantic_status": "ready"},
        "player": {"zone_id": 439, "position": {"grid": {"x": 1, "y": 0, "z": 2}}},
        "semantic": {
            "frame": 10,
            "ready_for_input": True,
            "context": {
                "screen_type": "GAMESCREENTYPE.MAIN_MENU",
                "is_dialogue_active": False,
                "can_move_player": False,
                "choices": [],
            },
        },
    }
    payload = project_layered_state(snapshot, {"active": False, "active_status": "candidate"})
    assert payload["primary_context"] == "menu"
    assert payload["active_layers"] == ["menu"]
    assert payload["input"]["owner"] == "menu"
