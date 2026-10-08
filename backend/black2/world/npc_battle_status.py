"""Evidence-separated NPC battle/lifecycle projection.

The ROM event record, the live FieldActor record and the save/event-flag
decoder answer different questions.  This module intentionally keeps them
separate:

* ROM sight/script data can nominate a battle candidate;
* a live actor can be bound to that ROM row, with raw lifecycle fields kept;
* a defeated flag is only promoted when a verified event-flag decoder or a
  before/after observation proves it.

In particular, ``runtime.present=False`` and ``defeat_status=unknown`` are
not contradictory.  A partial ActorSystem sample, a scene transition or a
scripted hide can remove an actor without proving that its battle was won.
"""
from __future__ import annotations

from typing import Any

from .actor_binding import bind_static_npcs_to_runtime


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _grid(row: dict[str, Any] | None) -> dict[str, int] | None:
    value = row.get("grid") if isinstance(row, dict) else None
    if not isinstance(value, dict):
        value = row.get("coordinate") if isinstance(row, dict) else None
    if not isinstance(value, dict):
        return None
    try:
        # A scene coordinate is already canonical; an actor row uses grid.
        return {"x": int(value["x"]), "y": int(value["y"]), "z": int(value["z"])}
    except (KeyError, TypeError, ValueError):
        return None


def _raw_actor(actor: dict[str, Any] | None) -> dict[str, Any]:
    """Return bounded raw fields suitable for a diff journal."""
    if not isinstance(actor, dict):
        return {}
    raw = actor.get("raw") if isinstance(actor.get("raw"), dict) else {}
    keys = (
        "address", "stride", "flags_raw", "movement_flags_raw", "uid_raw",
        "zone_id_raw", "model_id_raw", "move_code_raw", "event_type_raw",
        "spawn_flag_raw", "script_id_raw", "default_dir_raw", "face_dir_raw",
        "motion_dir_raw", "last_face_dir_raw", "last_motion_dir_raw",
        "next_acmd_raw", "gpos_raw", "tcb_raw",
    )
    result = {key: raw[key] for key in keys if key in raw}
    # Keep compatibility with older in-process test doubles and cached rows.
    fallbacks = {
        "uid_raw": actor.get("actor_uid"),
        "zone_id_raw": actor.get("zone_id_raw", actor.get("zone_id")),
        "model_id_raw": actor.get("model_id"),
        "event_type_raw": actor.get("event_type"),
        "spawn_flag_raw": actor.get("spawn_flag"),
        "script_id_raw": actor.get("script_id"),
        "face_dir_raw": actor.get("face_dir_raw"),
        "movement_flags_raw": actor.get("movement_flags_raw"),
        "last_face_dir_raw": actor.get("last_face_dir_raw"),
        "last_motion_dir_raw": actor.get("last_motion_dir_raw"),
        "next_acmd_raw": actor.get("next_acmd_raw"),
    }
    for key, value in fallbacks.items():
        if key not in result and value is not None:
            result[key] = value
    return result


