"""Compile runtime and ROM evidence into typed navigation constraints."""
from __future__ import annotations

from typing import Any

from .navigation_constraints import DEFAULT_AGENT_POLICY, NavigationConstraint


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _grid(value: Any) -> tuple[int | None, int, int, int] | None:
    if not isinstance(value, dict):
        return None
    nested = value.get("grid")
    if not isinstance(nested, dict):
        nested = value.get("grid_candidate")
    if not isinstance(nested, dict):
        position = value.get("position")
        nested = position.get("grid") if isinstance(position, dict) else None
    if not isinstance(nested, dict):
        nested = value
    x, y, z = _int(nested.get("x")), _int(nested.get("y")), _int(nested.get("z"))
    if x is None or y is None or z is None:
        return None
    zone = _int(value.get("zone_id"))
    if zone is None:
        zone = _int(value.get("effective_zone_id_candidate"))
    return zone, x, y, z


def _rom_static_grid(
    value: Any,
    *,
    default_zone: int | None,
) -> tuple[int | None, int, int, int] | None:
    """Normalize one ROM event record without changing runtime actor axes.

    Gen V NPC and trigger records encode their horizontal second axis in
    ``y`` and elevation in ``z``.  The navigation contract is instead
    ``(x, y=elevation, z=horizontal)``.  A nested ``grid`` is already an
    API-level canonical coordinate and is intentionally left unchanged.
    """
    if not isinstance(value, dict):
        return None

    nested = value.get("grid")
    if not isinstance(nested, dict):
        nested = value.get("grid_candidate")
    if not isinstance(nested, dict):
        position = value.get("position")
        nested = position.get("grid") if isinstance(position, dict) else None
    if isinstance(nested, dict):
        tile = _grid(nested)
        if tile is None:
            return None
        zone, x, y, z = tile
        record_zone = _int(value.get("zone_id"))
        if record_zone is None:
            record_zone = _int(value.get("effective_zone_id_candidate"))
        if record_zone is not None:
            zone = record_zone
    else:
        x = _int(value.get("x"))
        horizontal_z = _int(value.get("y"))
        elevation_y = _int(value.get("z"))
        if x is None or horizontal_z is None or elevation_y is None:
            return None
        zone = _int(value.get("zone_id"))
        if zone is None:
            zone = _int(value.get("effective_zone_id_candidate"))
        y, z = elevation_y, horizontal_z

    return (default_zone if zone in (None, 0) else zone), x, y, z


