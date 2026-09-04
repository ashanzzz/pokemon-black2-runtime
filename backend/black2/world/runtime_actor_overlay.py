"""Low-cost runtime actor overlay for the 3D workbench.

The expensive full-RAM Field discovery belongs to explicit diagnostics only.
Once PlayerRuntime has a coherent locator, this service reads just the
ActorSystem header plus the bounded actor heap and re-validates back pointers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..memory.reader import MemoryReader
from .runtime_field_resolver import ACTOR, ACTOR_SYSTEM, DIRECTIONS, FX32_ONE
from .runtime_player_state import player_runtime_service


@dataclass
class RuntimeActorOverlayService:
    max_capacity: int = 256

    @staticmethod
    def _bytes(result: dict[str, Any]) -> bytes:
        values = result.get("bytes") if isinstance(result, dict) else None
        if values is not None:
            return bytes(int(v) & 0xFF for v in values)
        try:
            return bytes.fromhex(str((result or {}).get("hex", "")))
        except ValueError:
            return b""

    async def sample(self, reader: MemoryReader) -> dict[str, Any]:
        addresses = player_runtime_service.locator.addresses
        latest = player_runtime_service.latest or {}
        if not addresses:
            return {
                "format": "black2-world3d-runtime-actors/v8",
                "status": "unresolved",
                "reason": "PlayerRuntime locator has not been discovered",
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
        coherent = (
            1 <= capacity <= self.max_capacity
            and declared_count <= capacity
            and u32(ACTOR_SYSTEM["field"]) == field_addr
            and u32(ACTOR_SYSTEM["g3d_mapper"]) == mapper_addr
            and 0x02000000 <= heap_addr < 0x02400000
        )
        if not coherent:
            return {"format": "black2-world3d-runtime-actors/v8", "status": "unresolved", "reason": "cached ActorSystem no longer passes pointer coherence", "frame": frame, "actors": []}

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
            result.append({
                "slot": slot,
                "actor_uid": ru16(ACTOR["uid"]),
                "model_id": ru16(ACTOR["model_id"]),
                "obj_code_candidate": ru16(ACTOR["model_id"]),
                "obj_code_semantics": "candidate: runtime model_id is tested against the Gen5 MModel registry; do not promote until sprite/model identity is visually verified",
                "zone_id": ru16(ACTOR["zone_id"]),
                "is_player": actor_addr == player_actor_addr,
                "world": {
                    "x": rs32(ACTOR["wpos_x"]) / FX32_ONE,
                    "y": rs32(ACTOR["wpos_y"]) / FX32_ONE,
                    "z": rs32(ACTOR["wpos_z"]) / FX32_ONE,
                },
                "grid": {
                    "x": ru16(ACTOR["gpos_x"]),
                    "y": rs16(ACTOR["gpos_y"]),
                    "z": ru16(ACTOR["gpos_z"]),
                },
                "facing": DIRECTIONS.get(face, str(face)),
                "face_dir_raw": face,
                "movement_flags_raw": ru32(ACTOR["movement_flags"]),
            })

        return {
            "format": "black2-world3d-runtime-actors/v8",
            "status": "resolved" if result else "candidate",
            "frame": frame,
            "refresh_policy": "bounded ActorSystem+heap read; never a 4 MiB discovery pass",
            "bytes_requested": 0x50 + heap_length,
            "capacity": capacity,
            "declared_count": declared_count,
            "resolved_count": len(result),
            "actors": result,
        }


runtime_actor_overlay_service = RuntimeActorOverlayService()
