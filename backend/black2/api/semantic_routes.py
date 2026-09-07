"""AI observation API: strict ROM terrain plus one cached runtime snapshot."""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from ..memory.reader import MemoryReader
from ..runtime.hub import RuntimeHub
from ..world.gen5_rom_map import Gen5RomMap
from ..world.map_graph import RomMapGraphService
from ..world.observed_navigation import NavNode, observed_navigation_graph
from ..world.semantic_world import SemanticWorldService
from ..world.npc_classifier import npc_classifier
from ..state.memory_goals import goal_memory_manager


router = APIRouter(prefix="/api/v1", tags=["ai-semantic"])
Zone = Annotated[int, Query(ge=0, le=65534)]
Coord = Annotated[int, Query(ge=-32768, le=32767)]
Radius = Annotated[int, Query(ge=0, le=16)]
_hub: RuntimeHub | None = None
_rom_graph: RomMapGraphService | None = None
_world_service: SemanticWorldService | None = None
_connector_service = None
_door_world_service = None
_services_lock = threading.RLock()


def configure_semantic_routes(reader: MemoryReader, hub: RuntimeHub) -> None:
    global _hub
    _hub = hub


def _snapshot() -> dict[str, Any]:
    return _hub.snapshot() if _hub is not None else {"runtime": {"status": "unavailable"}}


def _graph() -> RomMapGraphService:
    global _rom_graph
    with _services_lock:
        if _rom_graph is None:
            _rom_graph = RomMapGraphService(Gen5RomMap())
        return _rom_graph


def _world() -> SemanticWorldService:
    global _world_service
    with _services_lock:
        if _world_service is None:
            _world_service = SemanticWorldService(_graph().rom)
        return _world_service


def _connectors():
    global _connector_service
    from ..world.semantic_connectors import SemanticConnectorService
    with _services_lock:
        graph = _graph()
        if _connector_service is None or _connector_service.graph is not graph:
            service = SemanticConnectorService(graph)
            service.coverage()
            _connector_service = service
        return _connector_service


def _door_world():
    """Return the ROM world decoder used for static building/door metadata."""
    global _door_world_service
    from ..world.original_world import OriginalWorldService
    with _services_lock:
        if _door_world_service is None:
            _door_world_service = OriginalWorldService()
        return _door_world_service


def _door_catalog(zone_id: int, *, include_raw: bool = False) -> list[dict[str, Any]]:
    """Flatten AreaBuildingResource door metadata into a stable AI schema."""
    world = _door_world().zone(zone_id)
    doors: list[dict[str, Any]] = []
    for item in world.get("buildings", []):
        if item.get("belongs_to_zone") is False:
            continue
        resource = item.get("resource") or {}
        door_uid = resource.get("door_uid")
        if door_uid is None or door_uid == 0xFFFF:
            continue
        base = item.get("world_position") or item.get("world_position_candidate") or {}
        rotation = float(item.get("rotation_degrees") or 0.0)
        import math
        angle = math.radians(rotation)
        offset = resource.get("door_offset") or {}
        ox, oy, oz = float(offset.get("x") or 0), float(offset.get("y") or 0), float(offset.get("z") or 0)
        bx, by, bz = float(base.get("x") or 0), float(base.get("y") or 0), float(base.get("z") or 0)
        world_pos = {"x": bx + ox * math.cos(angle) - oz * math.sin(angle),
                     "y": by + oy, "z": bz + ox * math.sin(angle) + oz * math.cos(angle)}
        grid = {"space": "gen5-field-grid-v1", "zone_id": zone_id,
                "x": math.floor(world_pos["x"] / 16), "y": None, "z": math.floor(world_pos["z"] / 16)}
        model_uid = item.get("model_uid")
        record = {
            "id": str(item.get("instance_id") or f"zone-{zone_id}-building-{model_uid}"),
            "zone_id": zone_id,
            "kind": "door",
            "door_uid": int(door_uid),
            "building": {"instance_id": item.get("instance_id"), "model_uid": model_uid,
                          "chunk_id": item.get("chunk_id"), "placement_index": item.get("placement_index"),
                          "rotation_degrees": item.get("rotation_degrees")},
            "coordinate": {"space": "gen5-field-world-v1", "world": world_pos,
                           "grid_candidate": grid, "units_per_tile": 16},
            "door_offset": {"x": ox, "y": oy, "z": oz},
            "resource": {"asset_url": f"/api/v1/map/v5/building/{zone_id}/{model_uid}/model.glb",
                         "model_source": resource.get("model_source"),
                         "independent_asset": {"available": False, "status": "unresolved",
                                               "reason": "ROM stores DoorUID/offset metadata; no independent door mesh is exposed."}},
            "render_hint": {
                "mode": "semantic_overlay",
                "asset_status": "no_independent_rom_mesh",
                "recommended_visual": "door_frame_threshold_ground_ring_direction_marker",
                "label_fields": ["door_uid", "building.instance_id", "warp_association"],
                "meaning": "UI marker only; it does not claim that the door is open or traversable",
            },
            "warp_association": [],
            "traversal": {"can_traverse": None, "status": "unverified",
                          "reason": "Door metadata does not prove collision, script gates, or a transition."},
            "confidence": "candidate",
            "evidence": {"source": "ROM ChunkBuildings + AreaBuildingResource", "verified": False,
                         "semantic_status": "door_candidate"},
        }
        if include_raw:
            record["raw"] = {"building": item, "resource": resource}
        doors.append(record)
    return sorted(doors, key=lambda row: (row["id"], row["door_uid"]))


def _associate_door_warps(doors: list[dict[str, Any]], zone_id: int) -> None:
    """Attach nearest same-zone Warp candidates without asserting linkage."""
    import math
    try:
        warps = _connectors().query(zone_id=zone_id, offset=0, limit=2048).get("warps", [])
    except Exception:
        warps = []
    for door in doors:
        # Test fixtures and future decoders may omit the optional association
        # field; initialize it here so enrichment remains additive.
        door.setdefault("warp_association", [])
        coordinate = door.get("coordinate") if isinstance(door, dict) else None
        pos = coordinate.get("world") if isinstance(coordinate, dict) else None
        if not isinstance(pos, dict) or not isinstance(pos.get("x"), (int, float)) or not isinstance(pos.get("z"), (int, float)):
            continue
        candidates = []
        for warp in warps:
            source = warp.get("source") or {}
            world = source.get("world") or {}
            if not isinstance(world, dict) or not isinstance(world.get("x"), (int, float)) or not isinstance(world.get("z"), (int, float)):
                continue
            distance = math.hypot(float(world["x"]) - pos["x"], float(world["z"]) - pos["z"])
            candidates.append((distance, warp))
        for distance, warp in sorted(candidates, key=lambda row: (row[0], str(row[1].get("id"))))[:3]:
            door["warp_association"].append({"warp_id": warp.get("id"), "distance_world": round(distance, 3),
                                               "role": "nearest_candidate", "status": "candidate"})


async def _rom_call(fn, *args, **kwargs):
    try:
        return await run_in_threadpool(fn, *args, **kwargs)
    except IndexError as error:
        raise HTTPException(404, detail=str(error)) from error
    except (FileNotFoundError, OSError, ValueError, RuntimeError) as error:
        raise HTTPException(503, detail=f"ROM decode unavailable: {error}") from error


def _freshness(snap: dict) -> dict:
    transport = snap.get("transport") or {}
    age = snap.get("age_seconds")
    player = snap.get("player") or {}
    grid = (player.get("position") or {}).get("grid") or {}
    resolved = (player.get("status") == "resolved" and type(player.get("frame")) is int
                and type(player.get("zone_id")) is int
                and all(type(grid.get(k)) is int for k in ("x", "y", "z")))
    fresh = bool(resolved and transport.get("bridge_connected") and isinstance(age, (int, float)) and 0 <= age <= 3
                 and (snap.get("runtime") or {}).get("semantic_status") == "ready")
    return {"fresh": fresh, "sampled_at": snap.get("sampled_at"), "age_seconds": age,
            "session_id": transport.get("session_id"), "source_frame": (snap.get("player") or {}).get("frame")}


