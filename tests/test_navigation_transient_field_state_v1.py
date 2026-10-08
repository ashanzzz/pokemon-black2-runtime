from backend.black2.world.navigation_tasks import NavigationTaskService


def test_turning_with_overworld_input_lock_is_treated_as_transient_locomotion():
    player = {
        "status": "resolved",
        "position": {
            "grid": {"x": 73, "y": 1, "z": 693},
            "world": {"x": 1176.0, "y": 16.0, "z": 11096.0},
        },
        "locomotion": {"phase": "Turning", "semantic_state": "Turning (原地转向)"},
    }
    control = {
        "runtime": {"status": "ready"},
        "semantic": {
            "map_loaded": False,
            "ready_for_input": True,
            "context": {
                "screen_type": "OVERWORLD",
                "can_move_player": True,
                "is_dialogue_active": False,
            },
        },
    }
    service = NavigationTaskService(
        planner=None,
        client=None,
        player_sample=lambda: player,
        control_sample=lambda: control,
    )

    error = service._not_controllable()

    assert error is not None
    assert error["reason"] == "locomotion_not_idle"
    assert error["field_semantics_transient"] is True

