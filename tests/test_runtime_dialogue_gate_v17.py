from backend.black2.state.engine import SemanticStateEngine


def _batch(script_value, window_value):
    return {
        "script_message_active": {"bytes": [script_value]},
        "dialogue_window_active": {"bytes": [window_value]},
    }


def test_dialogue_gate_rejects_non_boolean_script_candidate_from_idle_save():
    assert SemanticStateEngine._active_flag(_batch(0x37, 0)) is False


def test_dialogue_gate_requires_both_live_candidates():
    assert SemanticStateEngine._active_flag(_batch(1, 0)) is False
    assert SemanticStateEngine._active_flag(_batch(0, 1)) is False
    assert SemanticStateEngine._active_flag(_batch(1, 1)) is True
