"""AI observation API: strict ROM terrain plus one cached runtime snapshot."""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any
import asyncio
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from starlette.concurrency import run_in_threadpool

from ..memory.reader import MemoryReader
from ..runtime.hub import RuntimeHub
from ..runtime.events import agent_event_bus
from ..runtime.layered_status import normalize_screen_type, project_layered_state
from ..runtime.wait_state import derive_wait_state
from ..world.gen5_rom_map import Gen5RomMap
from ..world.map_graph import RomMapGraphService, ZONE_LABEL_OVERRIDES
from ..world.observed_navigation import NavNode, observed_navigation_graph
from ..world.semantic_world import SemanticWorldService
from ..world.npc_classifier import npc_classifier
from ..world.runtime_actor_overlay import runtime_actor_overlay_service
from ..world.npc_battle_status import build_npc_battle_status
from ..runtime.npc_battle_history import npc_battle_history
from ..decoders.trainer_rom import TrainerCatalogError, TrainerRomCatalog
from ..decoders.party_runtime import PlayerPartyDecoder
from ..decoders.inventory_runtime import PlayerInventoryDecoder
from ..dex.store import DexStore
from ..state.memory_goals import goal_memory_manager
from ..state.playtest_memory import playtest_memory
from ..progression.state import progression_state_service
from ..world.runtime_player_state import player_runtime_service


router = APIRouter(prefix="/api/v1", tags=["ai-semantic"])
Zone = Annotated[int, Query(ge=0, le=65534)]
Coord = Annotated[int, Query(ge=-32768, le=32767)]
Radius = Annotated[int, Query(ge=0, le=16)]
_hub: RuntimeHub | None = None
_reader: MemoryReader | None = None
_rom_graph: RomMapGraphService | None = None
_world_service: SemanticWorldService | None = None
_trainer_catalog: TrainerRomCatalog | None = None
_trainer_catalog_error: str | None = None
_party_decoder = PlayerPartyDecoder()
_inventory_decoder = PlayerInventoryDecoder()
_dex: DexStore | None = None
_connector_service = None
_door_world_service = None
_services_lock = threading.RLock()


def _get_dex() -> DexStore:
    global _dex
    if _dex is None:
        _dex = DexStore()
    return _dex


def configure_semantic_routes(reader: MemoryReader, hub: RuntimeHub) -> None:
    global _reader, _hub, _trainer_catalog, _trainer_catalog_error
    _reader = reader
    _hub = hub
    _trainer_catalog = None
    _trainer_catalog_error = None
    _party_decoder.configure(reader)
    _inventory_decoder.configure(reader)


