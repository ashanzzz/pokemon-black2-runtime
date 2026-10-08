"""Single-flight runtime sampling hub shared by every frontend module.

Why this exists
---------------
Historically each browser page called ``/api/state`` (and sometimes two or
three other endpoints) on its own timer.  BizHawk serves memory requests from a
single emulator-frame Lua loop, so concurrent browser polling could queue
behind a dialogue or map read.  The dashboard then interpreted one failed
semantic request as "backend offline" even while the bridge was healthy.

The hub owns one background semantic sampler.  HTTP handlers return cached
snapshots immediately.  Transport health is independent from semantic decode
health: a decoder may be degraded while the HTTP server and BizHawk bridge
remain online.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..bizhawk.bridge_client import BridgeClient
from ..bizhawk.process_probe import public_bizhawk_popup
from ..memory.reader import MemoryReader
from ..state.engine import SemanticStateEngine
from ..world.runtime_actor_overlay import runtime_actor_overlay_service
from ..world.runtime_player_state import player_runtime_service
from ..world.observed_navigation import observed_navigation_graph
from ..world.world3d_scene import canonical_player
from ..world.warp_transition_evidence import WarpTransitionEvidence, runtime_warp_evidence
from .events import agent_event_bus
from .json_safety import sanitize_json
from .wait_state import derive_wait_state
from ..state.playtest_memory import playtest_memory


def _battle_causal_actor_context(
    overlay: dict[str, Any] | None,
    player: dict[str, Any] | None,
    *,
    zone_id: int | None,
) -> dict[str, Any] | None:
    """Select a compact, same-scene NPC candidate at a battle transition.

    This is deliberately a candidate binding, not proof that a particular
    actor owns the battle.  The transition sample is the useful causal
    boundary: the player is still at the overworld grid and the actor heap is
    still available.  Keeping the candidate and its raw script/model fields
    lets the identity decoder and later ROM work compare the encounter without
    another full-RAM scan or screenshot-driven input.
    """
    if not isinstance(overlay, dict):
        return None
    player_position = player.get("position") if isinstance(player.get("position"), dict) else {}
    player_grid = player_position.get("grid") if isinstance(player_position.get("grid"), dict) else {}
    if not all(isinstance(player_grid.get(key), int) for key in ("x", "y", "z")):
        overlay_grid = overlay.get("player_grid") if isinstance(overlay.get("player_grid"), dict) else {}
        player_grid = overlay_grid
    if not all(isinstance(player_grid.get(key), int) for key in ("x", "y", "z")):
        return None
    actors = overlay.get("actors")
    if not isinstance(actors, list):
        return None

    candidates: list[tuple[int, int, dict[str, Any]]] = []
    for actor in actors:
        if not isinstance(actor, dict) or actor.get("is_player") is True:
            continue
        if (
            actor.get("actor_uid") == 255
            or actor.get("model_id") in {231, 232}
            or (overlay.get("player_slot") is not None and actor.get("slot") == overlay.get("player_slot"))
        ):
            continue
        grid = actor.get("grid") if isinstance(actor.get("grid"), dict) else {}
        if not all(isinstance(grid.get(key), int) for key in ("x", "y", "z")):
            continue
        actor_zone = actor.get("effective_zone_id_candidate")
        if not isinstance(actor_zone, int):
            actor_zone = actor.get("zone_id")
        if isinstance(zone_id, int) and isinstance(actor_zone, int) and actor_zone not in {0, zone_id}:
            continue
        distance = sum(abs(int(grid[key]) - int(player_grid[key])) for key in ("x", "y", "z"))
        # A battle-causing trainer must be a distinct nearby NPC (distance 1..3),
        # not the player tile (distance 0) and not an unrelated actor elsewhere.
        if distance == 0 or distance > 3:
            continue
        script_id = actor.get("script_id") if isinstance(actor.get("script_id"), int) else 0
        event_type = actor.get("event_type") if isinstance(actor.get("event_type"), int) else 0
        compact = {
            "actor_uid": actor.get("actor_uid"),
            "slot": actor.get("slot"),
            "address": actor.get("address"),
            "zone_id": actor_zone,
            "grid": copy.deepcopy(grid),
            "model_id": actor.get("model_id"),
            "script_id": script_id,
            "event_type": event_type,
            "move_code": actor.get("move_code"),
            "facing": actor.get("facing"),
            "distance_manhattan": distance,
            "scene_membership": copy.deepcopy(actor.get("scene_membership")),
            "source": "runtime_actor_overlay_at_battle_transition",
        }
        # Script-bearing/event actors sort before a purely decorative nearby
        # actor at the same distance, while distance remains primary.
        candidates.append((distance, 0 if script_id or event_type else 1, compact))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1], str(row[2].get("actor_uid"))))
    selected = copy.deepcopy(candidates[0][2])
    return {
        "status": "candidate",
        "actor": selected,
        "actor_candidates": [copy.deepcopy(row[2]) for row in candidates[:5]],
        "player_grid": copy.deepcopy(player_grid),
        "zone_id": zone_id,
        "source": "runtime_actor_overlay_at_battle_transition",
        "reason": "Nearest same-scene non-player FieldActor was captured at the first active battle sample; actor/script ownership still needs a direct trigger readback.",
    }


@dataclass
class RuntimeHub:
    client: BridgeClient
    reader: MemoryReader
    state_engine: SemanticStateEngine
    transport: Any
    sample_interval: float = 0.20
    process_probe: Callable[[], Any] | None = None
    _task: asyncio.Task | None = field(default=None, init=False)
    _sample_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _latest: dict[str, Any] = field(default_factory=dict, init=False)
    _last_good_semantic: dict[str, Any] | None = field(default=None, init=False)
    _last_sample_at: float = field(default=0.0, init=False)
    _last_semantic_error: str | None = field(default=None, init=False)
    _process_cache: dict[str, Any] = field(default_factory=dict, init=False)
    _process_probe_at: float = field(default=0.0, init=False)
    _last_event_projection: dict[str, Any] = field(default_factory=dict, init=False)
    _last_overworld_context: dict[str, Any] | None = field(default=None, init=False)
    _battle_overworld_context: dict[str, Any] | None = field(default=None, init=False)
    _last_battle_identity_probe_at: float = field(default=0.0, init=False)
    # Optional observer installed by the battle API after all decoders are
    # configured.  Keeping this callback here makes battle identity sampling
    # part of the same single-flight runtime loop instead of depending on a
    # browser polling the identity endpoint at the exact transition frame.
    battle_identity_callback: Callable[[dict[str, Any]], Any] | None = None
    # Optional bounded battle-menu observer.  The hub invokes it only while a
    # battle is active and throttles it, so the event projection can expose a
    # move/command decision without making every HTTP client scan RAM.
    battle_ui_callback: Callable[[dict[str, Any]], Any] | None = None
    session_reset_callback: Callable[[str | None, str | None], Any] | None = None
    _last_battle_ui_probe_at: float = field(default=0.0, init=False)
    _wait_event_cursor: int | None = field(default=None, init=False)
    _party_decoder: Any | None = field(default=None, init=False)

    @property
    def party_decoder(self) -> Any:
        if self._party_decoder is None and self.reader is not None:
            try:
                from ..decoders.party_runtime import PlayerPartyDecoder
                self._party_decoder = PlayerPartyDecoder(self.reader)
            except Exception:
                pass
        return self._party_decoder

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="black2-runtime-hub")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.sample_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # containment boundary: never kill the hub
                self._last_semantic_error = f"{type(exc).__name__}: {exc}"
                self._publish_without_semantic()
            await asyncio.sleep(max(0.05, self.sample_interval))

    def _transport(self) -> dict[str, Any]:
        now = time.time()
        heartbeat = float(getattr(self.transport, "last_heartbeat", 0.0) or 0.0)
        heartbeat_age = (now - heartbeat) if heartbeat else None
        connected = bool(self.client.is_connected)
        return {
            "backend_http": "online",
            "bridge_connected": connected,
            "bridge_state": "connected" if connected else "waiting",
            "frame": int(getattr(self.transport, "last_frame", 0) or 0),
            "bridge_version": getattr(self.transport, "bridge_version", "unknown"),
            "session_id": getattr(self.transport, "session_id", None),
            "last_heartbeat": heartbeat or None,
            "heartbeat_age_seconds": heartbeat_age,
        }

    def _probe_process_cached(self) -> dict[str, Any]:
        if self.process_probe is None:
            return {}
        now = time.monotonic()
        if self._process_cache and now - self._process_probe_at < 2.0:
            return dict(self._process_cache)
        try:
            probe = self.process_probe()
            popup = public_bizhawk_popup(getattr(probe, "popup", None))
            self._process_cache = {
                "emulator_running": bool(getattr(probe, "running", False)),
                "emulator_pid": getattr(probe, "pid", None),
                "emulator_exe": getattr(probe, "exe_path", None),
                "popup": popup,
                "emulation_blocked_by_popup": bool(popup.get("blocking")),
                "popup_checked_at": time.time(),
            }
        except Exception as exc:
            self._process_cache = {
                "emulator_running": None,
                "process_probe_error": f"{type(exc).__name__}: {exc}",
            }
        self._process_probe_at = now
        return dict(self._process_cache)

    @staticmethod
    def _dialogue_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
        ctx = (state or {}).get("context") or {}
        return {
            "active": bool(ctx.get("is_dialogue_active")),
            "screen_type": ctx.get("screen_type"),
            "speaker": ctx.get("speaker"),
            "speaker_category": ctx.get("speaker_category"),
            "visible_text": ctx.get("dialogue_text") or "",
            "loaded_text": ctx.get("loaded_dialogue_text") or "",
            "full_text": ctx.get("full_dialogue_text") or "",
            "active_pointer": ctx.get("active_pointer"),
            "printer": ctx.get("printer") or {},
            "choices": ctx.get("choices") or [],
            "can_move_player": ctx.get("can_move_player"),
        }

    @staticmethod
    def _profile_from_state(state: dict[str, Any] | None) -> dict[str, Any]:
        state = state or {}
        # Null is intentional: profile fields are not upgraded from UI defaults.
        return {
            "player_name": state.get("player_name"),
            "rival_name": state.get("rival_name"),
            "gender": state.get("gender"),
            "money": state.get("money"),
            "badges": state.get("badges"),
            "party_count": state.get("party_count"),
            "confidence": "verified" if any(
                state.get(key) is not None
                for key in ("player_name", "gender", "money", "badges", "party_count")
            ) else "unresolved",
        }

    @staticmethod
    def _event_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
        semantic = snapshot.get("semantic") or {}
        context = semantic.get("context") or {}
        player = snapshot.get("player") or {}
        position = player.get("position") or {}
        grid = position.get("grid") or {}
        locomotion = player.get("locomotion") or {}
        dialogue = snapshot.get("dialogue") or {}
        battle = snapshot.get("battle") or {}
        presence = battle.get("presence") or {}
        wait_state = snapshot.get("wait_state") if isinstance(snapshot.get("wait_state"), dict) else derive_wait_state(snapshot)
        semantic = snapshot.get("semantic") or {}
        context = semantic.get("context") or {}
        return {
            "session_id": (snapshot.get("transport") or {}).get("session_id"),
            "bridge_connected": (snapshot.get("transport") or {}).get("bridge_connected"),
            "runtime_status": (snapshot.get("runtime") or {}).get("status"),
            "screen_type": context.get("screen_type"),
            "zone_id": player.get("zone_id"),
            "grid_x": grid.get("x"),
            "grid_y": grid.get("y"),
            "grid_z": grid.get("z"),
            "input_owner": context.get("input_owner"),
            "locomotion_phase": locomotion.get("phase"),
            "dialogue_active": dialogue.get("active"),
            "dialogue_text": dialogue.get("visible_text"),
            "dialogue_choices": dialogue.get("choices"),
            "battle_active": presence.get("active", battle.get("active")),
            "battle_request_id": (battle.get("request") or {}).get("request_id"),
            "wait_boundary_key": wait_state.get("boundary_key"),
            "wait_status": wait_state.get("status"),
            "wait_kind": wait_state.get("kind"),
            "wait_reason": wait_state.get("reason"),
            "wait_state": copy.deepcopy(wait_state),
        }

    @staticmethod
    def _compact_event_window(payload: dict[str, Any] | None, *, after: int | None) -> dict[str, Any]:
        """Keep the context between two wait boundaries useful but bounded."""
        if not isinstance(payload, dict):
            return {"status": "unavailable", "after": after, "events": []}
        if payload.get("status") != "ok":
            return {
                "status": payload.get("status", "unavailable"),
                "after": after,
                "oldest_available": payload.get("oldest_available"),
                "events": [],
            }
        compact: list[dict[str, Any]] = []
        for event in (payload.get("events") or [])[-32:]:
            if not isinstance(event, dict):
                continue
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            # Do not copy raw RAM or large text payloads into every wait
            # boundary.  Keep enough semantic evidence for another AI to know
            # what happened and follow the resource endpoint when needed.
            item = {
                "seq": event.get("seq"),
                "type": event.get("type"),
                "observed_at": event.get("observed_at"),
                "frame": event.get("frame"),
                "summary": str(event.get("summary") or "")[:240],
            }
            if data:
                try:
                    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
                    if len(encoded) <= 1200:
                        item["data"] = data
                    else:
                        item["data"] = {"truncated": True, "keys": sorted(str(key) for key in data)[:40]}
                except (TypeError, ValueError):
                    item["data"] = {"unserializable": True}
            compact.append(item)
        return {
            "status": "ok",
            "after": after,
            "through": payload.get("next_cursor", after),
            "count": len(compact),
            "events": compact,
        }

    async def _emit_projection_changes(self, current: dict[str, Any]) -> None:
        previous = self._last_event_projection
        now = self._event_projection(current)
        if not previous:
            self._last_event_projection = now
            self._wait_event_cursor = agent_event_bus.cursor
            return
        frame = (current.get("transport") or {}).get("frame")
        session_id = now.get("session_id")
        common = {
            "frame": frame,
            "session_id": session_id,
        }
        if previous.get("session_id") != session_id:
            await agent_event_bus.publish(
                "runtime.session.changed", **common,
                resources={"runtime": "/api/v1/runtime/snapshot"},
                summary="BizHawk runtime session changed; prior queued work is stale.",
                data={"previous": previous.get("session_id"), "current": session_id},
            )
            if self.session_reset_callback is not None:
                try:
                    result = self.session_reset_callback(previous.get("session_id"), session_id)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
        if previous.get("bridge_connected") != now.get("bridge_connected"):
            await agent_event_bus.publish(
                "runtime.connected" if now.get("bridge_connected") else "runtime.disconnected",
                **common, resources={"runtime": "/api/v1/runtime/snapshot"},
                summary="BizHawk bridge connection state changed.",
                data={"connected": now.get("bridge_connected")},
            )
        if previous.get("runtime_status") != now.get("runtime_status"):
            await agent_event_bus.publish(
                "runtime.ready.changed", **common,
                resources={"runtime": "/api/v1/runtime/snapshot"},
                summary="Runtime readiness changed.",
                data={"previous": previous.get("runtime_status"), "current": now.get("runtime_status")},
            )
        if previous.get("screen_type") != now.get("screen_type"):
            await agent_event_bus.publish(
                "runtime.primary_mode.changed", **common,
                resources={"runtime": "/api/v1/runtime/snapshot"},
                summary=f"Primary mode changed from {previous.get('screen_type')} to {now.get('screen_type')}",
                data={"previous": previous.get("screen_type"), "current": now.get("screen_type")},
            )
        if previous.get("zone_id") != now.get("zone_id"):
            await agent_event_bus.publish(
                "map.zone.changed", **common,
                resources={"runtime": "/api/v1/runtime/snapshot", "map": "/api/v1/map/truth/current"},
                summary="Current runtime Zone changed.",
                data={"previous": previous.get("zone_id"), "current": now.get("zone_id")},
            )
        if previous.get("grid_y") != now.get("grid_y"):
            await agent_event_bus.publish(
                "map.layer.changed", **common,
                resources={"runtime": "/api/v1/runtime/snapshot", "map": "/api/v1/map/truth/current"},
                summary="Current runtime layer changed.",
                data={"previous": previous.get("grid_y"), "current": now.get("grid_y")},
            )
        if (previous.get("grid_x"), previous.get("grid_y"), previous.get("grid_z")) != (
            now.get("grid_x"), now.get("grid_y"), now.get("grid_z")
        ) and all(isinstance(now.get(key), int) for key in ("grid_x", "grid_y", "grid_z")):
            await agent_event_bus.publish(
                "map.player.position.changed", **common,
                resources={
                    "player_runtime": "/api/v1/player/runtime",
                    "navigation_context": "/api/v1/navigation/context",
                    "game_current": "/api/v1/game/current",
                },
                summary="PlayerRuntime grid position changed.",
                data={
                    "previous": {key: previous.get(key) for key in ("grid_x", "grid_y", "grid_z")},
                    "current": {key: now.get(key) for key in ("grid_x", "grid_y", "grid_z")},
                },
            )
        if previous.get("input_owner") != now.get("input_owner"):
            await agent_event_bus.publish(
                "runtime.input_owner.changed", **common,
                resources={"game_current": "/api/v1/game/current", "agent_state": "/api/v1/agent/state"},
                summary="The state machine assigned a different input owner.",
                data={"previous": previous.get("input_owner"), "current": now.get("input_owner")},
            )
        if previous.get("dialogue_active") != now.get("dialogue_active"):
            await agent_event_bus.publish(
                "dialogue.started" if now.get("dialogue_active") else "dialogue.ended",
                **common,
                resources={"dialogue": "/api/state", "runtime": "/api/v1/runtime/snapshot"},
                summary="Dialogue overlay started." if now.get("dialogue_active") else "Dialogue overlay ended.",
                data={"active": now.get("dialogue_active")},
            )
        if previous.get("dialogue_text") != now.get("dialogue_text") and now.get("dialogue_text"):
            await agent_event_bus.publish(
                "dialogue.text.changed", **common,
                resources={"dialogue": "/api/state"},
                summary="Visible dialogue text changed.",
                data={
                    "text_available": True,
                    "text": str(now.get("dialogue_text") or "")[:2000],
                    "choices": now.get("dialogue_choices") or [],
                },
            )
        if previous.get("dialogue_choices") != now.get("dialogue_choices") and now.get("dialogue_choices"):
            await agent_event_bus.publish(
                "dialogue.choice.required", **common,
                resources={"dialogue": "/api/state"},
                summary="Dialogue choices changed and may require input.", data={},
            )
        if previous.get("battle_active") != now.get("battle_active"):
            await agent_event_bus.publish(
                "battle.detected" if now.get("battle_active") else "battle.ended",
                **common,
                resources={"battle": "/api/v1/battle/state", "battle_request": "/api/v1/battle/request"},
                summary="Battle presence changed.", data={"active": now.get("battle_active")},
            )
        if previous.get("battle_request_id") != now.get("battle_request_id") and now.get("battle_request_id") is not None:
            await agent_event_bus.publish(
                "battle.request.changed", **common,
                resources={"battle_request": "/api/v1/battle/request"},
                summary="Battle request identity changed.", data={"request_id": now.get("battle_request_id")},
            )

        previous_wait = previous.get("wait_state") if isinstance(previous.get("wait_state"), dict) else derive_wait_state(previous)
        current_wait = current.get("wait_state") if isinstance(current.get("wait_state"), dict) else derive_wait_state(current)
        previous_key = previous_wait.get("boundary_key")
        current_key = current_wait.get("boundary_key")
        if previous_key != current_key:
            if self._wait_event_cursor is None:
                self._wait_event_cursor = agent_event_bus.cursor
            window = self._compact_event_window(
                agent_event_bus.read_since(self._wait_event_cursor, limit=64),
                after=self._wait_event_cursor,
            )
            wait_event = await agent_event_bus.publish(
                "runtime.wait.changed", **common,
                resources={
                    "wait_state": "/api/v1/agent/wait-state",
                    "runtime": "/api/v1/runtime/snapshot",
                    "events": "/api/v1/agent/events",
                    "memory": "/api/v1/ai/memory",
                },
                summary=f"Wait boundary changed to {current_wait.get('kind')}:{current_wait.get('reason')}",
                data={
                    "previous_wait": previous_wait if previous else None,
                    "current_wait": current_wait,
                    "event_window": window,
                    "policy": "one event per semantic boundary; automatic actions must use wait_id",
                },
            )
            try:
                playtest_memory.record_event({
                    "type": "runtime.wait.changed",
                    "source": "runtime_hub",
                    "frame": frame,
                    "session_id": session_id,
                    "event_id": wait_event.get("event_id"),
                    "wait": current_wait,
                    "previous_wait": previous_wait if previous else None,
                    "event_window": window,
                })
            except Exception:
                # The live event bus remains authoritative if the auxiliary
                # short-term memory file is temporarily locked.
                pass
            self._wait_event_cursor = agent_event_bus.cursor
        self._last_event_projection = now

    @staticmethod
    def _map_summary(player: dict[str, Any] | None, state: dict[str, Any] | None) -> dict[str, Any]:
        player = player or {}
        state = state or {}
        position = player.get("position") or {}
        mapper = player.get("mapper") or {}
        return {
            "location_label": state.get("location"),
            "zone_id": player.get("zone_id"),
            "grid_position": position.get("grid"),
            "world_position": position.get("world"),
            "player_chunk": mapper.get("player_chunk"),
            "chunk_tile_size": mapper.get("chunk_tile_size"),
            "matrix_dimensions": {
                "width": mapper.get("matrix_width"),
                "height": mapper.get("matrix_height"),
            },
            "truth_endpoint": "/api/v1/map/truth/current",
            "scene_endpoint": "/api/v1/map/scene/current",
        }

    def _publish_without_semantic(self) -> None:
        state = copy.deepcopy(self._last_good_semantic) if self._last_good_semantic else None
        player = copy.deepcopy(player_runtime_service.latest) if player_runtime_service.latest else None
        transport = self._transport()
        if not transport["bridge_connected"] or self._last_semantic_error:
            observed_navigation_graph.reset_trace()
        self._latest = {
            "format": "black2-runtime-snapshot/v4",
            "sampled_at": self._last_sample_at or None,
            "age_seconds": (time.time() - self._last_sample_at) if self._last_sample_at else None,
            "transport": {**transport, **self._probe_process_cached()},
            "runtime": {
                "status": "degraded" if transport["bridge_connected"] else "waiting_bridge",
                "semantic_status": "degraded" if self._last_semantic_error else "unresolved",
                "semantic_error": self._last_semantic_error,
                "last_good_semantic_available": state is not None,
            },
            "semantic": state,
            "player": player,
            "dialogue": self._dialogue_from_state(state),
            "profile": self._profile_from_state(state),
            "map": self._map_summary(player, state),
            "battle": (state or {}).get("battle") or {},
            "battle_ui": None,
            "battle_context": copy.deepcopy(self._battle_overworld_context),
        }
        self._latest["wait_state"] = derive_wait_state(self._latest)

    async def sample_once(self) -> dict[str, Any]:
        async with self._sample_lock:
            if not self.client.is_connected:
                self._last_semantic_error = None
                # The player pointer chain is valid only for the active bridge
                # attachment.  Publishing it while the bridge is absent would
                # make a disconnected emulator look like a usable map cache.
                player_runtime_service.invalidate()
                self._publish_without_semantic()
                await self._emit_projection_changes(self._latest)
                return self.snapshot()

            previous_player = copy.deepcopy(self._latest.get("player")) if self._latest else None
            previous_transport = copy.deepcopy(self._latest.get("transport")) if self._latest else {}
            try:
                state_model = await self.state_engine.sample_once()
                state = state_model.model_dump()
                self._last_good_semantic = state
                self._last_semantic_error = None
            except Exception as exc:
                state = copy.deepcopy(self._last_good_semantic) if self._last_good_semantic else None
                self._last_semantic_error = f"{type(exc).__name__}: {exc}"

            if state is not None:
                try:
                    from ..progression.state import progression_state_service
                    prog_state = await progression_state_service.sample()
                    if isinstance(prog_state, dict):
                        b_info = prog_state.get("badges", {})
                        m_info = prog_state.get("money", {})
                        if b_info.get("status") == "verified" and b_info.get("count") is not None:
                            state["badges"] = b_info["count"]
                        if m_info.get("status") == "verified" and m_info.get("amount") is not None:
                            state["money"] = m_info["amount"]
                except Exception:
                    pass

                try:
                    if self.party_decoder is not None:
                        party_res = await self.party_decoder.sample()
                        if isinstance(party_res, dict) and party_res.get("status") in ("candidate", "resolved", "partial"):
                            state["party_count"] = party_res.get("count")
                except Exception:
                    pass

            # state_engine.read_live_map_state() uses player_runtime_service, so
            # reuse its exact latest sample instead of issuing another RAM read.
            player = copy.deepcopy(player_runtime_service.latest) if player_runtime_service.latest else None
            transport = self._transport()
            if (
                previous_transport.get("session_id")
                and transport.get("session_id")
                and previous_transport.get("session_id") != transport.get("session_id")
            ):
                self._last_overworld_context = None
                self._battle_overworld_context = None
            current_battle_active = bool(((state or {}).get("battle") or {}).get("active") is True)
            previous_battle_active = bool(((self._latest.get("battle") or {}).get("active")) is True)
            # Preserve the last usable overworld frame across the short
            # transition into a battle.  Battle-mode semantic context no
            # longer owns a trustworthy map/NPC projection, so the identity
            # decoder consumes this causal snapshot rather than guessing from
            # the current battle screen.
            if not current_battle_active:
                context = (state or {}).get("context") if isinstance((state or {}).get("context"), dict) else {}
                position = (player or {}).get("position") if isinstance((player or {}).get("position"), dict) else {}
                grid = position.get("grid") if isinstance(position.get("grid"), dict) else {}
                if isinstance((player or {}).get("zone_id"), int):
                    self._last_overworld_context = {
                        "zone_id": player.get("zone_id"),
                        "grid": copy.deepcopy(grid),
                        "frame": (player or {}).get("frame") or transport.get("frame"),
                        "screen_type": context.get("screen_type"),
                        "dialogue_text": context.get("dialogue_text"),
                        "speaker": context.get("speaker"),
                        "source": "runtime_hub:last_overworld_sample",
                    }
            elif not previous_battle_active:
                # If the first active sample arrived before a non-battle hub
                # tick, use the current PlayerRuntime as a weaker fallback.
                if self._last_overworld_context is None and isinstance((player or {}).get("zone_id"), int):
                    position = (player or {}).get("position") if isinstance((player or {}).get("position"), dict) else {}
                    self._last_overworld_context = {
                        "zone_id": player.get("zone_id"),
                        "grid": copy.deepcopy(position.get("grid") if isinstance(position.get("grid"), dict) else {}),
                        "frame": (player or {}).get("frame") or transport.get("frame"),
                        "source": "runtime_hub:first_active_sample_fallback",
                    }
                self._battle_overworld_context = copy.deepcopy(self._last_overworld_context)
                # Capture the bounded actor overlay exactly at the battle
                # boundary.  This makes NPC-vs-wild classification causal and
                # keeps the normal sampler at its existing read cost.
                try:
                    actor_overlay = await runtime_actor_overlay_service.sample(self.reader)
                    effective_zone_id = (player or {}).get("zone_id") if isinstance(player, dict) else None
                    if not isinstance(effective_zone_id, int):
                        candidate_zone = actor_overlay.get("zone_id_candidate") if isinstance(actor_overlay, dict) else None
                        effective_zone_id = candidate_zone if isinstance(candidate_zone, int) else None
                    causal_context = _battle_causal_actor_context(
                        actor_overlay,
                        player,
                        zone_id=effective_zone_id,
                    )
                    if causal_context is not None:
                        if self._battle_overworld_context is None:
                            self._battle_overworld_context = {}
                        if not isinstance(self._battle_overworld_context.get("zone_id"), int) and isinstance(causal_context.get("zone_id"), int):
                            self._battle_overworld_context["zone_id"] = causal_context["zone_id"]
                        if not isinstance(self._battle_overworld_context.get("grid"), dict) and isinstance(causal_context.get("player_grid"), dict):
                            self._battle_overworld_context["grid"] = copy.deepcopy(causal_context["player_grid"])
                        if self._battle_overworld_context.get("source") is None:
                            self._battle_overworld_context["source"] = "runtime_hub:actor_recovery_at_battle_transition"
                        self._battle_overworld_context["causal_context"] = causal_context
                        self._battle_overworld_context["runtime_actor_sample_frame"] = actor_overlay.get("frame")
                except Exception:
                    # Actor causality is diagnostic enrichment.  A stale or
                    # unavailable overlay must never stop the authoritative
                    # semantic sampler or downgrade battle presence.
                    pass
            # A live Zone change is a bounded transition observation.  It is
            # intentionally recorded separately from the static ROM graph:
            # one observation can expose source/destination/landing, but it
            # cannot name the target Warp record or authorize a future route.
            try:
                before_grid = ((previous_player or {}).get("position") or {}).get("grid") or {}
                after_grid = ((player or {}).get("position") or {}).get("grid") or {}
                before_zone = (previous_player or {}).get("zone_id")
                after_zone = (player or {}).get("zone_id")
                same_session = (
                    previous_transport.get("session_id") is not None
                    and previous_transport.get("session_id") == transport.get("session_id")
                )
                if (
                    same_session
                    and isinstance(before_zone, int)
                    and isinstance(after_zone, int)
                    and before_zone != after_zone
                    and all(isinstance(before_grid.get(k), int) for k in ("x", "y", "z"))
                    and all(isinstance(after_grid.get(k), int) for k in ("x", "y", "z"))
                ):
                    runtime_warp_evidence.record(WarpTransitionEvidence(
                        source_zone=before_zone,
                        source_record=None,
                        source_grid=(before_grid["x"], before_grid["y"], before_grid["z"]),
                        destination_zone=after_zone,
                        landing_grid=(after_grid["x"], after_grid["y"], after_grid["z"]),
                        frame_before=int((previous_player or {}).get("frame") or 0),
                        frame_after=int((player or {}).get("frame") or transport.get("frame") or 0),
                        raw_arg2=None,
                        session_id=transport.get("session_id"),
                    ))
            except Exception:
                # Evidence persistence must never make the semantic sampler
                # stale or turn a valid Zone transition into a runtime error.
                pass
            # Feed the already decoded PlayerRuntime sample into the observed
            # navigation graph.  This is cache-only bookkeeping: it performs
            # no additional bridge read and only persists bounded evidence
            # when a new node/edge is seen.  Zone changes therefore become
            # available to the future route planner without requiring a
            # calibration session to be running.
            try:
                if self._last_semantic_error:
                    observed_navigation_graph.reset_trace()
                else:
                    observed_player = canonical_player(player)
                    observed_player["session_id"] = transport.get("session_id")
                    observed_navigation_graph.observe_player(observed_player, source="runtime:hub")
            except Exception:
                # Navigation evidence must never be allowed to interrupt the
                # runtime sampler or make PlayerRuntime appear degraded.
                pass
            self._last_sample_at = time.time()
            semantic_ok = state is not None and self._last_semantic_error is None
            player_ok = bool(player and player.get("status") in {"resolved", "candidate"})
            self._latest = {
                "format": "black2-runtime-snapshot/v4",
                "sampled_at": self._last_sample_at,
                "age_seconds": 0.0,
                "transport": {**transport, **self._probe_process_cached()},
                "runtime": {
                    "status": "ready" if semantic_ok and player_ok else "degraded",
                    "semantic_status": "ready" if semantic_ok else "degraded",
                    "player_status": (player or {}).get("status", "unresolved"),
                    "semantic_error": self._last_semantic_error,
                    "last_good_semantic_available": self._last_good_semantic is not None,
                },
                "semantic": state,
                "player": player,
                "dialogue": self._dialogue_from_state(state),
                "profile": self._profile_from_state(state),
                "map": self._map_summary(player, state),
                "battle": (state or {}).get("battle") or {},
                "battle_ui": None,
                "battle_context": copy.deepcopy(self._battle_overworld_context),
            }
            # A bounded battle-menu observation is attached to the same cached
            # snapshot used by the wait-state projector.  It is deliberately
            # throttled and never runs while the field is idle.
            if current_battle_active and self.battle_ui_callback is not None:
                probe_now = time.monotonic()
                if probe_now - self._last_battle_ui_probe_at >= 0.30:
                    self._last_battle_ui_probe_at = probe_now
                    try:
                        observed_ui = self.battle_ui_callback(copy.deepcopy(self._latest))
                        if asyncio.iscoroutine(observed_ui):
                            observed_ui = await observed_ui
                        if isinstance(observed_ui, dict):
                            self._latest["battle_ui"] = observed_ui
                    except Exception:
                        self._latest["battle_ui"] = None
            else:
                self._last_battle_ui_probe_at = 0.0
            self._latest["wait_state"] = derive_wait_state(self._latest)
            await self._emit_projection_changes(self._latest)
            # The first active frame can precede BattlePokeParam allocation.
            # Retry at a bounded cadence while the battle remains active so
            # the history journal can upgrade an initial unresolved sample to
            # a species/trainer candidate without browser polling.
            probe_now = time.monotonic()
            should_probe_identity = current_battle_active and (
                not previous_battle_active
                or probe_now - self._last_battle_identity_probe_at >= 1.0
            )
            if should_probe_identity and self.battle_identity_callback is not None:
                self._last_battle_identity_probe_at = probe_now
                try:
                    observed = self.battle_identity_callback(copy.deepcopy(self._latest))
                    if asyncio.iscoroutine(observed):
                        await observed
                except Exception:
                    # Identity logging is diagnostic.  A decoder or journal
                    # failure must never stop the authoritative runtime loop.
                    pass
            elif not current_battle_active:
                self._last_battle_identity_probe_at = 0.0
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        if not self._latest:
            self._publish_without_semantic()
        result = copy.deepcopy(self._latest)
        if self._last_sample_at:
            result["age_seconds"] = max(0.0, time.time() - self._last_sample_at)
        result["transport"] = {**result.get("transport", {}), **self._transport()}
        # Text decoders operate on raw Gen V code units and may temporarily
        # expose lone UTF-16 surrogates.  Never let those poison the HTTP JSON
        # boundary (which otherwise becomes a misleading 500).
        return sanitize_json(result)

    def health(self) -> dict[str, Any]:
        snap = self.snapshot()
        transport = snap["transport"]
        return {
            "format": "black2-runtime-health/v2",
            "backend_http": "online",
            "bridge_connected": bool(transport.get("bridge_connected")),
            "bridge_state": transport.get("bridge_state"),
            "frame": transport.get("frame"),
            "heartbeat_age_seconds": transport.get("heartbeat_age_seconds"),
            "runtime_status": (snap.get("runtime") or {}).get("status"),
            "semantic_status": (snap.get("runtime") or {}).get("semantic_status"),
            "player_status": (snap.get("runtime") or {}).get("player_status"),
            "snapshot_age_seconds": snap.get("age_seconds"),
            "semantic_error": (snap.get("runtime") or {}).get("semantic_error"),
            "popup": transport.get("popup"),
            "emulation_blocked_by_popup": transport.get("emulation_blocked_by_popup"),
            "popup_checked_at": transport.get("popup_checked_at"),
        }

    def popup_status(self) -> dict[str, Any]:
        """Return the cached long-running native BizHawk popup observation."""
        snap = self.snapshot()
        transport = snap.get("transport") or {}
        popup = transport.get("popup")
        known = isinstance(popup, dict)
        blocked = bool(transport.get("emulation_blocked_by_popup")) if known else None
        return {
            "format": "black2-runtime-popup/v1",
            "status": "blocked" if blocked else "clear" if known else "unresolved",
            "popup": popup if known else {},
            "emulation_blocked_by_popup": blocked,
            "popup_checked_at": transport.get("popup_checked_at"),
            "emulator_running": transport.get("emulator_running"),
            "emulator_pid": transport.get("emulator_pid"),
            "bridge_connected": transport.get("bridge_connected"),
            "detection": "cached_local_win32_window_observation",
            "dismiss_endpoint": "/api/dev/bizhawk/popup/dismiss",
        }

    def semantic_state(self) -> dict[str, Any] | None:
        snap = self.snapshot()
        semantic = snap.get("semantic")
        return copy.deepcopy(semantic) if isinstance(semantic, dict) else None
