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
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..bizhawk.bridge_client import BridgeClient
from ..memory.reader import MemoryReader
from ..state.engine import SemanticStateEngine
from ..world.runtime_player_state import player_runtime_service


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
            self._process_cache = {
                "emulator_running": bool(getattr(probe, "running", False)),
                "emulator_pid": getattr(probe, "pid", None),
                "emulator_exe": getattr(probe, "exe_path", None),
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
        }

    async def sample_once(self) -> dict[str, Any]:
        async with self._sample_lock:
            if not self.client.is_connected:
                self._last_semantic_error = None
                self._publish_without_semantic()
                return self.snapshot()

            try:
                state_model = await self.state_engine.sample_once()
                state = state_model.model_dump()
                self._last_good_semantic = state
                self._last_semantic_error = None
            except Exception as exc:
                state = copy.deepcopy(self._last_good_semantic) if self._last_good_semantic else None
                self._last_semantic_error = f"{type(exc).__name__}: {exc}"

            # state_engine.read_live_map_state() uses player_runtime_service, so
            # reuse its exact latest sample instead of issuing another RAM read.
            player = copy.deepcopy(player_runtime_service.latest) if player_runtime_service.latest else None
            self._last_sample_at = time.time()
            transport = self._transport()
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
            }
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        if not self._latest:
            self._publish_without_semantic()
        result = copy.deepcopy(self._latest)
        if self._last_sample_at:
            result["age_seconds"] = max(0.0, time.time() - self._last_sample_at)
        result["transport"] = {**result.get("transport", {}), **self._transport()}
        return result

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
        }

    def semantic_state(self) -> dict[str, Any] | None:
        snap = self.snapshot()
        return copy.deepcopy(snap.get("semantic"))
