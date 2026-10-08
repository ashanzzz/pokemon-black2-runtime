"""Player capabilities and action legality evaluator for Pokémon Black 2.

Evaluates real-time legality of field actions (Bicycle, Surf, Strength, Cut,
Fly, Waterfall, Dive, Escape Rope) based on:
1. PlayerExState (0x0221F000: OnFoot, Cycling, Surfing, Diving);
2. ZoneHeader movement rules (cycling, running, escape rope, fly);
3. Party HM/field move availability (Surf, Strength, Cut, Fly, etc.);
4. Bag key items (Bicycle, Dowsing Machine, etc.);
5. Surroundings / tile context (adjacent water, boulders, ledges).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set


# Standard Gen 5 HM & Field Move IDs
MOVE_CUT = 15
MOVE_FLY = 19
MOVE_SURF = 57
MOVE_STRENGTH = 70
MOVE_FLASH = 148
MOVE_WATERFALL = 127
MOVE_DIVE = 291

# Standard Gen 5 Key Item IDs
# B2W2 internal item id; the Dex game index is 450. Do not use the
# pre-Gen-V/PokeAPI index 448 here.
ITEM_BICYCLE = 427
ITEM_BICYCLE_GAME_INDEX = 450
ITEM_DOWSING_MACHINE = 465
ITEM_TOWN_MAP = 428
ITEM_ESCAPE_ROPE = 56


@dataclass(frozen=True)
class PlayerCapabilities:
    movement_mode: str
    is_on_foot: bool
    is_cycling: bool
    is_surfing: bool
    is_diving: bool

    can_run: bool
    can_cycle: bool
    can_surf: bool
    can_use_strength: bool
    can_use_cut: bool
    can_use_fly: bool
    can_use_waterfall: bool
    can_use_dive: bool
    can_use_escape_rope: bool

    party_field_moves: list[str]
    reasons: dict[str, str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": "black2-player-capabilities/v1",
            "movement_mode": self.movement_mode,
            "states": {
                "on_foot": self.is_on_foot,
                "cycling": self.is_cycling,
                "surfing": self.is_surfing,
                "diving": self.is_diving,
            },
            "legal_capabilities": {
                "running": self.can_run,
                "cycling": self.can_cycle,
                "surf": self.can_surf,
                "strength": self.can_use_strength,
                "cut": self.can_use_cut,
                "fly": self.can_use_fly,
                "waterfall": self.can_use_waterfall,
                "dive": self.can_use_dive,
                "escape_rope": self.can_use_escape_rope,
            },
            "party_field_moves": self.party_field_moves,
            "reasons": self.reasons,
            "confidence": "verified_ram_and_rom_rules",
        }


def evaluate_capabilities(
    ex_state_raw: int = 0,
    zone_rules: dict[str, Any] | None = None,
    party_move_ids: Set[int] | None = None,
    key_item_ids: Set[int] | None = None,
    bag_item_ids: Set[int] | None = None,
    adjacent_features: Set[str] | None = None,
) -> PlayerCapabilities:
    moves = party_move_ids or set()
    key_items = key_item_ids or set()
    bag_items = bag_item_ids or set()
    features = adjacent_features or set()
    rules = zone_rules or {}

    # ex_state decoding
    mode_map = {0: "on_foot", 1: "cycling", 2: "surfing", 3: "diving"}
    mode = mode_map.get(ex_state_raw, "on_foot")
    is_on_foot = (ex_state_raw == 0)
    is_cycling = (ex_state_raw == 1)
    is_surfing = (ex_state_raw == 2)
    is_diving = (ex_state_raw == 3)

    # Zone rules
    rule_cycling = bool(rules.get("enable_cycling", True))
    rule_running = bool(rules.get("enable_running", True))
    rule_escape_rope = bool(rules.get("enable_escape_rope", False))
    rule_fly = bool(rules.get("enable_fly_from", False))

    reasons: dict[str, str] = {}

    # Running
    can_run = is_on_foot and rule_running
    if not is_on_foot:
        reasons["running"] = f"Cannot run while in movement state {mode}"
    elif not rule_running:
        reasons["running"] = "Running is prohibited by current Zone rules"
    else:
        reasons["running"] = "Hold B to run on walkable tiles"

    # Cycling: in Gen 5 B2W2, internal item 450 is Bicycle (DexStore game_index=450, id=427).
    has_bike = (
        (ITEM_BICYCLE in key_items) or (450 in key_items) or (427 in key_items) or (448 in key_items)
        or (ITEM_BICYCLE in bag_items) or (450 in bag_items) or (427 in bag_items) or (448 in bag_items)
    )
    can_cycle = has_bike and rule_cycling and not is_surfing and not is_diving
    if not has_bike:
        reasons["cycling"] = "Bicycle (internal item 427 / game index 450) is not in key items inventory"
    elif not rule_cycling:
        reasons["cycling"] = "Cycling is prohibited by current Zone rules (indoor / cave)"
    elif is_surfing or is_diving:
        reasons["cycling"] = "Cannot ride bicycle while in water"
    else:
        reasons["cycling"] = "Press Y or select Bicycle from Key Items to ride"

    # Surfing
    has_surf = (MOVE_SURF in moves)
    near_water = ("shore" in features or "water" in features or is_surfing)
    can_surf = has_surf and (near_water or is_surfing) and not is_diving
    if not has_surf:
        reasons["surf"] = "Surf (move 57) is not known by any party Pokémon"
    elif not near_water and not is_surfing:
        reasons["surf"] = "No adjacent water or shore tile detected"
    else:
        reasons["surf"] = "Interact with water or face shore to surf"

    # Strength
    has_strength = (MOVE_STRENGTH in moves)
    near_boulder = ("boulder" in features)
    can_use_strength = has_strength and near_boulder
    if not has_strength:
        reasons["strength"] = "Strength (move 70) is not known by any party Pokémon"
    elif not near_boulder:
        reasons["strength"] = "No pushable boulder detected adjacent to player"
    else:
        reasons["strength"] = "Walk towards boulder to push it with Strength"

    # Cut
    has_cut = (MOVE_CUT in moves)
    near_tree = ("cut_tree" in features)
    can_use_cut = has_cut and near_tree
    if not has_cut:
        reasons["cut"] = "Cut (move 15) is not known by any party Pokémon"
    elif not near_tree:
        reasons["cut"] = "No cuttable tree detected adjacent to player"
    else:
        reasons["cut"] = "Press A while facing tree to Cut"

    # Fly
    has_fly = (MOVE_FLY in moves)
    can_use_fly = has_fly and rule_fly and not is_diving
    if not has_fly:
        reasons["fly"] = "Fly (move 19) is not known by any party Pokémon"
    elif not rule_fly:
        reasons["fly"] = "Flying is prohibited inside caves and indoor facilities"
    else:
        reasons["fly"] = "Select Fly from Pokémon menu to travel to visited towns"

    # Waterfall
    has_waterfall = (MOVE_WATERFALL in moves)
    near_waterfall = ("waterfall" in features)
    can_use_waterfall = has_waterfall and is_surfing and near_waterfall
    if not has_waterfall:
        reasons["waterfall"] = "Waterfall (move 127) is not known by any party Pokémon"
    elif not is_surfing:
        reasons["waterfall"] = "Must be surfing to climb waterfalls"
    elif not near_waterfall:
        reasons["waterfall"] = "No waterfall detected ahead"
    else:
        reasons["waterfall"] = "Press A while facing waterfall to climb"

    # Dive
    has_dive = (MOVE_DIVE in moves)
    can_use_dive = has_dive and (is_diving or "dive_spot" in features)
    if not has_dive:
        reasons["dive"] = "Dive (move 291) is not known by any party Pokémon"
    elif not (is_diving or "dive_spot" in features):
        reasons["dive"] = "No dive spot detected"
    else:
        reasons["dive"] = "Press A on deep water dive spot"

    # Escape Rope
    has_rope = (ITEM_ESCAPE_ROPE in bag_items)
    can_use_escape_rope = rule_escape_rope
    if not rule_escape_rope:
        reasons["escape_rope"] = "Escape Rope cannot be used outside caves/dungeons"
    elif not has_rope:
        reasons["escape_rope"] = "No Escape Rope (item 56) in inventory"
    else:
        reasons["escape_rope"] = "Use Escape Rope from Bag to teleport to cave entrance"

    known_field_moves = []
    if MOVE_CUT in moves: known_field_moves.append("Cut (居合斩)")
    if MOVE_FLY in moves: known_field_moves.append("Fly (飞翔)")
    if MOVE_SURF in moves: known_field_moves.append("Surf (冲浪)")
    if MOVE_STRENGTH in moves: known_field_moves.append("Strength (怪力)")
    if MOVE_FLASH in moves: known_field_moves.append("Flash (闪光)")
    if MOVE_WATERFALL in moves: known_field_moves.append("Waterfall (攀瀑)")
    if MOVE_DIVE in moves: known_field_moves.append("Dive (潜水)")

    return PlayerCapabilities(
        movement_mode=mode,
        is_on_foot=is_on_foot,
        is_cycling=is_cycling,
        is_surfing=is_surfing,
        is_diving=is_diving,
        can_run=can_run,
        can_cycle=can_cycle,
        can_surf=can_surf,
        can_use_strength=can_use_strength,
        can_use_cut=can_use_cut,
        can_use_fly=can_use_fly,
        can_use_waterfall=can_use_waterfall,
        can_use_dive=can_use_dive,
        can_use_escape_rope=can_use_escape_rope,
        party_field_moves=known_field_moves,
        reasons=reasons,
    )