def _items(value: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
    return []


class NavigationContextCompiler:
    """Keep evidence collection separate from route policy evaluation."""

    def compile(
        self,
        *,
        player: dict[str, Any] | None,
        runtime_actors: list[dict[str, Any]] | None = None,
        static_entities: dict[str, Any] | None = None,
        story_state: dict[str, Any] | None = None,
        trainer_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        player = player if isinstance(player, dict) else {}
        static_entities = static_entities if isinstance(static_entities, dict) else {}
        player_zone = _int(player.get("zone_id"))
        constraints: list[NavigationConstraint] = []
        uncertainties: list[dict[str, Any]] = []

        for index, actor in enumerate(runtime_actors or []):
            if not isinstance(actor, dict) or actor.get("is_player") or actor.get("same_current_scene") is False:
                continue
            tile = _grid(actor)
            if tile is None:
                uncertainties.append({"kind": "dynamic_actor", "index": index, "reason": "actor_grid_unresolved"})
                continue
            zone, x, y, z = tile
            zone = player_zone if zone in (None, 0) else zone
            actor_id = actor.get("actor_id", actor.get("uid", index))
            constraints.append(NavigationConstraint(
                constraint_id=f"dynamic_actor:{actor_id}", kind="dynamic_actor", behavior="occupancy",
                tiles=((zone, x, y, z),), cost=None, dynamic=True, confidence="runtime",
                source="runtime_actor_system", status="present",
                metadata={"actor_id": actor_id},
            ))

        for index, trigger in enumerate(_items(static_entities, "triggers", "script_events", "events")):
            tile = _rom_static_grid(trigger, default_zone=player_zone)
            if tile is None:
                uncertainties.append({"kind": "script_trigger", "index": index, "reason": "trigger_grid_unresolved"})
                continue
            zone, x, y, z = tile
            w = max(1, int(trigger.get("width") or 1))
            h = max(1, int(trigger.get("height") or 1))
            all_tiles = tuple((zone, x + dx, y, z + dz) for dx in range(w) for dz in range(h))

            var_id = trigger.get("var_id")
            expected_val = trigger.get("expected_value")
            scrid = trigger.get("script_id")
            from ..progression.state import progression_state_service
            live_val = progression_state_service.get_event_var(var_id) if var_id is not None else None
            is_active = (live_val == expected_val) if (live_val is not None and expected_val is not None) else None

            if is_active is True:
                behavior = "hard_block"
                cost = None
                status = "active_blocking"
                dynamic = True
            elif is_active is False:
                behavior = "inactive"
                cost = 0.0
                status = "inactive_resolved"
                dynamic = False
            else:
                behavior = "unknown"
                cost = 25.0
                status = "activation_unresolved"
                dynamic = False

            constraints.append(NavigationConstraint(
                constraint_id=f"trigger:{zone}:{trigger.get('record_index', index)}",
                kind="script_trigger", behavior=behavior, tiles=all_tiles, cost=cost,
                dynamic=dynamic, confidence="live_event_work" if is_active is not None else "rom_record",
                source="rom:/a/1/2/6+MainRAM",
                status=status,
                metadata={
                    "script_id": scrid,
                    "var_id": f"0x{var_id:04X}" if var_id is not None else None,
                    "expected_value": expected_val,
                    "live_value": live_val,
                    "is_active": is_active,
                    "width": w,
                    "height": h,
                },
            ))

        # NPC records carry a raw facing and sight length.  Those fields are
        # useful for route warnings, but they do not prove that the sprite is
        # a trainer (or that its line-of-sight script is active).  Publish a
        # candidate trainer-sight constraint for every structurally plausible
        # sight record and keep the evidence/status explicit.  A runtime actor
        # overlay or battle event can later promote/refute it.
        direction_delta = {
            0: (0, -1),  # North: grid.z decreases
            1: (0, 1),   # South
            2: (-1, 0),  # West
            3: (1, 0),   # East
        }
        for index, npc in enumerate(_items(static_entities, "npcs", "actors")):
            if not isinstance(npc, dict):
                continue
            if npc.get("defeat_status") == "defeated" or npc.get("is_defeated") is True:
                continue
            sc = _int(npc.get("script_id"))
            if sc is not None and isinstance(trainer_state, dict):
                from ..progression.state import resolve_trainer_defeat_flag, is_event_flag_set
                df = resolve_trainer_defeat_flag(sc)
                fb = trainer_state.get("flag_bytes")
                if df is not None and fb and is_event_flag_set(df, fb):
                    continue
            raw_sight = _int(npc.get("sight_raw"))
            raw_direction = _int(npc.get("direction_raw", npc.get("facing_id")))
            if raw_sight is None or raw_sight <= 0 or raw_direction not in direction_delta:
                continue
            origin = _rom_static_grid(npc, default_zone=player_zone)
            if origin is None:
                uncertainties.append({"kind": "trainer_sight", "index": index, "reason": "npc_grid_unresolved"})
                continue
            zone, x, y, z = origin
            dx, dz = direction_delta[raw_direction]
            # Avoid producing an unbounded hazard from corrupt ROM values.
            distance = min(raw_sight, 32)
            sight_tiles = tuple((zone, x + dx * step, y, z + dz * step) for step in range(1, distance + 1))
            trainer_id = npc.get("id", npc.get("record_index", index))
            constraints.append(NavigationConstraint(
                constraint_id=f"trainer_sight:{zone}:{trainer_id}",
                kind="trainer_sight", behavior="soft_cost", tiles=sight_tiles, cost=10.0,
                dynamic=True, confidence="rom_record", source="rom:/a/1/2/6",
                status="candidate",
                metadata={
                    "trainer_id": f"zone:{zone}:npc:{trainer_id}",
                    "npc_record_index": npc.get("record_index", index),
                    "sprite_id": npc.get("sprite_id"),
                    "script_id": npc.get("script_id"),
                    "flag_id": npc.get("flag_id"),
                    "movement_id": npc.get("movement_id"),
                    "sight_raw": raw_sight,
                    "facing_raw": raw_direction,
                    "identity_status": "trainer_unverified",
                    "trigger_status": "runtime_confirmation_required",
                    "candidate_reason": "raw NPC sight/facing fields only; trainer identity and battle script are unresolved",
                    "occlusion_status": "not_checked",
                },
            ))

        for index, warp in enumerate(_items(static_entities, "warps", "portals")):
            tile = _grid(warp)
            if tile is None:
                coordinate = warp.get("source") or warp.get("coordinate")
                tile = _grid(coordinate)
            if tile is None:
                uncertainties.append({"kind": "warp", "index": index, "reason": "warp_grid_unresolved"})
                continue
            target = bool(warp.get("navigation_target") or warp.get("target") is True)
            zone, x, y, z = tile
            zone = player_zone if zone in (None, 0) else zone
            constraints.append(NavigationConstraint(
                constraint_id=f"warp:{zone}:{warp.get('record_index', warp.get('id', index))}",
                kind="warp", behavior="terminal" if target else "hard_block", tiles=((zone, x, y, z),),
                cost=None, dynamic=False, confidence=str(warp.get("confidence") or "candidate"),
                source="rom:/a/1/2/6", status="target" if target else "unverified",
                metadata={"navigation_target": target},
            ))

        for index, gate in enumerate(_items(story_state, "active_gates", "gates", "constraints")):
            tile = _grid(gate)
            if tile is None:
                continue
            zone, x, y, z = tile
            status = str(gate.get("status") or ("active" if gate.get("active") is True else "unresolved"))
            constraints.append(NavigationConstraint(
                constraint_id=str(gate.get("constraint_id") or f"story_gate:{zone}:{index}"),
                kind="story_gate",
                behavior="hard_block" if status in {"active", "confirmed_active"} else "unknown",
                tiles=((zone, x, y, z),), cost=None,
                dynamic=True, confidence=str(gate.get("confidence") or "runtime"),
                source=str(gate.get("source") or "runtime_story_state"), status=status,
                metadata={key: gate.get(key) for key in ("flag_id", "script_id") if key in gate},
            ))

        for index, sight in enumerate(_items(trainer_state, "sight_tiles", "trainer_sight", "constraints")):
            tiles = sight.get("tiles") if isinstance(sight.get("tiles"), list) else [sight]
            parsed = tuple(item for item in (_grid(tile) for tile in tiles) if item is not None)
            if not parsed:
                continue
            constraints.append(NavigationConstraint(
                constraint_id=str(sight.get("constraint_id") or f"trainer_sight:{index}"),
                kind="trainer_sight", behavior="soft_cost", tiles=parsed, cost=10.0,
                dynamic=True, confidence=str(sight.get("confidence") or "candidate"),
                source=str(sight.get("source") or "runtime_trainer_state"), status=str(sight.get("status") or "candidate"),
                metadata={"trainer_id": sight.get("trainer_id")},
            ))

        stair_corridors = []
        if player_zone is not None:
            try:
                from .staircase_corridors import staircase_corridor_service
                corrs = staircase_corridor_service.analyze_zone(player_zone)
                stair_corridors = [c.as_dict() for c in corrs]
            except Exception:
                stair_corridors = []

        return {
            "format": "black2-navigation-context/v1",
            "frame": player.get("frame"),
            "player": player,
            "zone_id": player_zone,
            "constraints": [constraint.public() for constraint in constraints],
            "staircase_corridors": stair_corridors,
            "policy_default": dict(DEFAULT_AGENT_POLICY),
            "policy_source": "default_only",
            "uncertainties": uncertainties,
        }


def compile_navigation_context(**kwargs: Any) -> dict[str, Any]:
    return NavigationContextCompiler().compile(**kwargs)
