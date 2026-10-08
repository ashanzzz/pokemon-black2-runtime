"""Gen 5 battle AI move evaluator and decision planner for Pokémon Black 2.

Comprehensive Gen V calculation oracle:
- Gen V physical/special damage formulas with min/max random variance (0.85 .. 1.00)
- Stat stage scaling (-6 .. +6)
- Weather modifiers (Rain Water 1.5x / Fire 0.5x, Sun Fire 1.5x / Water 0.5x)
- Status ailments (Burn physical 0.5x, Paralysis speed 0.25x)
- Speed tier calculation and move priority (-6 .. +5)
- Lethal damage knockout assessment
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional
from ..dex.store import DexStore

_shared_dex: DexStore | None = None


def _get_dex() -> DexStore:
    global _shared_dex
    if _shared_dex is None:
        _shared_dex = DexStore()
    return _shared_dex


def stage_multiplier(stage: int) -> float:
    """Gen V stat stage modifier for Attack, Defense, Sp. Atk, Sp. Def, Speed."""
    s = max(-6, min(6, int(stage or 0)))
    if s >= 0:
        return (2 + s) / 2.0
    return 2.0 / (2 - s)


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


def compute_gen5_damage(
    attacker_level: int,
    base_power: int,
    attacker_stat: int,
    defender_stat: int,
    *,
    is_stab: bool = False,
    type_mult: float = 1.0,
    weather: str | None = None,
    move_type_id: int = 1,
    is_burned: bool = False,
    damage_class: str = "physical",
) -> dict[str, Any]:
    """Compute Gen V standard damage roll [0.85, 1.00] with weather and burn modifiers."""
    if base_power <= 0 or type_mult == 0.0 or defender_stat <= 0:
        return {
            "min": 0, "max": 0, "avg": 0,
            "weather_factor": 1.0, "burn_factor": 1.0, "stab_factor": 1.0,
        }

    lvl = max(1, attacker_level)
    atk = max(1, attacker_stat)
    dfn = max(1, defender_stat)

    # Base damage
    level_factor = math.floor(2 * lvl / 5) + 2
    base_dmg = math.floor(math.floor(level_factor * base_power * atk / dfn) / 50) + 2

    # Weather modifier: Water=11, Fire=10
    weather_factor = 1.0
    if weather == "rain":
        if move_type_id == 11:
            weather_factor = 1.5
        elif move_type_id == 10:
            weather_factor = 0.5
    elif weather == "sun":
        if move_type_id == 10:
            weather_factor = 1.5
        elif move_type_id == 11:
            weather_factor = 0.5

    # Burn penalty (physical moves do half damage)
    burn_factor = 0.5 if (is_burned and damage_class == "physical") else 1.0

    # STAB modifier
    stab_factor = 1.5 if is_stab else 1.0

    # Multiplied damage before random roll
    dmg_pre = base_dmg * weather_factor
    dmg_pre = math.floor(dmg_pre)
    dmg_pre = dmg_pre * burn_factor
    dmg_pre = math.floor(dmg_pre)

    dmg_max = math.floor(dmg_pre * stab_factor * type_mult)
    dmg_min = math.floor(dmg_max * 0.85)
    dmg_avg = math.floor((dmg_min + dmg_max) / 2)

    return {
        "min": max(1 if type_mult > 0 else 0, dmg_min),
        "max": max(1 if type_mult > 0 else 0, dmg_max),
        "avg": max(1 if type_mult > 0 else 0, dmg_avg),
        "weather_factor": weather_factor,
        "burn_factor": burn_factor,
        "stab_factor": stab_factor,
    }


def compute_turn_speed(
    user_base_speed: int,
    user_speed_stage: int,
    user_status: str | None,
    opp_base_speed: int,
    opp_speed_stage: int,
    opp_status: str | None,
    move_priority: int,
) -> dict[str, Any]:
    """Determine effective speed and priority turn order."""
    user_eff_speed = user_base_speed * stage_multiplier(user_speed_stage)
    if user_status == "paralysis":
        user_eff_speed *= 0.25

    opp_eff_speed = opp_base_speed * stage_multiplier(opp_speed_stage)
    if opp_status == "paralysis":
        opp_eff_speed *= 0.25

    # Positive priority always goes first; negative priority goes last
    if move_priority > 0:
        moves_first = True
    elif move_priority < 0:
        moves_first = False
    else:
        moves_first = user_eff_speed >= opp_eff_speed

    return {
        "user_speed": round(user_eff_speed, 1),
        "opp_speed": round(opp_eff_speed, 1),
        "priority": move_priority,
        "user_moves_first": moves_first,
    }


def evaluate_move(
    move_id: int,
    current_pp: int,
    max_pp: int,
    *,
    slot: int,
    user_level: int = 50,
    user_stats: dict[str, Any] | None = None,
    user_stages: dict[str, Any] | None = None,
    user_status: str | None = None,
    user_type_ids: list[int] | None = None,
    opp_hp: dict[str, Any] | None = None,
    opp_stats: dict[str, Any] | None = None,
    opp_stages: dict[str, Any] | None = None,
    opp_status: str | None = None,
    opp_type_ids: list[int] | None = None,
    weather: str | None = None,
) -> dict[str, Any]:
    """Score a single move against the active opponent with full Gen V mechanics."""
    dex = _get_dex()
    move_data = dex.get("moves", move_id) or {}
    move_type = move_data.get("type", {})
    move_type_id = move_type.get("id") or 1
    move_name_zh = (move_data.get("names") or {}).get("zh-Hans") or move_data.get("identifier") or f"Move {move_id}"
    move_name_en = (move_data.get("names") or {}).get("en") or move_data.get("identifier") or f"Move {move_id}"

    base_power = move_data.get("power") or 0
    accuracy = move_data.get("accuracy") or 100
    damage_class = (move_data.get("damage_class") or {}).get("identifier", "status")
    priority = move_data.get("priority") or 0

    opp_types = opp_type_ids or []
    usr_types = user_type_ids or []

    # Type effectiveness
    type_mult = compute_type_effectiveness(move_type_id, opp_types) if damage_class != "status" else 1.0

    # STAB: 1.5x in Gen 5
    is_stab = move_type_id in usr_types

    u_stats = user_stats or {}
    u_stages = user_stages or {}
    o_stats = opp_stats or {}
    o_stages = opp_stages or {}
    o_hp = opp_hp or {}

    # Damage computation
    if damage_class == "physical":
        atk = int(u_stats.get("attack", 50)) * stage_multiplier(u_stages.get("attack", 0))
        dfn = int(o_stats.get("defense", 50)) * stage_multiplier(o_stages.get("defense", 0))
    elif damage_class == "special":
        atk = int(u_stats.get("special_attack", 50)) * stage_multiplier(u_stages.get("special_attack", 0))
        dfn = int(o_stats.get("special_defense", 50)) * stage_multiplier(o_stages.get("special_defense", 0))
    else:
        atk = 1
        dfn = 1

    dmg_calc = compute_gen5_damage(
        attacker_level=user_level,
        base_power=base_power,
        attacker_stat=int(atk),
        defender_stat=int(dfn),
        is_stab=is_stab,
        type_mult=type_mult,
        weather=weather,
        move_type_id=move_type_id,
        is_burned=(user_status == "burn"),
        damage_class=damage_class,
    )

    opp_cur_hp = o_hp.get("current", 1) or 1
    opp_max_hp = o_hp.get("max", 1) or 1
    lethal = dmg_calc["min"] >= opp_cur_hp
    possible_lethal = dmg_calc["max"] >= opp_cur_hp

    # Speed & Priority check
    user_spd = int(u_stats.get("speed", 50))
    opp_spd = int(o_stats.get("speed", 50))
    speed_info = compute_turn_speed(
        user_base_speed=user_spd,
        user_speed_stage=u_stages.get("speed", 0),
        user_status=user_status,
        opp_base_speed=opp_spd,
        opp_speed_stage=o_stages.get("speed", 0),
        opp_status=opp_status,
        move_priority=priority,
    )

    usable = current_pp > 0
    # Expected score based on average damage and accuracy
    expected_score = round(dmg_calc["avg"] * (accuracy / 100.0), 2) if usable else 0.0

    # Verdict
    if not usable:
        verdict = "no_pp"
    elif type_mult == 0.0:
        verdict = "immune"
    elif lethal:
        verdict = "guaranteed_knockout"
    elif possible_lethal:
        verdict = "possible_knockout"
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
        "priority": priority,
        "current_pp": current_pp,
        "max_pp": max_pp,
        "usable": usable,
        "type_multiplier": type_mult,
        "is_stab": is_stab,
        "damage": {
            "min": dmg_calc["min"],
            "max": dmg_calc["max"],
            "avg": dmg_calc["avg"],
            "percent_min": round(dmg_calc["min"] / opp_max_hp * 100, 1),
            "percent_max": round(dmg_calc["max"] / opp_max_hp * 100, 1),
            "lethal": lethal,
            "possible_lethal": possible_lethal,
        },
        "turn_speed": speed_info,
        "expected_score": expected_score,
        "verdict": verdict,
    }


def plan_battle_decision(
    *,
    player_mon: dict[str, Any] | None = None,
    opponent_mon: dict[str, Any] | None = None,
    battle_kind: str = "wild",
    capture_eval: dict[str, Any] | None = None,
    weather: str | None = None,
) -> dict[str, Any]:
    """Formulate an AI battle decision recommendation from live combatant facts."""
    dex = _get_dex()

    player_species = (player_mon or {}).get("species") or (player_mon or {}).get("species_id")
    opp_species = (opponent_mon or {}).get("species") or (opponent_mon or {}).get("species_id")

    player_pkm = dex.get("pokemon", player_species) if isinstance(player_species, int) else None
    opp_pkm = dex.get("pokemon", opp_species) if isinstance(opp_species, int) else None

    user_type_ids = [t["id"] for t in (player_pkm.get("types") or []) if "id" in t] if player_pkm else []
    opp_type_ids = [t["id"] for t in (opp_pkm.get("types") or []) if "id" in t] if opp_pkm else []

    user_level = int((player_mon or {}).get("level", 50) or 50)
    user_stats = (player_mon or {}).get("stats") or {}
    user_stages = (player_mon or {}).get("stages") or (player_mon or {}).get("stat_stages") or {}
    user_status = ((player_mon or {}).get("status") or {}).get("major")

    opp_hp = (opponent_mon or {}).get("hp") or {}
    opp_stats = (opponent_mon or {}).get("stats") or {}
    opp_stages = (opponent_mon or {}).get("stages") or (opponent_mon or {}).get("stat_stages") or {}
    opp_status = ((opponent_mon or {}).get("status") or {}).get("major")

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
            user_level=user_level,
            user_stats=user_stats,
            user_stages=user_stages,
            user_status=user_status,
            user_type_ids=user_type_ids,
            opp_hp=opp_hp,
            opp_stats=opp_stats,
            opp_stages=opp_stages,
            opp_status=opp_status,
            opp_type_ids=opp_type_ids,
            weather=weather,
        )
        scored_moves.append(scored)

    # Sort moves: prioritize lethal knockout moves, then expected_score descending
    usable_moves = [m for m in scored_moves if m["usable"]]
    usable_moves.sort(key=lambda x: (x["damage"]["lethal"], x["damage"]["possible_lethal"], x["expected_score"]), reverse=True)

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
            f"威力 {best_move['base_power']}, 克制 {best_move['type_multiplier']}x, "
            f"预估伤害 {best_move['damage']['min']}~{best_move['damage']['max']} ({best_move['damage']['percent_min']}%~{best_move['damage']['percent_max']}%), "
            f"先手: {best_move['turn_speed']['user_moves_first']}"
        )
        recommended_action = {
            "type": "use_move",
            "actor": "player:0",
            "move_slot": best_move["slot"],
            "move_id": best_move["move_id"],
            "move_name": best_move["name_zh"],
            "reason": reason,
        }
    # 3. Fallback: Flee if wild, else struggle/unresolved
    elif battle_kind == "wild":
        recommended_action = {
            "type": "run",
            "actor": "player:0",
            "reason": "PP exhausted for all moves. Fleeing wild battle.",
        }
    else:
        recommended_action = {
            "type": "use_move",
            "actor": "player:0",
            "move_slot": 1,
            "move_id": 165,  # Struggle
            "move_name": "拼命 (Struggle)",
            "reason": "All move PP exhausted in trainer battle. Using Struggle.",
        }

    return {
        "status": "ready" if best_move else "exhausted",
        "best_move": best_move,
        "recommended_action": recommended_action,
        "moves_evaluated": scored_moves,
    }