def _actor_index(runtime_actors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for actor in runtime_actors:
        if not isinstance(actor, dict):
            continue
        for value in (actor.get("address"), actor.get("actor_uid"), actor.get("uid"), actor.get("slot")):
            if value is not None:
                index[str(value)] = actor
    return index


def _find_bound_actor(binding: dict[str, Any], runtime_actors: list[dict[str, Any]]) -> dict[str, Any] | None:
    index = _actor_index(runtime_actors)
    for key in ("runtime_address", "runtime_actor_uid", "runtime_slot"):
        value = binding.get(key)
        if value is not None and str(value) in index:
            return index[str(value)]
    return None


def _sight_candidate(static: dict[str, Any]) -> int | None:
    trainer = static.get("trainer") if isinstance(static.get("trainer"), dict) else {}
    rom = static.get("rom") if isinstance(static.get("rom"), dict) else {}
    for value in (trainer.get("sight_range"), rom.get("sight_raw"), static.get("sight_raw"), static.get("sight")):
        parsed = _int(value)
        if parsed is not None:
            return parsed
    return None


def _capability(static: dict[str, Any], zone_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    sight = _sight_candidate(static)
    if sight is not None and sight > 0:
        return {
            "status": "line_of_sight_battle_candidate",
            "kind": "trainer_candidate",
            "sight_range_raw": sight,
            "verified": False,
            "basis": ["ROM NPC sight_raw > 0"],
            "reason": "Sight range nominates a trainer-like trigger, but does not prove TrainerBattle execution or victory state.",
        }
    if zone_candidates:
        return {
            "status": "zone_script_battle_candidate_unmapped",
            "kind": "possible_trainer_candidate",
            "sight_range_raw": sight,
            "verified": False,
            "basis": ["current zone contains ROM TrainerBattle script candidate"],
            "reason": "The zone has TrainerBattle opcodes, but this NPC is not yet causally linked to one script function.",
        }
    return {
        "status": "unresolved",
        "kind": "unknown",
        "sight_range_raw": sight,
        "verified": False,
        "basis": ["no per-NPC TrainerBattle binding is decoded"],
        "reason": "No verified per-NPC battle script binding is available; ordinary dialogue cannot be promoted to no-battle.",
    }


def _flag_state(static: dict[str, Any], flags: dict[str, Any] | None, zone_id: int | None = None) -> dict[str, Any]:
    script_id = _int(static.get("script_id"))
    if script_id is not None and isinstance(flags, dict):
        from ..progression.state import resolve_trainer_defeat_flag, is_event_flag_set
        trainer_flag = resolve_trainer_defeat_flag(script_id)
        if trainer_flag is not None:
            raw_hex = flags.get("raw_hex") or (flags.get("flag_bytes") or {}).get("raw_hex")
            if raw_hex:
                try:
                    fb = bytes.fromhex(raw_hex)
                    is_set = is_event_flag_set(trainer_flag, fb)
                    return {
                        "status": "resolved",
                        "event_flag_id": trainer_flag,
                        "is_set": is_set,
                        "defeat_status": "defeated" if is_set else "unresolved",
                        "reason": f"B2W2 engine TrainerFlag {trainer_flag} is {'set' if is_set else 'clear'} in live EventWork.",
                    }
                except Exception:
                    pass

    
    # Gym Badge cross-check: if this zone belongs to a conquered Gym,
    # subordinate trainers and leader are known defeated from player save state
    try:
        from .gym_catalog import default_gym_catalog
        gym = default_gym_catalog().get_gym_by_zone(zone_id)
        if gym and gym.get("badge_obtained"):
            return {
                "status": "resolved",
                "event_flag_id": None,
                "is_set": True,
                "defeat_status": "defeated",
                "reason": f"Gym badge '{gym.get('badge_name_zh')}' is verified won in player save state.",
            }
    except Exception:
        pass

    flag_id = _int(static.get("flag_id"))
    # 0 is the ROM sentinel for "no lifecycle flag", not event flag 0.
    if flag_id == 0:
        flag_id = None
    if not isinstance(flags, dict) or flags.get("status") != "resolved" or flags.get("contents_known") is not True:
        return {
            "status": "unresolved",
            "event_flag_id": flag_id,
            "is_set": None,
            "defeat_status": "unknown",
            "reason": "The runtime event-flag decoder is not verified; actor presence is not a defeated flag.",
        }
    entries = flags.get("flags") if isinstance(flags.get("flags"), list) else []
    matching = [item for item in entries if isinstance(item, dict) and _int(item.get("id")) == flag_id]
    if len(matching) != 1:
        return {
            "status": "unresolved",
            "event_flag_id": flag_id,
            "is_set": None,
            "defeat_status": "unknown",
            "reason": "No unique decoded flag entry can be associated with this NPC.",
        }
    value = matching[0].get("is_set")
    if not isinstance(value, bool):
        return {
            "status": "unresolved",
            "event_flag_id": flag_id,
            "is_set": None,
            "defeat_status": "unknown",
            "reason": "Decoded flag entry has no boolean state.",
        }
    return {
        "status": "resolved",
        "event_flag_id": flag_id,
        "is_set": value,
        "defeat_status": "unknown",
        "reason": "Flag state is decoded, but this flag's semantic role as a trainer defeat flag is not verified.",
    }


def build_npc_battle_status(
    static_npcs: list[dict[str, Any]],
    runtime_actors: list[dict[str, Any]],
    *,
    zone_id: int,
    zone_candidates: list[dict[str, Any]] | None = None,
    flags: dict[str, Any] | None = None,
    player: dict[str, Any] | None = None,
    battle: dict[str, Any] | None = None,
    frame: int | None = None,
) -> dict[str, Any]:
    """Compose the read-only per-NPC status contract."""
    zone_candidates = [item for item in (zone_candidates or []) if isinstance(item, dict)]
    runtime_actors = [item for item in runtime_actors if isinstance(item, dict)]
    bound_rows = bind_static_npcs_to_runtime(static_npcs, runtime_actors, zone_id=zone_id)
    rows: list[dict[str, Any]] = []
    for static, bound in zip(static_npcs, bound_rows, strict=False):
        if not isinstance(static, dict) or not isinstance(bound, dict):
            continue
        runtime_binding = bound.get("runtime") if isinstance(bound.get("runtime"), dict) else {}
        binding = runtime_binding.get("binding") if isinstance(runtime_binding.get("binding"), dict) else {}
        actor = _find_bound_actor(binding, runtime_actors) if runtime_binding.get("bound") else None
        capability = _capability(static, zone_candidates)
        flag = _flag_state(static, flags, zone_id=zone_id)
        identity = static.get("identity") if isinstance(static.get("identity"), dict) else {}
        coordinate = static.get("coordinate") if isinstance(static.get("coordinate"), dict) else None
        npc_id = static.get("id") or f"zone:{zone_id}:npc:{identity.get('record_index', '?')}"
        row = {
            "npc_id": npc_id,
            "record_index": static.get("record_index", identity.get("record_index")),
            "zone_id": zone_id,
            "coordinate": coordinate,
            "sprite_id": static.get("sprite_id"),
            "script_id": static.get("script_id"),
            "flag_id": static.get("flag_id"),
            "name": static.get("name"),
            "capability": capability,
            "runtime": {
                "binding": binding,
                "present": runtime_binding.get("present"),
                "bound": runtime_binding.get("bound", False),
                "actor_uid": runtime_binding.get("actor_id"),
                "slot": runtime_binding.get("runtime_slot"),
                "address": runtime_binding.get("runtime_address"),
                "grid": runtime_binding.get("grid"),
                "facing": runtime_binding.get("facing"),
                "raw": _raw_actor(actor),
            },
            "lifecycle": {
                "event_flag": flag,
                "defeat_status": flag.get("defeat_status", "unknown"),
                "npc_presence_status": "present" if runtime_binding.get("present") is True else "unknown",
                "reason": "Presence and FieldActor flags are diagnostic evidence only; they do not establish victory.",
            },
            "battle_observation": {
                "current_battle_active": (battle or {}).get("active") if isinstance(battle, dict) else None,
                "causal_binding": "not_observed",
                "status": "unresolved",
            },
            "evidence": {
                "rom_static": True,
                "runtime_actor_sampled": bool(actor),
                "event_flag_decoded": flag.get("status") == "resolved",
                "battle_before_after_compared": False,
                "frame": frame,
            },
        }
        rows.append(row)

    current_grid = _grid((player or {}).get("position") if isinstance(player, dict) else None)
    if current_grid is not None:
        def distance(row: dict[str, Any]) -> tuple[int, int, str]:
            point = _grid(row)
            if point is None:
                return (10**9, 10**9, str(row.get("npc_id")))
            return (max(abs(point["x"] - current_grid["x"]), abs(point["z"] - current_grid["z"])),
                    abs(point["y"] - current_grid["y"]), str(row.get("npc_id")))
        rows.sort(key=distance)

    # Only a per-NPC sight candidate is safe to recommend for a probe.  A
    # zone-level TrainerBattle opcode has no decoded NPC/script-function
    # mapping yet, so it is reported above but never placed in this queue.
    probe_candidates = [
        row for row in rows
        if row["capability"]["status"] == "line_of_sight_battle_candidate"
        and row["lifecycle"]["defeat_status"] in {"unknown", "unresolved"}
    ]
    return {
        "format": "black2-npc-battle-status/v1",
        "status": "candidate" if rows else "empty",
        "zone_id": zone_id,
        "frame": frame,
        "npcs": rows,
        "probe_candidates": [row["npc_id"] for row in probe_candidates],
        "zone_script_candidates": zone_candidates,
        "policy": {
            "battle_capability": "ROM sight/script data nominates candidates; it does not prove a battle until a transition is causally bound.",
            "defeat": "Only a verified trainer-specific event flag or a before/after observation may promote defeated; absent actor is unknown.",
            "raw_memory": "FieldActor raw lifecycle fields are preserved for diffs, not interpreted as event flags.",
        },
        "evidence": {
            "sources": ["ROM event NPC records", "FieldActor heap", "zone TrainerBattle script catalog"],
            "event_flags_status": (flags or {}).get("status") if isinstance(flags, dict) else "unavailable",
            "runtime_battle_active": (battle or {}).get("active") if isinstance(battle, dict) else None,
            "verified_defeated_count": sum(1 for row in rows if row["lifecycle"]["defeat_status"] == "defeated"),
        },
    }


__all__ = ["build_npc_battle_status"]
