from __future__ import annotations

import asyncio
import pytest

from backend.black2.battle.battle_planner import (
    compute_type_effectiveness,
    evaluate_move,
    plan_battle_decision,
)
from backend.black2.battle.battle_state_machine import BattleStateMachine


def test_type_effectiveness_calculations():
    # Normal (1) vs Ghost (8) = 0.0
    assert compute_type_effectiveness(1, [8]) == 0.0

    # Water (11) vs Fire (10) = 2.0
    assert compute_type_effectiveness(11, [10]) == 2.0

    # Electric (13) vs Water (11) + Flying (3) = 4.0
    assert compute_type_effectiveness(13, [11, 3]) == 4.0

    # Ground (5) vs Flying (3) = 0.0
    assert compute_type_effectiveness(5, [3]) == 0.0

    # Grass (12) vs Water (11) + Ground (5) = 4.0
    assert compute_type_effectiveness(12, [11, 5]) == 4.0


def test_move_evaluation_and_stab():
    # Move with PP > 0 and STAB (Water type 11 used by Water type 11 user against Fire type 10)
    res = evaluate_move(
        move_id=57,  # Surf (Water)
        current_pp=15,
        max_pp=15,
        slot=2,
        user_type_ids=[11],
        opponent_type_ids=[10],
    )
    assert res["usable"] is True
    assert res["is_stab"] is True
    assert res["type_multiplier"] == 2.0
    assert res["verdict"] == "super_effective"
    assert res["expected_score"] > 100.0

    # Move with PP == 0
    res_no_pp = evaluate_move(
        move_id=57,
        current_pp=0,
        max_pp=15,
        slot=2,
        user_type_ids=[11],
        opponent_type_ids=[10],
    )
    assert res_no_pp["usable"] is False
    assert res_no_pp["verdict"] == "no_pp"
    assert res_no_pp["expected_score"] == 0.0


def test_battle_planner_picks_best_move_against_ghost():
    # Golduck (Water) facing Frillish (Water/Ghost)
    # Move 1: Fury Swipes (Normal, power 18) -> Immune!
    # Move 2: Surf (Water, power 95) -> 0.5x
    # Move 3: Zen Headbutt (Psychic, power 80) -> 1.0x (best!)
    player_mon = {
        "species": 55,
        "moves": [
            {"slot": 1, "move_id": 154, "current_pp": 15, "max_pp": 15},
            {"slot": 2, "move_id": 57, "current_pp": 15, "max_pp": 15},
            {"slot": 3, "move_id": 428, "current_pp": 15, "max_pp": 15},
            {"slot": 4, "move_id": 401, "current_pp": 10, "max_pp": 10},
        ],
    }
    opp_mon = {"species": 592}  # Frillish (Water / Ghost)

    plan = plan_battle_decision(player_mon=player_mon, opponent_mon=opp_mon, battle_kind="wild")
    assert plan["format"] == "black2-battle-decision-plan/v1"
    assert plan["recommended_action"]["type"] == "use_move"
    assert plan["recommended_action"]["move_slot"] == 3
    assert plan["recommended_action"]["move_id"] == 428
    assert plan["best_move"]["move_id"] == 428

    # Verify Normal move Fury Swipes is marked immune
    fury_swipes = next(m for m in plan["moves_evaluated"] if m["move_id"] == 154)
    assert fury_swipes["verdict"] == "immune"
    assert fury_swipes["type_multiplier"] == 0.0


def test_battle_state_machine_not_in_battle():
    sm = BattleStateMachine()
    res = asyncio.run(sm.sample())
    assert res["status"] == "not_in_battle"
    assert res["active"] is False
    assert res["can_act"] is False
    assert res["decision"] is None