def _runtime_observation(snap: dict) -> dict[str, Any]:
    """Describe whether modal/profile facts are current runtime observations.

    RuntimeHub intentionally retains its last semantic sample across a bridge
    outage.  That sample is useful diagnostic evidence, but it must not be
    presented as the game's current menu, task, flag, or cutscene state.
    """
    transport = snap.get("transport") if isinstance(snap.get("transport"), dict) else {}
    runtime = snap.get("runtime") if isinstance(snap.get("runtime"), dict) else {}
    context = _semantic_context(snap)
    screen = str(context.get("screen_type") or "").upper()
    connected = transport.get("bridge_connected") is True
    semantic_ready = runtime.get("semantic_status") == "ready"
    screen_resolved = bool(screen and screen != "RUNTIME_UNRESOLVED")
    age = snap.get("age_seconds")
    sample_fresh = bool(isinstance(age, (int, float)) and 0 <= age <= 3)
    current = bool(connected and semantic_ready and screen_resolved and sample_fresh)
    if not connected:
        reason = "BizHawk bridge is disconnected; cached semantic values are not current facts."
    elif not semantic_ready:
        reason = "Runtime semantic sampling is not ready."
    elif not screen_resolved:
        reason = "The current screen/modal state is unresolved."
    elif not sample_fresh:
        reason = "The last semantic sample is stale; cached values are not current facts."
    else:
        reason = "Current semantic screen observation is available."
    return {
        "current": current,
        "bridge_connected": connected,
        "semantic_status": runtime.get("semantic_status"),
        "screen_resolved": screen_resolved,
        "sample_fresh": sample_fresh,
        "age_seconds": age,
        "reason": reason,
    }


def _state(snap: dict) -> dict:
    player = snap.get("player") or {}
    context = (snap.get("semantic") or {}).get("context") or {}
    freshness = _freshness(snap)
    observation = _runtime_observation(snap)
    screen = dict(context) if isinstance(context, dict) else {}
    if not observation["current"]:
        # SemanticGameState uses conservative booleans internally while a RAM
        # read is failing.  At the public boundary these are tri-state facts:
        # false means observed false, null means no current observation.
        screen = {
            "screen_type": "RUNTIME_UNRESOLVED",
            "screen_description": observation["reason"],
            "available_actions": None,
            "can_move_player": None,
            "is_dialogue_active": None,
            "dialogue_text": None,
            "full_dialogue_text": None,
            "speaker": None,
            "speaker_category": None,
            "choices": None,
            "active_pointer": None,
            "recommended_action": None,
        }
    screen["observation_status"] = "current" if observation["current"] else "unresolved"
    can_act = context.get("can_move_player") if freshness["fresh"] else None
    if not isinstance(can_act, bool):
        can_act = None
    return {
        "format": "black2-game-state/v1", "status": "current" if freshness["fresh"] else "unresolved",
        "frame": player.get("frame"), "freshness": freshness,
        "runtime": snap.get("runtime") or {}, "screen": screen,
        "profile": snap.get("profile") or {}, "player": player,
        "map": {**(snap.get("map") or {}), "zone_id": player.get("zone_id"),
                "grid_position": (player.get("position") or {}).get("grid")},
        "can_act": can_act,
        "execution_available": can_act is True,
        "evidence": {"source": "RuntimeHub cache", "verified": player.get("confidence") == "verified" and freshness["fresh"],
                     "confidence": player.get("confidence", "unverified") if freshness["fresh"] else "unresolved",
                     "runtime_observation": observation},
    }


def _semantic_context(snap: dict) -> dict[str, Any]:
    """Return the normalized semantic state context without inventing facts."""
    semantic = snap.get("semantic")
    if not isinstance(semantic, dict):
        semantic = {}
    context = semantic.get("context")
    if not isinstance(context, dict):
        context = snap.get("context") if isinstance(snap.get("context"), dict) else {}
    return context


def _goal_input(snap: dict) -> dict[str, Any]:
    """Adapt the RuntimeHub snapshot to the existing goal evaluator contract."""
    semantic = snap.get("semantic") if isinstance(snap.get("semantic"), dict) else {}
    profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
    position = player.get("position") if isinstance(player.get("position"), dict) else {}
    return {
        **semantic,
        "context": _semantic_context(snap),
        "party_count": profile.get("party_count", semantic.get("party_count")),
        "badges": profile.get("badges", semantic.get("badges")),
        "location": semantic.get("location") or (snap.get("map") or {}).get("location_label"),
        "player_world_pos": position.get("world") or semantic.get("player_world_pos") or {},
    }


def _objectives(snap: dict) -> dict[str, Any]:
    """Build the progress contract used by tasks and the frontend.

    GoalMemory contains a useful story-phase heuristic, but it is not a ROM
    quest decoder.  The response therefore carries an explicit confidence and
    keeps coordinates as candidates until runtime evidence confirms them.
    """
    source_profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    observation = _runtime_observation(snap)
    party_count = source_profile.get("party_count")
    badges = source_profile.get("badges")
    # GoalMemory branches on party_count first and badges second.  Supplying
    # None used to fall through to its "one or more badges" branch, creating a
    # plausible-looking objective from no evidence.
    progress_sufficient = (
        type(party_count) is int
        and (party_count == 0 or type(badges) is int)
        and source_profile.get("confidence") not in {"unresolved", "unverified"}
    )
    inputs_current = bool(observation["current"] and progress_sufficient)
    goal_error = None
    goal = {
        "immediate": None,
        "mid_term": None,
        "long_term": None,
        "action_directive": None,
        "priority_target": None,
        "milestones_completed": None,
    }
    if inputs_current:
        try:
            goal = goal_memory_manager.evaluate(_goal_input(snap)).model_dump()
        except Exception as exc:  # containment boundary for a read-only endpoint
            inputs_current = False
            goal_error = f"{type(exc).__name__}: {exc}"
    confidence = "candidate" if inputs_current else "unresolved"
    context = _semantic_context(snap)
    return {
        "format": "black2-game-objectives/v1",
        "status": "available" if inputs_current and goal.get("immediate") else "unresolved",
        "confidence": confidence,
        "contents_known": inputs_current,
        "objectives": {
            "immediate": goal.get("immediate"),
            "mid_term": goal.get("mid_term"),
            "long_term": goal.get("long_term"),
            "action_directive": goal.get("action_directive"),
            "priority_target": goal.get("priority_target"),
            "milestones_completed": (goal.get("milestones_completed") or []) if inputs_current else None,
        },
        "runtime": {
            "screen_type": context.get("screen_type") if observation["current"] else "RUNTIME_UNRESOLVED",
            "can_move_player": context.get("can_move_player") if observation["current"] else None,
            "frame": ((snap.get("semantic") or {}).get("frame")
                      if observation["current"] and isinstance(snap.get("semantic"), dict) else None),
        },
        "evidence": {
            "source": "GoalMemoryManager + RuntimeHub snapshot",
            "progress_fields": ([key for key in ("party_count", "badges", "money")
                                 if source_profile.get(key) is not None] if observation["current"] else []),
            "confidence": confidence,
            "verified": False,
            "reason": ("Story heuristic is not a decoded quest journal; verify objectives against observed events."
                       if inputs_current else goal_error or observation["reason"]
                       if not observation["current"] else
                       "Party/badge evidence is insufficient for the story heuristic."),
            "runtime_observation": observation,
        },
    }


def _tasks(snap: dict) -> dict[str, Any]:
    objectives = _objectives(snap)
    obj = objectives["objectives"]
    context = _semantic_context(snap)
    if not obj.get("immediate"):
        return {
            "format": "black2-game-tasks/v1",
            "status": "unresolved",
            "count": None,
            "contents_known": False,
            "tasks": [],
            "active_task_id": None,
            "evidence": objectives["evidence"],
        }
    confidence = objectives["confidence"]
    if context.get("screen_type") == "RUNTIME_UNRESOLVED":
        task_status = "blocked"
    elif context.get("can_move_player") is False:
        task_status = "waiting"
    else:
        task_status = "active" if confidence == "candidate" else "candidate"
    task = {
        "task_id": "story.immediate",
        "kind": "story",
        "title": obj["immediate"],
        "description": obj["immediate"],
        "status": task_status,
        "priority": "high",
        "target": obj.get("priority_target"),
        "requirements": [],
        "source": "goal_memory",
        "confidence": confidence,
        "evidence": {
            "verified": False,
            "reason": "Candidate objective derived from current progress heuristic; no quest journal decoder is available.",
        },
    }
    return {
        "format": "black2-game-tasks/v1",
        "status": "available",
        "count": 1,
        "contents_known": True,
        "tasks": [task],
        "active_task_id": task["task_id"] if task_status in {"active", "waiting", "blocked"} else None,
        "objectives_endpoint": "/api/v1/game/objectives",
        "evidence": objectives["evidence"],
    }


def _flag_records(snap: dict) -> list[dict[str, Any]]:
    """Normalize optional decoder output while preserving unknown state.

    Future RAM decoders can publish ``semantic.flags`` as a mapping or list;
    this endpoint accepts both formats without treating missing data as false.
    """
    semantic = snap.get("semantic") if isinstance(snap.get("semantic"), dict) else {}
    raw = semantic.get("flags")
    if raw is None:
        raw = snap.get("flags")
    if isinstance(raw, dict):
        return [{"id": str(key), "value": value, "scope": "runtime"} for key, value in raw.items()]
    if isinstance(raw, list):
        rows = []
        for index, item in enumerate(raw):
            if isinstance(item, dict):
                rows.append({"id": str(item.get("id", item.get("flag_id", index))), **item})
            else:
                rows.append({"id": str(index), "value": item, "scope": "runtime"})
        return rows
    return []


