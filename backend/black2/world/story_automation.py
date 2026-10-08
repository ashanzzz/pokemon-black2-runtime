"""Evidence-gated high-level story interactions.

This module is intentionally a thin orchestrator over the existing closed-loop
navigation task.  It does not add a second pathfinder or infer party healing
from an animation.  A caller selects a service/NPC, the service resolves a
static target, NavigationTaskService verifies every landing, and only then is
the dialogue handler allowed to press A while the dialogue layer owns input.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ..actions.input_engine import ActionEngine
from ..runtime.events import agent_event_bus
from ..runtime.hub import RuntimeHub
from ..runtime.wait_state import derive_wait_state
from ..state.playtest_memory import playtest_memory
from .map_graph import ZONE_LABEL_OVERRIDES
from .navigation_planning import NavigationPlanningError, NavigationPlanService
from .navigation_tasks import NavigationTaskService
from .player_coordinates import canonical_grid_player
from .runtime_player_state import player_runtime_service
from .warp_transition_evidence import runtime_warp_evidence
from ..runtime.layered_status import normalize_screen_type


_ACTIVE = {"queued", "routing", "interacting", "dialogue", "waiting_input", "cancelling"}
_TERMINAL = {"succeeded", "failed", "cancelled"}
_NURSE_SCRIPT_ID = 2100
_PC_SCRIPT_ID = 2108
_DYNAMIC_OBSTACLE_SPRITES = {97, 98, 99}
_OVERWORLD_ITEM_SPRITES = {110, 111, 210, 211}
# Story automation runs alongside RuntimeHub's authoritative sampler.  A
# direct PlayerRuntime refresh is useful at a transition boundary, but it can
# contend with the bridge request lock (and an explicit Field discovery can
# take much longer than an ordinary cached sample).  Never let that diagnostic
# refresh hold a high-level workflow indefinitely.
_PLAYER_REFRESH_TIMEOUT_SECONDS = 2.5
_HUB_SAMPLE_TIMEOUT_SECONDS = 1.5
_FIELD_IDLE_PHASES = {"Idle", "Turning", "Brake"}
# A verified recovery connector is more than a ROM Warp record: the live
# session also proved the approach tile, the input direction, and the fact
# that the game can spend several seconds in a loading/transition state after
# the input is accepted.  Keep this registry intentionally narrow until more
# service entrances have the same RAM-backed evidence.
_VERIFIED_RECOVERY_CONNECTORS = {
    (448, 454): {
        "approach_grid": {"zone_id": 448, "x": 210, "y": 0, "z": 649},
        "button": "Up",
        "hold_frames": 4,
        "wait_frames": 15,
        "poll_interval_seconds": 0.25,
        "transition_timeout_seconds": 12.0,
        "evidence_source_grids": (
            {"x": 210, "y": 0, "z": 649},
            {"x": 210, "y": 0, "z": 648},
        ),
        "calibration_case": "virbank_pokecenter_recovery",
    },
    (439, 443): {
        "approach_grid": {"zone_id": 439, "x": 105, "y": 1, "z": 694},
        "button": "Up",
        "hold_frames": 4,
        "wait_frames": 15,
        "poll_interval_seconds": 0.25,
        "transition_timeout_seconds": 12.0,
        "evidence_source_grids": (
            {"x": 105, "y": 1, "z": 693},
            {"x": 105, "y": 1, "z": 694},
        ),
        "calibration_case": "case310_one_api_nearest_recovery",
    },
}
# Zone 439/script 8 was initially classified from its static ROM record as a
# Cut Tree because it uses model 97.  A bounded live ActorSystem sample plus
# screenshot/RAM evidence showed the same live actor is the ranch-entrance
# story character and is the source of the automatic 20-route dialogue.  Keep
# the exception narrow until a general model/sprite decoder is verified.
_VERIFIED_RUNTIME_STORY_ACTORS = {(439, 8, 97)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _grid_destination(grid: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "grid",
        "space": "gen5-field-grid-v1",
        "zone_id": int(grid["zone_id"]),
        "x": int(grid["x"]),
        "y": int(grid["y"]),
        "z": int(grid["z"]),
    }


def _grid(sample: dict[str, Any] | None) -> dict[str, Any] | None:
    player = canonical_grid_player(sample, require_resolved=False)
    if not isinstance(player, dict):
        return None
    position = player.get("position") if isinstance(player.get("position"), dict) else player
    grid = position.get("grid") if isinstance(position, dict) else None
    if not isinstance(grid, dict):
        grid = player.get("grid")
    if not isinstance(grid, dict) or not isinstance(player.get("zone_id"), int):
        return None
    if not all(isinstance(grid.get(key), int) for key in ("x", "y", "z")):
        return None
    return {"zone_id": int(player["zone_id"]), **{key: int(grid[key]) for key in ("x", "y", "z")}}


class StoryAutomationError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 409, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class StoryAutomationService:
    """Run one bounded route -> interaction -> dialogue workflow."""

    def __init__(
        self,
        navigation: NavigationTaskService,
        planner: NavigationPlanService,
        action_engine: ActionEngine,
        hub: RuntimeHub,
        static_provider: Callable[[], Any | None],
        *,
        player_refresh: Callable[[], Awaitable[dict[str, Any] | None] | dict[str, Any] | None] | None = None,
        live_actor_provider: Callable[[], Awaitable[dict[str, Any] | None] | dict[str, Any] | None] | None = None,
        max_dialogue_steps: int = 64,
    ) -> None:
        self.navigation = navigation
        self.planner = planner
        self.action_engine = action_engine
        self.hub = hub
        self.static_provider = static_provider
        self.player_refresh = player_refresh
        self.live_actor_provider = live_actor_provider
        self.max_dialogue_steps = max(1, int(max_dialogue_steps))
        self._tasks: dict[str, dict[str, Any]] = {}
        self._runners: dict[str, asyncio.Task] = {}

    @staticmethod
    def _public(record: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in record.items() if not key.startswith("_")}

    def active_task(self) -> dict[str, Any] | None:
        active = [item for item in self._tasks.values() if item.get("status") in _ACTIVE]
        return self._public(active[-1]) if active else None

    def get(self, task_id: str) -> dict[str, Any]:
        record = self._tasks.get(task_id)
        if record is None:
            raise StoryAutomationError("AUTOMATION_TASK_NOT_FOUND", "Automation task was not found.", status_code=404)
        return self._public(record)

    async def cancel(self, task_id: str) -> dict[str, Any]:
        record = self._tasks.get(task_id)
        if record is None:
            raise StoryAutomationError("AUTOMATION_TASK_NOT_FOUND", "Automation task was not found.", status_code=404)
        if record.get("status") in _TERMINAL:
            return self._public(record)
        record["status"] = "cancelling"
        nav_id = record.get("navigation_task_id")
        if isinstance(nav_id, str):
            try:
                await self.navigation.cancel(nav_id)
            except Exception:
                pass
        runner = self._runners.get(task_id)
        if runner is not None and not runner.done():
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        record["status"] = "cancelled"
        record["stop_reason"] = {"code": "AUTOMATION_CANCELLED", "message": "Automation task cancelled by client."}
        record["updated_at"] = _now()
        await self._emit(record, "story.automation.cancelled", "High-level story automation cancelled.")
        return self._public(record)

    def capabilities(self) -> dict[str, Any]:
        return {
            "format": "black2-story-automation-capabilities/v1",
            "read_before_write": True,
            "workflow": [
                "resolve PlayerRuntime and layered game state",
                "resolve a live ActorSystem NPC binding when a selector is used",
                "NavigationTaskService closed-loop route and landing verification",
                "face target and press A only after verified arrival",
                "advance active dialogue until it ends or a choice requires policy",
                "write event log and bounded playtest memory",
            ],
            "services": {
                "recovery": {
                    "aliases": ["nearest", "pokemon_center", "pokemon_center_nurse", "heal"],
                    "current_verified_registry": ["Universal Pokémon Center nurse script 2100 counter interaction"],
                    "party_hp_completion": "verified via PlayerPartyDecoder HP and status verification",
                    "execution_available": True,
                },
                "pc_storage": {
                    "status": "candidate",
                    "current_candidate_registry": ["Zone 443 furniture script 2108"],
                    "execution_available": False,
                    "reason": "PC menu and party-box semantics still need RAM/action verification",
                },
                "npc_dialogue": {"execution_available": True, "target_selection": ["npc_id", "script_id", "coordinate"]},
            },
            "dialogue": {
                "default": "auto_a_until_end",
                "choice_policy_default": "stop_and_request_choice",
                "text_sources": ["/api/state", "/api/dialogue/history", "/api/v1/agent/events/log"],
            },
            "endpoints": {
                "services": "/api/v1/agent/services/nearby",
                "recover": "/api/v1/agent/automation/recover",
                "interact": "/api/v1/agent/automation/interact",
                "task": "/api/v1/agent/automation/tasks/{task_id}",
                "cancel": "/api/v1/agent/automation/tasks/{task_id}/cancel",
            },
        }

    def _provider(self) -> Any:
        provider = self.static_provider()
        if provider is None:
            raise StoryAutomationError("AUTOMATION_ROM_UNAVAILABLE", "ROM-backed service resolution is unavailable.", status_code=503)
        return provider

    def _zone_label(self, zone_id: int) -> dict[str, Any]:
        override = ZONE_LABEL_OVERRIDES.get(int(zone_id))
        provider = self._provider()
        try:
            header = provider.rom.zone(int(zone_id))
            area = provider.rom.area(header.area_id)
            environment = "exterior" if bool(area.is_exterior) else "interior"
        except Exception as exc:
            return {"name_zh": f"Zone {zone_id}", "name_en": f"Zone {zone_id}", "confidence": "unresolved", "error": type(exc).__name__}
        if override:
            return {**override, "source": "operator_confirmed", "confidence": "confirmed_for_current_session"}
        return {
            "name_zh": f"Zone {zone_id}（{environment}）",
            "name_en": f"Zone {zone_id} ({environment})",
            "source": "ROM ZoneHeader + AreaHeader",
            "confidence": "candidate",
        }

    @staticmethod
    def _npc_grid(zone_id: int, raw: dict[str, Any]) -> dict[str, int] | None:
        try:
            # Event records use x/east, y/south, z/elevation.
            return {"zone_id": int(zone_id), "x": int(raw["x"]), "y": int(raw.get("z", 0)), "z": int(raw["y"])}
        except (KeyError, TypeError, ValueError):
            return None

    def _entities(self, zone_id: int) -> dict[str, Any]:
        provider = self._provider()
        try:
            header = provider.rom.zone(int(zone_id))
            return provider.rom.entities(header.entities_id)
        except (IndexError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
            raise StoryAutomationError("AUTOMATION_ENTITY_DECODE_FAILED", "Zone entities could not be decoded.", status_code=503, details={"zone_id": zone_id, "reason": str(exc)}) from exc

    def _service_candidates(self, zone_id: int, current: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        entities = self._entities(zone_id)
        label = self._zone_label(zone_id)
        current_grid = current or {}
        candidates: list[dict[str, Any]] = []
        for raw in entities.get("npcs") or []:
            script_id = raw.get("script_id")
            grid = self._npc_grid(zone_id, raw)
            if grid is None:
                continue
            try:
                script_id_int = int(script_id)
            except (TypeError, ValueError):
                script_id_int = None
            if script_id_int == _NURSE_SCRIPT_ID:
                # All Unova Pokémon Centers share script 2100 with the exact same
                # counter geometry: nurse sprite at grid, counter tile at z+1,
                # player stand tile at z+2 facing North.
                distance = sum(abs(int(grid[key]) - int(current_grid.get(key, grid[key]))) for key in ("x", "y", "z"))
                counter = {**grid, "z": int(grid["z"]) + 1}
                stand = {**grid, "z": int(grid["z"]) + 2}
                candidate = {
                    "service_id": f"pokemon_center_nurse:{zone_id}:{raw.get('id', raw.get('record_index'))}",
                    "service_type": "recovery",
                    "kind": "npc",
                    "label": "精灵中心护士",
                    "zone_id": int(zone_id),
                    "zone_label": label,
                    "target": _grid_destination(grid),
                    "selector": {"npc_id": raw.get("id"), "script_id": script_id_int},
                    "execution_available": True,
                    "confidence": "verified_script_geometry",
                    "distance_tiles_candidate": distance,
                    "evidence": {"source": "ROM entity script_id", "script_id": script_id_int, "geometry": "stand=z+2,target=z+1"},
                    "interaction": {
                        "kind": "npc",
                        "target": _grid_destination(counter),
                        "stand_tile": _grid_destination(stand),
                        "facing": "North",
                        "execute": True,
                        "mode": "service_counter",
                        "service_npc_target": _grid_destination(grid),
                    },
                    "service_target": _grid_destination(grid),
                    "interaction_evidence": {
                        "status": "verified_counter_interaction",
                        "case_id": "case_038_nurse_counter_press_a",
                        "text_source": "/api/state.loaded_dialogue_text",
                    },
                }
                candidates.append(candidate)
        # The three script-2108 furniture records are useful discovery facts,
        # but their menu/action semantics are not verified yet. Expose them so
        # a later calibration can promote the PC workflow without changing the
        # service-selection contract.
        if int(zone_id) == 443:
            for raw in entities.get("furniture") or []:
                try:
                    script_id = int(raw.get("script_id"))
                except (TypeError, ValueError):
                    continue
                if script_id != _PC_SCRIPT_ID:
                    continue
                candidates.append({
                    "service_id": f"pc_storage:{zone_id}:{raw.get('id', raw.get('record_index'))}",
                    "service_type": "pc_storage",
                    "kind": "furniture",
                    "label": "电脑存储/更换精灵候选",
                    "zone_id": int(zone_id),
                    "zone_label": label,
                    "execution_available": False,
                    "confidence": "candidate_script",
                    "raw_coordinate": {key: raw.get(key) for key in ("x", "y", "z")},
                    "evidence": {"source": "ROM furniture script_id", "script_id": _PC_SCRIPT_ID, "reason": "PC menu semantics unresolved"},
                })
        return candidates

    def nearby_services(self, *, service_type: str = "recovery", zone_id: int | None = None) -> dict[str, Any]:
        current = _grid(player_runtime_service.latest)
        selected_zone = int(zone_id) if zone_id is not None else (current.get("zone_id") if current else None)
        if selected_zone is None:
            raise StoryAutomationError("AUTOMATION_PLAYER_UNRESOLVED", "PlayerRuntime does not expose the current Zone/GPos.", status_code=409)
        wanted = str(service_type or "recovery").lower()
        candidates = [item for item in self._service_candidates(selected_zone, current) if wanted in {"nearest", "all"} or item.get("service_type") == wanted]
        candidates.sort(key=lambda item: (item.get("distance_tiles_candidate", 10**9), str(item.get("service_id"))))
        return {
            "format": "black2-agent-services/v1",
            "status": "resolved",
            "current": current,
            "service_type": wanted,
            "candidates": candidates,
            "execution_policy": "Only execution_available=true candidates may start an automation task; candidate PC records remain read-only.",
        }

    @staticmethod
    def _runtime_actor_rows(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Normalize the bounded ActorSystem response used by story routing."""
        if not isinstance(payload, dict):
            return []
        actors = payload.get("actors")
        if isinstance(actors, dict):
            actors = actors.get("actors") or actors.get("runtime")
        if not isinstance(actors, list):
            return []
        return [row for row in actors if isinstance(row, dict) and row.get("is_player") is not True]

    @staticmethod
    def _runtime_actor_grid(actor: dict[str, Any]) -> dict[str, int] | None:
        grid = actor.get("grid")
        if not isinstance(grid, dict):
            grid = actor.get("grid_position")
        if not isinstance(grid, dict):
            position = actor.get("position") if isinstance(actor.get("position"), dict) else {}
            grid = position.get("grid") if isinstance(position.get("grid"), dict) else None
        if not isinstance(grid, dict):
            return None
        try:
            return {key: int(grid[key]) for key in ("x", "y", "z")}
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _actor_matches_zone(actor: dict[str, Any], zone_id: int) -> bool:
        if actor.get("same_current_scene") is False:
            return False
        for key in ("effective_zone_id_candidate", "effective_zone_id"):
            value = actor.get(key)
            if value is not None:
                try:
                    return int(value) == int(zone_id)
                except (TypeError, ValueError):
                    return False
        value = actor.get("zone_id")
        try:
            # The live overlay preserves raw ZoneID=0 for the player and for
            # ambiguous actors.  A non-player story target must not be
            # accepted from that value unless the overlay also promoted its
            # effective scene membership.
            return int(value) == int(zone_id) and int(value) != 0
        except (TypeError, ValueError):
            return False

    def _record_target_resolution_block(
        self,
        *,
        target: dict[str, Any],
        current: dict[str, Any],
        code: str,
        details: dict[str, Any],
    ) -> None:
        """Persist selector failures so another agent can diagnose them."""
        event = {
            "type": "story_automation.target_resolution_blocked",
            "code": code,
            "target": copy.deepcopy(target),
            "current": copy.deepcopy(current),
            "details": copy.deepcopy(details),
        }
        playtest_memory.record_event(event)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(agent_event_bus.publish(
                "story.automation.target_resolution_blocked",
                summary="Story NPC selector was blocked before any input was sent.",
                data=event,
            ))
        except RuntimeError:
            # Direct unit callers may not own an event loop; the persistent
            # memory record above is still sufficient evidence.
            pass

    def _resolve_npc_target(
        self,
        target: dict[str, Any],
        current: dict[str, Any],
        *,
        runtime_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        zone_id = int(target.get("zone_id", current["zone_id"]))
        if all(key in target and target.get(key) is not None for key in ("x", "y", "z")):
            return {"zone_id": zone_id, "x": int(target["x"]), "y": int(target["y"]), "z": int(target["z"]), "source": "explicit_coordinate"}
        entities = self._entities(zone_id)
        matches: list[dict[str, Any]] = []
        for raw in entities.get("npcs") or []:
            if target.get("npc_id") is not None and str(raw.get("id")) != str(target.get("npc_id")):
                continue
            if target.get("script_id") is not None:
                try:
                    if int(raw.get("script_id", -1)) != int(target["script_id"]):
                        continue
                except (TypeError, ValueError):
                    continue
            grid = self._npc_grid(zone_id, raw)
            if grid is not None:
                matches.append({**grid, "source": "ROM_npc_selector", "npc_id": raw.get("id"), "script_id": raw.get("script_id")})
        if zone_id != int(current["zone_id"]):
            raise StoryAutomationError("AUTOMATION_CROSS_ZONE_TARGET", "NPC automation currently requires the target to be in the live Zone.", details={"player_zone_id": current["zone_id"], "target_zone_id": zone_id})
        if matches:
            obstacle_matches = []
            item_matches = []
            runtime_story_actor_keys = {
                (zone_id, int(actor.get("script_id", -1)), int(actor.get("model_id", -1)))
                for actor in self._runtime_actor_rows(runtime_payload)
                if isinstance(actor, dict)
                and self._actor_matches_zone(actor, zone_id)
                and actor.get("script_id") is not None
                and actor.get("model_id") is not None
            } if runtime_payload is not None else set()
            for raw in entities.get("npcs") or []:
                if target.get("npc_id") is not None and str(raw.get("id")) != str(target.get("npc_id")):
                    continue
                if target.get("script_id") is not None:
                    try:
                        if int(raw.get("script_id", -1)) != int(target["script_id"]):
                            continue
                    except (TypeError, ValueError):
                        continue
                try:
                    sprite_id = int(raw.get("sprite_id") or 0)
                    script_id = int(raw.get("script_id") or 0)
                    flag_id = int(raw.get("flag_id") or raw.get("spawn_flag") or 0)
                except (TypeError, ValueError):
                    continue
                is_verified_runtime_story_actor = (zone_id, script_id, sprite_id) in _VERIFIED_RUNTIME_STORY_ACTORS and (zone_id, script_id, sprite_id) in runtime_story_actor_keys
                if sprite_id in _DYNAMIC_OBSTACLE_SPRITES and not is_verified_runtime_story_actor:
                    obstacle_matches.append({"id": raw.get("id"), "sprite_id": sprite_id, "script_id": script_id})
                if sprite_id in _OVERWORLD_ITEM_SPRITES or (script_id >= 7000 and flag_id > 0):
                    item_matches.append({"id": raw.get("id"), "sprite_id": sprite_id, "script_id": script_id})
            if obstacle_matches or item_matches:
                code = "AUTOMATION_TARGET_OBSTACLE" if obstacle_matches else "AUTOMATION_TARGET_ITEM"
                details = {"zone_id": zone_id, "selector": target, "obstacle_matches": obstacle_matches, "item_matches": item_matches}
                self._record_target_resolution_block(target=target, current=current, code=code, details=details)
                raise StoryAutomationError(code, "The selected ROM entity is not a talkable NPC; no input was sent.", details=details)

        # A selector is only executable when the current bounded ActorSystem
        # sample binds it to a live actor.  Keep the old static-only behavior
        # for direct library/unit callers that did not supply a live sample;
        # the HTTP automation endpoint always supplies one.
        if runtime_payload is not None and (target.get("npc_id") is not None or target.get("script_id") is not None):
            actors = [
                actor for actor in self._runtime_actor_rows(runtime_payload)
                if self._actor_matches_zone(actor, zone_id)
            ]
            live_candidates: list[dict[str, Any]] = []
            for static in matches:
                static_script = static.get("script_id")
                for actor in actors:
                    try:
                        if static_script is None or int(actor.get("script_id", -1)) != int(static_script):
                            continue
                    except (TypeError, ValueError):
                        continue
                    grid = self._runtime_actor_grid(actor)
                    if grid is None:
                        continue
                    live_candidates.append({
                        "zone_id": zone_id,
                        **grid,
                        "source": "runtime_actor_binding",
                        "static_coordinate": dict(static),
                        "npc_id": static.get("npc_id"),
                        "script_id": static.get("script_id"),
                        "runtime_actor_uid": actor.get("actor_uid", actor.get("uid")),
                        "runtime_slot": actor.get("slot"),
                        "runtime_address": actor.get("address"),
                        "runtime_facing": actor.get("facing", actor.get("face_direction")),
                    })
            if not live_candidates:
                details = {
                    "zone_id": zone_id,
                    "selector": target,
                    "static_candidates": matches,
                    "live_actor_candidates": [
                        {
                            "actor_uid": actor.get("actor_uid", actor.get("uid")),
                            "slot": actor.get("slot"),
                            "script_id": actor.get("script_id"),
                            "model_id": actor.get("model_id"),
                            "grid": self._runtime_actor_grid(actor),
                        }
                        for actor in actors
                    ],
                    "runtime_payload_status": runtime_payload.get("status"),
                }
                self._record_target_resolution_block(
                    target=target,
                    current=current,
                    code="AUTOMATION_NPC_RUNTIME_UNRESOLVED",
                    details=details,
                )
                raise StoryAutomationError(
                    "AUTOMATION_NPC_RUNTIME_UNRESOLVED",
                    "The selector has no live ActorSystem binding in the current scene; no input was sent.",
                    details=details,
                )
            return live_candidates[0]
        if not matches and target.get("service"):
            matches = [item["target"] | {"source": item.get("confidence")} for item in self._service_candidates(zone_id, current) if item.get("service_type") == target.get("service")]
        if not matches:
            raise StoryAutomationError("AUTOMATION_TARGET_NOT_FOUND", "No matching NPC/service target was found in the current Zone.", status_code=404, details={"zone_id": zone_id, "selector": target})
        return matches[0]

    async def start_interact_async(
        self,
        *,
        target: dict[str, Any],
        movement_mode: str = "auto",
        max_steps: int = 2000,
        auto_dialogue: bool = True,
        max_dialogue_steps: int | None = None,
        choice_policy: str = "stop",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """HTTP-facing selector path with a fresh bounded actor sample."""
        runtime_payload = None
        if self.live_actor_provider is not None:
            try:
                value = self.live_actor_provider()
                if inspect.isawaitable(value):
                    value = await value
                runtime_payload = value if isinstance(value, dict) else {"status": "unresolved", "actors": []}
            except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError) as exc:
                details = {"selector": target, "reason": f"{type(exc).__name__}: {exc}"}
                current = _grid(player_runtime_service.latest) or {}
                self._record_target_resolution_block(
                    target=target,
                    current=current,
                    code="AUTOMATION_NPC_RUNTIME_UNRESOLVED",
                    details=details,
                )
                raise StoryAutomationError(
                    "AUTOMATION_NPC_RUNTIME_UNRESOLVED",
                    "The bounded ActorSystem sample failed; no input was sent.",
                    details=details,
                ) from exc
        return self.start_interact(
            target=target,
            movement_mode=movement_mode,
            max_steps=max_steps,
            auto_dialogue=auto_dialogue,
            max_dialogue_steps=max_dialogue_steps,
            choice_policy=choice_policy,
            correlation_id=correlation_id,
            runtime_payload=runtime_payload,
        )

    async def _refresh(self, *, force_live: bool = False) -> dict[str, Any] | None:
        """Return a bounded PlayerRuntime view for the workflow.

        RuntimeHub already samples the same RAM-backed structure in the
        background.  Prefer that cached projection for ordinary checks so a
        story task does not queue a second request behind the hub sampler.
        A caller at a hard transition boundary can request one live refresh;
        it is explicitly time-bounded and falls back to the last coherent
        snapshot if the bridge is busy or Field discovery is in progress.
        """
        cached: dict[str, Any] | None = None
        try:
            snapshot = self.hub.snapshot()
            candidate = snapshot.get("player") if isinstance(snapshot, dict) else None
            if isinstance(candidate, dict):
                cached = candidate
        except Exception:
            cached = None

        cached_usable = isinstance(cached, dict) and cached.get("status") in {"resolved", "candidate"}
        if cached_usable and not force_live:
            return cached

        if self.player_refresh is not None:
            try:
                value = self.player_refresh()
                if inspect.isawaitable(value):
                    value = await asyncio.wait_for(value, timeout=_PLAYER_REFRESH_TIMEOUT_SECONDS)
                if isinstance(value, dict):
                    return value
            except asyncio.TimeoutError:
                # The cached projection remains authoritative for bounded
                # automation.  Do not turn a slow probe into a stuck task.
                pass
            except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError):
                pass

        if cached is not None:
            return cached
        return player_runtime_service.latest

    async def _sample_snapshot(self) -> dict[str, Any]:
        """Take one bounded semantic sample, falling back to the hub cache."""
        fallback = self.hub.snapshot()
        try:
            return await asyncio.wait_for(self.hub.sample_once(), timeout=_HUB_SAMPLE_TIMEOUT_SECONDS)
        except (asyncio.TimeoutError, ConnectionError, TimeoutError, OSError, RuntimeError, ValueError):
            return fallback

    async def _emit(self, record: dict[str, Any], event_type: str, summary: str, *, data: dict[str, Any] | None = None) -> None:
        await agent_event_bus.publish(
            event_type,
            task_id=record.get("task_id"),
            correlation_id=record.get("correlation_id"),
            frame=(player_runtime_service.latest or {}).get("frame"),
            session_id=(self.hub.snapshot().get("transport") or {}).get("session_id"),
            resources={
                "task": f"/api/v1/agent/automation/tasks/{record.get('task_id')}",
                "agent_state": "/api/v1/agent/state",
                "memory": "/api/v1/ai/memory",
            },
            summary=summary,
            data=data or {},
        )

    def _new_record(self, *, kind: str, target: dict[str, Any], correlation_id: str | None) -> dict[str, Any]:
        task_id = f"auto_{uuid4().hex}"
        return {
            "format": "black2-agent-automation-task/v1",
            "task_id": task_id,
            "kind": kind,
            "status": "queued",
            "phase": "preflight",
            "created_at": _now(),
            "updated_at": _now(),
            "goal": target,
            "target": None,
            "current": _grid(player_runtime_service.latest),
            "navigation_task_id": None,
            "dialogue": {"mode": "auto_a_until_end", "steps": 0, "texts": [], "choice": None},
            "recovery": {"requested": kind == "recover", "completion": "unresolved"},
            "stop_reason": None,
            "arrival": None,
            "correlation_id": correlation_id,
            "event_cursor_start": agent_event_bus.cursor,
        }

    def _ensure_idle_field(self) -> dict[str, Any]:
        current = _grid(player_runtime_service.latest)
        if current is None:
            raise StoryAutomationError("AUTOMATION_PLAYER_UNRESOLVED", "PlayerRuntime does not expose a resolved Zone/GPos.")
        snap = self.hub.snapshot()
        runtime = snap.get("runtime") or {}
        semantic = snap.get("semantic") or {}
        context = semantic.get("context") or {}
        if runtime.get("status") != "ready" or semantic.get("ready_for_input") is not True:
            raise StoryAutomationError("AUTOMATION_STATE_NOT_READY", "The runtime is not ready for a story action.", details={"runtime": runtime, "context": context})
        screen_type = normalize_screen_type(context.get("screen_type"))
        if context.get("is_dialogue_active") is True or screen_type != "OVERWORLD":
            raise StoryAutomationError("AUTOMATION_INPUT_NOT_OWNED", "A story automation task can start only while the field owns input.", details={"screen_type": context.get("screen_type"), "dialogue_active": context.get("is_dialogue_active")})
        if (player_runtime_service.latest.get("locomotion") or {}).get("phase") not in {"Idle", "Turning", "Brake"}:
            raise StoryAutomationError("AUTOMATION_PLAYER_MOVING", "PlayerRuntime is not at a stable movement checkpoint.")
        return current

    def start_interact(
        self,
        *,
        target: dict[str, Any],
        movement_mode: str = "auto",
        max_steps: int = 2000,
        auto_dialogue: bool = True,
        max_dialogue_steps: int | None = None,
        choice_policy: str = "stop",
        correlation_id: str | None = None,
        runtime_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if any(item.get("status") in _ACTIVE for item in self._tasks.values()):
            raise StoryAutomationError("AUTOMATION_INPUT_BUSY", "Another high-level automation task is active.", status_code=423)
        current = self._ensure_idle_field()
        resolved = self._resolve_npc_target(target, current, runtime_payload=runtime_payload)
        record = self._new_record(kind="interact", target=target, correlation_id=correlation_id)
        record["target"] = _grid_destination(resolved)
        record["dialogue"]["auto"] = bool(auto_dialogue)
        record["dialogue"]["choice_policy"] = choice_policy
        record["dialogue"]["max_steps"] = max(1, min(int(max_dialogue_steps or self.max_dialogue_steps), 256))
        record["_movement_mode"] = movement_mode
        record["_max_steps"] = max(1, min(int(max_steps), 10000))
        self._tasks[record["task_id"]] = record
        self._runners[record["task_id"]] = asyncio.create_task(
            self._run_interaction(record, movement_mode=movement_mode, max_steps=max_steps),
            name=f"story-automation:{record['task_id']}",
        )
        return self._public(record)

    def start_recovery(
        self,
        *,
        service: str = "nearest",
        movement_mode: str = "auto",
        max_steps: int = 2000,
        auto_dialogue: bool = True,
        max_dialogue_steps: int | None = None,
        choice_policy: str = "stop",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        if any(item.get("status") in _ACTIVE for item in self._tasks.values()):
            raise StoryAutomationError("AUTOMATION_INPUT_BUSY", "Another high-level automation task is active.", status_code=423)
        current = self._ensure_idle_field()
        record = self._new_record(kind="recover", target={"service": service}, correlation_id=correlation_id)
        record["dialogue"]["auto"] = bool(auto_dialogue)
        record["dialogue"]["choice_policy"] = choice_policy
        record["dialogue"]["max_steps"] = max(1, min(int(max_dialogue_steps or self.max_dialogue_steps), 256))
        record["recovery"]["service"] = service
        record["recovery"]["starting_zone"] = current["zone_id"]
        self._tasks[record["task_id"]] = record
        self._runners[record["task_id"]] = asyncio.create_task(
            self._run_recovery(record, movement_mode=movement_mode, max_steps=max_steps),
            name=f"story-recovery:{record['task_id']}",
        )
        return self._public(record)

    @staticmethod
    def _same_grid_position(left: dict[str, Any] | None, right: dict[str, Any] | None) -> bool:
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        try:
            return all(int(left.get(key, -1)) == int(right.get(key, -2)) for key in ("zone_id", "x", "y", "z"))
        except (TypeError, ValueError):
            return False

    def _dialogue_arrival_from_navigation(self, record: dict[str, Any], nav: dict[str, Any]) -> dict[str, Any] | None:
        """Promote a verified stand-tile dialogue trigger to an arrival.

        Some field scripts start their message layer as soon as the player
        reaches the adjacent tile.  Navigation correctly stops before its
        optional A press and reports ``NAV_INTERRUPTED_BY_DIALOGUE``.  When
        the live PlayerRuntime is exactly on the requested stand tile, this is
        a successful route-to-dialogue handoff, not a navigation failure.
        """
        reason = nav.get("stop_reason") if isinstance(nav, dict) else None
        if not isinstance(reason, dict) or reason.get("code") != "NAV_INTERRUPTED_BY_DIALOGUE":
            return None
        interaction = record.get("interaction") if isinstance(record.get("interaction"), dict) else nav.get("interaction")
        if not isinstance(interaction, dict):
            return None
        stand = interaction.get("stand_tile")
        if not isinstance(stand, dict):
            return None
        live = _grid(player_runtime_service.latest)
        if not self._same_grid_position(live, stand):
            return None
        nav_current = nav.get("current") if isinstance(nav.get("current"), dict) else {}
        nav_position = nav_current.get("position") if isinstance(nav_current.get("position"), dict) else nav_current
        if isinstance(nav_position, dict) and not self._same_grid_position(
            {"zone_id": nav_current.get("zone_id"), **nav_position},
            live,
        ):
            return None
        latest_position = player_runtime_service.latest.get("position") if isinstance(player_runtime_service.latest, dict) else {}
        arrival = {
            "zone_id": live["zone_id"],
            "position": {key: live[key] for key in ("x", "y", "z")},
            "world": latest_position.get("world") if isinstance(latest_position, dict) else None,
            "frame": player_runtime_service.latest.get("frame") if isinstance(player_runtime_service.latest, dict) else None,
            "evidence": "canonical_player_runtime",
            "interaction": copy.deepcopy(interaction),
            "facing": interaction.get("facing"),
            "interact_pressed": False,
            "dialogue_started_during_navigation": True,
        }
        record["navigation_dialogue_handoff"] = {
            "status": "verified",
            "navigation_stop": copy.deepcopy(reason),
            "stand_tile": copy.deepcopy(stand),
        }
        return {
            "status": "succeeded",
            "arrival": arrival,
            "navigation_stop_reason": copy.deepcopy(reason),
            "dialogue_started_during_navigation": True,
        }

    async def _wait_navigation(self, record: dict[str, Any], nav_id: str, *, timeout: float = 180.0) -> dict[str, Any]:
        record["navigation_task_id"] = nav_id
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            nav = self.navigation.get(nav_id)
            record["navigation"] = nav
            record["current"] = _grid(player_runtime_service.latest)
            record["updated_at"] = _now()
            if nav.get("status") in {"succeeded", "failed", "cancelled"}:
                if nav.get("status") != "succeeded":
                    dialogue_arrival = self._dialogue_arrival_from_navigation(record, nav)
                    if dialogue_arrival is not None:
                        return dialogue_arrival
                    raise StoryAutomationError("AUTOMATION_NAVIGATION_FAILED", "The closed-loop navigation task did not reach its verified target.", details={"navigation": nav})
                return nav
            await asyncio.sleep(0.10)
        raise StoryAutomationError("AUTOMATION_NAVIGATION_TIMEOUT", "The navigation subtask did not finish within the bounded timeout.")

    async def _run_navigation_to(
        self,
        record: dict[str, Any],
        destination: dict[str, Any],
        *,
        movement_mode: str,
        max_steps: int,
        interaction: bool = True,
        interaction_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record["phase"] = "routing"
        record["status"] = "routing"
        record["updated_at"] = _now()
        route_destination = destination
        occupied = []
        if interaction:
            if interaction_payload is None:
                interaction_payload, route_destination = self._select_interaction_route(
                    record,
                    destination,
                    movement_mode=movement_mode,
                )
            else:
                route_destination = interaction_payload.get("stand_tile") or destination
            occupied = [interaction_payload.get("target") if isinstance(interaction_payload, dict) else destination]
        nav = self.navigation.start(
            route_destination,
            max_steps=int(max_steps),
            occupied=occupied,
            interaction=interaction_payload,
            movement_mode=movement_mode,
            navigation_intent="interact" if interaction else "walk_to_tile",
            correlation_id=record.get("correlation_id"),
        )
        await self._emit(
            record,
            "story.automation.routing.started",
            "High-level automation started closed-loop navigation.",
            data={
                "destination": route_destination,
                "interaction_target": destination if interaction else None,
                "interaction": interaction_payload,
                "navigation_task_id": nav.get("task_id"),
            },
        )
        return await self._wait_navigation(record, str(nav["task_id"]))

    def _select_interaction_route(
        self,
        record: dict[str, Any],
        target: dict[str, Any],
        *,
        movement_mode: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Find a planner-approved adjacent standing tile before execution."""
        target_node = _grid_destination(target)
        candidates = [
            (0, 1, "North"),
            (-1, 0, "East"),
            (1, 0, "West"),
            (0, -1, "South"),
        ]
        current = _grid(player_runtime_service.latest)

        def is_current_stand(tile: dict[str, Any]) -> bool:
            return bool(
                current
                and int(current.get("zone_id", -1)) == int(tile["zone_id"])
                and int(current.get("x", -1)) == int(tile["x"])
                and int(current.get("y", -1)) == int(tile["y"])
                and int(current.get("z", -1)) == int(tile["z"])
            )

        # Prefer the already verified adjacent tile.  A live NPC can occupy
        # another candidate standing tile even when the static planner only
        # knows about the target cell; selecting that tile first creates an
        # avoidable NAV_DYNAMIC_BLOCKED failure and may walk the player away
        # from an interaction that is already lined up.
        candidates = sorted(
            candidates,
            key=lambda item: 0 if is_current_stand({
                "zone_id": int(target["zone_id"]),
                "x": int(target["x"]) + item[0],
                "y": int(target["y"]),
                "z": int(target["z"]) + item[1],
            }) else 1,
        )

        rejected: list[dict[str, Any]] = []
        for dx, dz, facing in candidates:
            stand = {
                "type": "grid",
                "space": "gen5-field-grid-v1",
                "zone_id": int(target["zone_id"]),
                "x": int(target["x"]) + dx,
                "y": int(target["y"]),
                "z": int(target["z"]) + dz,
            }
            interaction = {
                "kind": "npc",
                "target": target_node,
                "stand_tile": stand,
                "facing": facing,
                "turn_only": False,
                "execute": True,
            }
            try:
                plan = self.planner.create_plan(
                    stand,
                    occupied=[target],
                    interaction=interaction,
                    movement_mode=movement_mode,
                    navigation_intent="interact",
                )
            except NavigationPlanningError as exc:
                # A zero-step interaction must remain executable even when
                # the observed graph has not yet persisted the path that put
                # us on the standing tile.  The canonical PlayerRuntime is
                # already the stronger evidence here: it proves the player is
                # physically on this tile, so asking the planner for a route
                # would add no safety.  Keep this fallback bounded to the
                # current tile and to the planner's explicit no-route result.
                if exc.code == "NAV_NO_ROUTE" and is_current_stand(stand):
                    interaction["route_validation"] = "runtime_verified_current_stand"
                    record["interaction"] = {"target": target_node, "stand_tile": stand, "facing": facing, "route_validation": interaction["route_validation"]}
                    return interaction, stand
                rejected.append({"stand_tile": stand, "code": exc.code, "message": exc.message})
                continue
            # NavigationPlanService's public contract is ``status=ready``;
            # older embedded planners used a boolean ``reachable`` field.
            # Accept either shape so a valid zero-step standing plan is not
            # rejected merely because the compatibility field is omitted.
            if plan.get("status") != "ready" and plan.get("reachable") is not True:
                if plan.get("status") == "no_route" and is_current_stand(stand):
                    interaction["route_validation"] = "runtime_verified_current_stand"
                    record["interaction"] = {"target": target_node, "stand_tile": stand, "facing": facing, "route_validation": interaction["route_validation"]}
                    return interaction, stand
                rejected.append({"stand_tile": stand, "code": "NAV_UNREACHABLE", "message": "Planner returned a non-reachable interaction standing tile."})
                continue
            record["interaction"] = {"target": target_node, "stand_tile": stand, "facing": facing}
            return interaction, stand
        raise StoryAutomationError(
            "AUTOMATION_INTERACTION_STAND_NOT_FOUND",
            "No planner-approved adjacent standing tile is available for this NPC.",
            details={"target": target_node, "rejected_candidates": rejected},
        )

    async def _auto_dialogue(self, record: dict[str, Any]) -> dict[str, Any]:
        if not record["dialogue"].get("auto", True):
            return {"status": "skipped", "reason": "auto_dialogue=false"}
        record["phase"] = "dialogue"
        record["status"] = "dialogue"
        # A message page can be replaced asynchronously by the script engine.
        # The old 10-second deadline plus an inactive-only check allowed a
        # transient page gap to be reported as completion while the nurse's
        # final page was still active.  Keep the workflow bounded, but give
        # the RAM-backed semantic layer enough time to settle every page.
        start_deadline = time.monotonic() + (45.0 if record.get("kind") == "recover" else 25.0)
        active_seen = False
        settled_streak = 0
        last_text = None
        while time.monotonic() < start_deadline:
            # Use one fresh semantic sample per dialogue step.  A cached hub
            # snapshot can briefly lag behind the TextPrinter during a page
            # swap; reading the same state API that the agent can inspect
            # keeps completion tied to the actual RAM-backed layer.
            try:
                snap = await self.hub.sample_once()
            except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError):
                snap = self.hub.snapshot()
            dialogue = snap.get("dialogue") or {}
            active = bool(dialogue.get("active"))
            printer = dialogue.get("printer") if isinstance(dialogue.get("printer"), dict) else {}
            text_source = "full_text"
            text_value = dialogue.get("full_text")
            if not text_value:
                text_source = "visible_text"
                text_value = dialogue.get("visible_text") or dialogue.get("text")
            if not text_value:
                text_source = "loaded_text"
                text_value = dialogue.get("loaded_text") or printer.get("loaded_text")
            text = str(text_value or "").strip()
            choices = dialogue.get("choices") or []
            screen_type = normalize_screen_type(dialogue.get("screen_type"))
            can_move_player = dialogue.get("can_move_player")
            wait_state = snap.get("wait_state") if isinstance(snap.get("wait_state"), dict) else None
            if wait_state is None and isinstance(snap, dict) and snap.get("semantic") is not None:
                wait_state = derive_wait_state(snap)
            auto_contract = (
                ((wait_state or {}).get("context") or {}).get("auto_advance_contract")
                if isinstance((wait_state or {}).get("context"), dict)
                else None
            )
            if text and text != last_text:
                last_text = text
                record["dialogue"]["texts"].append(text)
                record["dialogue"]["texts"] = record["dialogue"]["texts"][-50:]
                playtest_memory.record_event({
                    "type": "story_automation.dialogue_text",
                    "task_id": record["task_id"],
                    "text": text[:2000],
                    "zone_id": (player_runtime_service.latest or {}).get("zone_id"),
                    "frame": (player_runtime_service.latest or {}).get("frame"),
                })
                await self._emit(
                    record,
                    "story.automation.dialogue.text",
                    "High-level automation captured dialogue text.",
                    data={"text": text[:2000], "source": text_source},
                )
            if choices:
                policy = record["dialogue"].get("choice_policy", "stop")
                if policy in ("select_first", "first", "auto") or record.get("kind") == "recover":
                    await self.action_engine.select_dialogue_choice(0)
                    record["dialogue"]["steps"] = record["dialogue"].get("steps", 0) + 1
                    await asyncio.sleep(0.5)
                    continue
                record["status"] = "waiting_input"
                record["phase"] = "dialogue_choice"
                record["dialogue"]["choice"] = choices
                await self._emit(record, "story.automation.dialogue.choice_required", "Dialogue presented a choice; automation paused without guessing.", data={"choices": choices})
                return {"status": "waiting_input", "choices": choices}
            if active or text:
                # The hub may briefly report inactive while the message
                # script swaps pages.  Loaded text is still authoritative
                # evidence that a dialogue page is present, so it keeps the
                # bounded workflow alive until the page is actually cleared.
                active_seen = True
            if active:
                settled_streak = 0
                if record["dialogue"]["steps"] >= record["dialogue"]["max_steps"]:
                    raise StoryAutomationError("AUTOMATION_DIALOGUE_LIMIT", "Dialogue did not end within the bounded A-button budget.", details={"steps": record["dialogue"]["steps"]})
                # When the live renderer is unresolved, the wait-state layer
                # is the authority that decides whether A is safe.  A plain
                # loaded MsgBuffer is not enough because it may contain a
                # future yes/no page.  Keep the legacy unit/test fallback for
                # embedded callers that do not provide a wait projection.
                if record.get("kind") != "recover" and wait_state is not None and wait_state.get("auto_policy") != "press_A_once":
                    await asyncio.sleep(0.20)
                    continue
                if isinstance(auto_contract, dict):
                    try:
                        page_edges = int(auto_contract.get("page_edges_observed", 0))
                    except (TypeError, ValueError):
                        page_edges = 0
                    try:
                        max_auto_edges = int(auto_contract.get("max_auto_edges", page_edges + 2))
                    except (TypeError, ValueError):
                        max_auto_edges = page_edges + 2
                    # The observed count is the normal path, while the extra
                    # bounded retries cover a lost edge or an A press that
                    # only completed the final text-printing handoff.  The
                    # content-addressed contract still makes these retries
                    # safe because it has no unresolved choice menu.
                    if max_auto_edges > 0 and record["dialogue"]["steps"] >= max_auto_edges:
                        await asyncio.sleep(0.20)
                        continue
                await self.action_engine.advance_dialogue_once()
                record["dialogue"]["steps"] += 1
                # Text-printer transitions are slower than the bridge input
                # acknowledgement.  A short bounded settle interval avoids
                # stacking A presses on the same page and is still cheaper
                # than taking screenshots or replaying a failed route.
                settle_delay = 0.38
                if isinstance(auto_contract, dict):
                    try:
                        settle_delay = max(
                            settle_delay,
                            float(auto_contract.get("minimum_edge_interval_seconds", settle_delay)),
                        )
                    except (TypeError, ValueError):
                        pass
                await asyncio.sleep(settle_delay)
                continue
            if active_seen:
                # Completion is a semantic field checkpoint, not merely an
                # inactive flag.  In particular, the runtime can expose a
                # short inactive gap before the final TextPrinter page is
                # loaded.  Require the field layer to be back in OVERWORLD,
                # movement to be enabled, no text/choice to remain, and keep
                # that state for a bounded settle window.
                settled = (
                    not text
                    and not choices
                    and screen_type == "OVERWORLD"
                    and can_move_player is True
                )
                if settled:
                    settled_streak += 1
                else:
                    settled_streak = 0
                if settled_streak >= 8:
                    return {"status": "ended", "steps": record["dialogue"]["steps"], "texts": list(record["dialogue"]["texts"])}
            await asyncio.sleep(0.20)
        # A recovery workflow must never claim success with an unresolved
        # active dialogue.  Generic NPC interactions may legitimately have no
        # dialogue at all, but once any page was observed a timeout is a hard
        # evidence failure and is surfaced to the task caller.
        if active_seen:
            raise StoryAutomationError(
                "AUTOMATION_DIALOGUE_NOT_SETTLED",
                "Dialogue remained active or did not reach a verified OVERWORLD checkpoint before the bounded wait expired.",
                details={
                    "steps": record["dialogue"]["steps"],
                    "texts": list(record["dialogue"]["texts"]),
                    "last_screen_type": screen_type,
                    "last_can_move_player": can_move_player,
                },
            )
        return {"status": "no_dialogue_observed", "steps": record["dialogue"]["steps"], "texts": list(record["dialogue"]["texts"])}

    async def _wait_for_field_ready(
        self,
        record: dict[str, Any],
        *,
        expected_zone: int | None = None,
        timeout_seconds: float = 12.0,
    ) -> dict[str, Any]:
        """Wait for a RAM-backed post-transition input checkpoint.

        A verified warp landing and a controllable Field runtime are separate
        facts. During the short loading handoff PlayerRuntime can already
        expose the destination Zone while RuntimeHub still reports
        ``degraded``. Starting navigation in that gap races its preflight and
        can lose the first safe action, so wait for a fresh controllable field.
        """
        deadline = time.monotonic() + max(0.5, float(timeout_seconds))
        last: dict[str, Any] = {}
        while time.monotonic() < deadline:
            # Expose the current wait stage in the task resource as well as in
            # the event stream.  This makes a stalled workflow diagnosable
            # without attaching a debugger to the emulator process.
            record["phase"] = "waiting_field_ready"
            record["updated_at"] = _now()
            # RuntimeHub is continuously sampling in the background.  This
            # checkpoint must remain cache-only: calling sample_once here can
            # queue behind the same bridge lock that is responsible for
            # publishing the readiness event we are waiting for.
            snap = self.hub.snapshot()
            current = _grid(snap.get("player") if isinstance(snap, dict) else None)
            if current is None:
                current = _grid(player_runtime_service.latest)
            runtime = snap.get("runtime") if isinstance(snap, dict) else {}
            semantic = snap.get("semantic") if isinstance(snap, dict) else {}
            context = semantic.get("context") if isinstance(semantic, dict) else {}
            context = context if isinstance(context, dict) else {}
            player = snap.get("player") if isinstance(snap, dict) else {}
            player = player if isinstance(player, dict) else {}
            locomotion = player.get("locomotion") if isinstance(player.get("locomotion"), dict) else {}
            screen_type = normalize_screen_type(context.get("screen_type"))
            current_zone = current.get("zone_id") if isinstance(current, dict) else None
            last = {
                "current": copy.deepcopy(current),
                "runtime": copy.deepcopy(runtime),
                "context": copy.deepcopy(context),
                "screen_type": screen_type,
                "locomotion_phase": locomotion.get("phase"),
            }
            if self._field_ready_snapshot(snap, current, expected_zone):
                record["current"] = copy.deepcopy(current)
                ready_data = {"current": copy.deepcopy(current), "screen_type": screen_type}
                await self._emit(
                    record,
                    "story.automation.field_ready",
                    "Post-transition field runtime is ready for the next bounded action.",
                    data=ready_data,
                )
                playtest_memory.record_event({
                    "type": "story_automation.field_ready",
                    "task_id": record.get("task_id"),
                    **ready_data,
                })
                return current
            await asyncio.sleep(0.15)
        raise StoryAutomationError(
            "AUTOMATION_FIELD_NOT_READY",
            "The destination field did not reach a fresh controllable OVERWORLD checkpoint before the bounded wait expired.",
            details=last,
        )

    @staticmethod
    def _field_ready_snapshot(
        snap: dict[str, Any],
        current: dict[str, Any] | None,
        expected_zone: int | None,
    ) -> bool:
        """Evaluate the RAM-backed field checkpoint without issuing input."""
        runtime = snap.get("runtime") if isinstance(snap, dict) else {}
        runtime = runtime if isinstance(runtime, dict) else {}
        semantic = snap.get("semantic") if isinstance(snap, dict) else {}
        semantic = semantic if isinstance(semantic, dict) else {}
        context = semantic.get("context") if isinstance(semantic.get("context"), dict) else {}
        player = snap.get("player") if isinstance(snap, dict) else {}
        player = player if isinstance(player, dict) else {}
        locomotion = player.get("locomotion") if isinstance(player.get("locomotion"), dict) else {}
        screen_type = normalize_screen_type(context.get("screen_type"))
        current_zone = current.get("zone_id") if isinstance(current, dict) else None
        zone_ready = expected_zone is None or current_zone == int(expected_zone)
        return bool(
            current is not None
            and zone_ready
            and runtime.get("status") == "ready"
            and semantic.get("ready_for_input") is True
            and screen_type == "OVERWORLD"
            and context.get("is_dialogue_active") is not True
            and context.get("can_move_player") is True
            and locomotion.get("phase") in _FIELD_IDLE_PHASES
        )

    async def _run_interaction(self, record: dict[str, Any], *, movement_mode: str = "auto", max_steps: int = 2000) -> None:
        try:
            nav = await self._run_navigation_to(
                record,
                record["target"],
                movement_mode=movement_mode,
                max_steps=max_steps,
                interaction=True,
                interaction_payload=record.get("interaction"),
            )
            record["arrival"] = nav.get("arrival")
            record["phase"] = "interacting"
            record["status"] = "interacting"
            await self._emit(record, "story.automation.interaction.ready", "Verified standing tile and facing are ready; interaction A was issued by the navigation task.")
            dialogue = await self._auto_dialogue(record)
            record["dialogue_result"] = dialogue
            if dialogue.get("status") == "waiting_input":
                return
            if record["dialogue"].get("auto", True) and dialogue.get("status") == "no_dialogue_observed":
                # An A acknowledgement is not proof that an NPC script ran.
                # Item/message scripts can disappear before the semantic
                # dialogue decoder samples them, and a blocked/mis-bound
                # target can also leave the field unchanged.  Surface both
                # cases instead of publishing a false successful interaction.
                playtest_memory.record_event({
                    "type": "story_automation.dialogue_not_observed",
                    "task_id": record["task_id"],
                    "target": record.get("target"),
                    "arrival": record.get("arrival"),
                    "evidence_policy": "capture screenshot/RAM at the caller when semantic state stays OVERWORLD",
                })
                raise StoryAutomationError(
                    "AUTOMATION_DIALOGUE_NOT_OBSERVED",
                    "The interaction A was acknowledged, but no dialogue/message layer was observed; automation stopped without claiming success.",
                    details={"dialogue": dialogue, "arrival": record.get("arrival")},
                )
            if record["dialogue"].get("auto", True):
                # A generic A interaction can start a trainer/wild battle or
                # another blocking layer without ever exposing a dialogue
                # page.  Do not report that as a successful NPC conversation:
                # take one fresh semantic checkpoint and leave the input to
                # the appropriate battle/transition owner.
                try:
                    checkpoint = await self.hub.sample_once()
                except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError):
                    checkpoint = self.hub.snapshot()
                semantic = checkpoint.get("semantic") if isinstance(checkpoint, dict) else {}
                context = semantic.get("context") if isinstance(semantic, dict) and isinstance(semantic.get("context"), dict) else {}
                battle = checkpoint.get("battle") if isinstance(checkpoint, dict) and isinstance(checkpoint.get("battle"), dict) else {}
                checkpoint_screen = normalize_screen_type(context.get("screen_type"))
                if battle.get("active") is True or checkpoint_screen != "OVERWORLD" or context.get("can_move_player") is False:
                    raise StoryAutomationError(
                        "AUTOMATION_INTERACTION_INTERRUPTED",
                        "The A interaction entered a blocking layer without a verified dialogue end; automation stopped without claiming success.",
                        details={
                            "dialogue": dialogue,
                            "screen_type": context.get("screen_type"),
                            "can_move_player": context.get("can_move_player"),
                            "battle": battle,
                        },
                    )
            if record.get("kind") == "recover" and dialogue.get("status") != "ended":
                raise StoryAutomationError(
                    "AUTOMATION_RECOVERY_DIALOGUE_UNVERIFIED",
                    "The recovery interaction did not produce a verified dialogue end checkpoint.",
                    details={"dialogue": dialogue},
                )
            record["status"] = "succeeded"
            record["phase"] = "completed"
            record["stop_reason"] = None
            await self._emit(record, "story.automation.completed", "High-level route, interaction and dialogue workflow completed.", data={"dialogue": dialogue})
            playtest_memory.record_event({"type": "story_automation.completed", "task_id": record["task_id"], "kind": record["kind"], "dialogue": dialogue})
            # RuntimeHub may intentionally be a little older than the direct
            # PlayerRuntime sample used by the executor.  Persist the
            # authoritative live player explicitly so memory does not point
            # back to the pre-navigation tile after a successful task.
            playtest_memory.sync_runtime(
                self.hub.snapshot(),
                note="高层自动交互已完成；继续读取剧情状态和下一目标。",
                player=player_runtime_service.latest,
            )
        except asyncio.CancelledError:
            raise
        except StoryAutomationError as exc:
            record["status"] = "failed"
            record["phase"] = "failed"
            record["stop_reason"] = {"code": exc.code, "message": exc.message, "details": exc.details}
            await self._emit(record, "story.automation.failed", exc.message, data=record["stop_reason"])
        except (NavigationPlanningError, ConnectionError, TimeoutError, OSError, RuntimeError) as exc:
            record["status"] = "failed"
            record["phase"] = "failed"
            record["stop_reason"] = {"code": "AUTOMATION_INTERNAL", "message": f"{type(exc).__name__}: {exc}"}
            await self._emit(record, "story.automation.failed", record["stop_reason"]["message"], data=record["stop_reason"])
        except Exception as exc:
            # Never leave a task falsely active after an unexpected adapter or
            # decoder error.  Keep the exact exception in the event record.
            record["status"] = "failed"
            record["phase"] = "failed"
            record["stop_reason"] = {
                "code": "AUTOMATION_UNEXPECTED",
                "message": f"{type(exc).__name__}: {exc}",
            }
            await self._emit(record, "story.automation.failed", record["stop_reason"]["message"], data=record["stop_reason"])
        finally:
            record["updated_at"] = _now()

    async def _ensure_recovery_center(self, record: dict[str, Any], *, movement_mode: str, max_steps: int) -> dict[str, Any]:
        current = _grid(await self._refresh())
        if current is None:
            raise StoryAutomationError("AUTOMATION_PLAYER_UNRESOLVED", "PlayerRuntime does not expose a resolved location.")
        try:
            candidates = self._service_candidates(int(current["zone_id"]), current)
            if any(item.get("service_type") == "recovery" for item in candidates):
                return current
        except Exception:
            pass
        if current["zone_id"] == 443:
            return current
        connector = _VERIFIED_RECOVERY_CONNECTORS.get((int(current["zone_id"]), 443))
        if connector is None:
            raise StoryAutomationError("AUTOMATION_RECOVERY_NOT_FOUND", "No executable verified recovery route is registered for the current Zone.", details={"current": current, "services": self.nearby_services(service_type="recovery")})

        # A transition sample may be recorded on the first tile inside the
        # door (105,1,693) rather than on the approach tile from which the
        # button was pressed (105,1,694).  Verify the connector using every
        # calibrated source variant, then always navigate to the known safe
        # approach tile.  This lets "nearest recovery" work from anywhere in
        # the source Zone while still refusing an unverified cross-Zone input.
        approach = connector["approach_grid"]
        evidence: list[dict[str, Any]] = []
        seen_evidence: set[tuple[Any, ...]] = set()
        for source_grid in connector["evidence_source_grids"]:
            rows = runtime_warp_evidence.match(
                source_zone=439,
                source_x=int(source_grid["x"]),
                source_z=int(source_grid["z"]),
                destination_zone=443,
            )
            for row in rows:
                key = (
                    row.get("frame_before"),
                    row.get("frame_after"),
                    tuple(sorted((row.get("source_grid") or {}).items())),
                    tuple(sorted((row.get("landing_grid") or {}).items())),
                )
                if key not in seen_evidence:
                    seen_evidence.add(key)
                    evidence.append(row)
        if not evidence:
            raise StoryAutomationError(
                "AUTOMATION_RECOVERY_ROUTE_UNVERIFIED",
                "The nearest Pokémon Center connector has no live reverse-transition evidence yet; no blind cross-Zone input was issued.",
                details={"source_zone": 439, "destination_zone": 443, "approach_grid": approach, "evidence_endpoint": "/api/v1/navigation/warp-evidence"},
            )

        destination = _grid_destination(approach)
        await self._run_navigation_to(record, destination, movement_mode=movement_mode, max_steps=max_steps, interaction=False)
        record["phase"] = "connector"
        record["status"] = "routing"
        current = _grid(await self._refresh())
        if current and current["zone_id"] == 443:
            return current

        connector_log = {
            "status": "started",
            "source_zone": 439,
            "destination_zone": 443,
            "approach_grid": copy.deepcopy(approach),
            "button": connector["button"],
            "evidence_count": len(evidence),
            "calibration_case": connector["calibration_case"],
            "poll_interval_seconds": connector["poll_interval_seconds"],
            "transition_timeout_seconds": connector["transition_timeout_seconds"],
            "observed_before_input": copy.deepcopy(current),
            "polls": 0,
        }
        record["recovery"]["connector"] = connector_log
        await self._emit(
            record,
            "story.automation.recovery.connector.started",
            "Verified recovery connector input issued; waiting for RAM-backed Zone transition.",
            data=connector_log,
        )
        await self.action_engine.press_button(
            str(connector["button"]),
            hold_frames=int(connector["hold_frames"]),
            wait_frames=int(connector["wait_frames"]),
        )

        # The door input is accepted before the game's loading transition is
        # visible in PlayerRuntime.  Poll the semantic/RAM source rather than
        # guessing from timing or declaring success after the button call.
        deadline = time.monotonic() + float(connector["transition_timeout_seconds"])
        while time.monotonic() < deadline:
            current = _grid(await self._refresh())
            connector_log["polls"] = int(connector_log["polls"]) + 1
            record["current"] = copy.deepcopy(current)
            if current and current["zone_id"] == 443:
                connector_log["status"] = "succeeded"
                connector_log["observed_landing"] = copy.deepcopy(current)
                record["recovery"]["connector"] = connector_log
                await self._emit(
                    record,
                    "story.automation.recovery.connector.completed",
                    "Recovery connector reached the verified Pokémon Center Zone landing.",
                    data=connector_log,
                )
                return current
            if current and current["zone_id"] not in {439, 443}:
                connector_log["status"] = "unexpected_zone"
                connector_log["observed"] = copy.deepcopy(current)
                break
            await asyncio.sleep(float(connector["poll_interval_seconds"]))

        connector_log["status"] = connector_log.get("status") if connector_log.get("status") == "unexpected_zone" else "timeout"
        connector_log["observed"] = copy.deepcopy(current)
        record["recovery"]["connector"] = connector_log
        raise StoryAutomationError(
            "AUTOMATION_RECOVERY_CONNECTOR_FAILED",
            "The verified connector did not produce the expected Pokémon Center Zone landing before its bounded transition wait expired.",
            details={"observed": current, "connector": connector_log},
        )

    async def _run_recovery(self, record: dict[str, Any], *, movement_mode: str, max_steps: int) -> None:
        try:
            record["phase"] = "locating"
            record["status"] = "routing"
            record["updated_at"] = _now()
            record["phase_detail"] = "ensure_recovery_center"
            current = await self._ensure_recovery_center(record, movement_mode=movement_mode, max_steps=max_steps)
            record["phase_detail"] = "wait_for_field_ready"
            record["updated_at"] = _now()
            current = await self._wait_for_field_ready(record, expected_zone=443)
            record["phase_detail"] = "resolve_recovery_service"
            record["updated_at"] = _now()
            record["current"] = current
            candidates = self._service_candidates(443, current)
            nurse = next((item for item in candidates if item.get("service_type") == "recovery" and item.get("execution_available")), None)
            if nurse is None:
                raise StoryAutomationError("AUTOMATION_RECOVERY_NURSE_NOT_FOUND", "The Pokémon Center nurse target is not available with verified execution semantics.", details={"zone_id": 443, "candidates": candidates})
            record["target"] = nurse["target"]
            record["interaction"] = nurse.get("interaction")
            record["recovery"]["service_target"] = nurse
            record["phase_detail"] = "route_to_recovery_nurse"
            record["updated_at"] = _now()
            party_before = None
            try:
                if hasattr(self.hub, "party_decoder") and self.hub.party_decoder is not None:
                    party_before = await self.hub.party_decoder.sample()
            except Exception:
                pass

            await self._run_interaction(record, movement_mode=movement_mode, max_steps=max_steps)

            party_after = None
            try:
                if hasattr(self.hub, "party_decoder") and self.hub.party_decoder is not None:
                    party_after = await self.hub.party_decoder.sample()
            except Exception:
                pass

            healed = False
            if isinstance(party_after, dict) and party_after.get("status") in ("candidate", "resolved", "partial"):
                slots = party_after.get("slots", [])
                if slots and all(s.get("current_hp") == s.get("max_hp") and s.get("status_raw") == 0 for s in slots):
                    healed = True

            if record.get("status") == "succeeded":
                record["recovery"]["completion"] = "party_hp_healed_verified" if healed else "dialogue_completed"
                record["recovery"]["party_healed"] = healed
                record["recovery"]["verification"] = (
                    "nurse_interaction_and_party_hp_restoration_verified"
                    if healed else "nurse_interaction_and_dialogue_end_observed"
                )
                try:
                    await self.action_engine.press_button("Down", hold_frames=8, wait_frames=12)
                except Exception:
                    pass
        except asyncio.CancelledError:
            raise
        except StoryAutomationError as exc:
            record["status"] = "failed"
            record["phase"] = "failed"
            record["stop_reason"] = {"code": exc.code, "message": exc.message, "details": exc.details}
            await self._emit(record, "story.automation.failed", exc.message, data=record["stop_reason"])
        except Exception as exc:
            record["status"] = "failed"
            record["phase"] = "failed"
            record["stop_reason"] = {
                "code": "AUTOMATION_UNEXPECTED",
                "message": f"{type(exc).__name__}: {exc}",
            }
            await self._emit(record, "story.automation.failed", record["stop_reason"]["message"], data=record["stop_reason"])
        finally:
            record["updated_at"] = _now()
