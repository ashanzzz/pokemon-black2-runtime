"""Evidence-ranked binding between static ROM NPC records and live actors."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ActorBinding:
    rom_entity_id: int | None
    rom_record_index: int | None
    runtime_actor_uid: int | None
    runtime_slot: int | None
    runtime_address: str | None
    status: str  # verified | probable | candidate | unresolved
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _runtime_grid(actor: dict[str, Any]) -> dict[str, int] | None:
    grid = actor.get("grid")
    if not isinstance(grid, dict):
        grid = actor.get("grid_position")
    if not isinstance(grid, dict):
        return None
    try:
        return {"x": int(grid["x"]), "y": int(grid["y"]), "z": int(grid["z"])}
    except (KeyError, TypeError, ValueError):
        return None


def _static_grid(static: dict[str, Any]) -> dict[str, int] | None:
    """Read a static event in the canonical field-grid axis order.

    Decoded event records store their horizontal second coordinate as ``y``
    and elevation as ``z``.  A scene projection may already have supplied a
    canonical ``grid``; prefer it so connected-scene consumers and this
    binding routine use exactly the same address.
    """
    grid = _runtime_grid(static)
    if grid is not None:
        return grid
    x, event_y, elevation = _int(static.get("x")), _int(static.get("y")), _int(static.get("z"))
    if x is None or event_y is None or elevation is None:
        return None
    return {"x": x, "y": elevation, "z": event_y}


def _same_zone(actor: dict[str, Any], zone_id: int) -> bool:
    if actor.get("same_current_scene") is False:
        return False
    effective_zone = _int(actor.get("effective_zone_id_candidate"))
    if effective_zone is not None:
        return effective_zone == zone_id
    # ZoneID 0 is a known ambiguous raw runtime value.  It only becomes a
    # current-scene identity after RuntimeActorOverlay derives an effective
    # ZoneID from coherent mapper evidence; otherwise it must not bind an NPC
    # in every Zone of a connected scene.
    raw_zone = _int(actor.get("zone_id"))
    return raw_zone is not None and raw_zone != 0 and raw_zone == zone_id


def _match(static: dict[str, Any], actor: dict[str, Any], zone_id: int) -> ActorBinding | None:
    if not _same_zone(actor, zone_id):
        return None
    rom_id = _int(static.get("id"))
    record_index = _int(static.get("record_index"))
    actor_uid = _int(actor.get("actor_uid", actor.get("uid")))
    slot = _int(actor.get("slot"))
    address = actor.get("address")
    evidence: list[str] = []

    # Runtime decoders may expose a direct ROM identity after a future event
    # decoder is verified.  Only this explicit identity path is verified.
    for key in ("rom_entity_id", "entity_id", "static_entity_id"):
        if _int(actor.get(key)) is not None and _int(actor.get(key)) == rom_id:
            evidence.append(f"runtime.{key}=rom.id")
            return ActorBinding(rom_id, record_index, actor_uid, slot, address, "verified", tuple(evidence))

    static_script = _int(static.get("script_id"))
    static_flag = _int(static.get("flag_id", static.get("spawn_flag")))
    static_sprite = _int(static.get("sprite_id"))
    runtime_script = _int(actor.get("script_id"))
    runtime_flag = _int(actor.get("spawn_flag"))
    runtime_model = _int(actor.get("model_id"))
    if static_script is not None and runtime_script == static_script:
        evidence.append("script_id")
    if static_flag is not None and runtime_flag == static_flag:
        evidence.append("spawn_flag")
    if static_sprite is not None and runtime_model == static_sprite:
        evidence.append("sprite/model")

    if {"script_id", "spawn_flag", "sprite/model"}.issubset(evidence):
        return ActorBinding(rom_id, record_index, actor_uid, slot, address, "probable", tuple(evidence))
    if {"script_id", "spawn_flag"}.issubset(evidence):
        return ActorBinding(rom_id, record_index, actor_uid, slot, address, "candidate", tuple(evidence))

    static_grid = _static_grid(static)
    live_grid = _runtime_grid(actor)
    if static_grid and live_grid and static_grid["y"] == live_grid["y"]:
        # Coordinates are only fallback evidence.  A same X/Z actor on a
        # different layer is intentionally not promoted to a strong match.
        if (static_grid["x"], static_grid["z"]) == (live_grid["x"], live_grid["z"]):
            evidence.append("spawn_coordinate_fallback")
            return ActorBinding(rom_id, record_index, actor_uid, slot, address, "candidate", tuple(evidence))
    return None


def bind_static_npcs_to_runtime(
    static_npcs: list[dict[str, Any]],
    runtime_actors: list[dict[str, Any]],
    *,
    zone_id: int,
) -> list[dict[str, Any]]:
    """Return static NPCs enriched with a conservative runtime binding.

    Every output includes ``runtime.present`` as ``True`` only when an actor
    matched.  Absence is unknown rather than false because a partial heap
    sample, story flag, or scene transition can hide a valid NPC.
    """
    actors = [item for item in runtime_actors if isinstance(item, dict) and not item.get("is_player")]
    entries: list[tuple[dict[str, Any], list[tuple[ActorBinding, dict[str, Any], int]]]] = []
    for static in static_npcs:
        if not isinstance(static, dict):
            continue
        matches: list[tuple[ActorBinding, dict[str, Any], int]] = []
        for index, actor in enumerate(actors):
            binding = _match(static, actor, zone_id)
            if binding is not None:
                matches.append((binding, actor, index))
        matches.sort(key=lambda item: {"verified": 0, "probable": 1, "candidate": 2}.get(item[0].status, 3))
        entries.append((static, matches))

    # Strong evidence gets a global first pass.  A coordinate/script
    # candidate that happened to occur earlier in ROM record order must never
    # consume the actor needed by a later verified/probable record.
    selected: dict[int, tuple[ActorBinding, dict[str, Any], int]] = {}
    used_strong: set[int] = set()
    for strength in ("verified", "probable"):
        for static_index, (_static, matches) in enumerate(entries):
            if static_index in selected:
                continue
            for match in matches:
                if match[0].status == strength and match[2] not in used_strong:
                    selected[static_index] = match
                    used_strong.add(match[2])
                    break

    # Candidate evidence is informative but deliberately non-exclusive.  It
    # may describe several ROM events near the same actor and never suppresses
    # their static markers, so it cannot block a strong assignment above.
    for static_index, (_static, matches) in enumerate(entries):
        if static_index in selected:
            continue
        candidate = next((match for match in matches if match[0].status == "candidate"), None)
        if candidate is not None:
            selected[static_index] = candidate

    result: list[dict[str, Any]] = []
    for static_index, (static, _matches) in enumerate(entries):
        row = dict(static)
        match = selected.get(static_index)
        if match:
            binding, actor, _actor_index = match
            grid = _runtime_grid(actor)
            row["runtime"] = {
                "bound": True,
                "actor_id": actor.get("actor_uid", actor.get("uid")),
                "runtime_slot": actor.get("slot"),
                "runtime_address": actor.get("address"),
                "grid": grid,
                "facing": actor.get("facing", actor.get("face_direction")),
                "present": True,
                "frame": actor.get("frame"),
                "binding": binding.as_dict(),
            }
        else:
            binding = ActorBinding(
                _int(static.get("id")), _int(static.get("record_index")), None, None, None,
                "unresolved", ("no_identity_or_runtime_match",),
            )
            row["runtime"] = {
                "bound": False, "actor_id": None, "runtime_slot": None,
                "runtime_address": None, "grid": None, "facing": None,
                "present": None, "frame": None, "binding": binding.as_dict(),
            }
        result.append(row)
    return result


def static_npc_entity_id(static: dict[str, Any], zone_id: int) -> str:
    """Return the stable scene identifier used to control a ROM NPC marker."""
    entity_id = _int(static.get("id"))
    record_index = _int(static.get("record_index"))
    return f"rom-npc:z{int(zone_id)}:id{entity_id if entity_id is not None else '?'}:r{record_index if record_index is not None else '?'}"


def merge_scene_npcs(
    static_npcs_by_zone: dict[int, list[dict[str, Any]]], runtime_actors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a lightweight, conservative static/live NPC render contract.

    Only verified or probable identity evidence may suppress a ROM marker.
    Coordinate-only candidate matches intentionally leave the candidate visible:
    a matching spawn tile is insufficient proof that the runtime actor is that
    particular event record.
    """
    rows: list[dict[str, Any]] = []
    suppressed: list[str] = []
    actor_bindings: dict[str, list[str]] = {}
    for zone_id, static_npcs in static_npcs_by_zone.items():
        if not isinstance(zone_id, int) or not isinstance(static_npcs, list):
            continue
        for item in bind_static_npcs_to_runtime(static_npcs, runtime_actors, zone_id=zone_id):
            static = {key: value for key, value in item.items() if key != "runtime"}
            entity_id = static_npc_entity_id(static, zone_id)
            runtime = item.get("runtime") or {}
            binding = runtime.get("binding") or {}
            status = str(binding.get("status") or "unresolved")
            suppress = bool(runtime.get("bound")) and status in {"verified", "probable"}
            actor_key = None
            slot = _int(runtime.get("runtime_slot"))
            actor_id = _int(runtime.get("actor_id"))
            if slot is not None:
                actor_key = f"slot:{slot}"
            elif actor_id is not None:
                actor_key = f"uid:{actor_id}"
            if suppress:
                suppressed.append(entity_id)
                if actor_key is not None:
                    actor_bindings.setdefault(actor_key, []).append(entity_id)
            rows.append({
                "static_entity_id": entity_id,
                "source_zone_id": int(zone_id),
                "static": static,
                "runtime": runtime,
                "binding": binding,
                "presence": "runtime_present" if runtime.get("bound") else "rom_candidate",
                "render_mode": "live_actor" if suppress else "static_candidate",
                "suppress_static_candidate": suppress,
            })
    return {
        "format": "black2-scene-npc-merge/v1",
        "npcs": rows,
        "suppressed_static_entity_ids": suppressed,
        "runtime_actor_bindings": actor_bindings,
        "policy": {
            "static": "ROM spawn candidates are presentation only and never navigation occupancy",
            "suppression": "only verified or probable runtime identity suppresses a static marker",
        },
    }