def _flags(snap: dict) -> dict[str, Any]:
    records = _flag_records(snap)
    semantic = snap.get("semantic") if isinstance(snap.get("semantic"), dict) else {}
    decoder_status = semantic.get("flags_decode_status") or ("observed" if records else "unresolved")
    observation = _runtime_observation(snap)
    values_current = bool(
        observation["current"] and records and decoder_status not in {"unresolved", "unverified", "error"}
    )
    return {
        "format": "black2-game-flags/v1",
        "status": "available" if values_current else "unresolved",
        "decode_status": decoder_status if observation["current"] else "unresolved",
        "count": len(records) if values_current else None,
        "contents_known": values_current,
        "flags": records if values_current else [],
        "last_observed_flags": records if records and not values_current else [],
        "scopes": ["story", "system", "map", "runtime"],
        "evidence": {
            "source": "RuntimeHub semantic snapshot",
            "verified": bool(values_current and decoder_status == "verified"),
            "confidence": "verified" if values_current and decoder_status == "verified" else "unresolved",
            "reason": ("Current runtime flag values are available."
                       if values_current else observation["reason"]
                       if not observation["current"] else
                       "No current RAM event-flag decoder values are available; an empty list means unknown, not all flags cleared."),
            "runtime_observation": observation,
        },
    }


def _cutscene(snap: dict) -> dict[str, Any]:
    context = _semantic_context(snap)
    observation = _runtime_observation(snap)
    screen = str(context.get("screen_type") or "").upper()
    explicit = context.get("cutscene") if isinstance(context.get("cutscene"), dict) else {}
    phase = explicit.get("phase") or context.get("cutscene_phase") if observation["current"] else "unknown"
    if not phase:
        if screen in {"DIALOGUE_ACTIVE", "DIALOGUE_CHOICE"} or context.get("is_dialogue_active"):
            phase = "dialogue"
        elif screen == "BATTLE":
            phase = "battle_transition"
        elif screen in {"MAIN_MENU", "BAG_MENU", "PARTY_MENU"}:
            phase = "menu"
        elif screen == "RUNTIME_UNRESOLVED":
            phase = "unknown"
        elif screen:
            phase = "idle"
        else:
            phase = "unknown"
    phase_known = bool(observation["current"] and phase != "unknown")
    active = (phase != "idle") if phase_known else None
    if observation["current"] and isinstance(explicit.get("active"), bool):
        active = explicit["active"]
    can_move = context.get("can_move_player") if observation["current"] else None
    can_move = can_move if isinstance(can_move, bool) else None
    blocking_phases = {"dialogue", "battle_transition", "fade", "scripted_movement", "menu"}
    if not observation["current"]:
        blocking = None
    elif isinstance(explicit.get("blocking"), bool):
        blocking = explicit["blocking"]
    elif phase in blocking_phases or can_move is False:
        blocking = True
    elif can_move is True:
        blocking = False
    else:
        blocking = None
    dialogue_active_raw = context.get("is_dialogue_active")
    if not observation["current"]:
        dialogue_active = None
    elif isinstance(dialogue_active_raw, bool):
        dialogue_active = dialogue_active_raw
    else:
        dialogue_active = phase == "dialogue" if phase_known else None
    return {
        "format": "black2-game-cutscene/v1",
        "status": "available" if phase_known else "unresolved",
        "active": active,
        "phase": phase,
        "blocking": blocking,
        "can_move_player": can_move,
        "interruptible": bool(can_move) if can_move is not None else None,
        "dialogue": {
            "active": dialogue_active,
            "contents_known": observation["current"],
            "text": context.get("dialogue_text") if observation["current"] else None,
            "full_text": context.get("full_dialogue_text") if observation["current"] else None,
            "speaker": context.get("speaker") if observation["current"] else None,
            "speaker_category": context.get("speaker_category") if observation["current"] else None,
            "choices": (context.get("choices") or []) if observation["current"] else None,
            "active_pointer": context.get("active_pointer") if observation["current"] else None,
        },
        "transition": explicit.get("transition") if observation["current"] else None,
        "frame": ((snap.get("semantic") or {}).get("frame")
                  if observation["current"] and isinstance(snap.get("semantic"), dict) else None),
        "evidence": {
            "source": "SemanticScreenContext",
            "confidence": "verified" if phase_known else "unresolved",
            "verified": phase_known,
            "active_known": active is not None,
            "blocking_known": blocking is not None,
            "runtime_observation": observation,
        },
    }


def _party(snap: dict) -> dict:
    profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    observation = _runtime_observation(snap)
    count = profile.get("party_count")
    count_known = bool(
        observation["current"] and type(count) is int
        and profile.get("confidence") not in {"unresolved", "unverified"}
    )
    return {"format": "black2-party/v1", "status": "partial" if count_known else "unresolved",
            "count": count if count_known else None,
            "count_known": count_known, "slots": [],
            "decode_status": "unverified", "contents_known": False,
            "available_fields": ["species", "nickname", "level", "hp", "max_hp", "status", "held_item", "moves"],
            "evidence": {"verified": False, "confidence": "unverified" if observation["current"] else "unresolved",
                         "reason": ("Party slot decoder is not yet available; empty slots means unknown."
                                    if observation["current"] else observation["reason"]),
                         "runtime_observation": observation}}


def _inventory(snap: dict) -> dict:
    observation = _runtime_observation(snap)
    return {"format": "black2-inventory/v1", "status": "unresolved",
            "pockets": [], "items": [], "pocket_count": None, "item_count": None,
            "decode_status": "unverified", "contents_known": False,
            "available_fields": ["pocket", "item_id", "name", "quantity", "key_item", "registered"],
            "evidence": {
                "verified": False,
                "confidence": "unverified" if observation["current"] else "unresolved",
                "reason": ("Bag decoder is not yet available; empty items means unknown, not an empty bag."
                           if observation["current"] else observation["reason"]),
                "runtime_observation": observation,
            }}


def _height_anchor(snap: dict) -> dict | None:
    """Calibrate only a unique surface under a stationary, centered live actor."""
    if not _freshness(snap)["fresh"]:
        return None
    player = snap.get("player") or {}
    position = player.get("position") or {}
    grid, world = position.get("grid") or {}, position.get("world") or {}
    live = (player.get("environment") or {}).get("tile_under") or {}
    if (player.get("locomotion") or {}).get("phase") not in {"Idle", "Turning", "Brake"}:
        return None
    if not all(isinstance(grid.get(k), int) and isinstance(world.get(k), (int, float)) for k in ("x", "y", "z")):
        return None
    if any(abs(world[k] - (grid[k] * 16 + 8)) > 0.1 for k in ("x", "z")):
        return None
    tile = _world().tile(player["zone_id"], grid["x"], grid["y"], grid["z"])
    surfaces = [s for s in tile["surfaces"] if s["sampled_tile_type"]["class"] == live.get("class")
                and s["sampled_tile_type"]["flags"] == live.get("flags") and s["height"]["chunk_relative_world_y"] is not None]
    if len(surfaces) != 1:
        return None
    return {"zone_id": player["zone_id"], "chunk": tile["rom"]["chunk"], "grid_y": grid["y"],
            "world_y": world["y"], "offset": world["y"] - surfaces[0]["height"]["chunk_relative_world_y"],
            "frame": player.get("frame"), "session_id": _freshness(snap)["session_id"]}


def _align_height(data: dict, anchor: dict | None) -> None:
    if not anchor or data["coordinate"]["zone_id"] != anchor["zone_id"] or data.get("rom", {}).get("chunk") != anchor["chunk"]:
        return
    selected = []
    for surface in data.get("surfaces", []):
        relative = surface["height"].get("chunk_relative_world_y")
        if relative is None:
            continue
        world_y = relative + anchor["offset"]
        surface["height"].update(world_y=world_y, alignment="same_chunk_live_player_anchor", anchor_frame=anchor["frame"])
        # Only same-height samples establish the actor's exact GPos floor.
        # Stairs and a different floor remain explicit surfaces for a planner.
        if data["coordinate"]["y"] == anchor["grid_y"] and abs(world_y - anchor["world_y"]) < 0.1:
            selected.append(surface)
    if len(selected) == 1:
        surface = selected[0]
        data["terrain"] = {**surface["material"], "flags": surface["raw"]["flags"]}
        data["collision"] = {**surface["collision"], "height_alignment": "candidate_same_height_in_current_chunk"}
        data["height"] = {"requested_y": data["coordinate"]["y"], "status": "candidate",
                          "selected_surface": surface["layer_index"], "anchor_frame": anchor["frame"], "world_y": surface["height"]["world_y"]}
    elif len(selected) > 1:
        data["height"] = {"requested_y": data["coordinate"]["y"], "status": "ambiguous", "selected_surface": None}


