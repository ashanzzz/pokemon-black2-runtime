"""Low-cost runtime actor overlay for the 3D workbench.

The expensive full-RAM Field discovery belongs to explicit diagnostics only.
Once PlayerRuntime has a coherent locator, this service reads just the
ActorSystem header plus the bounded actor heap and re-validates back pointers.
Raw actor ZoneID is preserved. Scene membership is a separate evidence field.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..memory.reader import MemoryReader
from .runtime_field_resolver import (
    ACTOR,
    ACTOR_SYSTEM,
    ARM9_BASE,
    DIRECTIONS,
    FX32_ONE,
    MAIN_RAM_SIZE,
    MAPPER,
)
from .runtime_player_state import player_runtime_service


@dataclass
class RuntimeActorOverlayService:
    max_capacity: int = 256
    _recovered_addresses: dict[str, int] | None = None
    _recovered_zone_id: int | None = None
    _recovered_player_grid: dict[str, int] | None = None
    _recovered_session_id: str | None = None

    @staticmethod
    def _u16(raw: bytes, offset: int) -> int | None:
        if offset < 0 or offset + 2 > len(raw):
            return None
        return int.from_bytes(raw[offset:offset + 2], "little")

    @staticmethod
    def _u32(raw: bytes, offset: int) -> int | None:
        if offset < 0 or offset + 4 > len(raw):
            return None
        return int.from_bytes(raw[offset:offset + 4], "little")

    @classmethod
    def _discover_actor_system_from_ram(cls, raw: bytes) -> dict[str, Any] | None:
        """Recover ActorSystem/FieldActor roots while battle hides Field.Player.

        The normal player locator requires the live Field -> Mapper -> Player
        graph.  Battle mode can tear down that graph while leaving the
        ActorSystem and its FieldActor heap resident.  This explicit,
        one-shot recovery is therefore bounded to the already supported
        4-MiB snapshot and promotes only a candidate whose actor back-pointers
        agree repeatedly.  It is never used by the ordinary 5 Hz sampler.
        """
        if not isinstance(raw, (bytes, bytearray, memoryview)) or len(raw) != MAIN_RAM_SIZE:
            return None
        data = bytes(raw)
        base = ARM9_BASE

        def valid_pointer(value: int | None) -> bool:
            return isinstance(value, int) and base <= value < base + MAIN_RAM_SIZE and value % 4 == 0

        best: tuple[int, dict[str, Any]] | None = None
        # ActorSystem objects are word-aligned.  The pointer/value gates make
        # this scan cheap compared with the full snapshot transfer itself.
        for system_offset in range(0, len(data) - 0x44, 4):
            capacity = cls._u16(data, system_offset + ACTOR_SYSTEM["capacity"])
            if not isinstance(capacity, int) or not 1 <= capacity <= 256:
                continue
            address = base + system_offset
            heap = cls._u32(data, system_offset + ACTOR_SYSTEM["actor_heap"])
            mapper = cls._u32(data, system_offset + ACTOR_SYSTEM["g3d_mapper"])
            field = cls._u32(data, system_offset + ACTOR_SYSTEM["field"])
            if not valid_pointer(heap) or not valid_pointer(mapper) or not valid_pointer(field):
                continue
            heap_offset = heap - base
            mapper_offset = mapper - base
            if heap_offset + capacity * ACTOR["stride"] > len(data):
                continue
            width = cls._u16(data, mapper_offset + MAPPER["matrix_width"])
            height = cls._u16(data, mapper_offset + MAPPER["matrix_height"])
            chunk_count = cls._u32(data, mapper_offset + MAPPER["chunk_id_count"])
            if not (
                isinstance(width, int) and 1 <= width <= 256
                and isinstance(height, int) and 1 <= height <= 256
                and isinstance(chunk_count, int) and 1 <= chunk_count <= 4096
            ):
                continue
            actors: list[dict[str, Any]] = []
            for slot in range(capacity):
                actor_offset = heap_offset + slot * ACTOR["stride"]
                if cls._u32(data, actor_offset + ACTOR["actor_system"]) != address:
                    continue
                uid = cls._u16(data, actor_offset + ACTOR["uid"])
                zone_id = cls._u16(data, actor_offset + ACTOR["zone_id"])
                model_id = cls._u16(data, actor_offset + ACTOR["model_id"])
                script_id = cls._u16(data, actor_offset + ACTOR["script_id"])
                event_type = cls._u16(data, actor_offset + ACTOR["event_type"])
                grid = {
                    "x": cls._u16(data, actor_offset + ACTOR["gpos_x"]),
                    "y": int.from_bytes(data[actor_offset + ACTOR["gpos_y"]:actor_offset + ACTOR["gpos_y"] + 2], "little", signed=True),
                    "z": cls._u16(data, actor_offset + ACTOR["gpos_z"]),
                }
                actors.append({
                    "slot": slot,
                    "actor_uid": uid,
                    "zone_id": zone_id,
                    "model_id": model_id,
                    "script_id": script_id,
                    "event_type": event_type,
                    "grid": grid,
                })
            if len(actors) < 2:
                continue
            player = next((actor for actor in actors if actor.get("actor_uid") == 0xFF), None)
            non_player_zones = [
                actor.get("zone_id") for actor in actors
                if actor.get("actor_uid") != 0xFF and isinstance(actor.get("zone_id"), int) and actor.get("zone_id") not in {0, 0xFFFF}
            ]
            zone_id = max(set(non_player_zones), key=non_player_zones.count) if non_player_zones else None
            score = len(actors) * 10 + (100 if player is not None else 0) + (20 if zone_id is not None else 0)
            candidate = {
                "addresses": {
                    "field": field,
                    "mapper": mapper,
                    "player_actor": base + heap_offset + (int(player["slot"]) * ACTOR["stride"]) if player else 0,
                    "actor_system": address,
                },
                "zone_id": zone_id,
                "player_grid": player.get("grid") if isinstance(player, dict) else None,
                "actor_count": len(actors),
                "actors": actors,
                "source": "explicit_full_main_ram_actor_system_recovery/v1",
            }
            if best is None or score > best[0]:
                best = (score, candidate)
        return best[1] if best is not None else None

    @staticmethod
    def _transport_session_id(reader: MemoryReader) -> str | None:
        client = getattr(reader, "client", None)
        transport = getattr(client, "transport", None)
        value = getattr(transport, "session_id", None)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _bytes(result: dict[str, Any]) -> bytes:
        values = result.get("bytes") if isinstance(result, dict) else None
        if values is not None:
            return bytes(int(v) & 0xFF for v in values)
        try:
            return bytes.fromhex(str((result or {}).get("hex", "")))
        except ValueError:
            return b""

    @staticmethod
    def _scene_membership(raw_zone: int, grid: dict[str, int], latest: dict[str, Any]) -> dict[str, Any]:
        current_zone = latest.get("zone_id")
        mapper = latest.get("mapper") or {}
        tile_size = mapper.get("chunk_tile_size")
        width = mapper.get("matrix_width")
        height = mapper.get("matrix_height")
        inside_mapper = False
        if all(isinstance(v, int) and v > 0 for v in (tile_size, width, height)):
            inside_mapper = (
                0 <= grid["x"] < width * tile_size
                and 0 <= grid["z"] < height * tile_size
            )
        if isinstance(current_zone, int) and raw_zone == current_zone:
            return {
                "same_current_scene": True,
                "effective_zone_id": current_zone,
                "confidence": "probable",
                "reason": "FieldActor.ZoneID agrees with PlayerState.ZoneID in the same coherent ActorSystem",
            }
        if isinstance(current_zone, int) and raw_zone == 0 and inside_mapper:
            return {
                "same_current_scene": True,
                "effective_zone_id": current_zone,
                "confidence": "candidate",
                "reason": (
                    "raw FieldActor.ZoneID is 0, but the actor is live in the current coherent ActorSystem "
                    "and its GPos lies inside the current runtime mapper bounds; raw ZoneID is preserved"
                ),
            }
        return {
            "same_current_scene": False,
            "effective_zone_id": None,
            "confidence": "unresolved",
            "reason": "no current-scene membership rule is satisfied",
        }

    async def sample(self, reader: MemoryReader) -> dict[str, Any]:
        session_id = self._transport_session_id(reader)
        if self._recovered_session_id is not None and self._recovered_session_id != session_id:
            self._recovered_addresses = None
            self._recovered_zone_id = None
            self._recovered_player_grid = None
        self._recovered_session_id = session_id
        addresses = player_runtime_service.locator.addresses or self._recovered_addresses
        latest = player_runtime_service.latest or {}
        # RuntimeHub can publish a fresh PlayerRuntime sample while the
        # locator is between lifecycle retries (notably just after a backend
        # restart).  Reuse only the exact discovered pointer chain already
        # present in that sample; ActorSystem/mapper coherence below still
        # gates the bounded heap read.  This avoids a second full-RAM scan and
        # keeps the live NPC endpoint usable during that short window.
        if not addresses:
            root = latest.get("root") if isinstance(latest.get("root"), dict) else {}
            required = ("field", "mapper", "player", "core", "grid", "state", "player_actor", "actor_system")
            if all(isinstance(root.get(key), str) for key in required):
                try:
                    addresses = {key: int(root[key], 16) for key in required}
                except (TypeError, ValueError):
                    addresses = None
        recovery = None
        if not addresses:
            snapshot_reader = getattr(reader, "read_full_main_ram_snapshot", None)
            if callable(snapshot_reader):
                try:
                    recovery = self._discover_actor_system_from_ram(await snapshot_reader())
                except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError, TypeError):
                    recovery = None
            if isinstance(recovery, dict) and isinstance(recovery.get("addresses"), dict):
                addresses = recovery["addresses"]
                self._recovered_addresses = dict(addresses)
                self._recovered_zone_id = recovery.get("zone_id") if isinstance(recovery.get("zone_id"), int) else None
                self._recovered_player_grid = recovery.get("player_grid") if isinstance(recovery.get("player_grid"), dict) else None
                if self._recovered_zone_id is not None:
                    latest = {**latest, "zone_id": self._recovered_zone_id}
        if not addresses:
            return {
                "format": "black2-world3d-runtime-actors/v8",
                "status": "unresolved",
                "reason": "PlayerRuntime locator and explicit ActorSystem recovery are unresolved",
                "read_policy": "zero full-RAM scans; waits for the shared Field locator",
                "actors": [],
            }

        actor_system_addr = int(addresses["actor_system"])
        field_addr = int(addresses["field"])
        mapper_addr = int(addresses["mapper"])
        player_actor_addr = int(addresses["player_actor"])

        header_payload = await reader.read_batch_snapshot([
            {"id": "actor_system", "addr": actor_system_addr, "length": 0x50},
        ])
        header = self._bytes((header_payload.get("results") or {}).get("actor_system", {}))
        frame = int(header_payload.get("frame", latest.get("frame") or 0))
        if len(header) < 0x50:
            return {"format": "black2-world3d-runtime-actors/v8", "status": "unresolved", "reason": "ActorSystem header read was truncated", "actors": []}

        u16 = lambda off: int.from_bytes(header[off:off + 2], "little")
        u32 = lambda off: int.from_bytes(header[off:off + 4], "little")
        capacity = u16(ACTOR_SYSTEM["capacity"])
        declared_count = u16(ACTOR_SYSTEM["count"])
        heap_addr = u32(ACTOR_SYSTEM["actor_heap"])
        fail_reasons = []
        if not (1 <= capacity <= self.max_capacity):
            fail_reasons.append(f"capacity {capacity} not in 1..{self.max_capacity}")
        if u32(ACTOR_SYSTEM["field"]) != field_addr:
            fail_reasons.append(f"field 0x{u32(ACTOR_SYSTEM['field']):08X} != 0x{field_addr:08X}")
        if u32(ACTOR_SYSTEM["g3d_mapper"]) != mapper_addr:
            fail_reasons.append(f"mapper 0x{u32(ACTOR_SYSTEM['g3d_mapper']):08X} != 0x{mapper_addr:08X}")
        if not (0x02000000 <= heap_addr < 0x02400000):
            fail_reasons.append(f"heap 0x{heap_addr:08X} not in Main RAM")
        coherent = len(fail_reasons) == 0
        if not coherent:
            return {"format": "black2-world3d-runtime-actors/v8", "status": "unresolved", "reason": f"cached ActorSystem no longer passes pointer coherence: {', '.join(fail_reasons)}", "frame": frame, "actors": []}

        heap_length = capacity * ACTOR["stride"]
        heap_payload = await reader.read_batch_snapshot([
            {"id": "actor_heap", "addr": heap_addr, "length": heap_length},
        ])
        heap = self._bytes((heap_payload.get("results") or {}).get("actor_heap", {}))
        frame = int(heap_payload.get("frame", frame))
        if len(heap) != heap_length:
            return {"format": "black2-world3d-runtime-actors/v8", "status": "unresolved", "reason": f"actor heap read truncated: expected {heap_length}, got {len(heap)}", "frame": frame, "actors": []}

        result: list[dict[str, Any]] = []
        stride = ACTOR["stride"]
        for slot in range(capacity):
            rec = heap[slot * stride:(slot + 1) * stride]
            ru16 = lambda off: int.from_bytes(rec[off:off + 2], "little")
            rs16 = lambda off: int.from_bytes(rec[off:off + 2], "little", signed=True)
            ru32 = lambda off: int.from_bytes(rec[off:off + 4], "little")
            rs32 = lambda off: int.from_bytes(rec[off:off + 4], "little", signed=True)
            if ru32(ACTOR["actor_system"]) != actor_system_addr:
                continue
            actor_addr = heap_addr + slot * stride
            face = ru16(ACTOR["face_dir"])
            raw_zone = ru16(ACTOR["zone_id"])
            grid = {
                "x": ru16(ACTOR["gpos_x"]),
                "y": rs16(ACTOR["gpos_y"]),
                "z": ru16(ACTOR["gpos_z"]),
            }
            world = {
                "x": rs32(ACTOR["wpos_x"]) / FX32_ONE,
                "y": rs32(ACTOR["wpos_y"]) / FX32_ONE,
                "z": rs32(ACTOR["wpos_z"]) / FX32_ONE,
            }
            expected_x = grid["x"] * 16 + 8
            expected_z = grid["z"] * 16 + 8
            membership = self._scene_membership(raw_zone, grid, latest)
            result.append({
                "slot": slot,
                "address": f"0x{actor_addr:08X}",
                # Keep the raw lifecycle fields beside their semantic aliases.
                # These are the first fields we need when comparing a trainer
                # before/after an encounter; an actor disappearing from the
                # heap is not itself proof that its battle flag changed.
                "raw": {
                    "address": f"0x{actor_addr:08X}",
                    "stride": stride,
                    "flags_raw": ru32(ACTOR["flags"]),
                    "movement_flags_raw": ru32(ACTOR["movement_flags"]),
                    "uid_raw": ru16(ACTOR["uid"]),
                    "zone_id_raw": raw_zone,
                    "model_id_raw": ru16(ACTOR["model_id"]),
                    "move_code_raw": ru16(ACTOR["move_code"]),
                    "event_type_raw": ru16(ACTOR["event_type"]),
                    "spawn_flag_raw": ru16(ACTOR["spawn_flag"]),
                    "script_id_raw": ru16(ACTOR["script_id"]),
                    "default_dir_raw": ru16(ACTOR["default_dir"]),
                    "face_dir_raw": face,
                    "motion_dir_raw": ru16(ACTOR["motion_dir"]),
                    "last_face_dir_raw": ru16(ACTOR["last_face_dir"]),
                    "last_motion_dir_raw": ru16(ACTOR["last_motion_dir"]),
                    "next_acmd_raw": ru32(ACTOR["next_acmd"]),
                    "gpos_raw": {
                        "x": ru16(ACTOR["gpos_x"]),
                        "y": rs16(ACTOR["gpos_y"]),
                        "z": ru16(ACTOR["gpos_z"]),
                    },
                    "tcb_raw": f"0x{ru32(ACTOR['tcb']):08X}",
                    "source": "Main RAM FieldActor heap after ActorSystem pointer coherence",
                },
                "actor_uid": ru16(ACTOR["uid"]),
                "model_id": ru16(ACTOR["model_id"]),
                "obj_code_candidate": ru16(ACTOR["model_id"]),
                "obj_code_semantics": "candidate: runtime model_id is tested against the Gen5 MModel registry; do not promote until sprite/model identity is visually verified",
                # Backward compatible name remains raw; never silently rewrite it.
                "zone_id": raw_zone,
                "zone_id_raw": raw_zone,
                "scene_membership": membership,
                "same_current_scene": membership["same_current_scene"],
                "effective_zone_id_candidate": membership["effective_zone_id"],
                "is_player": actor_addr == player_actor_addr,
                "world": world,
                "grid": grid,
                "validation": {
                    "stationary_grid_centre": {"x": expected_x, "z": expected_z},
                    "residual_world": {"x": abs(world["x"] - expected_x), "z": abs(world["z"] - expected_z)},
                },
                "facing": DIRECTIONS.get(face, str(face)),
                "face_dir_raw": face,
                "default_dir_raw": ru16(ACTOR["default_dir"]),
                "motion_dir_raw": ru16(ACTOR["motion_dir"]),
                "move_code": ru16(ACTOR["move_code"]),
                "event_type": ru16(ACTOR["event_type"]),
                "spawn_flag": ru16(ACTOR["spawn_flag"]),
                "script_id": ru16(ACTOR["script_id"]),
                "movement_flags_raw": ru32(ACTOR["movement_flags"]),
                "last_face_dir_raw": ru16(ACTOR["last_face_dir"]),
                "last_motion_dir_raw": ru16(ACTOR["last_motion_dir"]),
                "next_acmd_raw": ru32(ACTOR["next_acmd"]),
            })

        player_actor = next((actor for actor in result if actor.get("is_player") is True), None)
        return {
            "format": "black2-world3d-runtime-actors/v8",
            "status": "resolved" if result else "candidate",
            "frame": frame,
            "refresh_policy": "bounded ActorSystem+heap read; never a 4 MiB discovery pass",
            "scene_membership_policy": "preserve raw FieldActor.ZoneID; a zero ZoneID may be a candidate current-scene actor only when ActorSystem and mapper bounds agree",
            "bytes_requested": 0x50 + heap_length,
            "capacity": capacity,
            "declared_count_raw": declared_count,
            "declared_count": declared_count,
            "active_slot_count": len(result),
            "resolved_count": len(result),
            "count_semantics": "ActorSystem.count is raw metadata; it is not used to bound active slots",
            "zone_id_candidate": self._recovered_zone_id if recovery is not None else latest.get("zone_id"),
            "player_grid": player_actor.get("grid") if isinstance(player_actor, dict) else self._recovered_player_grid,
            "recovery": recovery.get("source") if isinstance(recovery, dict) else None,
            "actors": result,
        }


runtime_actor_overlay_service = RuntimeActorOverlayService()
