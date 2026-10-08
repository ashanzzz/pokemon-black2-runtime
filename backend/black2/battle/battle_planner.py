"""Gen 5 battle AI move evaluator and decision planner for Pokémon Black 2.

Evaluates type effectiveness, STAB, accuracy, and expected damage for each move
against the opponent, and emits prioritized battle action recommendations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from ..dex.store import DexStore

_shared_dex: DexStore | None = None


def _get_dex() -> DexStore:
    global _shared_dex
    if _shared_dex is None:
        _shared_dex = DexStore()
    return _shared_dex


def compute_type_effectiveness(move_type_id: int, target_type_ids: list[int]) -> float:
    """Compute Gen 5 type multiplier (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)."""
    dex = _get_dex()
    t_data = dex.get("types", move_type_id)
    if not t_data:
        return 1.0
    efficacy_map = {e["target_type_id"]: e["damage_factor"] / 100.0 for e in t_data.get("efficacy_vs", [])}
    multiplier = 1.0
    for tid in target_type_ids:
        multiplier *= efficacy_map.get(tid, 1.0)
    return multiplier


def evaluate_move(
    move_id: int,
    current_pp: int,
    max_pp: int,
    *,
    slot: int,
    user_type_ids: list[int] | None = None,
    opponent_type_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Score a single move against the active opponent."""
    dex = _get_dex()
    move_data = dex.get("moves", move_id) or {}
    move_type = move_data.get("type", {})
    move_type_id = move_type.get("id") or 1
    move_name_zh = (move_data.get("names") or {}).get("zh-Hans") or move_data.get("identifier") or f"Move {move_id}"
    move_name_en = (move_data.get("names") or {}).get("en") or move_data.get("identifier") or f"Move {move_id}"

    base_power = move_data.get("power")
    accuracy = move_data.get("accuracy") or 100
    damage_class = (move_data.get("damage_class") or {}).get("identifier", "status")

    opp_types = opponent_type_ids or []
    usr_types = user_type_ids or []

    # Type effectiveness
    type_mult = compute_type_effectiveness(move_type_id, opp_types) if damage_class != "status" else 1.0

    # STAB (Same-Type Attack Bonus): 1.5x in Gen 5
    is_stab = move_type_id in usr_types
    stab_mult = 1.5 if (is_stab and damage_class != "status") else 1.0

    # Score calculation
    usable = current_pp > 0
    effective_power = (base_power if base_power is not None else (40 if damage_class == "status" else 50))
    expected_score = round(effective_power * type_mult * stab_mult * (accuracy / 100.0), 2) if usable else 0.0

    # Verdict
    if not usable:
        verdict = "no_pp"
    elif type_mult == 0.0:
        verdict = "immune"
    elif type_mult >= 2.0:
        verdict = "super_effective"
    elif type_mult <= 0.5:
        verdict = "not_very_effective"
    else:
        verdict = "normal"

    return {
        "slot": slot,
        "move_id": move_id,
        "name_zh": move_name_zh,
        "name_en": move_name_en,
        "move_type_id": move_type_id,
        "move_type_name": (move_type.get("names") or {}).get("zh-Hans", "未知"),
        "damage_class": damage_class,
        "base_power": base_power,
        "accuracy": accuracy,
        "current_pp": current_pp,
        "max_pp": max_pp,
        "usable": usable,
        "type_multiplier": type_mult,
        "is_stab": is_stab,
        "expected_score": expected_score,
        "verdict": verdict,
    }


def plan_battle_decision(
    *,
    player_mon: dict[str, Any] | None = None,
    opponent_mon: dict[str, Any] | None = None,
    battle_kind: str = "wild",
    capture_eval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Formulate an AI battle decision recommendation from live combatant facts."""
    dex = _get_dex()

    player_species = (player_mon or {}).get("species") or (player_mon or {}).get("species_id")
    opp_species = (opponent_mon or {}).get("species") or (opponent_mon or {}).get("species_id")

    player_pkm = dex.get("pokemon", player_species) if isinstance(player_species, int) else None
    opp_pkm = dex.get("pokemon", opp_species) if isinstance(opp_species, int) else None

    user_type_ids = [t["id"] for t in (player_pkm.get("types") or []) if "id" in t] if player_pkm else []
    opp_type_ids = [t["id"] for t in (opp_pkm.get("types") or []) if "id" in t] if opp_pkm else []

    moves_raw = (player_mon or {}).get("moves") or []
    scored_moves = []

    for idx, m in enumerate(moves_raw, 1):
        if not isinstance(m, dict):
            continue
        mid = m.get("move_id")
        if not isinstance(mid, int) or mid <= 0:
            continue
        cpp = m.get("current_pp", 0)
        mpp = m.get("max_pp", 0)
        scored = evaluate_move(
            mid, cpp, mpp,
            slot=m.get("slot", idx),
            user_type_ids=user_type_ids,
            opponent_type_ids=opp_type_ids,
        )
        scored_moves.append(scored)

    # Sort moves by expected_score descending
    usable_moves = [m for m in scored_moves if m["usable"]]
    usable_moves.sort(key=lambda x: x["expected_score"], reverse=True)

    best_move = usable_moves[0] if usable_moves else None

    # Decision logic:
    # 1. If capture evaluation recommends throw_ball:
    if (
        battle_kind == "wild"
        and isinstance(capture_eval, dict)
        and capture_eval.get("recommendation") == "catch_now"
        and capture_eval.get("catchable") is True
    ):
        recommended_action = {
            "type": "throw_ball",
            "actor": "player:0",
            "item_id": (capture_eval.get("available_balls") or [{}])[0].get("item_id", 4),
            "reason": capture_eval.get("reason", "Capture opportunity verified."),
        }
    # 2. If usable moves exist, recommend the best move:
    elif best_move is not None:
        reason = (
            f"使用「{best_move['name_zh']}」({best_move['name_en']}): "
            f"威力 {best_move['base_power'] or '变化'}, "
            f"克制倍率 {best_move['type_multiplier']}x"
            f"{' (本系加成 STAB)' if best_move['is_stab'] else ''}, "
            f"预估评分 {best_move['expected_score']}"
        )
        recommended_action = {
            "type": "use_move",
            "actor": "player:0",
            "move_slot": best_move["slot"],
            "move_id": best_move["move_id"],
            "move_name": best_move["name_zh"],
            "reason": reason,
        }
    # 3. If no usable moves and wild, run:
    elif battle_kind == "wild":
        recommended_action = {
            "type": "run",
            "actor": "player:0",
            "reason": "当前无可用招式 PP，在野外战斗中优先脱离",
        }
    else:
        recommended_action = {
            "type": "switch",
            "actor": "player:0",
            "party_slot": 2,
            "reason": "首发宝可梦招式 PP 耗尽，建议切换下一位出战宝可梦",
        }

    return {
        "format": "black2-battle-decision-plan/v1",
        "battle_kind": battle_kind,
        "player_combatant": {
            "species_id": player_species,
            "species_name": (player_pkm or {}).get("name"),
            "types": user_type_ids,
        },
        "opponent_combatant": {
            "species_id": opp_species,
            "species_name": (opp_pkm or {}).get("name"),
            "types": opp_type_ids,
        },
        "moves_evaluated": scored_moves,
        "best_move": best_move,
        "recommended_action": recommended_action,
    }