def _tile(snap: dict, zone_id: int, x: int, y: int, z: int, include_raw: bool = False) -> dict:
    data = _world().tile(zone_id, x, y, z, include_raw=include_raw)
    _align_height(data, _height_anchor(snap))
    data["observations"] = observed_navigation_graph.tile_evidence(NavNode(zone_id, x, y, z))
    _attach_runtime_tile(data, snap)
    data["freshness"] = _freshness(snap)
    data["interactions"] = _connectors_at_tile(zone_id, x, y, z)
    return data


def _attach_runtime_tile(data: dict, snap: dict) -> None:
    point = data["coordinate"]
    zone_id, x, y, z = (point[k] for k in ("zone_id", "x", "y", "z"))
    player = snap.get("player") or {}
    grid = (player.get("position") or {}).get("grid") or {}
    same_tile = player.get("zone_id") == zone_id and (grid.get("x"), grid.get("y"), grid.get("z")) == (x, y, z)
    data["runtime_tile_type"] = (player.get("environment") or {}).get("tile_under") if same_tile and _freshness(snap)["fresh"] else None
    live = data["runtime_tile_type"]
    if isinstance(live, dict) and type(live.get("class")) is int and type(live.get("flags")) is int:
        from ..world.tile_semantics import decode_tile_semantics
        semantics = decode_tile_semantics(live["class"], live["flags"])
        data["terrain"] = semantics["material"]
        data["collision"] = semantics["collision"]
        data["height"] = {"requested_y": y, "status": "runtime_player_tile", "source_frame": player.get("frame")}


def _connectors_at_tile(zone_id: int, x: int, y: int, z: int) -> list:
    # The connector service preserves unknown elevation, so callers cannot
    # confuse a projected portal with a verified portal on a bridge/floor.
    records = _connectors().query(zone_id=zone_id)["warps"]
    return _matching_connectors(records, x, z)


def _matching_connectors(records: list, x: int, z: int) -> list:
    result = []
    for record in records:
        source = record.get("source") if isinstance(record, dict) else None
        if not isinstance(source, dict):
            continue
        p = source.get("grid_candidate") or {}
        size = source.get("tile") or {}
        resolution = source.get("coordinate_resolution") or {}
        # Lightweight callers may provide only grid_candidate.  A missing
        # resolution object means "candidate by shape", never verified.
        resolution_status = resolution.get("status", "candidate") if isinstance(resolution, dict) else "candidate"
        if resolution_status == "candidate" and isinstance(p.get("x"), int) and isinstance(p.get("z"), int):
            if p["x"] <= x < p["x"] + size.get("width", 1) and p["z"] <= z < p["z"] + size.get("height", 1):
                result.append({"kind": "warp", "id": record["id"], "destination": record["destination"],
                               "floor_match": None, "status": "candidate"})
    return result


def _terrain_mark(terrain: dict, collision: dict) -> str:
    kind = terrain.get("kind") or "unknown"
    if collision.get("static_blocked") is True or collision.get("can_walk") is False:
        return "#"
    if "grass" in kind:
        return "G"
    if kind in {"water", "deep_water", "surf_water", "water_edge"}:
        return "~"
    if kind in {"ground", "normal", "road", "plain", "ground_path", "sand", "snow", "deep_sand"}:
        return "."
    if kind == "ledge":
        return "L"
    if terrain.get("interaction"):
        return "I"
    if terrain.get("hazard"):
        return "!"
    if collision.get("static_blocked") is False:
        return ":"
    return "?"


def _window(snap: dict, zone_id: int, x: int, y: int, z: int, radius: int, include_raw: bool = False) -> dict:
    data = _world().window(zone_id, x, y, z, radius, include_raw=include_raw)
    anchor = _height_anchor(snap)
    for tile in data["tiles"]:
        _align_height(tile, anchor)
        _attach_runtime_tile(tile, snap)
    data["legend"] = {"?": "unknown or unresolved elevation", "#": "static blocked surface", ".": "ground/path/sand/snow",
                      ":": "static collision flag clear; material or dynamic passability unknown", "G": "grass",
                      "~": "water/shore", "L": "directional ledge", "I": "interaction surface", "!": "terrain hazard",
                      "W": "warp projection; floor and landing not confirmed", "@": "current player", " ": "outside zone"}
    rows, projected_rows = [], []
    portals = _connectors().query(zone_id=zone_id)["warps"]
    player = snap.get("player") or {}
    grid = (player.get("position") or {}).get("grid") or {}
    for start in range(0, len(data["tiles"]), data["width"]):
        row, projected_row = "", ""
        for tile in data["tiles"][start:start + data["width"]]:
            point = tile["coordinate"]
            mark = _terrain_mark(tile["terrain"], tile["collision"])
            projections = {_terrain_mark(s["material"], s["collision"]) for s in tile.get("surfaces", [])}
            projected = next(iter(projections)) if len(projections) == 1 else "?"
            if tile["status"] in {"outside_matrix", "outside_zone", "empty_chunk"}:
                mark = projected = " "
            tile["interactions"] = _matching_connectors(portals, point["x"], point["z"])
            if tile["interactions"]:
                projected = "W"
            if player.get("zone_id") == zone_id and _freshness(snap)["fresh"] and grid == {k: point[k] for k in ("x", "y", "z")}:
                mark = "@"
                projected = "@"
            row += mark
            projected_row += projected
        rows.append(row)
        projected_rows.append(projected_row)
    data["ascii"] = "\n".join(rows)
    data["rows"] = rows
    data["projected_rows"] = projected_rows
    data["projection_policy"] = "X/Z projection of agreeing surface symbols; ignores elevation. Never use projected_rows as an executable walkability grid."
    data["origin"] = {"zone_id": zone_id, "x": x - radius, "y": y, "z": z - radius}
    data["freshness"] = _freshness(snap)
    data["portals"] = portals
    data["coverage"] = {"static_tiles": "requested bounded window", "actors": "ROM spawns are not live obstacles", "floor": "see each tile height selection"}
    return data


def _map(snap: dict, zone_id: int | None = None, radius: int = 4, include_raw: bool = False) -> dict:
    player = snap.get("player") or {}
    zone_id = zone_id if zone_id is not None else player.get("zone_id")
    if zone_id is None:
        return {"format": "black2-ai-map/v2", "status": "unavailable", "reason": "No cached player Zone; specify zone_id for a static query."}
    source = _world().zone(zone_id)
    data = {"format": "black2-ai-map/v2", "status": "decoded", "map_header_id": zone_id,
            "coordinate_space": "gen5-field-grid-v1", "matrix": source["matrix"], "rules": source["rules"],
            "events": source["events"], "actors": [], "actors_status": "live_positions_not_sampled",
            "event_coordinate_space": "ROM event coordinates; per-record units preserved",
            "player": {"zone_id": player.get("zone_id"), **(player.get("position") or {})},
            "freshness": _freshness(snap), "warps": _connectors().query(zone_id=zone_id),
            "collision": {"semantic_status": "decoded terrain class/flags with explicit height selection; dynamic obstacles require runtime evidence"}}
    grid = (player.get("position") or {}).get("grid") or {}
    if player.get("zone_id") == zone_id and all(isinstance(grid.get(k), int) for k in ("x", "y", "z")):
        data["window"] = _window(snap, zone_id, grid["x"], grid["y"], grid["z"], radius, include_raw)
    data["ai_text"] = f"BLACK2_AI_MAP/v2 ZONE={zone_id} X=east Z=south Y=elevation\n" + (data.get("window") or {}).get("ascii", "")
    data["ai_text"] += "\n" + "\n".join(
        f"WARP {w['id']} FROM {w['source']} TO {w['destination']}" for w in data["warps"]["warps"]
    )
    return data




def _door_response(zone_id: int, *, offset: int = 0, limit: int = 256,
                   include_raw: bool = False) -> dict[str, Any]:
    """Shared paginated door response used by the standalone and scene APIs."""
    doors = _door_catalog(int(zone_id), include_raw=include_raw)
    _associate_door_warps(doors, int(zone_id))
    total = len(doors)
    page = doors[offset:offset + limit]
    return {
        "format": "black2-ai-doors/v1",
        "coordinate_space": "gen5-field-world-v1",
        "grid_coordinate_space": "gen5-field-grid-v1",
        "zone_filter": int(zone_id),
        "count": len(page), "total_count": total,
        "offset": offset, "limit": limit,
        "next_offset": offset + limit if offset + limit < total else None,
        "doors": page,
        "coverage": {"source": "ROM ChunkBuildings + AreaBuildingResource", "static": True,
                     "live_transition_observed": False},
        "semantic_policy": {
            "coordinates": "door_offset is rotated by building rotation; world units are 16 per tile",
            "warp_association": "nearest same-zone Warp candidates only; association is not verified",
            "independent_asset": "false when ROM has no standalone door mesh; building asset_url remains available",
            "traversal": "collision, scripts, event flags, approach direction and landing require runtime evidence",
        },
    }




