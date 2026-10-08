"""Pokémon Black 2 - Overworld Entity Semantic Classifier.

Classifies raw ROM NPC entities (from a/1/2/6) and dynamic runtime actors into
explicit, high-level semantic categories:
- OVERWORLD_ITEM: Ground item balls (Pokéballs, item crates)
- NPC_TRAINER: Trainers that trigger battles or line-of-sight challenges
- NPC_TALKER: Peaceful talking inhabitants
- LEGENDARY_OVERWORLD: Static legendary Pokémon encounters
- DYNAMIC_OBSTACLE: Cut trees, Strength boulders, Rock Smash rocks
- STORY_ACTOR: Key story characters (Rival, Bianca, Cheren, Team Plasma)

Enriches entities with human-readable names, interaction triggers, reward
previews from offline Dex, and lifecycle flag mappings.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
from ..dex.store import DexStore

# Common Sprite ID mappings in Generation V (Black 2 / White 2)
# Ref: DSPRE / CTRMap / SWAN Overworld Sprite Tables
ITEM_BALL_SPRITES = {110, 111, 210, 211}  # 110: Standard Pokéball, 111: Item crate, 210: Hidden item marker
OBSTACLE_SPRITES = {
    97: "Cut Tree",
    98: "Strength Boulder",
    99: "Rock Smash Boulder",
}
LEGENDARY_SPRITES = {
    # Gen 5 legendaries overworld sprites
    490: "Cobalion",
    491: "Terrakion",
    492: "Virizion",
    493: "Tornadus",
    494: "Thundurus",
    495: "Reshiram",
    496: "Zekrom",
    497: "Landorus",
    498: "Kyurem",
}

# Research hint only.  A movement code is not sufficient evidence for trainer
# identity, facing, sight range, battle readiness, or defeated state.
LEGACY_TRAINER_MOVEMENT_HINTS = frozenset({1, 2, 3, 4})

# Evidence-backed labels for recurring story actors.  These are labels only;
# battle/defeat/interaction state remains decoded separately from RAM and ROM
# scripts.  Unknown residents deliberately keep an unresolved name status.
KNOWN_NPC_LABELS: dict[tuple[int, int, int], dict[str, str]] = {
    (439, 8, 97): {"name_zh": "阿戴克", "role_zh": "主线剧情封路角色"},
    (446, 4, 64): {"name_zh": "登山大叔", "role_zh": "主线剧情封路角色"},
}


class NPCClassifier:
    """Classifies raw ROM entities into rich semantic objects."""

    def __init__(self, dex_store: Optional[DexStore] = None) -> None:
        self.dex = dex_store or DexStore()

    def classify_entity(
        self,
        raw_npc: Dict[str, Any],
        zone_id: int,
        runtime_flags: Optional[Dict[int, bool]] = None,
    ) -> Dict[str, Any]:
        """Classify a single ROM entity or runtime actor record."""
        sprite_id = int(raw_npc.get("sprite_id") or raw_npc.get("model_id") or 0)
        script_id = int(raw_npc.get("script_id") or 0)
        flag_id = int(raw_npc.get("flag_id") or raw_npc.get("spawn_flag") or 0)
        movement_id = int(raw_npc.get("movement_id") or raw_npc.get("move_code") or 0)

        # 1. Check OVERWORLD_ITEM: true overworld item balls (Pokéball/crate models on ground).
        # Standard human NPC scripts (including delivery/event scripts >= 7000 or 10000) are never items.
        if sprite_id in ITEM_BALL_SPRITES or (7000 <= script_id < 8000 and flag_id > 0 and sprite_id in ITEM_BALL_SPRITES):
            return self._finalize(
                self._classify_item_ball(raw_npc, zone_id, sprite_id, script_id, flag_id, runtime_flags),
                raw_npc, zone_id, sprite_id, script_id, flag_id, movement_id,
            )

        # 2. Check DYNAMIC_OBSTACLE
        if sprite_id in OBSTACLE_SPRITES:
            return self._finalize(
                self._classify_obstacle(raw_npc, zone_id, sprite_id, script_id, flag_id),
                raw_npc, zone_id, sprite_id, script_id, flag_id, movement_id,
            )

        # 3. Check LEGENDARY_OVERWORLD
        if sprite_id in LEGENDARY_SPRITES:
            return self._finalize(
                self._classify_legendary(raw_npc, zone_id, sprite_id, script_id, flag_id),
                raw_npc, zone_id, sprite_id, script_id, flag_id, movement_id,
            )

        # 4. Movement patterns are retained as a low-confidence hypothesis.
        # An explicit runtime/story decoder may opt in to trainer semantics,
        # but movement_id + script_id alone must never do so.
        explicit_trainer = raw_npc.get("trainer_verified") is True or raw_npc.get("is_trainer") is True
        if explicit_trainer:
            return self._finalize(
                self._classify_trainer(raw_npc, zone_id, sprite_id, script_id, flag_id, movement_id),
                raw_npc, zone_id, sprite_id, script_id, flag_id, movement_id,
                trainer_verified=True,
            )

        # Default: peaceful talking inhabitant
        return self._finalize(
            self._classify_talker(raw_npc, zone_id, sprite_id, script_id, flag_id, movement_id),
            raw_npc, zone_id, sprite_id, script_id, flag_id, movement_id,
        )

    @staticmethod
    def _runtime_layers(raw_npc: Dict[str, Any]) -> Dict[str, Any]:
        runtime = raw_npc.get("runtime") if isinstance(raw_npc.get("runtime"), dict) else {}
        actor_id = runtime.get("actor_id", raw_npc.get("runtime_actor_uid", raw_npc.get("actor_uid")))
        grid = runtime.get("grid", raw_npc.get("grid"))
        return {
            "bound": bool(runtime.get("bound", actor_id is not None)),
            "actor_id": actor_id,
            "grid": grid if isinstance(grid, dict) else None,
            "facing": runtime.get("facing", raw_npc.get("facing")),
            "present": runtime.get("present") if "present" in runtime else None,
            "frame": runtime.get("frame", raw_npc.get("frame")),
        }

    def _finalize(
        self,
        result: Dict[str, Any],
        raw_npc: Dict[str, Any],
        zone_id: int,
        sprite_id: int,
        script_id: int,
        flag_id: int,
        movement_id: int,
        *,
        trainer_verified: bool = False,
    ) -> Dict[str, Any]:
        """Add evidence-separated identity/ROM/runtime/semantic layers."""
        sight_raw = raw_npc.get("sight_raw")
        identity = {
            "zone_id": zone_id,
            "rom_entity_id": raw_npc.get("id"),
            "record_index": raw_npc.get("record_index"),
            "script_id": script_id,
            "spawn_flag": flag_id,
            "sprite_id": sprite_id,
        }
        rom = {
            "movement_id": movement_id,
            "movement2_raw": raw_npc.get("movement2_raw"),
            "direction_raw": raw_npc.get("direction_raw", raw_npc.get("facing_id")),
            "sight_raw": sight_raw,
            "leash_lr_raw": raw_npc.get("leash_lr_raw"),
            "leash_ud_raw": raw_npc.get("leash_ud_raw"),
            "spawn_grid": {
                "x": raw_npc.get("x"), "y": raw_npc.get("y"), "z": raw_npc.get("z"),
            },
            "raw_hex": raw_npc.get("raw_hex"),
        }
        runtime = self._runtime_layers(raw_npc)
        legacy_kind = result.get("semantic_kind", "NPC_UNRESOLVED")
        unresolved_npc = legacy_kind in {"NPC_TRAINER", "NPC_TALKER"} and not trainer_verified
        semantic_kind = "NPC_UNRESOLVED" if unresolved_npc else legacy_kind
        trainer = {
            "is_trainer": True if trainer_verified else None,
            "is_defeated": None,
            "will_battle": True if trainer_verified and raw_npc.get("will_battle_verified") is True else None,
            "sight_range": sight_raw,
            "sight_range_status": "rom_raw_not_yet_semantically_verified",
            "hypothesis": (
                {"hypothesis": "trainer_movement_candidate", "confidence": "low", "basis": ["movement_id heuristic"]}
                if movement_id in LEGACY_TRAINER_MOVEMENT_HINTS else None
            ),
        }
        result["identity"] = identity
        result["rom"] = rom
        result["runtime"] = runtime
        result["semantics"] = {
            "kind": semantic_kind,
            "trainer": trainer,
            "can_interact_now": None,
            "interaction_mode_candidate": "action_button",
            "confidence": "candidate" if not unresolved_npc else "unresolved",
            "evidence": {
                "source": "static ROM entity record",
                "runtime_visual_verified": False,
            },
        }
        result["semantic_kind"] = semantic_kind
        if isinstance(result.get("interaction"), dict):
            result["interaction"]["can_interact_now"] = None
            result["interaction"]["interaction_mode_candidate"] = "action_button"
            if unresolved_npc:
                result["interaction"]["sight_range"] = sight_raw
                result["interaction"]["facing_direction"] = None
        result["trainer"] = trainer
        if sight_raw is not None and int(sight_raw or 0) > 0:
            result["candidate_role"] = "NPC_TRAINER_CANDIDATE"
            result["candidate_role_status"] = "sight_range_present_but_battle_binding_unverified"
        else:
            result["candidate_role"] = None
            result["candidate_role_status"] = "not_nominated"
        known = KNOWN_NPC_LABELS.get((int(zone_id), int(script_id), int(sprite_id)))
        if known:
            result["name"] = known["name_zh"]
            result["name_status"] = "verified_registry"
            result["role_zh"] = known["role_zh"]
        else:
            result["name_status"] = "unresolved_rom_name"
            result["role_zh"] = result.get("category_label")
        if result.get("semantics_confidence") == "verified_rule":
            result["semantics_confidence"] = "candidate_registry"
        result["evidence"] = {
            "source": "static sprite/model registry" if legacy_kind != "NPC_UNRESOLVED" else "static ROM entity record",
            "rom_profile": "IREJ rev.1",
            "runtime_visual_verified": False,
        }
        return result

    def _classify_item_ball(
        self,
        raw_npc: Dict[str, Any],
        zone_id: int,
        sprite_id: int,
        script_id: int,
        flag_id: int,
        runtime_flags: Optional[Dict[int, bool]],
    ) -> Dict[str, Any]:
        # Item Ball naming & item preview lookup
        item_info = self._resolve_item_reward(script_id, flag_id)
        name_label = f"地面道具球 ({item_info['name_zh']})" if item_info.get("name_zh") else "地面道具球 (待拾取)"

        is_collected = None
        if runtime_flags is not None and flag_id in runtime_flags:
            is_collected = runtime_flags[flag_id]

        return {
            "semantic_kind": "OVERWORLD_ITEM",
            "name": name_label,
            "category_label": "地面拾取物",
            "interaction": {
                "type": "pickup",
                "trigger_mode": "action_button",
                "can_interact_now": True,
                "reward": item_info,
            },
            "trainer": None,
            "lifecycle": {
                "flag_id": flag_id,
                "is_collected": is_collected,
                "availability": "collected" if is_collected is True else "present",
            },
            "semantics_confidence": "verified_rule",
        }

    def _classify_trainer(
        self,
        raw_npc: Dict[str, Any],
        zone_id: int,
        sprite_id: int,
        script_id: int,
        flag_id: int,
        movement_id: int,
    ) -> Dict[str, Any]:
        return {
            "semantic_kind": "NPC_TRAINER",
            "name": f"训练家 (Script {script_id})",
            "category_label": "对战训练家",
            "interaction": {
                "type": "battle",
                "trigger_mode": "sight_or_button",
                "sight_range": raw_npc.get("sight_raw"),
                "facing_direction": None,
                "can_interact_now": None,
                "reward": None,
            },
            "trainer": {
                "will_battle": None,
                "is_defeated": None,
                "sight_range": raw_npc.get("sight_raw"),
            },
            "lifecycle": {
                "flag_id": flag_id,
                "availability": "present",
            },
            "semantics_confidence": "candidate",
        }

    def _classify_talker(
        self,
        raw_npc: Dict[str, Any],
        zone_id: int,
        sprite_id: int,
        script_id: int,
        flag_id: int,
        movement_id: int,
    ) -> Dict[str, Any]:
        return {
            "semantic_kind": "NPC_TALKER",
            "name": f"居民 (Sprite {sprite_id})",
            "category_label": "对话居民",
            "interaction": {
                "type": "talk",
                "trigger_mode": "action_button",
                "sight_range": 0,
                "can_interact_now": None,
                "reward": None,
            },
            "trainer": None,
            "lifecycle": {
                "flag_id": flag_id,
                "availability": "present",
            },
            "semantics_confidence": "candidate",
        }

    def _classify_obstacle(
        self,
        raw_npc: Dict[str, Any],
        zone_id: int,
        sprite_id: int,
        script_id: int,
        flag_id: int,
    ) -> Dict[str, Any]:
        obs_name = OBSTACLE_SPRITES.get(sprite_id, "Obstacle")
        return {
            "semantic_kind": "DYNAMIC_OBSTACLE",
            "name": f"障碍物 ({obs_name})",
            "category_label": "秘传技/场景障碍",
            "interaction": {
                "type": "obstacle",
                "trigger_mode": "action_button",
                "sight_range": 0,
                "can_interact_now": None,
                "reward": None,
            },
            "trainer": None,
            "lifecycle": {
                "flag_id": flag_id,
                "availability": "present",
            },
            "semantics_confidence": "candidate_registry",
        }

    def _classify_legendary(
        self,
        raw_npc: Dict[str, Any],
        zone_id: int,
        sprite_id: int,
        script_id: int,
        flag_id: int,
    ) -> Dict[str, Any]:
        mon_name = LEGENDARY_SPRITES.get(sprite_id, "Legendary Pokémon")
        return {
            "semantic_kind": "LEGENDARY_OVERWORLD",
            "name": f"定点神兽 ({mon_name})",
            "category_label": "传说宝可梦",
            "interaction": {
                "type": "battle",
                "trigger_mode": "action_button",
                "sight_range": 0,
                "can_interact_now": None,
                "reward": None,
            },
            "trainer": None,
            "lifecycle": {
                "flag_id": flag_id,
                "availability": "present",
            },
            "semantics_confidence": "candidate_registry",
        }

    def _resolve_item_reward(self, script_id: int, flag_id: int) -> Dict[str, Any]:
        """Resolve item ball reward metadata using RomItemCatalog and DexStore."""
        try:
            from .item_catalog import default_item_catalog
            catalog = default_item_catalog()
            resolved = catalog.resolve_field_item(script_id, flag_id)
            if resolved and resolved.get("status") == "resolved":
                return resolved
        except Exception:
            pass
        return {
            "item_id": None,
            "name_zh": "道具球",
            "name_en": "Item Ball",
            "count": 1,
            "status": "candidate",
        }


# Singleton instance
npc_classifier = NPCClassifier()