def _npc_trainer_catalog() -> TrainerRomCatalog | None:
    global _trainer_catalog, _trainer_catalog_error
    if _trainer_catalog is not None:
        return _trainer_catalog
    if _trainer_catalog_error is not None:
        return None
    try:
        _trainer_catalog = TrainerRomCatalog()
    except Exception as exc:
        _trainer_catalog_error = f"{type(exc).__name__}: {exc}"
        return None
    return _trainer_catalog


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
            _connector_service = SemanticConnectorService(graph)
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
        "zone_id": player.get("zone_id") or (snap.get("map") or {}).get("zone_id"),
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
    confidence_ok = source_profile.get("confidence") not in {"unresolved", "unverified"}
    if party_count is None and _party_decoder.latest and _party_decoder.latest.get("status") in ("candidate", "resolved", "partial"):
        party_count = _party_decoder.latest.get("count")
        if badges is None:
            badges = 0
        confidence_ok = True
    progress_sufficient = (
        type(party_count) is int
        and (party_count == 0 or type(badges) is int)
        and confidence_ok
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
            "active_milestone_id": goal.get("active_milestone_id") if inputs_current else None,
            "clear_condition": goal.get("clear_condition") if inputs_current else None,
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
    latest = progression_state_service.latest if isinstance(progression_state_service.latest, dict) else {}
    event_work = latest.get("event_work") if isinstance(latest.get("event_work"), dict) else {}
    flag_bytes = event_work.get("flag_bytes") if isinstance(event_work.get("flag_bytes"), dict) else {}
    set_ids = flag_bytes.get("set_flag_ids") if isinstance(flag_bytes.get("set_flag_ids"), list) else []
    raw_verified = bool(flag_bytes.get("raw_hex")) and latest.get("status") in {"verified", "partial"}

    return {
        "format": "black2-game-flags/v1",
        "status": "available" if values_current else ("raw_verified" if raw_verified else "unresolved"),
        "decode_status": decoder_status if observation["current"] else ("verified_raw" if raw_verified else "unresolved"),
        "count": len(records) if values_current else (len(set_ids) if raw_verified else None),
        "contents_known": values_current or raw_verified,
        "flags": records if values_current else ([{"id": int(flag_id), "is_set": True, "source": "live EventWork bitfield"} for flag_id in set_ids] if raw_verified else []),
        "last_observed_flags": records if records and not values_current else [],
        "set_flag_ids": [int(flag_id) for flag_id in set_ids] if raw_verified else [],
        "scopes": ["story", "system", "map", "runtime"] if values_current else ["raw_eventwork"],
        "evidence": {
            "source": "RuntimeHub semantic snapshot" if values_current else "GameData -> EventWorkSave live Main RAM",
            "verified": bool(values_current and decoder_status == "verified") or raw_verified,
            "confidence": "verified" if (values_current and decoder_status == "verified") else ("verified_raw" if raw_verified else "unresolved"),
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
            "text": ((context.get("dialogue_text") or context.get("loaded_dialogue_text") or "")
                     if observation["current"] else None),
            "full_text": ((context.get("full_dialogue_text") or context.get("loaded_dialogue_text") or "")
                          if observation["current"] else None),
            "loaded_text": context.get("loaded_dialogue_text") if observation["current"] else None,
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


def _enrich_party_slot(slot: dict[str, Any], dex: DexStore) -> dict[str, Any]:
    enriched = dict(slot)
    species_id = slot.get("species")
    if isinstance(species_id, int) and species_id > 0:
        pk_info = dex.get("pokemon", species_id) or {}
        pk_names = pk_info.get("names") or {}
        enriched["species_name"] = pk_names.get("zh-Hans") or pk_names.get("zh") or f"宝可梦 #{species_id}"
        enriched["species_name_en"] = pk_names.get("en") or f"Pokemon #{species_id}"
        types = []
        for t in pk_info.get("types") or []:
            t_names = t.get("names") or {}
            types.append({"id": t.get("id"), "name": t_names.get("zh-Hans") or t_names.get("zh"), "name_en": t_names.get("en")})
        enriched["types"] = types
    enriched["species_name_zh"] = enriched.get("species_name")
    enriched_moves = []
    for m in slot.get("moves", []):
        m_row = dict(m)
        mid = m.get("move_id")
        if isinstance(mid, int) and mid > 0:
            m_entity = dex.get("moves", mid) or {}
            m_names = m_entity.get("names") or {}
            zh_name = m_names.get("zh-Hans") or m_names.get("zh") or m_entity.get("name_zh") or f"招式#{mid}"
            en_name = m_names.get("en") or m_entity.get("name_en") or f"Move #{mid}"
            m_row["name_zh"] = zh_name
            m_row["name"] = zh_name
            m_row["name_en"] = en_name
            t_obj = m_entity.get("type") or {}
            m_row["type"] = t_obj.get("names", {}).get("zh-Hans") if isinstance(t_obj, dict) else (str(t_obj) or "一般")
            m_row["power"] = m_entity.get("power")
            m_row["accuracy"] = m_entity.get("accuracy")
            m_row["max_pp"] = m_entity.get("pp") or 15
        enriched_moves.append(m_row)
    enriched["moves"] = enriched_moves
    cur_hp = slot.get("current_hp")
    max_hp = slot.get("max_hp")
    if isinstance(cur_hp, (int, float)) and isinstance(max_hp, (int, float)) and max_hp > 0:
        enriched["hp_percent"] = round(cur_hp / max_hp, 3)
    status_raw = slot.get("status_raw") or 0
    if status_raw == 0:
        enriched["status_name"] = "HEALTHY"
    elif status_raw & 0x7:
        enriched["status_name"] = "SLEEP"
    elif status_raw & 0x8:
        enriched["status_name"] = "POISON"
    elif status_raw & 0x10:
        enriched["status_name"] = "BURN"
    elif status_raw & 0x20:
        enriched["status_name"] = "FREEZE"
    elif status_raw & 0x40:
        enriched["status_name"] = "PARALYSIS"
    elif status_raw & 0x80:
        enriched["status_name"] = "TOXIC"
    else:
        enriched["status_name"] = "UNKNOWN"
    enriched_moves = []
    for mv in slot.get("moves") or []:
        m_rec = dict(mv)
        m_id = mv.get("move_id")
        if isinstance(m_id, int) and m_id > 0:
            m_info = dex.get("moves", m_id) or {}
            m_names = m_info.get("names") or {}
            m_rec["name"] = m_names.get("zh-Hans") or m_names.get("zh") or f"招式 #{m_id}"
            m_rec["name_en"] = m_names.get("en") or f"Move #{m_id}"
            m_type = m_info.get("type") or {}
            m_t_names = m_type.get("names") or {}
            m_rec["type"] = m_t_names.get("zh-Hans") or m_t_names.get("zh") or m_type.get("identifier")
            m_rec["power"] = m_info.get("power")
            m_rec["accuracy"] = m_info.get("accuracy")
            m_rec["max_pp"] = m_info.get("pp")
        enriched_moves.append(m_rec)
    enriched["moves"] = enriched_moves
    return enriched


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
    _attach_movement_rules(data)
    data["freshness"] = _freshness(snap)
    data["interactions"] = _connectors_at_tile(zone_id, x, y, z)
    return data


def _attach_movement_rules(data: dict[str, Any]) -> None:
    """Expose transport eligibility without conflating it with ROM collision.

    A clear collision flag is only a *static surface* candidate.  Water still
    requires an already-active Surf transport and very-tall grass/catwalk
    classes can reject a bicycle.  The result is intentionally tri-state:
    ``True``/``False`` for one unambiguous surface and ``None`` when height or
    layer selection leaves multiple candidates unresolved.
    """
    surfaces = [item for item in data.get("surfaces") or () if isinstance(item, dict)]
    selected = data.get("height", {}).get("selected_surface") if isinstance(data.get("height"), dict) else None
    if selected is not None:
        surfaces = [item for item in surfaces if item.get("layer_index") == selected]
    if len(surfaces) != 1:
        reason = "surface_layer_ambiguous" if surfaces else "no_static_surface"
        data["movement"] = {
            "status": "unresolved",
            "source": "ROM terrain class/flags",
            "modes": {mode: {"allowed": None, "reason": reason} for mode in ("walk", "run", "bike", "surf")},
        }
        return
    surface = surfaces[0]
    collision = surface.get("collision") or {}
    material = surface.get("material") or {}
    blocked = collision.get("static_blocked") is True
    kind = str(material.get("kind") or "unknown")
    requires = material.get("requires")
    mode = {
        "walk": {"allowed": False if blocked or kind == "water" or requires == "surf" else True,
                  "reason": "static_collision" if blocked else "surf_required" if kind == "water" or requires == "surf" else "static_candidate"},
        "run": {"allowed": False if blocked or kind == "water" or requires == "surf" else True,
                "reason": "static_collision" if blocked else "surf_required" if kind == "water" or requires == "surf" else "static_candidate"},
        "bike": {"allowed": False if blocked or kind == "water" or requires == "surf" or material.get("blocks_cycling") else True,
                 "reason": "static_collision" if blocked else "surf_required" if kind == "water" or requires == "surf" else "cycling_blocked_by_terrain" if material.get("blocks_cycling") else "static_candidate"},
        "surf": {"allowed": False if blocked or kind not in {"water", "water_edge"} else True,
                 "reason": "static_collision" if blocked else "surf_requires_water" if kind not in {"water", "water_edge"} else "static_candidate"},
    }
    data["movement"] = {
        "status": "candidate",
        "source": "ROM terrain class/flags",
        "tile_kind": kind,
        "modes": mode,
        "runtime_transport_required": True,
        "note": "allowed is static transport eligibility; current inventory, mount state and live scripts remain unresolved",
    }


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
    service = _connectors()
    window_warps = getattr(service, "window_warps", None)
    if callable(window_warps):
        records = window_warps(zone_id)
    else:
        query = getattr(service, "query", None)
        try:
            payload = query(zone_id=zone_id, offset=0, limit=2048) if callable(query) else {}
        except Exception:
            payload = {}
        records = payload.get("warps", []) if isinstance(payload, dict) else []
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
    # A local window only needs source trigger footprints.  Building every
    # Zone's portal graph here turned a small map query into a ROM-wide load.
    # ``window_warps`` is the bounded fast path used by the production
    # connector service.  Keep a compatibility fallback for embedded/test
    # connector implementations that only expose the paginated ``query``
    # method; a scene request must not fail merely because that optional
    # optimisation is unavailable.
    connector_service = _connectors()
    window_warps = getattr(connector_service, "window_warps", None)
    if callable(window_warps):
        portals = window_warps(zone_id)
    else:
        query = getattr(connector_service, "query", None)
        try:
            queried = query(zone_id=zone_id, offset=0, limit=2048) if callable(query) else {}
        except Exception:
            queried = {}
        portals = queried.get("warps", []) if isinstance(queried, dict) else []
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
    view = data.setdefault("view", {
        "kind": "static_rom_window",
        "is_current_nds_view": False,
        "screen_verified": False,
        "memory_backed": False,
    })
    view["runtime_overlay"] = "Current-player marker and tile type are snapshot overlays; they do not establish screen visibility."
    return data


def _map(snap: dict, zone_id: int | None = None, radius: int = 4, include_raw: bool = False) -> dict:
    player = snap.get("player") or {}
    zone_id = zone_id if zone_id is not None else player.get("zone_id")
    if zone_id is None:
        try:
            from ..world.runtime_player_state import player_runtime_service, canonical_grid_player
            canonical = canonical_grid_player(player_runtime_service.latest or {}, require_resolved=False)
            if canonical and canonical.get("zone_id") is not None:
                zone_id = canonical["zone_id"]
        except Exception:
            pass
    if zone_id is None:
        return {"format": "black2-ai-map/v2", "status": "unavailable", "reason": "No cached player Zone; specify zone_id for a static query."}
    source = _world().zone(zone_id)
    try:
        from ..world.location_catalog import RomLocationCatalog
        zone_label = RomLocationCatalog(_world().rom).zone_label(int(zone_id)).as_dict()
    except Exception:
        zone_label = {"zone_id": int(zone_id), "name_zh": f"Zone {zone_id}", "confidence": "unresolved"}
    data = {"format": "black2-ai-map/v2", "status": "decoded", "map_header_id": zone_id,
            "zone_label": zone_label,
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
            "record_index": identity,
            "kind": "npc",
            "semantic_kind": classification["semantic_kind"],
            "name": classification["name"],
            "name_status": classification.get("name_status", "unresolved_rom_name"),
            "role_zh": classification.get("role_zh"),
            "candidate_role": classification.get("candidate_role"),
            "candidate_role_status": classification.get("candidate_role_status"),
            "category_label": classification["category_label"],
            "live": False,
            "coordinate": _event_coordinate(zone_id, record),
            "sprite_id": record.get("sprite_id"),
            "movement_id": record.get("movement_id"),
            "facing_id": record.get("facing_id"),
            "script_id": record.get("script_id"),
            "sight_raw": record.get("sight_raw"),
            "flag_id": record.get("flag_id"),
            "interaction": classification["interaction"],
            "trainer": classification["trainer"],
            "lifecycle": classification["lifecycle"],
            "availability": classification["lifecycle"],
            "identity": classification.get("identity"),
            "rom": classification.get("rom"),
            "runtime": classification.get("runtime"),
            "semantics": classification.get("semantics"),
            "evidence": classification.get("evidence"),
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


def _enrich_runtime_actor_identity(
    runtime_actors: list[dict[str, Any]],
    static_actors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Join live FieldActor rows to ROM NPC identity without inventing names."""
    output: list[dict[str, Any]] = []
    for actor in runtime_actors:
        row = dict(actor)
        candidates: list[tuple[int, dict[str, Any]]] = []
        for static in static_actors:
            score = 0
            if actor.get("script_id") is not None and actor.get("script_id") == static.get("script_id"):
                score += 100
            if actor.get("model_id") is not None and actor.get("model_id") == static.get("sprite_id"):
                score += 25
            if actor.get("spawn_flag") is not None and actor.get("spawn_flag") == static.get("flag_id"):
                score += 20
            if score:
                candidates.append((score, static))
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            score, static = candidates[0]
            row["static_identity"] = {
                "npc_id": static.get("id"),
                "record_index": static.get("record_index"),
                "name": static.get("name"),
                "name_status": static.get("name_status", "unresolved_rom_name"),
                "role_zh": static.get("role_zh"),
                "semantic_kind": static.get("semantic_kind"),
                "script_id": static.get("script_id"),
                "sprite_id": static.get("sprite_id"),
                "match_score": score,
                "match_basis": [
                    key for key, present in (("script_id", score >= 100), ("model_id", score >= 25), ("spawn_flag", score >= 20)) if present
                ],
            }
            row["semantic_name"] = static.get("name")
            row["name_status"] = static.get("name_status", "unresolved_rom_name")
            row["role_zh"] = static.get("role_zh")
        else:
            row["static_identity"] = None
            row["semantic_name"] = None
            row["name_status"] = "unresolved_rom_name"
            row["role_zh"] = None
        output.append(row)
    return output


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
    # ROM sight_raw is a battle-vision candidate, not proof of a live battle.
    # Expose it explicitly so the AI never confuses a static candidate with a
    # verified trainer encounter.
    for actor in static_actors:
        sight_raw = actor.get("sight_raw")
        if actor.get("semantic_kind") == "NPC_UNRESOLVED" and isinstance(sight_raw, int) and sight_raw > 0:
            actor["semantic_kind"] = "NPC_TRAINER_CANDIDATE"
            actor["battle_status"] = "candidate_rom_sight_only"
            actor["battle_evidence"] = {"source": "ROM NPC sight_raw", "verified": False}
    runtime_actors = _scene_runtime_actors(snap)

    # Semantic categorization
    items = [a for a in static_actors if a.get("semantic_kind") == "OVERWORLD_ITEM"]
    trainers = [a for a in static_actors if a.get("semantic_kind") in {"NPC_TRAINER", "NPC_TRAINER_CANDIDATE"}]
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
    x_world = record.get("x_raw")
    z_world = record.get("y_raw")
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
        "axis_mapping": "floor(warp.x_raw/16)->grid.x; floor(warp.y_raw/16)->grid.z; elevation unresolved",
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
        script_id = _event_integer(record.get("script_id"))
        arg3 = _event_integer(record.get("arg3_raw"))
        role = "unknown_interactable"
        if arg3 == 6:
            role, action, input_hint = "signpost", "read_signpost", "stand_front_middle_then_press_A"
        elif arg3 == 1:
            role, action, input_hint = "trash_can", "inspect_trash_can", "face_object_then_press_A"
        elif script_id == 2108:
            role, action, input_hint = "pc_terminal", "open_pc", "face_terminal_then_press_A"
        elif script_id == 2109:
            role, action, input_hint = "shop_counter_candidate", "open_shop", "face_counter_then_press_A"
        elif arg3 == 4 and script_id in (None, 0):
            role, action, input_hint = "hidden_item_candidate", "pickup_hidden_item", "stand_on_candidate_then_press_A"
        else:
            action, input_hint = "interact", "face_object_then_press_A"
        # Furniture with no script is normally decorative, but the record does
        # not prove that. Keep it explicitly unresolved for an autonomous client.
        status = "candidate" if script_id not in (None, 0) or role != "unknown_interactable" else "unverified"
    return {
        "action": action,
        "role": role if kind not in {"warp", "npc", "trigger"} else None,
        "script_id": _event_integer(record.get("script_id")),
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


_OBSERVE_ZONE_LABEL_CANDIDATES: dict[int, dict[str, str]] = {
    # Runtime traversal plus the current Black 2 story route identify these
    # zones, but they are intentionally kept as candidates until the ROM
    # location-name/script decoder is promoted to a verified source.
    427: {
        "name_zh": "桧扇市（室外候选）",
        "name_en": "Aspertia City (Exterior Candidate)",
        "source": "runtime_navigation_plus_story_route",
        "confidence": "candidate",
    },
    435: {
        "name_zh": "桧扇市宝可梦中心（室内候选）",
        "name_en": "Aspertia City Pokémon Center (Interior Candidate)",
        "source": "runtime_navigation_plus_service_candidate",
        "confidence": "candidate",
    },
    436: {
        "name_zh": "桧扇市训练家学校/道馆（室内候选）",
        "name_en": "Aspertia Trainer School/Gym (Interior Candidate)",
        "source": "runtime_story_route_plus_rom_scene_candidate",
        "confidence": "candidate",
    },
}


def _observe_zone_label(zone_id: int, source: dict[str, Any]) -> dict[str, Any]:
    override = ZONE_LABEL_OVERRIDES.get(int(zone_id))
    if override:
        return {
            **override,
            "source": "operator_confirmed",
            "confidence": "confirmed_for_current_session",
        }
    candidate = _OBSERVE_ZONE_LABEL_CANDIDATES.get(int(zone_id))
    if candidate:
        return dict(candidate)
    header = source.get("header") if isinstance(source.get("header"), dict) else {}
    environment = "unknown"
    area_id = header.get("area_id")
    rom = getattr(_world(), "rom", None)
    try:
        area = rom.area(int(area_id)) if rom is not None and isinstance(area_id, int) else None
        environment = "exterior" if bool(getattr(area, "is_exterior", False)) else "interior" if area is not None else "unknown"
    except (IndexError, OSError, RuntimeError, TypeError, ValueError):
        environment = "unknown"
    return {
        "name_zh": f"Zone {zone_id}（{environment}）",
        "name_en": f"Zone {zone_id} ({environment})",
        "source": "zone_id_fallback",
        "confidence": "unresolved_name",
    }


def _observe_compact_warp(row: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    destination = row.get("destination") if isinstance(row.get("destination"), dict) else {}
    destination_zone_id = destination.get("zone_id")
    destination_label = (
        _observe_zone_label(destination_zone_id, {})
        if isinstance(destination_zone_id, int)
        else None
    )
    result = {
        "id": row.get("id"),
        "kind": row.get("kind", "warp"),
        "role": row.get("role"),
        "source": {
            "zone_id": source.get("zone_id"),
            "warp_id": source.get("warp_id"),
            "grid_candidate": source.get("grid_candidate"),
            "coordinate_resolution": source.get("coordinate_resolution"),
        },
        "destination": {
            "zone_id": destination_zone_id,
            "label": destination_label or destination.get("label"),
            "landing_tile": destination.get("landing_tile"),
            "landing_status": destination.get("landing_status"),
            "status": destination.get("status"),
            "resolution": destination.get("resolution"),
            "reason": destination.get("reason"),
        },
        "traversal": row.get("traversal"),
        "verification": row.get("verification"),
    }
    if include_raw:
        result["raw"] = row.get("raw")
    return result


def _observe_compact_static_npc(row: dict[str, Any]) -> dict[str, Any]:
    coordinate = row.get("coordinate") if isinstance(row.get("coordinate"), dict) else None
    return {
        "id": row.get("id"),
        "record_index": row.get("record_index"),
        "semantic_kind": row.get("semantic_kind"),
        "name": row.get("name"),
        "category_label": row.get("category_label"),
        "coordinate": coordinate,
        "sprite_id": row.get("sprite_id"),
        "movement_id": row.get("movement_id"),
        "facing_id": row.get("facing_id"),
        "script_id": row.get("script_id"),
        "flag_id": row.get("flag_id"),
        "interaction": row.get("interaction"),
        "trainer": row.get("trainer"),
        "lifecycle": row.get("lifecycle"),
        "identity": row.get("identity"),
        "runtime": row.get("runtime"),
        "evidence": row.get("evidence"),
    }


def _observe_compact_live_actor(row: dict[str, Any], *, include_raw: bool = False) -> dict[str, Any]:
    membership = row.get("scene_membership") if isinstance(row.get("scene_membership"), dict) else {}
    result = {
        "slot": row.get("slot"),
        "actor_uid": row.get("actor_uid"),
        "is_player": row.get("is_player"),
        "model_id": row.get("model_id"),
        "script_id": row.get("script_id"),
        "event_type": row.get("event_type"),
        "spawn_flag": row.get("spawn_flag"),
        "move_code": row.get("move_code"),
        "facing": row.get("facing"),
        "grid": row.get("grid"),
        "world": row.get("world"),
        "zone_id_raw": row.get("zone_id_raw", row.get("zone_id")),
        "effective_zone_id": row.get("effective_zone_id_candidate"),
        "same_current_scene": row.get("same_current_scene"),
        "membership": membership,
        "status": "player" if row.get("is_player") else "live_actor",
        "source": "Main RAM FieldActor heap",
    }
    if include_raw:
        result["raw"] = row.get("raw")
        result["address"] = row.get("address")
    return result


def _observe_nearby_rows(rows: list[dict[str, Any]], origin: dict[str, Any], radius: int) -> list[dict[str, Any]]:
    if not all(isinstance(origin.get(key), int) for key in ("x", "y", "z")):
        return rows[:128]
    result: list[dict[str, Any]] = []
    for row in rows:
        coordinate = row.get("coordinate") if isinstance(row.get("coordinate"), dict) else {}
        if not isinstance(coordinate.get("x"), int) or not isinstance(coordinate.get("z"), int):
            continue
        if max(abs(coordinate["x"] - origin["x"]), abs(coordinate["z"] - origin["z"])) <= radius:
            result.append(row)
    return result[:128]


def _observe_static_world(
    snap: dict[str, Any],
    *,
    zone_id: int | None,
    radius: int,
    include_raw: bool,
) -> dict[str, Any]:
    if not isinstance(zone_id, int):
        return {
            "status": "unresolved",
            "zone_id": None,
            "reason": "Player Zone is unresolved; static world query needs an explicit Zone.",
            "actors": {"static_npcs": [], "count": None, "status": "unresolved"},
            "interactions": {"status": "unresolved", "count": None, "interactions": []},
            "warps": {"status": "unresolved", "count": None, "warps": []},
        }
    source = _world().zone(zone_id)
    player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
    position = player.get("position") if isinstance(player.get("position"), dict) else {}
    grid = position.get("grid") if isinstance(position.get("grid"), dict) else {}
    origin = {
        key: grid.get(key)
        for key in ("x", "y", "z")
    }
    has_origin = all(isinstance(origin.get(key), int) for key in ("x", "y", "z"))
    connectors = _connectors().query(zone_id=zone_id, offset=0, limit=2048, include_raw=include_raw)
    interactions = _interaction_records(
        zone_id,
        source.get("events") if isinstance(source, dict) else {},
        connectors,
        origin_x=origin.get("x") if has_origin else None,
        origin_y=origin.get("y") if has_origin else None,
        origin_z=origin.get("z") if has_origin else None,
        radius=radius,
        include_raw=include_raw,
    )
    interaction_rows = []
    for row in interactions.get("interactions") or []:
        if not isinstance(row, dict):
            continue
        record = row.get("record") if isinstance(row.get("record"), dict) else {}
        compact = {
            "id": row.get("id"),
            "kind": row.get("kind"),
            "coordinate": row.get("coordinate"),
            "distance": row.get("distance"),
            "affordance": row.get("affordance"),
            "availability": row.get("availability"),
            "runtime": row.get("runtime"),
            "destination": row.get("destination"),
            "role": row.get("role"),
            "connector": row.get("connector"),
            "record": {
                key: record.get(key)
                for key in ("id", "record_index", "sprite_id", "movement_id", "facing_id",
                            "script_id", "flag_id", "x", "y", "z", "target_zone_or_map_raw")
                if key in record
            },
        }
        if include_raw:
            compact["record"]["raw_record_hex"] = record.get("raw_record_hex")
        interaction_rows.append(compact)

    static_npcs = _scene_static_actors(zone_id, source.get("events"))
    nearby_static = _observe_nearby_rows(
        [_observe_compact_static_npc(row) for row in static_npcs],
        origin,
        radius,
    )
    warps = connectors.get("warps") if isinstance(connectors, dict) else []
    compact_warps = [
        _observe_compact_warp(row, include_raw=include_raw)
        for row in (warps or [])
        if isinstance(row, dict)
    ]
    header = source.get("header") if isinstance(source.get("header"), dict) else {}
    return {
        "status": "decoded",
        "zone_id": zone_id,
        "label": _observe_zone_label(zone_id, source),
        "header": {
            key: header.get(key)
            for key in ("area_id", "matrix_id", "entities_id", "map_type",
                        "parent_zone_id", "location_name_id", "location_name_display_type")
            if key in header
        },
        "matrix": source.get("matrix"),
        "rules": source.get("rules"),
        "coordinate_space": source.get("coordinate_space", "gen5-field-grid-v1"),
        "actors": {
            "static_npcs": nearby_static,
            "nearby_count": len(nearby_static),
            "total_static_count": len(static_npcs),
            "status": "decoded_static_rom",
            "live_positions": "attached separately from bounded ActorSystem sample",
        },
        "interactions": {
            "format": interactions.get("format"),
            "status": "candidate",
            "count": len(interaction_rows),
            "interactions": interaction_rows,
            "coverage": interactions.get("coverage"),
            "query": interactions.get("query"),
        },
        "warps": {
            "format": connectors.get("format") if isinstance(connectors, dict) else None,
            "status": "candidate" if compact_warps else "decoded_empty_or_unresolved",
            "count": len(compact_warps),
            "warps": compact_warps,
            "coverage": connectors.get("coverage") if isinstance(connectors, dict) else None,
        },
        "coverage": {
            "static_rom": "current Zone header, matrix, entities and connector catalog",
            "live_actor_positions": "not included in this object; see world.actors.live",
            "flags_scripts_collision": "candidate/static only unless separately verified",
        },
    }


async def _observe_live_actors(*, include_raw: bool = False) -> dict[str, Any]:
    if _reader is None:
        return {
            "status": "unresolved",
            "actors": [],
            "reason": "MemoryReader is not configured.",
            "source": "ActorSystem+FieldActor heap",
        }
    try:
        payload = await runtime_actor_overlay_service.sample(_reader)
    except Exception as exc:
        return {
            "status": "unresolved",
            "actors": [],
            "reason": f"{type(exc).__name__}: {exc}",
            "source": "ActorSystem+FieldActor heap",
        }
    actors = payload.get("actors") if isinstance(payload, dict) else []
    return {
        "status": payload.get("status", "unresolved") if isinstance(payload, dict) else "unresolved",
        "frame": payload.get("frame") if isinstance(payload, dict) else None,
        "capacity": payload.get("capacity") if isinstance(payload, dict) else None,
        "active_slot_count": payload.get("active_slot_count") if isinstance(payload, dict) else None,
        "actors": [
            _observe_compact_live_actor(row, include_raw=include_raw)
            for row in (actors or [])
            if isinstance(row, dict)
        ],
        "source": "Main RAM FieldActor heap after ActorSystem pointer coherence",
        "evidence": {
            "recovery": payload.get("recovery") if isinstance(payload, dict) else None,
            "read_policy": payload.get("refresh_policy") if isinstance(payload, dict) else None,
        },
    }


async def _observe_party(fallback_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    if _reader is None:
        return _party(fallback_snapshot if isinstance(fallback_snapshot, dict) else _snapshot())
    try:
        from .battle_routes import _party_decoder
        decoded = await _party_decoder.sample()
    except Exception as exc:
        return {
            "format": "black2-agent-party/v1",
            "status": "unresolved",
            "count": None,
            "capacity": None,
            "slots": [],
            "contents_known": False,
            "source": "GameData.PokeParty",
            "reason": f"{type(exc).__name__}: {exc}",
            "evidence": {"verified": False, "confidence": "unresolved"},
        }
    known = decoded.get("status") == "candidate"
    slots = []
    for slot in decoded.get("slots") or []:
        if not isinstance(slot, dict):
            continue
        row = dict(slot)
        species_id = row.get("species")
        if isinstance(species_id, int):
            try:
                from ..dex.store import dex_store
                entity = dex_store.get("pokemon", species_id)
                if isinstance(entity, dict):
                    names = entity.get("names") if isinstance(entity.get("names"), dict) else {}
                    row["species_info"] = {
                        "id": species_id,
                        "identifier": entity.get("identifier"),
                        "name": names.get("en") or entity.get("identifier"),
                        "names": names,
                        "source": "black2-offline-dex/v1",
                    }
            except Exception:
                row["species_info"] = {
                    "id": species_id,
                    "name": None,
                    "names": None,
                    "source": "dex_unavailable",
                }
        slots.append(row)
    return {
        "format": "black2-agent-party/v1",
        "status": "partial" if known else "unresolved",
        "count": decoded.get("count") if known else None,
        "capacity": decoded.get("capacity") if known else None,
        "slots": slots if known else [],
        "contents_known": known,
        "source": "GameData.PokeParty",
        "integrity": "checksum_verified" if known else None,
        "confidence": "candidate" if known else None,
        "frame": decoded.get("frame"),
        "reason": decoded.get("reason"),
        "limitations": [
            "Persistent PartyPkm is not the active BattleMon.",
            "Move IDs and PP do not authorize a battle command or prove target legality.",
        ],
    }


async def _observe_inventory(fallback_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """Expose the same verified inventory decoder used by /game/inventory.

    Agent observation must not downgrade a decoded bag to the old unresolved
    placeholder; otherwise the single observation contradicts the dedicated
    inventory endpoint and hides key items from the decision layer.
    """
    if _reader is None:
        return _inventory(fallback_snapshot if isinstance(fallback_snapshot, dict) else _snapshot())
    try:
        from .battle_routes import _inventory_decoder
        decoded = await _inventory_decoder.sample()
    except Exception as exc:
        return {
            "format": "black2-agent-inventory/v1",
            "status": "unresolved",
            "pockets": [],
            "items": [],
            "pocket_count": None,
            "item_count": None,
            "contents_known": False,
            "decode_status": "unresolved",
            "source": "GameData.Bag",
            "reason": f"{type(exc).__name__}: {exc}",
            "evidence": {"verified": False, "confidence": "unresolved"},
        }
    if decoded.get("status") == "ready" and decoded.get("contents_known"):
        return {
            **decoded,
            "format": "black2-agent-inventory/v1",
            "source": decoded.get("source", "GameData.Bag"),
            "evidence": {
                "verified": decoded.get("decode_status") == "verified",
                "confidence": "verified" if decoded.get("decode_status") == "verified" else "candidate",
                "frame": decoded.get("frame"),
                "reason": "Inventory decoder is shared with /api/v1/game/inventory.",
            },
        }
    return {
        **decoded,
        "format": "black2-agent-inventory/v1",
        "contents_known": False,
        "evidence": {
            "verified": False,
            "confidence": "unresolved",
            "reason": decoded.get("reason", "Inventory contents are unresolved."),
        },
    }


def _observe_dialogue(snap: dict[str, Any]) -> dict[str, Any]:
    context = _semantic_context(snap)
    observation = _runtime_observation(snap)
    active = context.get("is_dialogue_active")
    active = active if observation["current"] and isinstance(active, bool) else None
    choices = context.get("choices")
    choices = choices if observation["current"] and isinstance(choices, list) else None
    return {
        "status": "current" if observation["current"] else "unresolved",
        "active": active,
        "speaker": context.get("speaker") if observation["current"] else None,
        "speaker_category": context.get("speaker_category") if observation["current"] else None,
        "text": context.get("dialogue_text") if observation["current"] else None,
        "loaded_text": context.get("loaded_dialogue_text") if observation["current"] else None,
        "full_text": context.get("full_dialogue_text") if observation["current"] else None,
        "choices": choices,
        "active_pointer": context.get("active_pointer") if observation["current"] else None,
        "ready_for_input": (
            (snap.get("semantic") or {}).get("ready_for_input")
            if observation["current"] and isinstance(snap.get("semantic"), dict) else None
        ),
        "history_endpoint": "/api/dialogue/history",
        "evidence": {
            "source": "RuntimeHub semantic context",
            "confidence": "verified" if observation["current"] else "unresolved",
            "runtime_observation": observation,
        },
    }


def _observe_battle(snap: dict[str, Any], wait: dict[str, Any]) -> dict[str, Any]:
    battle = snap.get("battle") if isinstance(snap.get("battle"), dict) else {}
    ui = snap.get("battle_ui") if isinstance(snap.get("battle_ui"), dict) else None
    active = battle.get("active") if isinstance(battle.get("active"), bool) else None
    return {
        "status": "active_candidate" if active is True else "inactive" if active is False else "unresolved",
        "active": active,
        "active_status": battle.get("active_status"),
        "classification": battle.get("classification"),
        "field_busy": battle.get("field_busy"),
        "ui": {
            "status": "candidate" if ui else "unresolved",
            "phase": (ui or {}).get("phase"),
            "cursor": (ui or {}).get("cursor"),
            "source": "bounded battle UI RAM decoder",
        },
        "available_actions": (
            wait.get("allowed_actions") if active is True and isinstance(wait, dict) else []
        ),
        "identity": {
            "status": "available_via_endpoint",
            "trainer": None,
            "opponent": None,
            "reason": "Current unified observation does not duplicate the bounded Battle Identity RAM scan.",
            "endpoint": "/api/v1/battle/identity",
        },
        "party_endpoint": "/api/v1/battle/party",
        "moves_endpoint": "/api/v1/battle/moves",
        "legal_action_status": "unresolved",
        "execution_available": False,
        "evidence": {
            "source": "RuntimeHub battle presence plus optional cached UI cursor",
            "verified": False,
            "reason": "BattleMon identity, phase, target legality and closed-loop action verification remain incomplete.",
        },
    }


def _observe_memory() -> dict[str, Any]:
    memory = playtest_memory.snapshot()
    long_term = memory.get("long_term") if isinstance(memory.get("long_term"), dict) else {}
    medium = memory.get("medium_term") if isinstance(memory.get("medium_term"), dict) else {}
    short = memory.get("short_term") if isinstance(memory.get("short_term"), dict) else {}
    return {
        "status": "available",
        "story_progress": long_term.get("story_progress"),
        "verified_facts": (long_term.get("verified_facts") or [])[-8:],
        "current_goal": medium.get("current_goal"),
        "current_state": medium.get("current_state"),
        "next_action": medium.get("next_action"),
        "recent_interactions": (short.get("recent") or [])[-8:],
        "endpoint": "/api/v1/ai/memory",
        "authority": "operator/API memory; ROM flags and quest journal remain separate evidence sources",
    }


def _observe_actions(
    snap: dict[str, Any],
    layered: dict[str, Any],
    wait: dict[str, Any],
    *,
    services: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    fresh = _freshness(snap)["fresh"]
    player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
    battle = snap.get("battle") if isinstance(snap.get("battle"), dict) else {}
    dialogue = _semantic_context(snap)
    primary = layered.get("primary_context")
    service_candidates = (services or {}).get("candidates") if isinstance(services, dict) else []
    recovery_available = any(
        isinstance(item, dict) and item.get("execution_available") is True
        for item in (service_candidates or [])
    )
    battle_active = battle.get("active") is True
    dialogue_active = dialogue.get("is_dialogue_active") is True
    return [
        {
            "type": "navigate_to",
            "endpoint": "/api/v1/navigation/tasks",
            "mutates_game": True,
            "can_execute": True if fresh and primary == "exploration" and not battle_active else False if battle_active or dialogue_active else None,
            "preconditions": ["fresh PlayerRuntime", "exploration owns input", "destination has a route or explicit target"],
            "completion": ["PlayerRuntime reaches requested destination", "Zone transition/interaction is observed when applicable"],
            "failure_codes": ["NAV_BRIDGE_OFFLINE", "NAV_INPUT_BUSY", "NAV_NO_PATH", "NAV_STUCK", "NAV_LANDING_MISMATCH", "NAV_SESSION_CHANGED"],
            "verify": ["/api/v1/runtime/snapshot", "/api/v1/navigation/tasks/{task_id}", "/api/v1/agent/events/log"],
        },
        {
            "type": "talk_to_actor",
            "endpoint": "/api/v1/agent/automation/interact",
            "mutates_game": True,
            "can_execute": None,
            "preconditions": ["actor binding and approach tile verified", "dialogue/battle input is not already owned by another layer"],
            "completion": ["dialogue or battle transition observed", "target interaction result is correlated"],
            "failure_codes": ["actor_not_found", "path_blocked", "interaction_failed", "timeout", "unexpected_transition"],
            "verify": ["/api/v1/agent/automation/tasks/{task_id}", "/api/v1/agent/observe"],
        },
        {
            "type": "advance_dialogue",
            "endpoint": "/api/v1/agent/wait/advance",
            "mutates_game": True,
            "can_execute": True if wait.get("auto_policy") == "press_A_once" else False,
            "preconditions": ["matching wait_id", "wait.kind=auto_transition", "auto_policy=press_A_once"],
            "completion": ["next wait boundary or dialogue end is observed"],
            "failure_codes": ["WAIT_STATE_STALE", "WAIT_ACTION_NOT_AUTHORIZED", "WAIT_AUTO_ADVANCE_FAILED"],
            "verify": ["/api/v1/agent/wait-state", "/api/v1/agent/events/log"],
        },
        {
            "type": "choose_dialogue_option",
            "endpoint": "/api/actions/dialogue/choice",
            "mutates_game": True,
            "can_execute": True if dialogue_active and isinstance(dialogue.get("choices"), list) and dialogue.get("choices") else False,
            "preconditions": ["dialogue choice list decoded", "choice policy supplied by Agent"],
            "completion": ["choice boundary changes and next dialogue/script state is observed"],
            "failure_codes": ["wrong_game_state", "choice_unresolved", "unexpected_transition", "timeout"],
            "verify": ["/api/v1/agent/wait-state", "/api/v1/runtime/snapshot"],
        },
        {
            "type": "battle_select_move",
            "endpoint": "/api/v1/battle/ui-actions",
            "mutates_game": True,
            "can_execute": True if "battle.use_move" in (wait.get("allowed_actions") or []) else None if battle_active else False,
            "preconditions": ["battle phase and cursor decoded", "move slot and target legal", "post-action verification contract available"],
            "completion": ["Battle UI leaves move selection and HP/PP/result transition is observed"],
            "failure_codes": ["BATTLE_RUNTIME_UNRESOLVED", "BATTLE_ACTION_EXECUTION_UNVERIFIED", "BATTLE_CURSOR_STALE", "BATTLE_POST_ACTION_UNVERIFIED"],
            "verify": ["/api/v1/battle/state", "/api/v1/battle/ui-cursor", "/api/v1/battle/evidence"],
        },
        {
            "type": "heal_party",
            "endpoint": "/api/v1/agent/automation/recover",
            "mutates_game": True,
            "can_execute": True if recovery_available else None,
            "preconditions": ["nearest recovery service resolved", "execution_available=true", "party state sampled before/after"],
            "completion": ["recovery dialogue completes and party HP/status change is observed"],
            "failure_codes": ["AUTOMATION_PLAYER_UNRESOLVED", "AUTOMATION_RECOVERY_NOT_FOUND", "AUTOMATION_INTERNAL", "timeout"],
            "verify": ["/api/v1/agent/automation/tasks/{task_id}", "/api/v1/battle/party", "/api/v1/agent/events/log"],
        },
        {
            "type": "enter_warp",
            "endpoint": "/api/v1/navigation/tasks",
            "mutates_game": True,
            "can_execute": None,
            "preconditions": ["source Warp candidate is reachable", "floor/landing/script gate is verified or explicitly allowed by policy"],
            "completion": ["fresh PlayerRuntime Zone transition and landing grid observed"],
            "failure_codes": ["NAV_NO_PATH", "NAV_LANDING_MISMATCH", "NAV_DYNAMIC_OBSTACLE", "NAV_STUCK", "unexpected_transition"],
            "verify": ["/api/v1/runtime/snapshot", "/api/v1/navigation/warp-evidence", "/api/v1/agent/events/log"],
        },
    ]


async def _observe_services(zone_id: int | None) -> dict[str, Any]:
    try:
        from .story_automation_routes import _service
        if _service is None:
            raise RuntimeError("story automation service is not configured")
        return await run_in_threadpool(
            _service.nearby_services,
            service_type="all",
            zone_id=zone_id,
        )
    except Exception as exc:
        return {
            "format": "black2-agent-services/v1",
            "status": "unresolved",
            "current": None,
            "service_type": "all",
            "candidates": [],
            "reason": f"{type(exc).__name__}: {exc}",
            "endpoint": "/api/v1/agent/services/nearby?service_type=all",
        }


def _observe_events() -> dict[str, Any]:
    cursor = agent_event_bus.cursor
    recent = agent_event_bus.read_since(max(0, cursor - 8), limit=8)
    if recent.get("status") == "cursor_expired":
        recent = agent_event_bus.persisted_recent(8)
    return {
        "cursor": cursor,
        "state_revision": agent_event_bus.state_revision,
        "recent": recent.get("events", []),
        "status": recent.get("status", "ok"),
        "next": f"/api/v1/agent/events/wait?after={cursor}",
        "log": "/api/v1/agent/events/log",
    }


@router.get("/agent/observe")
async def agent_observe(
    radius: Radius = 4,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Return one compact, read-only observation for a future Pokémon Agent.

    RuntimeHub supplies the authoritative cached semantic snapshot.  The
    additional reads are bounded ROM/ActorSystem/Party decoders and are
    attached with their own evidence status; this endpoint never sends input
    or changes emulator state.
    """
    snap = _snapshot()
    freshness = _freshness(snap)
    player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
    position = player.get("position") if isinstance(player.get("position"), dict) else {}
    zone_id = player.get("zone_id") if isinstance(player.get("zone_id"), int) else None
    battle = snap.get("battle") if isinstance(snap.get("battle"), dict) else {}
    layered = project_layered_state(snap, battle)
    wait = snap.get("wait_state") if isinstance(snap.get("wait_state"), dict) else derive_wait_state(snap)

    try:
        world = await _rom_call(
            _observe_static_world,
            snap,
            zone_id=zone_id,
            radius=radius,
            include_raw=include_raw,
        )
    except HTTPException as exc:
        world = {
            "status": "unresolved",
            "zone_id": zone_id,
            "reason": exc.detail,
            "actors": {"static_npcs": [], "count": None, "status": "unresolved"},
            "interactions": {"status": "unresolved", "count": None, "interactions": []},
            "warps": {"status": "unresolved", "count": None, "warps": []},
        }
    live_actors, party, inventory, services = await asyncio.gather(
        _observe_live_actors(include_raw=include_raw),
        _observe_party(snap),
        _observe_inventory(snap),
        _observe_services(zone_id),
    )
    world["actors"]["live"] = live_actors
    world["actors"]["live_current_zone"] = [
        actor for actor in live_actors.get("actors", [])
        if actor.get("same_current_scene") is True
        and (
            actor.get("effective_zone_id") in {None, zone_id}
            or actor.get("zone_id_raw") in {None, 0, zone_id}
        )
    ]
    world["actors"]["status"] = (
        "live_and_static" if live_actors.get("status") in {"resolved", "candidate"} else "static_only"
    )

    memory = _observe_memory()
    observation_status = "current" if freshness["fresh"] else "partial" if world.get("status") == "decoded" else "unresolved"
    return {
        "format": "black2-agent-observation/v1",
        "status": observation_status,
        "read_only": True,
        "writes_performed": False,
        "input_sent": False,
        "snapshot_policy": "one RuntimeHub.snapshot plus bounded ROM, ActorSystem and Party reads",
        "observation": {
            "frame": player.get("frame") if isinstance(player.get("frame"), int) else (snap.get("transport") or {}).get("frame"),
            "session_id": freshness.get("session_id"),
            "sampled_at": freshness.get("sampled_at"),
            "freshness": freshness,
            "runtime": _runtime_observation(snap),
            "event_cursor": agent_event_bus.cursor,
            "state_revision": agent_event_bus.state_revision,
        },
        "state": {
            "primary_context": layered.get("primary_context"),
            "active_layers": layered.get("active_layers"),
            "overlays": layered.get("overlays"),
            "screen_type": layered.get("legacy", {}).get("screen_type"),
            "input": layered.get("input"),
            "wait_state": wait,
            "available_actions": wait.get("allowed_actions") if isinstance(wait, dict) else None,
            "layered": layered,
        },
        "player": {
            "status": player.get("status", "unresolved"),
            "confidence": player.get("confidence", "unresolved"),
            "zone_id": zone_id,
            "position": position,
            "facing": player.get("facing") or player.get("orientation"),
            "locomotion": player.get("locomotion"),
            "transport": player.get("transport"),
            "frame": player.get("frame"),
            "source": "PlayerRuntime",
            "evidence": {
                "verified": freshness["fresh"] and player.get("status") == "resolved",
                "freshness": freshness,
            },
        },
        "world": world,
        "dialogue": _observe_dialogue(snap),
        "battle": _observe_battle(snap, wait),
        "party": party,
        "inventory": inventory,
        "story": {
            "objectives": _objectives(snap),
            "flags": _flags(snap),
            "cutscene": _cutscene(snap),
            "memory": memory,
        },
        "services": services,
        "actions": _observe_actions(snap, layered, wait, services=services),
        "events": _observe_events(),
        "resources": {
            "self": "/api/v1/agent/observe",
            "state": "/api/v1/agent/state",
            "wait_state": "/api/v1/agent/wait-state",
            "runtime": "/api/v1/runtime/snapshot",
            "map": "/api/v1/ai/map/scene",
            "interactions": "/api/v1/ai/map/interactions",
            "warps": "/api/v1/ai/map/warps",
            "navigation": "/api/v1/navigation/tasks",
            "services": "/api/v1/agent/services/nearby?service_type=all",
            "dialogue_history": "/api/dialogue/history",
            "battle": "/api/v1/battle/state",
            "battle_identity": "/api/v1/battle/identity",
            "battle_party": "/api/v1/battle/party",
            "battle_moves": "/api/v1/battle/moves",
            "inventory": "/api/v1/game/inventory",
            "flags": "/api/v1/game/flags",
            "progression": "/api/v1/progression/state",
            "progression_gates": "/api/v1/progression/gates",
            "story_plan": "/api/v1/agent/story/plan",
            "global_route": "/api/v1/navigation/global/route?to_zone={target_zone}",
            "battle_decisions": "/api/v1/battle/decisions",
            "memory": "/api/v1/ai/memory",
            "events": "/api/v1/agent/events/log",
        },
        "missing_api": [
            *([] if True else [{"id": "runtime_story_flags_and_script_state", "priority": "high", "status": "candidate", "endpoint": "/api/v1/progression/flags"}]),
            {"id": "battle_identity_phase_legal_actions", "priority": "high", "status": "partial", "endpoint": "/api/v1/battle/identity"},
            *([] if inventory.get("contents_known") else [{"id": "inventory_contents_decoder", "priority": "high", "status": "unresolved", "endpoint": "/api/v1/game/inventory"}]),
            {"id": "npc_semantic_identity_and_battle_history", "priority": "medium", "status": "candidate", "endpoint": "/api/v1/ai/map/npc-battle-status"},
            {"id": "warp_script_gate_and_entrance_execution", "priority": "medium", "status": "candidate", "endpoint": "/api/v1/navigation/warp-evidence"},
            {"id": "encounter_species_profile", "priority": "medium", "status": "partial", "endpoint": "/api/v1/encounters/regions/current"},
            {"id": "dialogue_speaker_and_page_cursor", "priority": "medium", "status": "partial", "endpoint": "/api/dialogue/history"},
        ],
        "limitations": [
            "Static ROM NPCs are spawn definitions, not proof of current presence or unspent battle state.",
            "A live actor disappearing or changing flags is not by itself proof of trainer defeat.",
            "Battle identity/phase/target legality is linked by endpoint and remains conservative until independently verified.",
            "Unknown fields are null or unresolved; empty arrays never mean the game has no such resource.",
        ],
    }


@router.get("/game/capabilities")
async def game_capabilities() -> dict:
    return {"format": "black2-game-capabilities/v1",
            "coordinate_contract": {"space": "gen5-field-grid-v1", "zone_id": "Zone header id", "x": "east", "y": "elevation/floor", "z": "south", "world_units_per_tile": 16},
            "read": {"current": "/api/v1/game/current", "layers": "/api/v1/game/layers", "state": "/api/v1/game/state", "party": "/api/v1/game/party", "inventory": "/api/v1/game/inventory",
                     "map": "/api/v1/ai/map", "scene": "/api/v1/ai/map/scene", "tile": "/api/v1/ai/map/tile?zone_id=&x=&y=&z=", "window": "/api/v1/ai/map/window",
                     "warps": "/api/v1/ai/map/warps", "doors": "/api/v1/ai/map/doors", "zone": "/api/v1/ai/map/zone/{zone_id}", "materials": "/api/v1/ai/materials",
                     "map_interactions": "/api/v1/ai/map/interactions", "static_scene": "/api/v1/map/v6/scene/zone/{zone_id}",
                     "npc_battle_status": "/api/v1/ai/map/npc-battle-status", "npc_battle_history": "/api/v1/ai/map/npc-battle-status/history",
                     "context": "/api/v1/ai/context", "actions": "/api/v1/game/actions", "dialogue": "/api/dialogue/history",
                     "tasks": "/api/v1/game/tasks", "task": "/api/v1/game/tasks/{task_id}", "objectives": "/api/v1/game/objectives",
                     "flags": "/api/v1/game/flags", "cutscene": "/api/v1/game/cutscene",
                     "interactions": "/api/v1/game/interactions", "battle": "/api/v1/battle/state",
                     "battle_request": "/api/v1/battle/request", "battle_evidence": "/api/v1/battle/evidence"},
            "write": {"navigation_plan": "/api/v1/navigation/plans", "navigation_task": "/api/v1/navigation/tasks",
                      "press": "/api/actions/press", "touch": "/api/actions/touch", "dialogue_advance": "/api/actions/dialogue/advance", "dialogue_choice": "/api/actions/dialogue/choice"},
            "support": {"terrain": "ROM decoded", "warp_targets": "ROM referenced candidates", "interaction_index": "ROM static candidates", "static_scene_preview": "ROM-only read-only scene with synthetic camera anchor",
                        "cross_zone_execution": False,
                        "npc_battle_memory_diff": True,
                        "party_contents": True, "inventory_contents": True, "battle_presence_candidate": True,
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


@router.get("/game/completion")
async def game_completion() -> dict:
    """Return game completion, Hall of Fame, and Pokémon League state."""
    from ..decoders.completion_decoder import completion_decoder
    return await completion_decoder.sample()


@router.get("/ai/map/scripts")
async def ai_map_scripts(
    zone_id: int | None = Query(default=None, ge=0),
    script_index: int | None = Query(default=None, ge=0),
    prefix_words: int = Query(default=32, ge=0, le=256),
    include_raw: bool = False,
) -> dict[str, Any]:
    """Return bounded static map-script structure and entity bindings.

    The endpoint is separate from runtime script state.  A Pokémon Agent may
    use it to explain which NPC/trigger points at a script function, while
    runtime event/actor evidence is still required before claiming execution.
    """
    resolved_zone = zone_id
    if resolved_zone is None:
        player = (_snapshot().get("player") or {})
        resolved_zone = player.get("zone_id")
    if not isinstance(resolved_zone, int):
        return {
            "format": "black2-zone-script-catalog/v1",
            "status": "unresolved",
            "zone_id": None,
            "reason": "Player Zone is unresolved; provide zone_id for a static script query.",
        }
    catalog = _npc_trainer_catalog()
    if catalog is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ROM_SCRIPT_CATALOG_UNAVAILABLE",
                "message": "The local Black 2 ROM script catalog is unavailable.",
                "reason": _trainer_catalog_error,
            },
        )
    try:
        return catalog.zone_script_catalog(
            int(resolved_zone),
            script_index=script_index,
            prefix_words=prefix_words,
            include_raw=include_raw,
        )
    except (FileNotFoundError, IndexError, KeyError, OSError, RuntimeError, TrainerCatalogError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ROM_SCRIPT_CATALOG_QUERY_FAILED",
                "message": f"{type(exc).__name__}: {exc}",
                "zone_id": resolved_zone,
                "script_index": script_index,
            },
        ) from exc


@router.get("/ai/map/npc-battle-status")
async def ai_map_npc_battle_status(zone_id: Zone | None = None) -> dict[str, Any]:
    """Return per-NPC battle candidates and raw runtime lifecycle evidence.

    This is deliberately read-only.  A ROM sight range or a FieldActor flag
    is not promoted to ``defeated``; the latter requires a verified event-flag
    decoder or a recorded before/after probe.
    """
    snap = _snapshot()
    player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
    resolved_zone = zone_id if zone_id is not None else player.get("zone_id")
    if not isinstance(resolved_zone, int):
        raise HTTPException(status_code=503, detail="Current Zone is unavailable; specify zone_id.")
    try:
        source = _world().zone(resolved_zone)
    except (FileNotFoundError, OSError, ValueError, RuntimeError, IndexError) as exc:
        raise HTTPException(status_code=503, detail=f"ROM NPC event data unavailable: {exc}") from exc
    static_npcs = [
        row for row in _scene_static_actors(resolved_zone, source.get("events"))
        if row.get("semantic_kind") not in {"OVERWORLD_ITEM", "DYNAMIC_OBSTACLE", "LEGENDARY_OVERWORLD"}
    ]

    runtime_payload: dict[str, Any] = {"status": "unresolved", "actors": [], "reason": "MemoryReader is not configured."}
    if _reader is not None:
        try:
            runtime_payload = await runtime_actor_overlay_service.sample(_reader)
        except Exception as exc:
            runtime_payload = {"status": "unresolved", "actors": [], "reason": f"{type(exc).__name__}: {exc}"}
    runtime_actors = runtime_payload.get("actors") if isinstance(runtime_payload.get("actors"), list) else []

    zone_catalog: dict[str, Any] = {"status": "unresolved", "candidates": [], "trainer_battles": [],
                                    "reason": _trainer_catalog_error or "ROM trainer catalog is not available."}
    catalog = _npc_trainer_catalog()
    if catalog is not None:
        try:
            zone_catalog = catalog.zone_trainer_candidates(resolved_zone)
        except (TrainerCatalogError, ValueError) as exc:
            zone_catalog = {"status": "unresolved", "candidates": [], "trainer_battles": [],
                            "reason": f"{type(exc).__name__}: {exc}"}

    frame = player.get("frame") if isinstance(player.get("frame"), int) else (snap.get("transport") or {}).get("frame")
    # Use the same live EventWork sample as progression and radar.  The
    # historical probe journal is evidence-only and cannot decide defeat.
    progression = await progression_state_service.sample() if _reader is not None else {}
    event_work = progression.get("event_work") if isinstance(progression.get("event_work"), dict) else {}
    flag_bytes = event_work.get("flag_bytes") if isinstance(event_work.get("flag_bytes"), dict) else {}
    live_flags = {
        "status": "resolved" if flag_bytes.get("raw_hex") else "unresolved",
        "contents_known": bool(flag_bytes.get("raw_hex")),
        "raw_hex": flag_bytes.get("raw_hex"),
        "flag_bytes": flag_bytes,
        "source": "live EventWork bitfield",
    }
    result = build_npc_battle_status(
        static_npcs,
        runtime_actors,
        zone_id=resolved_zone,
        zone_candidates=zone_catalog.get("candidates") if isinstance(zone_catalog.get("candidates"), list) else [],
        flags=live_flags,
        player=player,
        battle=snap.get("battle") if isinstance(snap.get("battle"), dict) else {},
        frame=frame,
    )
    # Join only the latest bounded probe summary into the live rows.  The
    # full NDJSON journal stays behind the history endpoint; this makes the
    # normal candidate poll cheap while still exposing which NPCs have
    # already been compared before/after.
    latest_probes = npc_battle_history.latest_by_npc()
    probe_queue = {
        "unprobed": [],
        "retry_after_existing_modal": [],
        "retry_after_probe_failure": [],
        "uncertain_no_battle_transition": [],
        "observed_dialogue_without_battle": [],
        "battle_observed_candidates": [],
        "unresolved_history": [],
    }
    for row in result.get("npcs", []):
        if not isinstance(row, dict):
            continue
        npc_id = str(row.get("npc_id"))
        probe = latest_probes.get(npc_id)
        if not isinstance(probe, dict):
            continue
        outcome = probe.get("probe_outcome")
        status_by_outcome = {
            "dialogue_observed_without_battle": "dialogue_observed_without_battle",
            "battle_active_after_probe": "battle_observed_candidate",
            "no_battle_transition_observed": "no_battle_transition_observed",
            "precondition_modal_active": "probe_blocked_by_existing_modal",
            "probe_failed": "probe_failed",
            "read_only_observation": "read_only_observation",
        }
        observation = row.get("battle_observation") if isinstance(row.get("battle_observation"), dict) else {}
        row["battle_observation"] = {
            **observation,
            "status": status_by_outcome.get(outcome, "unresolved"),
            "causal_binding": (
                "interaction_to_battle_candidate" if outcome == "battle_active_after_probe"
                else "interaction_to_dialogue_no_battle" if outcome == "dialogue_observed_without_battle"
                else "interaction_without_battle_transition" if outcome == "no_battle_transition_observed"
                else "probe_not_started_modal_active" if outcome == "precondition_modal_active"
                else "probe_failed" if outcome == "probe_failed"
                else "read_only_observation" if outcome == "read_only_observation"
                else "not_observed"
            ),
            "last_probe": probe,
        }
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        row["evidence"] = {**evidence, "battle_before_after_compared": True}
    for row in result.get("npcs", []):
        if not isinstance(row, dict) or row.get("capability", {}).get("status") != "line_of_sight_battle_candidate":
            continue
        # A live TrainerFlag=1 is authoritative: it must never re-enter the
        # probe queue merely because an old journal row exists.
        if (row.get("lifecycle") or {}).get("defeat_status") == "defeated":
            continue
        npc_id = str(row.get("npc_id"))
        probe = latest_probes.get(npc_id)
        if not isinstance(probe, dict):
            probe_queue["unprobed"].append(npc_id)
            continue
        outcome = probe.get("probe_outcome")
        if outcome == "precondition_modal_active":
            probe_queue["retry_after_existing_modal"].append(npc_id)
        elif outcome == "probe_failed":
            probe_queue["retry_after_probe_failure"].append(npc_id)
        elif outcome == "no_battle_transition_observed":
            probe_queue["uncertain_no_battle_transition"].append(npc_id)
        elif outcome == "dialogue_observed_without_battle":
            probe_queue["observed_dialogue_without_battle"].append(npc_id)
        elif outcome == "battle_active_after_probe":
            probe_queue["battle_observed_candidates"].append(npc_id)
        else:
            probe_queue["unresolved_history"].append(npc_id)
    # Keep the legacy field, but make it an efficient next-probe queue.  A
    # dialogue-only observation is retained in the history and status row;
    # it is not automatically re-run on every poll.
    result["probe_candidates"] = (
        probe_queue["unprobed"]
        + probe_queue["retry_after_existing_modal"]
        + probe_queue["retry_after_probe_failure"]
        + probe_queue["unresolved_history"]
    )
    result["probe_queue"] = {
        **probe_queue,
        "policy": "probe_candidates contains only unprobed/blocked/unresolved line-of-sight candidates; dialogue/no-transition observations remain auditable but are not repeated automatically.",
    }
    result["runtime"] = {
        "status": runtime_payload.get("status"),
        "frame": runtime_payload.get("frame"),
        "active_actor_count": runtime_payload.get("active_slot_count"),
        "raw_source": "GET /api/v1/lab/actors/live",
    }
    result["observation_index"] = {
        "status": "available",
        "latest_by_npc_count": len(latest_probes),
        "source": "/api/v1/ai/map/npc-battle-status/history",
        "meaning": "last_probe is evidence for the most recent interaction only; it does not promote defeat_status.",
    }
    result["history"] = {
        "endpoint": "/api/v1/ai/map/npc-battle-status/history",
        "record_endpoint": "/api/v1/ai/map/npc-battle-status/record",
        "total_count": npc_battle_history.recent(1).get("total_count", 0),
    }
    return result


@router.get("/ai/map/npc-battle-status/history")
async def ai_map_npc_battle_history(limit: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    """Return bounded before/after NPC memory-diff observations."""
    return npc_battle_history.recent(limit)


@router.post("/ai/map/npc-battle-status/record")
async def ai_map_npc_battle_record(request: Request) -> dict[str, Any]:
    """Persist a caller-supplied before/after status pair.

    The endpoint records evidence only; it never presses a key, writes game
    RAM or marks a trainer as defeated by itself.
    """
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"invalid JSON body: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("before"), dict) or not isinstance(payload.get("after"), dict):
        raise HTTPException(status_code=422, detail="body must contain object fields 'before' and 'after'")
    npc_ids = payload.get("npc_ids")
    if npc_ids is not None and (not isinstance(npc_ids, list) or any(not isinstance(value, str) for value in npc_ids)):
        raise HTTPException(status_code=422, detail="npc_ids must be an array of strings when provided")
    return npc_battle_history.record(
        payload["before"], payload["after"],
        operation=str(payload.get("operation") or "npc_battle_probe"),
        session_id=payload.get("session_id") if isinstance(payload.get("session_id"), str) else None,
        battle_before=payload.get("battle_before") if isinstance(payload.get("battle_before"), dict) else None,
        battle_after=payload.get("battle_after") if isinstance(payload.get("battle_after"), dict) else None,
        dialogue_before=payload.get("dialogue_before") if isinstance(payload.get("dialogue_before"), dict) else None,
        dialogue_after=payload.get("dialogue_after") if isinstance(payload.get("dialogue_after"), dict) else None,
        note=payload.get("note") if isinstance(payload.get("note"), str) else None,
        npc_ids=npc_ids,
        probe_status=payload.get("probe_status") if isinstance(payload.get("probe_status"), str) else None,
        probe_failure=payload.get("probe_failure") if isinstance(payload.get("probe_failure"), dict) else None,
    )


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
    snap = _snapshot()
    if _reader is not None:
        try:
            live = await _party_decoder.sample()
            if live.get("status") in ("candidate", "resolved", "partial") and live.get("slots"):
                dex = _get_dex()
                enriched_slots = [_enrich_party_slot(s, dex) for s in live["slots"]]
                return {
                    "format": "black2-party/v1",
                    "status": "ready",
                    "decode_status": "verified",
                    "contents_known": True,
                    "count": live.get("count", len(enriched_slots)),
                    "capacity": live.get("capacity", 6),
                    "slots": enriched_slots,
                    "frame": live.get("frame"),
                    "available_fields": [
                        "species", "species_name", "level", "current_hp", "max_hp",
                        "hp_percent", "status_name", "held_item_id", "moves"
                    ],
                    "evidence": {
                        "source": "GameData.PokeParty (0x0223B570 -> +0x194)",
                        "verified": True,
                        "confidence": "verified",
                        "integrity": live.get("integrity", "checksum_verified"),
                        "reason": "Persistent player party decoded and checksum-verified through GameData.",
                    },
                }
        except Exception:
            pass
    return _party(snap)


@router.get("/game/inventory")
async def game_inventory() -> dict:
    snap = _snapshot()
    if _reader is not None:
        try:
            live = await _inventory_decoder.sample()
            if live.get("status") == "ready" and live.get("contents_known"):
                return live
        except Exception:
            pass
    return _inventory(snap)


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
    if zone_id is None and _reader is not None:
        try:
            from ..world.map_truth import MapTruthService
            truth = await MapTruthService().current(_reader)
            if truth:
                zid = truth.get("identity", {}).get("map_header", {}).get("value") or truth.get("zone_id")
                if zid is not None:
                    zone_id = int(zid)
        except Exception:
            pass
    snap = _snapshot()
    map_data = await _rom_call(_map, snap, zone_id, radius, include_raw)
    return _scene_projection(snap, map_data, include_raw=include_raw)


@router.get("/ai/scene/complete")
async def ai_scene_complete(
    zone_id: Zone | None = None,
    radius: Radius = 6,
    include_raw: bool = False,
) -> dict[str, Any]:
    """Return a richer, evidence-separated scene contract for AI planning.

    This is intentionally an explicit endpoint rather than a high-frequency
    runtime sampler.  It joins official ROM location/environment metadata,
    bounded terrain/collision, warps/doors, static event entities and an
    optional live ActorSystem sample.  Unknown NPC names and unverified script
    meanings remain explicit instead of being guessed.
    """
    if zone_id is None and _reader is not None:
        try:
            from ..world.map_truth import MapTruthService
            truth = await MapTruthService().current(_reader)
            if truth:
                zid = truth.get("identity", {}).get("map_header", {}).get("value") or truth.get("zone_id")
                if zid is not None:
                    zone_id = int(zid)
        except Exception:
            pass
    snap = _snapshot()
    map_data = await _rom_call(_map, snap, zone_id, radius, include_raw)
    if not isinstance(map_data, dict) or map_data.get("status") != "decoded":
        return {
            "format": "black2-ai-scene-complete/v1",
            "status": "unavailable",
            "reason": (map_data or {}).get("reason", "map unavailable"),
            "map": map_data,
        }
    selected_zone = int(map_data["map_header_id"])
    scene = _scene_projection(snap, map_data, include_raw=include_raw)
    static_actors = _scene_static_actors(selected_zone, map_data.get("events"))
    runtime_actors: list[dict[str, Any]] = []
    runtime_status = "not_sampled"
    if _reader is not None:
        try:
            payload = await runtime_actor_overlay_service.sample(_reader)
            if isinstance(payload, dict) and isinstance(payload.get("actors"), list):
                runtime_actors = [row for row in payload["actors"] if isinstance(row, dict)]
                runtime_status = payload.get("status", "sampled")
        except Exception as exc:
            runtime_status = f"unavailable:{type(exc).__name__}"
    runtime_actors = _enrich_runtime_actor_identity(runtime_actors, static_actors)

    interactions: list[dict[str, Any]] = []
    events = map_data.get("events") if isinstance(map_data.get("events"), dict) else {}
    for kind, rows in events.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            affordance = _interaction_affordance(kind.rstrip("s"), row)
            interactions.append({
                "kind": kind.rstrip("s"),
                "id": row.get("id", row.get("record_index")),
                "coordinate": _event_coordinate(selected_zone, row),
                "script_id": row.get("script_id"),
                "flag_id": row.get("flag_id"),
                "affordance": affordance,
                "raw_available": bool(include_raw),
            })

    try:
        from ..world.location_catalog import RomLocationCatalog
        label = RomLocationCatalog(_world().rom).zone_label(selected_zone).as_dict()
    except Exception:
        label = {"zone_id": selected_zone, "name_zh": map_data.get("location"), "confidence": "unresolved"}

    scripts = {"status": "unavailable", "candidates": [], "trainer_battles": []}
    catalog = _npc_trainer_catalog()
    if catalog is not None:
        try:
            scripts = catalog.zone_script_catalog(selected_zone)
        except Exception as exc:
            scripts = {"status": "unresolved", "candidates": [], "trainer_battles": [], "reason": f"{type(exc).__name__}: {exc}"}

    return {
        "format": "black2-ai-scene-complete/v1",
        "status": "decoded",
        "zone": label,
        "player": snap.get("player") or {},
        "screen": (snap.get("semantic") or {}).get("context") or {},
        "map": map_data,
        "scene": scene,
        "actors": {
            "static": static_actors,
            "runtime": runtime_actors,
            "runtime_status": runtime_status,
            "static_are_spawn_candidates": True,
            "runtime_is_current_position_evidence": True,
        },
        "interactions": interactions,
        "scripts": scripts,
        "capabilities": {
            "read_only": True,
            "player_position": "runtime_player_cache",
            "npc_position": "ActorSystem when sampled; ROM spawn otherwise",
            "npc_names": "registry/script/trainer catalog when resolved; otherwise explicit unresolved",
            "object_items": "ROM event records plus runtime flags when decoded",
            "gym_and_trainer_scripts": "/api/v1/ai/map/scripts?zone_id={zone_id}",
            "execution": "use deterministic action endpoints only after reading wait_state and affordance evidence",
        },
        "evidence": {
            "sources": ["ZoneHeader", "AreaHeader", "ROM event entity records", "bounded terrain collision", "cached RuntimeHub", "optional ActorSystem"],
            "screenshot_used": False,
            "unknowns_are_preserved": True,
        },
    }


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
            "views": {
                "current_endpoint": "/api/v1/ai/view/current",
                "profiles": ["strict", "assisted_local", "local_7x7", "global_static"],
                "global_endpoint": "/api/v1/ai/view/global",
                "coordinate_space": "gen5-matrix-grid-v1 for global and gen5-field-grid-v1 for tile detail",
                "nds_camera_verified": False,
            },
            "environment": {
                "endpoint": "/api/v1/game/environment",
                "fields": ["transport", "tile", "overworld", "battle"],
                "read_only": True,
                "weather_policy": "static ZoneHeader weather is candidate; active battle weather remains unresolved",
            },
            "navigation_hazards": "/api/v1/navigation/hazards",
            "confidence": {"verified": "explicit runtime/source evidence", "candidate": "structural candidate without traversal proof", "unverified": "no established meaning"},
            "materials_url": "/api/v1/ai/materials"}


async def _live_ai_memory_view(snap: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build AI memory from the current emulator sample only.

    Persisted playtest notes remain audit evidence, never authoritative prompt
    context.  No caller-supplied or historical party/bag/story values are used.
    """
    snap = snap if isinstance(snap, dict) else _snapshot()
    player = {}
    party = {}
    inventory = {}
    progression = {}
    if _reader is not None:
        try:
            player = await player_runtime_service.sample(_reader, allow_discovery=True)
        except Exception as exc:
            player = {"status": "unresolved", "reason": f"{type(exc).__name__}: {exc}"}
        try:
            party = await _party_decoder.sample()
        except Exception as exc:
            party = {"status": "unresolved", "reason": f"{type(exc).__name__}: {exc}"}
        try:
            inventory = await _inventory_decoder.sample()
        except Exception as exc:
            inventory = {"status": "unresolved", "reason": f"{type(exc).__name__}: {exc}"}
        try:
            progression = await progression_state_service.sample()
        except Exception as exc:
            progression = {"status": "unresolved", "reason": f"{type(exc).__name__}: {exc}"}
    live_player = player if isinstance(player, dict) else {}
    live_position = live_player.get("position") if isinstance(live_player.get("position"), dict) else {}
    badges = progression.get("badges") if isinstance(progression.get("badges"), dict) else {}
    historical = playtest_memory.snapshot()
    return {
        "format": "black2-live-ai-memory/v1",
        "status": "current" if live_player.get("status") in {"resolved", "candidate"} else "partial",
        "authoritative": "emulator_live_sample",
        "sample": {
            "frame": live_player.get("frame") or snap.get("frame"),
            "zone_id": live_player.get("zone_id"),
            "grid": live_position.get("grid"),
            "facing": (live_player.get("orientation") or {}).get("facing"),
            "party": party,
            "inventory": inventory,
            "progression": {
                "status": progression.get("status", "unresolved"),
                "badges": badges,
                "money": progression.get("money"),
                "raw_eventwork": progression.get("event_work"),
            },
        },
        "historical_memory": {
            "available": True,
            "excluded_from_authoritative_context": True,
            "source": "runtime/ai_context persisted audit files",
            "reason": "Historical test/playtest notes are never used as current game truth.",
        },
    }


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
            "memory": await _live_ai_memory_view(snap),
            "scene_url": "/api/v1/ai/map/scene",
            "navigation": {"capabilities": "/api/v1/navigation/capabilities", "context": "/api/v1/navigation/context", "hazards": "/api/v1/navigation/hazards", "plan": "/api/v1/navigation/plans", "task": "/api/v1/navigation/tasks"},
            "views": {"current": "/api/v1/ai/view/current?profile=local_7x7", "global": "/api/v1/ai/view/global"},
            "environment": "/api/v1/game/environment",
            "actions_url": "/api/v1/game/actions", "limitations": ["Bag/party contents and battle actions are not decoded.",
                "ROM connectors do not authorize cross-Zone execution.", "Current NPC positions and event flags may change static walkability."]}


@router.get("/ai/memory")
async def ai_memory() -> dict[str, Any]:
    """Return current emulator-backed memory; persisted notes are audit-only."""
    return await _live_ai_memory_view()


@router.post("/ai/memory/sync")
async def ai_memory_sync(body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Sync current RuntimeHub state into memory; this never mutates the game."""
    body = body if isinstance(body, dict) else {}
    note = body.get("next_action") if isinstance(body.get("next_action"), str) else None
    live = await _live_ai_memory_view()
    sample = live.get("sample", {})
    state = playtest_memory.sync_runtime(
        _snapshot(),
        note=note,
        party=sample.get("party") if isinstance(sample.get("party"), dict) else None,
        inventory=sample.get("inventory") if isinstance(sample.get("inventory"), dict) else None,
        player={"status": "resolved", "zone_id": sample.get("zone_id"), "position": {"grid": sample.get("grid")}},
    )
    return {"format": "black2-live-ai-memory-sync/v2", "memory": live, "persisted_state": {"updated_at": state.get("updated_at"), "source": "live emulator sample"}}


@router.post("/ai/memory/event")
async def ai_memory_event(body: dict[str, Any]) -> dict[str, Any]:
    """Append one bounded short-term interaction/action result."""
    return {"format": "black2-playtest-memory-event/v1", "event": playtest_memory.record_event(body), "memory": playtest_memory.snapshot()}


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
    """Return verified trainers plus explicit sight-range candidates.

    A sight range alone is not promoted to a guaranteed battle.  It is exposed
    as ``NPC_TRAINER_CANDIDATE`` so the AI can plan around the hazard while the
    causal TrainerBattle/defeat binding remains evidence-separated.
    """
    res = await ai_npcs(zone_id=zone_id)
    trainers = [row for row in res["npcs"] if row.get("semantic_kind") == "NPC_TRAINER" or row.get("candidate_role") == "NPC_TRAINER_CANDIDATE"]
    return {
        "format": "black2-ai-trainers/v2",
        "zone_id": res["zone_id"],
        "count": len(trainers),
        "verified_count": sum(1 for row in trainers if row.get("semantic_kind") == "NPC_TRAINER"),
        "candidate_count": sum(1 for row in trainers if row.get("candidate_role") == "NPC_TRAINER_CANDIDATE"),
        "trainers": trainers,
        "policy": "sight_raw nominates a candidate; TrainerBattle execution and defeat require separate evidence.",
    }



@router.get("/ai/gym/overview")
async def ai_gym_overview(zone_id: int | None = None, gym_index: int | None = None) -> dict[str, Any]:
    """Structured Gym, Leader, and Gym Trainer overview for all 8 Unova gyms."""
    from ..world.gym_catalog import default_gym_catalog
    catalog = default_gym_catalog()
    if zone_id is not None:
        gym = catalog.get_gym_by_zone(int(zone_id))
        if not gym:
            raise HTTPException(status_code=404, detail=f"Zone {zone_id} is not a recognized Gym")
        return gym
    if gym_index is not None:
        gym = catalog.get_gym_by_index(int(gym_index))
        if not gym:
            raise HTTPException(status_code=404, detail=f"Gym index {gym_index} is not recognized (must be 1..8)")
        return gym
    return {
        "format": "black2-gym-overview-catalog/v1",
        "count": 8,
        "gyms": catalog.get_all_gyms(),
    }


@router.get("/ai/items/catalog")
async def ai_items_catalog(item_id: int | None = None) -> dict[str, Any]:
    """Query official ROM item details by item ID."""
    from ..world.item_catalog import default_item_catalog
    catalog = default_item_catalog()
    if item_id is not None:
        item = catalog.get_item(int(item_id))
        if not item:
            raise HTTPException(status_code=404, detail=f"Item {item_id} not found")
        return item
    return {
        "format": "black2-item-catalog/v1",
        "sample_items": [catalog.get_item(i) for i in [1, 4, 17, 38, 50, 108, 384, 448]],
    }