def _scene_static_actors(zone_id: int, events: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Normalize ROM NPC records for the scene contract.

    These are spawn definitions, not live actor positions.  Keeping that
    distinction in each row prevents an AI planner from treating a scripted
    NPC as a current collision obstacle.
    """
    rows: list[dict[str, Any]] = []
    for index, record in enumerate((events or {}).get("npcs") or []):
        if not isinstance(record, dict):
            continue
        identity = record.get("record_index", record.get("id", index))
        classification = npc_classifier.classify_entity(record, zone_id)
        rows.append({
            "id": f"zone:{zone_id}:npc:{identity}",
            "kind": "npc",
            "semantic_kind": classification["semantic_kind"],
            "name": classification["name"],
            "category_label": classification["category_label"],
            "live": False,
            "coordinate": _event_coordinate(zone_id, record),
            "sprite_id": record.get("sprite_id"),
            "movement_id": record.get("movement_id"),
            "facing_id": record.get("facing_id"),
            "script_id": record.get("script_id"),
            "flag_id": record.get("flag_id"),
            "interaction": classification["interaction"],
            "trainer": classification["trainer"],
            "lifecycle": classification["lifecycle"],
            "availability": classification["lifecycle"],
            "source": "rom:/a/1/2/6",
        })
    return rows


def _scene_runtime_actors(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract optional live actor overlays without inventing positions."""
    candidates: Any = snap.get("actors")
    if candidates is None:
        semantic = snap.get("semantic") if isinstance(snap.get("semantic"), dict) else {}
        candidates = semantic.get("actors")
    if isinstance(candidates, dict):
        candidates = candidates.get("actors") or candidates.get("runtime") or []
    return [dict(item) for item in candidates if isinstance(item, dict)] if isinstance(candidates, list) else []


def _scene_geometry(zone_id: int, map_data: dict[str, Any], doors: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose renderable terrain/building candidates and their source URLs."""
    matrix = map_data.get("matrix") if isinstance(map_data.get("matrix"), dict) else {}
    cells: list[dict[str, Any]] = []
    raw_cells = matrix.get("cells") if isinstance(matrix.get("cells"), list) else []
    for cell in raw_cells:
        if not isinstance(cell, dict):
            continue
        x, z, chunk_id = cell.get("x"), cell.get("z"), cell.get("chunk_id")
        if not isinstance(x, int) or not isinstance(z, int):
            continue
        if chunk_id == 0xFFFFFFFF:
            continue
        cells.append({
            "id": f"terrain-{zone_id}-{x}-{z}",
            "cell": {"x": x, "z": z},
            "chunk_id": chunk_id,
            "asset_url": f"/api/v1/map/v5/terrain/{zone_id}/{x}/{z}/model.glb",
            "semantic_role": "map_geometry",
            "asset_status": "candidate",
        })
    building_map: dict[str, dict[str, Any]] = {}
    for door in doors:
        if not isinstance(door, dict):
            continue
        building = door.get("building") if isinstance(door.get("building"), dict) else {}
        identity = building.get("instance_id") or door.get("id")
        if identity is None:
            continue
        building_map[str(identity)] = {
            "id": identity,
            "model_uid": building.get("model_uid"),
            "chunk_id": building.get("chunk_id"),
            "placement_index": building.get("placement_index"),
            "rotation_degrees": building.get("rotation_degrees"),
            "door_uid": door.get("door_uid"),
            "world": (door.get("coordinate") or {}).get("world"),
            "asset_url": (door.get("resource") or {}).get("asset_url"),
            "semantic_role": "building_with_door_candidate",
        }
    return {
        "coordinate_space": "gen5-field-world-v1",
        "grid_coordinate_space": "gen5-field-grid-v1",
        "matrix": matrix,
        "terrain_cells": cells,
        "building_candidates": list(building_map.values()),
        "asset_policy": {
            "terrain": "BMD0/BTX0 GLB URL is a render candidate; texture pairing may remain unresolved",
            "buildings": "building asset includes DoorUID metadata; independent door mesh may not exist",
        },
        "status": "decoded" if cells or matrix else "unresolved",
        "source": "ROM matrix/chunk and AreaBuildingResource metadata",
    }


def _scene_collision(map_data: dict[str, Any]) -> dict[str, Any]:
    """Package static tile collision alongside explicit dynamic limitations."""
    window = map_data.get("window") if isinstance(map_data.get("window"), dict) else {}
    tiles = []
    for tile in window.get("tiles") or []:
        if not isinstance(tile, dict):
            continue
        tiles.append({
            "coordinate": tile.get("coordinate"),
            "status": tile.get("status"),
            "terrain": tile.get("terrain"),
            "collision": tile.get("collision"),
            "height": tile.get("height"),
        })
    static = map_data.get("collision") if isinstance(map_data.get("collision"), dict) else {}
    return {
        "status": "candidate",
        "static": static,
        "tiles": tiles,
        "dynamic": {
            "actors": "not_sampled",
            "event_flags": "not_decoded",
            "scripts": "not_decoded",
            "transport_mode": "runtime_dependent",
        },
        "executable": False,
        "reason": "Static terrain eligibility is available, but dynamic occupancy, scripts and floor transitions require runtime evidence.",
    }


def _scene_projection(snap: dict[str, Any], map_data: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    """Compose the stable AI scene envelope from the existing map services."""
    if not isinstance(map_data, dict) or map_data.get("status") != "decoded":
        reason = (map_data or {}).get("reason") or (map_data or {}).get("error") or "map scene unavailable"
        empty = {"portals": [], "doors": [], "actors": {"runtime": [], "rom_static_npcs": []},
                 "geometry": {"status": "unresolved", "terrain_cells": [], "building_candidates": []},
                 "collision": {"status": "unresolved", "executable": False}}
        return {"format": "black2-ai-scene/v1", "status": "unavailable", "reason": reason,
                "normalized": empty, **empty, "warps": {"warps": [], "count": 0},
                "zone_id": map_data.get("map_header_id"), "map": map_data}

    zone_id = map_data.get("map_header_id")
    if not isinstance(zone_id, int):
        return {"format": "black2-ai-scene/v1", "status": "unresolved", "reason": "map_header_id is unavailable", "map": map_data}
    warps = map_data.get("warps") if isinstance(map_data.get("warps"), dict) else {"warps": []}
    portals = list(warps.get("warps") or []) if isinstance(warps.get("warps"), list) else []
    doors: list[dict[str, Any]] = []
    doors_status = {"status": "unresolved", "reason": "Door resource service was not queried."}
    try:
        doors = _door_catalog(zone_id, include_raw=include_raw)
        _associate_door_warps(doors, zone_id)
        doors_status = {"status": "decoded", "reason": "ROM DoorUID/building metadata"}
    except (FileNotFoundError, OSError, ValueError, RuntimeError, IndexError, TypeError, AttributeError) as error:
        doors_status = {"status": "unavailable", "reason": f"{type(error).__name__}: {error}"}
    static_actors = _scene_static_actors(zone_id, map_data.get("events"))
    runtime_actors = _scene_runtime_actors(snap)

    # Semantic categorization
    items = [a for a in static_actors if a.get("semantic_kind") == "OVERWORLD_ITEM"]
    trainers = [a for a in static_actors if a.get("semantic_kind") == "NPC_TRAINER"]
    talkers = [a for a in static_actors if a.get("semantic_kind") == "NPC_TALKER"]
    legendaries = [a for a in static_actors if a.get("semantic_kind") == "LEGENDARY_OVERWORLD"]
    obstacles = [a for a in static_actors if a.get("semantic_kind") == "DYNAMIC_OBSTACLE"]

    actors = {
        "runtime": runtime_actors,
        "rom_static_npcs": static_actors,
        "items": items,
        "trainers": trainers,
        "talkers": talkers,
        "legendaries": legendaries,
        "obstacles": obstacles,
        "count": len(runtime_actors) + len(static_actors),
        "runtime_positions_status": "sampled" if runtime_actors else "not_sampled",
    }
    geometry = _scene_geometry(zone_id, map_data, doors)
    collision = _scene_collision(map_data)
    normalized = {"portals": portals, "doors": doors, "actors": actors, "geometry": geometry, "collision": collision}
    return {
        "format": "black2-ai-scene/v1",
        "status": "decoded",
        "zone_id": zone_id,
        "coordinate_space": "gen5-field-world-v1",
        "grid_coordinate_space": "gen5-field-grid-v1",
        "normalized": normalized,
        "portals": portals,
        "warps": warps,
        "doors": doors,
        "doors_status": doors_status,
        "actors": actors,
        "geometry": geometry,
        "collision": collision,
        "player": map_data.get("player"),
        "events": map_data.get("events"),
        "rules": map_data.get("rules"),
        "freshness": map_data.get("freshness"),
        "evidence": {
            "source": ["SemanticWorldService", "SemanticConnectorService", "DoorUID catalog", "RuntimeHub snapshot"],
            "confidence": "candidate",
            "verified": False,
            "reason": "ROM geometry and event records are combined; traversal, dynamic actor occupancy and script gates remain unverified.",
        },
    }


def _event_integer(value: Any) -> int | None:
    """Read an event coordinate without silently coercing booleans/floats."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _event_coordinate(zone_id: int, record: dict[str, Any]) -> dict[str, Any] | None:
    """Map the ROM event plane to the public grid while preserving its axes.

    Gen-5 overworld records store the horizontal plane as ``x``/``y`` and
    elevation as ``z``.  The public grid uses ``x``/``z`` horizontally and
    ``y`` for elevation, so this mapping is intentionally explicit.
    """
    event_x = _event_integer(record.get("x"))
    event_y = _event_integer(record.get("y"))
    event_z = _event_integer(record.get("z"))
    if event_x is None or event_y is None or event_z is None:
        return None
    return {
        "space": "gen5-field-grid-v1",
        "zone_id": zone_id,
        "x": event_x,
        "y": event_z,
        "z": event_y,
        "status": "candidate",
        "source_units": record.get("coordinate_units") or "unknown",
        "axis_mapping": "event.x->grid.x; event.y->grid.z; event.z->grid.y",
    }


def _warp_coordinate(record: dict[str, Any], zone_id: int) -> dict[str, Any] | None:
    """Derive a horizontal warp cell when no enriched connector is available."""
    x_world = record.get("x_world")
    z_world = record.get("y_world")
    if not isinstance(x_world, (int, float)) or isinstance(x_world, bool):
        return None
    if not isinstance(z_world, (int, float)) or isinstance(z_world, bool):
        return None
    return {
        "space": "gen5-field-grid-v1",
        "zone_id": zone_id,
        "x": int(x_world // 16),
        "y": None,
        "z": int(z_world // 16),
        "status": "candidate",
        "source_units": record.get("coordinate_units") or "unknown",
        "axis_mapping": "floor(event.x_world/16)->grid.x; floor(event.y_world/16)->grid.z; elevation unresolved",
    }


def _interaction_affordance(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    """Describe an input candidate without claiming that a script permits it."""
    if kind == "warp":
        action, input_hint = "enter_warp", "walk_onto_trigger_or_face_and_press_A"
        status = "candidate"
    elif kind == "npc":
        action, input_hint = "talk", "face_actor_then_press_A"
        status = "candidate"
    elif kind == "trigger":
        action, input_hint = "activate_trigger", "walk_onto_trigger_tile"
        status = "candidate"
    else:
        action, input_hint = "interact", "face_object_then_press_A"
        # Furniture with no script is normally decorative, but the record does
        # not prove that. Keep it explicitly unresolved for an autonomous client.
        status = "candidate" if _event_integer(record.get("script_id")) not in (None, 0) else "unverified"
    return {
        "action": action,
        "status": status,
        "can_execute": None,
        "input": input_hint,
        "endpoint": "/api/actions/press" if kind != "trigger" else "/api/actions/press",
        "requires": ["runtime position", "collision/approach check", "script and flag check"],
    }


def _interaction_records(
    zone_id: int,
    events: dict[str, Any] | None,
    connector_payload: dict[str, Any] | None,
    *,
    origin_x: int | None = None,
    origin_y: int | None = None,
    origin_z: int | None = None,
    radius: int = 8,
    filter_y: bool = False,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Normalize static overworld entities into an AI interaction index.

    This is a read-only ROM projection.  Dynamic actor positions, event flags,
    facing requirements and script outcomes remain unknown until observed.
    """
    events = events if isinstance(events, dict) else {}
    connectors = connector_payload if isinstance(connector_payload, dict) else {}
    connector_rows = connectors.get("warps") if isinstance(connectors.get("warps"), list) else []
    by_warp_id = {
        row.get("source", {}).get("warp_id"): row
        for row in connector_rows
        if isinstance(row, dict) and isinstance(row.get("source"), dict)
    }
    rows: list[dict[str, Any]] = []
    kind_order = {"warp": 0, "npc": 1, "furniture": 2, "trigger": 3}

    def append_row(kind: str, index: int, record: dict[str, Any], coordinate: dict[str, Any] | None,
                   connector: dict[str, Any] | None = None) -> None:
        if not isinstance(record, dict):
            return
        if origin_x is not None and origin_z is not None:
            if coordinate is None or not isinstance(coordinate.get("x"), int) or not isinstance(coordinate.get("z"), int):
                return
            dx = coordinate["x"] - origin_x
            dz = coordinate["z"] - origin_z
            if max(abs(dx), abs(dz)) > radius:
                return
            distance = {"dx": dx, "dz": dz, "chebyshev": max(abs(dx), abs(dz))}
            if filter_y and origin_y is not None and isinstance(coordinate.get("y"), int):
                if coordinate["y"] != origin_y:
                    return
        else:
            distance = None
        if kind == "warp":
            interaction_id = f"zone:{zone_id}:warp:{index}"
        else:
            identity = record.get("record_index", record.get("id", index))
            interaction_id = f"zone:{zone_id}:{kind}:{identity}"
        public_record = dict(record)
        if not include_raw:
            public_record.pop("raw_record_hex", None)
        row = {
            "id": interaction_id,
            "kind": kind,
            "coordinate": coordinate,
            "distance": distance,
            "affordance": _interaction_affordance(kind, record),
            "availability": {
                "status": "unknown",
                "can_interact": None,
                "reason": "ROM event data does not decode runtime flags, scripts, facing or dynamic occupancy.",
            },
            "runtime": {"position_sampled": False, "same_tile": None},
            "source": "rom:/a/1/2/6",
            "record": public_record,
        }
        if connector is not None:
            row["connector"] = connector
            row["destination"] = connector.get("destination")
            row["role"] = connector.get("role")
        rows.append(row)

    for raw in events.get("warps") or []:
        if not isinstance(raw, dict):
            continue
        index = _event_integer(raw.get("id"))
        if index is None:
            index = len(rows)
        connector = by_warp_id.get(index)
        coordinate = ((connector or {}).get("source") or {}).get("grid_candidate") if connector else None
        if not isinstance(coordinate, dict):
            coordinate = _warp_coordinate(raw, zone_id)
        append_row("warp", index, raw, coordinate, connector)

    singular_kind = {"npcs": "npc", "furniture": "furniture", "triggers": "trigger"}
    for kind in ("npcs", "furniture", "triggers"):
        for index, raw in enumerate(events.get(kind) or []):
            append_row(singular_kind[kind], index, raw, _event_coordinate(zone_id, raw))

    rows.sort(key=lambda row: (
        row["distance"]["chebyshev"] if row.get("distance") else 10**9,
        kind_order.get(row.get("kind"), 99),
        row.get("id", ""),
    ))
    counts = {kind: sum(row.get("kind") == kind for row in rows) for kind in kind_order}
    query = {
        "zone_id": zone_id,
        "origin": {"space": "gen5-field-grid-v1", "zone_id": zone_id, "x": origin_x, "y": origin_y, "z": origin_z}
        if origin_x is not None and origin_z is not None else None,
        "radius_tiles": radius if origin_x is not None and origin_z is not None else None,
        "distance_metric": "chebyshev",
        "elevation_policy": "unknown elevations are retained unless an explicit known y mismatches",
    }
    return {
        "format": "black2-ai-interactions/v1",
        "coordinate_space": "gen5-field-grid-v1",
        "query": query,
        "count": len(rows),
        "interactions": rows,
        "counts": counts,
        "coverage": {
            "rom_events": "decoded static furniture/NPC/warp/trigger records",
            "runtime_actor_positions": "not_sampled",
            "event_flags": "not_decoded",
            "script_effects": "not_decoded",
            "approach_and_facing": "not_verified",
        },
        "semantic_policy": {
            "affordances": "input candidates only; can_execute stays null until runtime observation",
            "coordinates": "event x/y are horizontal; event z is elevation and is mapped to public grid y",
            "warps": "connector destination and landing remain ROM candidates until a live transition is observed",
        },
    }


@router.get("/game/capabilities")
async def game_capabilities() -> dict:
    return {"format": "black2-game-capabilities/v1",
            "coordinate_contract": {"space": "gen5-field-grid-v1", "zone_id": "Zone header id", "x": "east", "y": "elevation/floor", "z": "south", "world_units_per_tile": 16},
            "read": {"current": "/api/v1/game/current", "layers": "/api/v1/game/layers", "state": "/api/v1/game/state", "party": "/api/v1/game/party", "inventory": "/api/v1/game/inventory",
                     "map": "/api/v1/ai/map", "scene": "/api/v1/ai/map/scene", "tile": "/api/v1/ai/map/tile?zone_id=&x=&y=&z=", "window": "/api/v1/ai/map/window",
                     "warps": "/api/v1/ai/map/warps", "doors": "/api/v1/ai/map/doors", "zone": "/api/v1/ai/map/zone/{zone_id}", "materials": "/api/v1/ai/materials",
                     "map_interactions": "/api/v1/ai/map/interactions", "static_scene": "/api/v1/map/v6/scene/zone/{zone_id}",
                     "context": "/api/v1/ai/context", "actions": "/api/v1/game/actions", "dialogue": "/api/dialogue/history",
                     "tasks": "/api/v1/game/tasks", "task": "/api/v1/game/tasks/{task_id}", "objectives": "/api/v1/game/objectives",
                     "flags": "/api/v1/game/flags", "cutscene": "/api/v1/game/cutscene",
                     "interactions": "/api/v1/game/interactions", "battle": "/api/v1/battle/state",
                     "battle_request": "/api/v1/battle/request", "battle_evidence": "/api/v1/battle/evidence"},
            "write": {"navigation_plan": "/api/v1/navigation/plans", "navigation_task": "/api/v1/navigation/tasks",
                      "press": "/api/actions/press", "touch": "/api/actions/touch", "dialogue_advance": "/api/actions/dialogue/advance", "dialogue_choice": "/api/actions/dialogue/choice"},
            "support": {"terrain": "ROM decoded", "warp_targets": "ROM referenced candidates", "interaction_index": "ROM static candidates", "static_scene_preview": "ROM-only read-only scene with synthetic camera anchor",
                        "cross_zone_execution": False,
                        "party_contents": False, "inventory_contents": False, "battle_presence_candidate": True,
                        "layered_game_state": True, "battle_semantic_actions": False},
            "evidence_policy": "Decoded source facts and runtime traversal are distinct. Unknown is null, never an empty-game assertion."}


@router.get("/game/state")
async def game_state() -> dict:
    return _state(_snapshot())


@router.get("/game/tasks")
async def game_tasks() -> dict:
    """Return the current task projection with explicit evidence confidence."""
    return _tasks(_snapshot())


@router.get("/game/tasks/{task_id}")
async def game_task(task_id: str) -> dict:
    """Return one task, keeping a stable 404 for unknown task identifiers."""
    payload = _tasks(_snapshot())
    task = next((item for item in payload.get("tasks", []) if item.get("task_id") == task_id), None)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Unknown task: {task_id}")
    return {"format": "black2-game-task/v1", "task": task, "evidence": payload.get("evidence", {})}


@router.get("/game/objectives")
async def game_objectives() -> dict:
    """Return immediate, mid-term and long-term objective projections."""
    return _objectives(_snapshot())


@router.get("/game/flags")
async def game_flags() -> dict:
    """Return decoded runtime flags when available; unknown remains unknown."""
    return _flags(_snapshot())


@router.get("/game/cutscene")
async def game_cutscene() -> dict:
    """Classify the current modal/script state for automation preconditions."""
    return _cutscene(_snapshot())


async def _game_interactions(
    *,
    zone_id: Zone | None,
    x: Coord | None,
    y: Coord | None,
    z: Coord | None,
    radius: Radius,
    include_raw: bool,
) -> dict[str, Any]:
    snap = _snapshot()
    player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
    position = player.get("position") if isinstance(player.get("position"), dict) else {}
    live_grid = position.get("grid") if isinstance(position.get("grid"), dict) else {}
    resolved_zone = zone_id if zone_id is not None else player.get("zone_id")
    if not isinstance(resolved_zone, int):
        raise HTTPException(status_code=503, detail="Current Zone is unavailable; specify zone_id.")
    provided = [x is not None, y is not None, z is not None]
    if any(provided) and not (x is not None and z is not None):
        raise HTTPException(status_code=422, detail="x and z must be provided together; y is optional.")
    origin_x, origin_y, origin_z = x, y, z
    origin_source = "query"
    if x is None and z is None and player.get("zone_id") == resolved_zone and _freshness(snap)["fresh"]:
        if all(isinstance(live_grid.get(key), int) for key in ("x", "y", "z")):
            origin_x, origin_y, origin_z = (live_grid[key] for key in ("x", "y", "z"))
            origin_source = "runtime_player"
    elif x is None and z is None:
        origin_source = "none_stale_or_unresolved"

    def build() -> dict[str, Any]:
        world = _world().zone(resolved_zone)
        connectors = _connectors().query(zone_id=resolved_zone, include_raw=include_raw)
        result = _interaction_records(
            resolved_zone,
            world.get("events") if isinstance(world, dict) else {},
            connectors,
            origin_x=origin_x,
            origin_y=origin_y,
            origin_z=origin_z,
            radius=radius,
            filter_y=origin_source == "query" and y is not None,
            include_raw=include_raw,
        )
        result["query"]["origin_source"] = origin_source
        result["freshness"] = _freshness(snap)
        result["runtime"] = {
            "zone_id": player.get("zone_id"),
            "frame": player.get("frame"),
            "position_sampled": bool(_freshness(snap)["fresh"] and player.get("status") == "resolved"),
        }
        return result

    return await _rom_call(build)


@router.get("/game/interactions")
async def game_interactions(
    zone_id: Zone | None = None,
    x: Coord | None = None,
    y: Coord | None = None,
    z: Coord | None = None,
    radius: Radius = 8,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Return nearby static entities and their unverified input affordances."""
    return await _game_interactions(zone_id=zone_id, x=x, y=y, z=z, radius=radius, include_raw=include_raw)


@router.get("/game/party")
async def game_party() -> dict:
    return _party(_snapshot())


@router.get("/game/inventory")
async def game_inventory() -> dict:
    return _inventory(_snapshot())


@router.get("/game/actions")
async def game_actions(request: Request) -> dict:
    """Discover exact registered input/navigation schemas, including $ref definitions."""
    spec = request.app.openapi()
    actions = []
    for path, operations in spec["paths"].items():
        if path.startswith(("/api/actions/", "/api/v1/navigation/")):
            for method, operation in operations.items():
                if method not in {"get", "post"}:
                    continue
                actions.append({"method": method.upper(), "path": path, "summary": operation.get("summary"),
                                "request_body": operation.get("requestBody"), "parameters": operation.get("parameters", []),
                                "mutates_game": method == "post" and not path.endswith("/plans")})
    return {"format": "black2-game-actions/v1", "actions": actions, "schemas": spec.get("components", {}).get("schemas", {}),
            "state": _state(_snapshot()), "policy": "Availability of a route does not establish the game-state preconditions for an action."}


@router.get("/ai/map")
async def ai_map(zone_id: Zone | None = None, radius: Radius = 4, include_raw: bool = False, text: bool = False):
    data = await _rom_call(_map, _snapshot(), zone_id, radius, include_raw)
    return PlainTextResponse(data.get("ai_text", data.get("reason", ""))) if text else data


@router.get("/ai/map/tile")
async def ai_map_tile(zone_id: Zone, x: Coord, y: Coord, z: Coord, include_raw: bool = False) -> dict:
    return await _rom_call(_tile, _snapshot(), zone_id, x, y, z, include_raw)


@router.get("/ai/map/window")
async def ai_map_window(zone_id: Zone, x: Coord, y: Coord, z: Coord, radius: Radius = 4, include_raw: bool = False, text: bool = False):
    data = await _rom_call(_window, _snapshot(), zone_id, x, y, z, radius, include_raw)
    return PlainTextResponse(f"ZONE={zone_id} Y={y} BOUNDS={data['bounds']}\n{data['ascii']}\nLEGEND={data['legend']}") if text else data


@router.get("/ai/map/warps")
async def ai_map_warps(zone_id: Zone | None = None, offset: Annotated[int, Query(ge=0)] = 0,
                       limit: Annotated[int, Query(ge=1, le=2048)] = 256, include_raw: bool = False) -> dict:
    return await _rom_call(lambda: _connectors().query(zone_id=zone_id, offset=offset, limit=limit, include_raw=include_raw))


@router.get("/ai/map/doors")
async def ai_map_doors(
    zone_id: Zone | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2048)] = 256,
    include_raw: bool = False,
) -> dict[str, Any]:
    """List static DoorUID candidates with coordinates and warp hints.

    Door metadata is attached to building resources in the ROM.  The endpoint
    deliberately reports independent door meshes and traversal as unresolved;
    a GLB building URL is available, but it is not a claim that the door has a
    separate render asset or that a script-gated transition will succeed.
    """
    def build() -> dict[str, Any]:
        selected_zone = zone_id
        if selected_zone is None:
            selected_zone = (_snapshot().get("player") or {}).get("zone_id")
        if selected_zone is None:
            raise HTTPException(503, detail="Current Zone is unavailable; specify zone_id.")
        doors = _door_catalog(int(selected_zone), include_raw=include_raw)
        _associate_door_warps(doors, int(selected_zone))
        total = len(doors)
        page = doors[offset:offset + limit]
        return {
            "format": "black2-ai-doors/v1",
            "coordinate_space": "gen5-field-world-v1",
            "grid_coordinate_space": "gen5-field-grid-v1",
            "zone_filter": int(selected_zone),
            "count": len(page), "total_count": total,
            "offset": offset, "limit": limit,
            "next_offset": offset + limit if offset + limit < total else None,
            "doors": page,
            "coverage": {"source": "ROM ChunkBuildings + AreaBuildingResource", "static": True,
                         "live_transition_observed": False},
            "semantic_policy": {
                "coordinates": "door_offset is rotated by building rotation; world units are 16 per tile",
                "warp_association": "nearest same-zone Warp candidates only; association is not verified",
                "independent_asset": "false when ROM has no standalone door mesh; building asset_url remains available",
                "traversal": "collision, scripts, event flags, approach direction and landing require runtime evidence",
            },
        }
    return await _rom_call(build)


@router.get("/ai/map/interactions")
async def ai_map_interactions(
    zone_id: Zone | None = None,
    x: Coord | None = None,
    y: Coord | None = None,
    z: Coord | None = None,
    radius: Radius = 8,
    include_raw: bool = False,
) -> dict[str, Any]:
    """AI map alias for the normalized static interaction index."""
    return await _game_interactions(zone_id=zone_id, x=x, y=y, z=z, radius=radius, include_raw=include_raw)


@router.get("/ai/map/portals")
async def ai_map_portals(zone_id: Zone | None = None) -> dict:
    zone_id = zone_id if zone_id is not None else (_snapshot().get("player") or {}).get("zone_id")
    if zone_id is None:
        raise HTTPException(503, detail="Current Zone is unavailable; specify zone_id.")
    return await ai_map_warps(zone_id)


@router.get("/ai/map/zone/{zone_id}")
async def ai_map_zone(zone_id: int) -> dict:
    def build():
        return {"format": "black2-ai-zone/v2", "zone": _graph().zone(zone_id), "map": _world().zone(zone_id),
                "connectors": _connectors().query(zone_id=zone_id)["warps"]}
    return await _rom_call(build)


@router.get("/ai/map/scene")
async def ai_map_scene(
    zone_id: Zone | None = None,
    radius: Radius = 4,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Return one normalized scene envelope for AI and the map workbench.

    The lower-level map, warp and door endpoints remain available for callers
    that need a narrower payload.  This route deliberately keeps aliases at
    the top level while placing the canonical five-part projection under
    ``normalized``.
    """
    snap = _snapshot()
    map_data = await _rom_call(_map, snap, zone_id, radius, include_raw)
    return _scene_projection(snap, map_data, include_raw=include_raw)


@router.get("/ai/materials")
async def ai_materials() -> dict:
    from ..world.tile_semantics import tile_semantics_catalog
    return tile_semantics_catalog()


@router.get("/ai/semantics")
async def ai_semantics() -> dict:
    return {"format": "black2-ai-semantics/v2",
            "coordinate_spaces": {"gen5-field-grid-v1": {"fields": ["zone_id", "x", "y", "z"], "y": "height layer"},
                                  "gen5-field-world-v1": {"units_per_tile": 16, "tile_center": "x*16+8,z*16+8"}},
            "terrain_layout": "interleaved 8-byte records: height descriptor, height index, tile class, flags",
            "tile": {"collision": "static eligibility does not include NPCs, scripts, inventory or current transport mode", "height": "multiple surfaces preserved; ambiguous elevation is unknown"},
            "interactions": {"endpoint": "/api/v1/ai/map/interactions", "kinds": ["warp", "npc", "furniture", "trigger"],
                             "execution": "affordance is a candidate input hint; availability.can_interact remains null until runtime evidence"},
            "scene": {"endpoint": "/api/v1/ai/map/scene", "fields": ["normalized", "portals", "doors", "actors", "geometry", "collision"],
                      "read_only": True, "dynamic_occupancy": "runtime-dependent", "cross_zone": "landing and traversal require runtime observation"},
            "confidence": {"verified": "explicit runtime/source evidence", "candidate": "structural candidate without traversal proof", "unverified": "no established meaning"},
            "materials_url": "/api/v1/ai/materials"}


@router.get("/ai/context")
async def ai_context(include_raw: bool = False, radius: Radius = 4) -> dict:
    snap = _snapshot()
    try:
        map_data = await _rom_call(_map, snap, None, radius, include_raw)
    except HTTPException as error:
        map_data = {"status": "unavailable", "error": error.detail}
    interactions = None
    doors = None
    if map_data.get("status") == "decoded":
        player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
        position = player.get("position") if isinstance(player.get("position"), dict) else {}
        grid = position.get("grid") if isinstance(position.get("grid"), dict) else {}
        zone_id = map_data.get("map_header_id")
        origin = {
            key: grid.get(key)
            for key in ("x", "y", "z")
        }
        if isinstance(zone_id, int) and all(isinstance(origin.get(key), int) for key in ("x", "y", "z")):
            interactions = _interaction_records(
                zone_id,
                map_data.get("events"),
                map_data.get("warps"),
                origin_x=origin["x"],
                origin_y=origin["y"],
                origin_z=origin["z"],
                radius=min(radius, 16),
                include_raw=include_raw,
            )
            interactions["query"]["origin_source"] = "runtime_player" if _freshness(snap)["fresh"] else "cached_player"
        if isinstance(zone_id, int):
            try:
                # Keep the context bounded but complete for the current Zone;
                # door records are small and include the warp candidates an
                # autonomous client needs to decide whether to inspect a
                # building entrance next.
                doors = await _rom_call(_door_response, zone_id, offset=0, limit=2048, include_raw=include_raw)
            except HTTPException as error:
                doors = {"format": "black2-ai-doors/v1", "status": "unavailable", "zone_filter": zone_id,
                         "doors": [], "count": 0, "total_count": 0, "error": error.detail}
    return {"format": "black2-ai-context/v2", "state": _state(snap), "party": _party(snap), "inventory": _inventory(snap),
            "dialogue": snap.get("dialogue"), "tasks": _tasks(snap), "objectives": _objectives(snap),
            "flags": _flags(snap), "cutscene": _cutscene(snap), "map": map_data, "warps": map_data.get("warps"),
            "doors": doors, "interactions": interactions, "semantics": await ai_semantics(),
            "scene_url": "/api/v1/ai/map/scene",
            "navigation": {"capabilities": "/api/v1/navigation/capabilities", "plan": "/api/v1/navigation/plans", "task": "/api/v1/navigation/tasks"},
            "actions_url": "/api/v1/game/actions", "limitations": ["Bag/party contents and battle actions are not decoded.",
                "ROM connectors do not authorize cross-Zone execution.", "Current NPC positions and event flags may change static walkability."]}


@router.get("/ai/npcs")
async def ai_npcs(
    zone_id: Zone | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """Expose classified, human-readable NPC/entity semantics for the scene."""
    snap = _snapshot()
    selected_zone = zone_id
    if selected_zone is None:
        selected_zone = (snap.get("player") or {}).get("zone_id")
    if selected_zone is None:
        raise HTTPException(503, detail="Current Zone is unavailable; specify zone_id.")

    source = await _rom_call(lambda: _world().zone(int(selected_zone)))
    static_actors = _scene_static_actors(int(selected_zone), source.get("events"))

    if kind:
        kind_upper = kind.strip().upper()
        filtered = [a for a in static_actors if a.get("semantic_kind") == kind_upper]
    else:
        filtered = static_actors

    return {
        "format": "black2-ai-npcs/v1",
        "zone_id": int(selected_zone),
        "total_count": len(static_actors),
        "count": len(filtered),
        "filter_kind": kind,
        "npcs": filtered,
    }


@router.get("/ai/items")
async def ai_items(zone_id: Zone | None = None) -> dict[str, Any]:
    """Direct lookup of all overworld item balls and pickup candidates in the scene."""
    res = await ai_npcs(zone_id=zone_id, kind="OVERWORLD_ITEM")
    return {
        "format": "black2-ai-items/v1",
        "zone_id": res["zone_id"],
        "count": res["count"],
        "items": res["npcs"],
    }


@router.get("/ai/trainers")
async def ai_trainers(zone_id: Zone | None = None) -> dict[str, Any]:
    """Direct lookup of all battle trainers and line-of-sight actors in the scene."""
    res = await ai_npcs(zone_id=zone_id, kind="NPC_TRAINER")
    return {
        "format": "black2-ai-trainers/v1",
        "zone_id": res["zone_id"],
        "count": res["count"],
        "trainers": res["npcs"],
    }

