"""Evidence-bounded Gen 5 field-map runtime resolver.

This module resolves the current Pokémon Black 2 field scene from *structure
coherence*, not fixed RAM addresses.  The offsets are hypotheses derived from
public Swan Gen-V structures and are only promoted when multiple forward and
back pointers agree in the same RAM image.

No memory is written.  The pure ``resolve_runtime_field_from_ram`` function is
also suitable for Universal Snapshot ``main_ram.bin`` artifacts.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import time
from typing import Any, Iterable

from ..memory.reader import MemoryReader

ARM9_BASE = 0x02000000
MAIN_RAM_SIZE = 0x400000
FX32_ONE = 4096
WORLD_UNITS_PER_TILE = 16

# Swan structure sizes/offsets used only after pointer-coherence validation.
FIELD = {
    "game_system": 0x04,
    "actor_system": 0x40,
    "no_grid_mapper": 0x44,
    "scene_area": 0x48,
    "scene_area_loader": 0x4C,
    "g3d_mapper": 0x50,
    "player": 0x94,
}
PLAYER = {"field": 0x00, "core": 0x04, "grid": 0x08, "rail": 0x0C}
PLAYER_CORE = {
    "game_system": 0x04,
    "field": 0x08,
    "state": 0x0C,
    "action_status": 0x10,
    "move_status": 0x14,
    "sex": 0x18,
    "actor": 0x1C,
    "key_move_dir_h": 0x20,
    "key_move_dir_v": 0x22,
    "special_sequence": 0x24,
    "terrain_fx_tcb": 0x28,
    "field_2c": 0x2C,
    "field_2e": 0x2E,
    "state_change_func_idx": 0x30,
    "is_state_change_done": 0x32,
}
PLAYER_STATE = {
    "zone_id": 0x00,
    "vec_x": 0x04,
    "vec_y": 0x08,
    "vec_z": 0x0C,
    "rail_component_id": 0x10,
    "rail_component_is_line": 0x12,
    "rail_direction": 0x13,
    "rail_pos_side": 0x14,
    "rail_pos_front": 0x16,
    "rotation_angle": 0x18,
    "field_1a": 0x1A,
    "is_pos_rail": 0x1B,
    "now_objcode": 0x1C,
    "field_1e": 0x1E,
    "ex_state": 0x40,
}
PLAYER_GRID = {
    "status": 0x00,
    "last_command": 0x04,
    "flags": 0x08,
    "core": 0x0C,
    "field": 0x10,
    "catwalk_focus_break_counter": 0x14,
    "catwalk_interrupted_by_menu": 0x16,
    "now_sfx": 0x18,
    "sfx_counter": 0x1A,
    "vertical_move_only": 0x1C,
}
ACTOR_SYSTEM = {
    "capacity": 0x04,
    "count": 0x06,
    "actor_heap": 0x1C,
    "g3d_mapper": 0x38,
    "no_grid_mapper": 0x3C,
    "field": 0x40,
}
ACTOR = {
    "stride": 0x100,
    "flags": 0x00,
    "movement_flags": 0x04,
    "uid": 0x08,
    "zone_id": 0x0A,
    "model_id": 0x0C,
    "move_code": 0x0E,
    "event_type": 0x10,
    "spawn_flag": 0x12,
    "script_id": 0x14,
    "default_dir": 0x16,
    "face_dir": 0x18,
    "motion_dir": 0x1A,
    "last_face_dir": 0x1C,
    "last_motion_dir": 0x1E,
    "next_acmd": 0x26,
    "gpos_x": 0x3C,
    "gpos_y": 0x3E,
    "gpos_z": 0x40,
    "wpos_x": 0x44,
    "wpos_y": 0x48,
    "wpos_z": 0x4C,
    "tile_class": 0x74,
    "tile_flags": 0x76,
    "tile_orig_y_class": 0x78,
    "tile_orig_y_flags": 0x7A,
    "collision_width": 0x7C,
    "collision_height": 0x7D,
    "model_pos_offset_x": 0x7E,
    "model_pos_offset_y": 0x7F,
    "model_pos_offset_z": 0x80,
    "shadow_group": 0x81,
    "tcb": 0x84,
    "actor_system": 0x88,
}
MAPPER = {
    "chunk_span": 0x04,
    "locator_gen_type": 0x0C,
    "chunk_arc_id": 0x10,
    "matrix_width": 0x14,
    "matrix_height": 0x16,
    "chunk_id_count": 0x18,
    "chunk_dat_ids": 0x1C,
    "chunk_handles": 0x24,
    "chunk_locators": 0x28,
    "load_diameter_x": 0x2C,
    "load_diameter_z": 0x2D,
    "chunk_capacity": 0x2E,
    "player_chunk_index": 0x30,
    "player_pos_x": 0x34,
    "player_pos_y": 0x38,
    "player_pos_z": 0x3C,
    "drawn_chunk_indices": 0x44,
    "chunk_base_x": 0x54,
    "chunk_base_y": 0x58,
    "chunk_base_z": 0x5C,
    "map_textures": 0x60,
    "prop_system": 0x64,
    "terrain_animator": 0x68,
    "wfbc": 0x6C,
    "resort_map": 0x70,
    "height_ex": 0x74,
}
CHUNK_HANDLE_STRIDE = 0x2C
CHUNK_HANDLE = {
    "chunk": 0x00,
    # inline FieldChunkContext starts at +0x04; ChunkIdx is +0x10 there.
    "context_chunk_index": 0x14,
    # inline FieldChunkLocator starts at +0x1C.
    "locator_chunk_index": 0x1C,
    "locator_pos_x": 0x20,
    "locator_pos_y": 0x24,
    "locator_pos_z": 0x28,
}
# Offsets in FieldChunk inferred from the named field_A8/field_AC boundary in
# Swan and validated against current runtime pointers.  They remain evidence-
# bounded and are never used to locate the Field root.
CHUNK = {
    "active": 0x00,
    "world_x": 0x04,
    "world_y": 0x08,
    "world_z": 0x0C,
    "map_textures": 0x94,
    "prop_res_bank": 0x98,
    "prop_instances_1": 0x9C,
    "prop_instances_2": 0xA0,
    "prop_capacity_1": 0xA4,
    "prop_capacity_2": 0xA6,
}
PROP_SYSTEM = {
    "mapper": 0x04,
    "day_part": 0x08,
    "previous_day_part": 0x0C,
    "day_part_changed": 0x10,
    "resource_bundle": 0x18,
    "resource_bundle_count": 0x21C,
    "resource_bank": 0x220,
    "resource_bank_count": 0x224,
    "resource_count": 0x238,
    "resources": 0x23C,
    "texture": 0x240,
    "resource_instance_count": 0x244,
    "resource_instances": 0x248,
}
PROP_RESOURCE_STRIDE = 0x18
PROP_INFO = {
    "uid": 0x00,
    "type": 0x02,
    "door_uid": 0x04,
    "door_x": 0x06,
    "door_y": 0x08,
    "door_z": 0x0A,
    "unknown_1": 0x0C,
    "unknown_2": 0x0E,
}
PROP_INSTANCE_STRIDE = 0x14

DIRECTIONS = {0: "North", 1: "South", 2: "West", 3: "East", 8: "Any", 9: "None"}
DIRECTION_ZH = {0: "上", 1: "下", 2: "左", 3: "右", 8: "任意", 9: "无"}
ROTATION_TO_DIRECTION = {0x0000: 0, 0x8000: 1, 0x4000: 2, 0xC000: 3}
MOVE_STATUS = {0: "Stand", 1: "Move", 2: "Turn"}
ACTION_STATUS = {0: "Idle", 1: "Begin", 2: "Performing", 3: "Finished"}
GRID_STATUS = {0: "Idle", 1: "Move", 2: "Turn", 3: "Brake", 4: "Catwalk", 5: "CatwalkExit", 6: "CatwalkExitWait", 7: "Fall"}
GRID_COMMAND = {0: "Busy", 1: "Idle", 2: "Move", 3: "Turn", 4: "Brake", 5: "Jump", 6: "CatwalkBalance", 7: "CatwalkExit", 8: "CatwalkExitWait", 9: "Fall"}
EX_STATE = {0: "OnFoot", 1: "Cycling", 2: "Surf", 3: "Dive"}


@dataclass(frozen=True)
class _Ram:
    data: bytes
    base: int = ARM9_BASE

    def _off(self, address: int, offset: int, size: int) -> int | None:
        pos = address - self.base + offset
        return pos if 0 <= pos <= len(self.data) - size else None

    def u8(self, address: int, offset: int = 0) -> int | None:
        pos = self._off(address, offset, 1)
        return self.data[pos] if pos is not None else None

    def u16(self, address: int, offset: int = 0) -> int | None:
        pos = self._off(address, offset, 2)
        return int.from_bytes(self.data[pos:pos + 2], "little") if pos is not None else None

    def s16(self, address: int, offset: int = 0) -> int | None:
        pos = self._off(address, offset, 2)
        return int.from_bytes(self.data[pos:pos + 2], "little", signed=True) if pos is not None else None

    def u32(self, address: int, offset: int = 0) -> int | None:
        pos = self._off(address, offset, 4)
        return int.from_bytes(self.data[pos:pos + 4], "little") if pos is not None else None

    def s32(self, address: int, offset: int = 0) -> int | None:
        pos = self._off(address, offset, 4)
        return int.from_bytes(self.data[pos:pos + 4], "little", signed=True) if pos is not None else None

    def valid_ptr(self, value: int | None, *, aligned: bool = True) -> bool:
        if value is None or not self.base <= value < self.base + len(self.data):
            return False
        return not aligned or value % 4 == 0

    def find_aligned(self, value: int) -> Iterable[int]:
        needle = int(value).to_bytes(4, "little")
        cursor = 0
        while True:
            pos = self.data.find(needle, cursor)
            if pos < 0:
                break
            cursor = pos + 1
            if pos % 4 == 0:
                yield self.base + pos


def _fx32(value: int | None) -> float | None:
    return None if value is None else value / FX32_ONE


def _mapper_candidates(r: _Ram) -> list[dict[str, Any]]:
    """Find FieldG3DMapper candidates without knowing any absolute address."""
    result: list[dict[str, Any]] = []
    data = r.data
    for pos in range(0, len(data) - 0x78, 4):
        address = r.base + pos
        width = r.u16(address, MAPPER["matrix_width"])
        height = r.u16(address, MAPPER["matrix_height"])
        if width is None or height is None or not (1 <= width <= 128 and 1 <= height <= 128):
            continue
        count = r.u32(address, MAPPER["chunk_id_count"])
        if count != width * height or count is None or not (1 <= count <= 4096):
            continue
        ids_ptr = r.u32(address, MAPPER["chunk_dat_ids"])
        handles_ptr = r.u32(address, MAPPER["chunk_handles"])
        locators_ptr = r.u32(address, MAPPER["chunk_locators"])
        load_x = r.u8(address, MAPPER["load_diameter_x"])
        load_z = r.u8(address, MAPPER["load_diameter_z"])
        capacity = r.u16(address, MAPPER["chunk_capacity"])
        player_chunk = r.s32(address, MAPPER["player_chunk_index"])
        chunk_span = r.s32(address, MAPPER["chunk_span"])
        score = 5  # width/height/count relation itself is strong.
        checks = {
            "matrix_dimensions": True,
            "chunk_count_matches_dimensions": True,
            "chunk_dat_ids_pointer": r.valid_ptr(ids_ptr),
            "chunk_handles_pointer": r.valid_ptr(handles_ptr),
            "chunk_locators_pointer": r.valid_ptr(locators_ptr),
            "load_diameter_plausible": bool(load_x and load_z and load_x <= 16 and load_z <= 16),
            "chunk_capacity_plausible": bool(capacity and capacity <= 256),
            "player_chunk_in_matrix": bool(player_chunk is not None and -1 <= player_chunk < count),
            "chunk_span_plausible": bool(chunk_span and 0 < chunk_span <= 0x10000000),
        }
        score += 2 * sum(checks[key] for key in (
            "chunk_dat_ids_pointer", "chunk_handles_pointer", "chunk_locators_pointer"
        ))
        score += sum(checks[key] for key in (
            "load_diameter_plausible", "chunk_capacity_plausible",
            "player_chunk_in_matrix", "chunk_span_plausible"
        ))
        sample_ok = 0
        sample_total = 0
        if r.valid_ptr(ids_ptr):
            for index in range(min(count, 32)):
                value = r.u32(ids_ptr, index * 4)
                if value is None:
                    break
                sample_total += 1
                if value == 0xFFFFFFFF or value < 4096:
                    sample_ok += 1
        if sample_total and sample_ok / sample_total >= 0.9:
            score += 3
            checks["chunk_id_table_plausible"] = True
        else:
            checks["chunk_id_table_plausible"] = False
        if score >= 11:
            result.append({
                "address": address,
                "score": score,
                "matrix_width": width,
                "matrix_height": height,
                "chunk_id_count": count,
                "checks": checks,
            })
    result.sort(key=lambda item: (-item["score"], item["address"]))
    return result[:16]


def _field_candidate(r: _Ram, field: int, mapper: int) -> dict[str, Any]:
    actor_system = r.u32(field, FIELD["actor_system"])
    player = r.u32(field, FIELD["player"])
    game_system = r.u32(field, FIELD["game_system"])
    checks: dict[str, bool] = {
        "mapper_pointer": r.u32(field, FIELD["g3d_mapper"]) == mapper,
        "game_system_pointer": r.valid_ptr(game_system),
        "actor_system_pointer": r.valid_ptr(actor_system),
        "player_pointer": r.valid_ptr(player),
    }
    score = sum(checks.values())
    core = None
    if r.valid_ptr(actor_system):
        checks["actor_system_field_backref"] = r.u32(actor_system, ACTOR_SYSTEM["field"]) == field
        checks["actor_system_mapper_backref"] = r.u32(actor_system, ACTOR_SYSTEM["g3d_mapper"]) == mapper
        score += 3 * int(checks["actor_system_field_backref"]) + 3 * int(checks["actor_system_mapper_backref"])
    if r.valid_ptr(player):
        core = r.u32(player, PLAYER["core"])
        checks["player_field_backref"] = r.u32(player, PLAYER["field"]) == field
        checks["player_core_pointer"] = r.valid_ptr(core)
        score += 3 * int(checks["player_field_backref"]) + int(checks["player_core_pointer"])
        if r.valid_ptr(core):
            checks["core_field_backref"] = r.u32(core, PLAYER_CORE["field"]) == field
            checks["core_game_system_matches"] = r.u32(core, PLAYER_CORE["game_system"]) == game_system
            score += 4 * int(checks["core_field_backref"]) + 2 * int(checks["core_game_system_matches"])
    return {
        "address": field,
        "score": score,
        "checks": checks,
        "game_system": game_system,
        "actor_system": actor_system,
        "player": player,
        "player_core": core,
    }


def _find_field(r: _Ram, mapper: int) -> list[dict[str, Any]]:
    result = []
    for pointer_address in r.find_aligned(mapper):
        field = pointer_address - FIELD["g3d_mapper"]
        if r.base <= field < r.base + len(r.data) - 0xA0:
            candidate = _field_candidate(r, field, mapper)
            if candidate["score"] >= 12:
                result.append(candidate)
    result.sort(key=lambda item: (-item["score"], item["address"]))
    return result


def _decode_actor(r: _Ram, address: int, actor_system: int | None = None) -> dict[str, Any]:
    gpos = {
        "x": r.u16(address, ACTOR["gpos_x"]),
        "y": r.s16(address, ACTOR["gpos_y"]),
        "z": r.u16(address, ACTOR["gpos_z"]),
    }
    face = r.u16(address, ACTOR["face_dir"])
    motion = r.u16(address, ACTOR["motion_dir"])
    last_face = r.u16(address, ACTOR["last_face_dir"])
    last_motion = r.u16(address, ACTOR["last_motion_dir"])
    return {
        "address": f"0x{address:08X}",
        "flags": r.u32(address, ACTOR["flags"]),
        "movement_flags": r.u32(address, ACTOR["movement_flags"]),
        "actor_uid": r.u16(address, ACTOR["uid"]),
        "zone_id": r.u16(address, ACTOR["zone_id"]),
        "model_id": r.u16(address, ACTOR["model_id"]),
        "move_code": r.u16(address, ACTOR["move_code"]),
        "event_type": r.u16(address, ACTOR["event_type"]),
        "spawn_flag": r.u16(address, ACTOR["spawn_flag"]),
        "script_id": r.u16(address, ACTOR["script_id"]),
        "default_direction_raw": r.u16(address, ACTOR["default_dir"]),
        "default_direction": DIRECTIONS.get(r.u16(address, ACTOR["default_dir"]), r.u16(address, ACTOR["default_dir"])),
        "face_direction_raw": face,
        "facing": DIRECTIONS.get(face, face),
        "facing_zh": DIRECTION_ZH.get(face, str(face)),
        "motion_direction_raw": motion,
        "motion_direction": DIRECTIONS.get(motion, motion),
        "last_face_direction_raw": last_face,
        "last_facing": DIRECTIONS.get(last_face, last_face),
        "last_motion_direction_raw": last_motion,
        "last_motion_direction": DIRECTIONS.get(last_motion, last_motion),
        "next_acmd_raw": r.u16(address, ACTOR["next_acmd"]),
        "grid_position": gpos,
        "world_position_fx32": {
            "x": r.s32(address, ACTOR["wpos_x"]),
            "y": r.s32(address, ACTOR["wpos_y"]),
            "z": r.s32(address, ACTOR["wpos_z"]),
        },
        "world_position": {
            "x": _fx32(r.s32(address, ACTOR["wpos_x"])),
            "y": _fx32(r.s32(address, ACTOR["wpos_y"])),
            "z": _fx32(r.s32(address, ACTOR["wpos_z"])),
        },
        "tile_under": {
            "class": r.u16(address, ACTOR["tile_class"]),
            "flags": r.u16(address, ACTOR["tile_flags"]),
            "original_y_class": r.u16(address, ACTOR["tile_orig_y_class"]),
            "original_y_flags": r.u16(address, ACTOR["tile_orig_y_flags"]),
            "semantic_status": "raw TileType; class/flag gameplay meaning is not inferred",
        },
        "collision_box": {
            "width": r.u8(address, ACTOR["collision_width"]),
            "height": r.u8(address, ACTOR["collision_height"]),
        },
        "model_position_offset": {
            "x": r.u8(address, ACTOR["model_pos_offset_x"]),
            "y": r.u8(address, ACTOR["model_pos_offset_y"]),
            "z": r.u8(address, ACTOR["model_pos_offset_z"]),
        },
        "actor_system_backref": f"0x{(r.u32(address, ACTOR['actor_system']) or 0):08X}",
        "structure_coherent": (
            actor_system is None or r.u32(address, ACTOR["actor_system"]) == actor_system
        ),
    }


def _decode_actor_system(r: _Ram, address: int, field: int, mapper: int, player_actor: int | None) -> dict[str, Any]:
    capacity = r.u16(address, ACTOR_SYSTEM["capacity"]) or 0
    declared_count = r.u16(address, ACTOR_SYSTEM["count"]) or 0
    heap = r.u32(address, ACTOR_SYSTEM["actor_heap"])
    coherent = (
        1 <= capacity <= 256
        and declared_count <= capacity
        and r.valid_ptr(heap)
        and r.u32(address, ACTOR_SYSTEM["field"]) == field
        and r.u32(address, ACTOR_SYSTEM["g3d_mapper"]) == mapper
    )
    actors: list[dict[str, Any]] = []
    player_index = None
    if coherent and heap is not None:
        for index in range(capacity):
            actor_address = heap + index * ACTOR["stride"]
            if not r.valid_ptr(actor_address):
                break
            if r.u32(actor_address, ACTOR["actor_system"]) != address:
                continue
            item = _decode_actor(r, actor_address, address)
            item["slot"] = index
            item["is_player"] = actor_address == player_actor
            if item["is_player"]:
                player_index = index
            actors.append(item)
    zones = Counter(
        int(item["zone_id"])
        for item in actors
        if item["zone_id"] not in (None, 0, 0xFFFF) and not item["is_player"]
    )
    zone_value, zone_votes = zones.most_common(1)[0] if zones else (None, 0)
    return {
        "address": f"0x{address:08X}",
        "capacity": capacity,
        "declared_count": declared_count,
        "resolved_count": len(actors),
        "actor_heap": f"0x{(heap or 0):08X}",
        "player_slot": player_index,
        "structure_coherent": coherent and len(actors) == declared_count,
        "actors": actors,
        "zone_consensus": {
            "value": zone_value,
            "votes": zone_votes,
            "population": sum(zones.values()),
            "confidence": (
                "probable" if zone_value is not None and zone_votes >= max(2, sum(zones.values()) // 2) else "candidate"
            ),
            "reason": "majority ZoneID among coherent non-player FieldActor slots",
        },
    }


def _decode_mapper(r: _Ram, address: int) -> dict[str, Any]:
    width = r.u16(address, MAPPER["matrix_width"]) or 0
    height = r.u16(address, MAPPER["matrix_height"]) or 0
    count = r.u32(address, MAPPER["chunk_id_count"]) or 0
    ids_ptr = r.u32(address, MAPPER["chunk_dat_ids"])
    chunk_ids: list[int] = []
    if r.valid_ptr(ids_ptr) and count <= 4096:
        for index in range(count):
            value = r.u32(ids_ptr, index * 4)
            if value is None:
                break
            chunk_ids.append(value)
    player_index = r.s32(address, MAPPER["player_chunk_index"])
    chunk_span_fx = r.s32(address, MAPPER["chunk_span"])
    chunk_tile_size = None
    if chunk_span_fx and chunk_span_fx > 0:
        world_span = chunk_span_fx / FX32_ONE
        candidate = world_span / WORLD_UNITS_PER_TILE
        if candidate.is_integer() and 1 <= candidate <= 256:
            chunk_tile_size = int(candidate)
    player_chunk = None
    player_model_id = None
    if player_index is not None and 0 <= player_index < count and width:
        player_chunk = {"index": player_index, "x": player_index % width, "y": player_index // width}
        if player_index < len(chunk_ids):
            player_model_id = chunk_ids[player_index]

    handles_ptr = r.u32(address, MAPPER["chunk_handles"])
    capacity = r.u16(address, MAPPER["chunk_capacity"]) or 0
    loaded = []
    if r.valid_ptr(handles_ptr) and capacity <= 256:
        for index in range(capacity):
            handle = handles_ptr + index * CHUNK_HANDLE_STRIDE
            chunk = r.u32(handle, CHUNK_HANDLE["chunk"])
            cell_index = r.u32(handle, CHUNK_HANDLE["locator_chunk_index"])
            if not r.valid_ptr(chunk) or cell_index is None or cell_index >= count:
                continue
            model_id = chunk_ids[cell_index] if cell_index < len(chunk_ids) else None
            loaded.append({
                "handle_index": index,
                "handle_address": f"0x{handle:08X}",
                "chunk_address": f"0x{chunk:08X}",
                "cell_index": cell_index,
                "cell": {"x": cell_index % width, "y": cell_index // width} if width else None,
                "model_id": model_id,
                "locator_world_fx32": {
                    "x": r.s32(handle, CHUNK_HANDLE["locator_pos_x"]),
                    "y": r.s32(handle, CHUNK_HANDLE["locator_pos_y"]),
                    "z": r.s32(handle, CHUNK_HANDLE["locator_pos_z"]),
                },
                "locator_world": {
                    "x": _fx32(r.s32(handle, CHUNK_HANDLE["locator_pos_x"])),
                    "y": _fx32(r.s32(handle, CHUNK_HANDLE["locator_pos_y"])),
                    "z": _fx32(r.s32(handle, CHUNK_HANDLE["locator_pos_z"])),
                },
            })
    return {
        "address": f"0x{address:08X}",
        "matrix_width": width,
        "matrix_height": height,
        "chunk_id_count": count,
        "chunk_dat_ids_address": f"0x{(ids_ptr or 0):08X}",
        "chunk_dat_ids": chunk_ids,
        "chunk_span_fx32": chunk_span_fx,
        "chunk_span_world": _fx32(chunk_span_fx),
        "chunk_tile_size": chunk_tile_size,
        "load_diameter": {
            "x": r.u8(address, MAPPER["load_diameter_x"]),
            "z": r.u8(address, MAPPER["load_diameter_z"]),
        },
        "chunk_capacity": capacity,
        "player_chunk": player_chunk,
        "player_model_id": player_model_id,
        "player_position_fx32": {
            "x": r.s32(address, MAPPER["player_pos_x"]),
            "y": r.s32(address, MAPPER["player_pos_y"]),
            "z": r.s32(address, MAPPER["player_pos_z"]),
        },
        "player_position": {
            "x": _fx32(r.s32(address, MAPPER["player_pos_x"])),
            "y": _fx32(r.s32(address, MAPPER["player_pos_y"])),
            "z": _fx32(r.s32(address, MAPPER["player_pos_z"])),
        },
        "map_textures": f"0x{(r.u32(address, MAPPER['map_textures']) or 0):08X}",
        "prop_system": f"0x{(r.u32(address, MAPPER['prop_system']) or 0):08X}",
        "terrain_animator": f"0x{(r.u32(address, MAPPER['terrain_animator']) or 0):08X}",
        "loaded_chunks": loaded,
    }


def _decode_prop_system(r: _Ram, mapper: int, mapper_data: dict[str, Any]) -> dict[str, Any]:
    prop = r.u32(mapper, MAPPER["prop_system"])
    if not r.valid_ptr(prop) or r.u32(prop, PROP_SYSTEM["mapper"]) != mapper:
        return {
            "status": "unresolved",
            "reason": "FieldPropSystem pointer/back-reference is not coherent",
            "resources": [],
            "instances": [],
            "doors": [],
        }
    count = r.u32(prop, PROP_SYSTEM["resource_count"]) or 0
    resources_ptr = r.u32(prop, PROP_SYSTEM["resources"])
    if count > 512 or not r.valid_ptr(resources_ptr):
        return {
            "status": "candidate",
            "address": f"0x{prop:08X}",
            "reason": "FieldPropSystem exists but resource table is not plausible",
            "resources": [],
            "instances": [],
            "doors": [],
        }
    resources = []
    by_index: dict[int, dict[str, Any]] = {}
    for index in range(count):
        item = resources_ptr + index * PROP_RESOURCE_STRIDE
        info = r.u32(item, 0)
        if not r.valid_ptr(info):
            continue
        resource = {
            "resource_index": index,
            "info_address": f"0x{info:08X}",
            "uid": r.u16(info, PROP_INFO["uid"]),
            "type": r.u16(info, PROP_INFO["type"]),
            "door_uid": r.u16(info, PROP_INFO["door_uid"]),
            "door_offset": {
                "x": r.s16(info, PROP_INFO["door_x"]),
                "y": r.s16(info, PROP_INFO["door_y"]),
                "z": r.s16(info, PROP_INFO["door_z"]),
            },
            "unknown_1": r.u16(info, PROP_INFO["unknown_1"]),
            "unknown_2": r.u16(info, PROP_INFO["unknown_2"]),
            "model_resource": f"0x{(r.u32(item, 4) or 0):08X}",
        }
        resource["has_door_metadata"] = resource["door_uid"] not in (None, 0xFFFF)
        resources.append(resource)
        by_index[index] = resource

    instances = []
    doors = []
    for loaded in mapper_data.get("loaded_chunks", []):
        chunk_s = loaded.get("chunk_address")
        if not isinstance(chunk_s, str):
            continue
        chunk = int(chunk_s, 16)
        chunk_world = {
            "x": _fx32(r.s32(chunk, CHUNK["world_x"])),
            "y": _fx32(r.s32(chunk, CHUNK["world_y"])),
            "z": _fx32(r.s32(chunk, CHUNK["world_z"])),
        }
        for bank, ptr_off, cap_off in (
            (1, CHUNK["prop_instances_1"], CHUNK["prop_capacity_1"]),
            (2, CHUNK["prop_instances_2"], CHUNK["prop_capacity_2"]),
        ):
            pointer = r.u32(chunk, ptr_off)
            capacity = r.u16(chunk, cap_off) or 0
            if not r.valid_ptr(pointer) or capacity > 256:
                continue
            for slot in range(capacity):
                address = pointer + slot * PROP_INSTANCE_STRIDE
                resource_index = r.u32(address, 0)
                if resource_index not in by_index:
                    continue
                local = {
                    "x": _fx32(r.s32(address, 4)),
                    "y": _fx32(r.s32(address, 8)),
                    "z": _fx32(r.s32(address, 12)),
                }
                world = {
                    axis: (
                        None if chunk_world[axis] is None or local[axis] is None
                        else chunk_world[axis] + local[axis]
                    )
                    for axis in ("x", "y", "z")
                }
                resource = by_index[resource_index]
                instance = {
                    "chunk_cell": loaded.get("cell"),
                    "chunk_cell_index": loaded.get("cell_index"),
                    "bank": bank,
                    "slot": slot,
                    "address": f"0x{address:08X}",
                    "resource_index": resource_index,
                    "resource_uid": resource.get("uid"),
                    "resource_type": resource.get("type"),
                    "local_world": local,
                    "absolute_world": world,
                    "rotation_y_raw": r.u16(address, 16),
                    "has_door_metadata": resource.get("has_door_metadata", False),
                    "door_uid": resource.get("door_uid"),
                    "door_offset": resource.get("door_offset"),
                }
                instances.append(instance)
                if instance["has_door_metadata"]:
                    doors.append(instance)
    return {
        "status": "probable",
        "address": f"0x{prop:08X}",
        "day_part": r.u32(prop, PROP_SYSTEM["day_part"]),
        "previous_day_part": r.u32(prop, PROP_SYSTEM["previous_day_part"]),
        "season": r.u8(prop, 0x15),
        "resource_count": count,
        "resource_instance_count": r.u32(prop, PROP_SYSTEM["resource_instance_count"]),
        "resources": resources,
        "instances": instances,
        "doors": doors,
        "semantic_status": (
            "runtime prop resources and placements are structure-coherent; UID/type/door fields "
            "remain raw until cross-scene behavioral validation"
        ),
    }


def resolve_runtime_field_from_ram(ram: bytes, *, frame: int | None = None) -> dict[str, Any]:
    """Resolve Field/Player/Actors/Mapper/Props from one 4 MiB Main RAM image."""
    if len(ram) != MAIN_RAM_SIZE:
        return {
            "format": "black2-runtime-field/v2",
            "status": "unavailable",
            "confidence": "unresolved",
            "reason": f"expected {MAIN_RAM_SIZE} Main RAM bytes, got {len(ram)}",
        }
    r = _Ram(ram)
    mappers = _mapper_candidates(r)
    solutions = []
    for mapper_candidate in mappers:
        mapper = int(mapper_candidate["address"])
        fields = _find_field(r, mapper)
        for field_candidate in fields:
            combined = mapper_candidate["score"] + field_candidate["score"]
            solutions.append((combined, mapper_candidate, field_candidate))
    solutions.sort(key=lambda item: (-item[0], item[1]["address"], item[2]["address"]))
    if not solutions:
        return {
            "format": "black2-runtime-field/v2",
            "status": "unresolved",
            "confidence": "unresolved",
            "frame": frame,
            "reason": "no FieldG3DMapper + Field back-reference chain passed structural coherence",
            "mapper_candidates": [
                {**item, "address": f"0x{int(item['address']):08X}"} for item in mappers[:8]
            ],
        }

    combined, mapper_candidate, field_candidate = solutions[0]
    second_score = solutions[1][0] if len(solutions) > 1 else None
    unique_margin = second_score is None or combined - second_score >= 4
    mapper = int(mapper_candidate["address"])
    field = int(field_candidate["address"])
    player = field_candidate.get("player")
    core = field_candidate.get("player_core")
    actor_system = field_candidate.get("actor_system")
    player_actor = r.u32(core, PLAYER_CORE["actor"]) if r.valid_ptr(core) else None

    mapper_data = _decode_mapper(r, mapper)
    player_data = _decode_actor(r, player_actor, actor_system) if r.valid_ptr(player_actor) else None
    actor_data = (
        _decode_actor_system(r, actor_system, field, mapper, player_actor)
        if r.valid_ptr(actor_system) else None
    )

    chunk_consistency = False
    if player_data and mapper_data.get("player_chunk") and mapper_data.get("chunk_tile_size"):
        gpos = player_data["grid_position"]
        size = int(mapper_data["chunk_tile_size"])
        if gpos.get("x") is not None and gpos.get("z") is not None:
            chunk_consistency = (
                gpos["x"] // size == mapper_data["player_chunk"]["x"]
                and gpos["z"] // size == mapper_data["player_chunk"]["y"]
            )

    core_action = r.u32(core, PLAYER_CORE["action_status"]) if r.valid_ptr(core) else None
    core_move = r.u32(core, PLAYER_CORE["move_status"]) if r.valid_ptr(core) else None
    player_state = r.u32(core, PLAYER_CORE["state"]) if r.valid_ptr(core) else None
    player_grid = r.u32(player, PLAYER["grid"]) if r.valid_ptr(player) else None
    rotation = r.u16(player_state, PLAYER_STATE["rotation_angle"]) if r.valid_ptr(player_state) else None
    rotation_dir = ROTATION_TO_DIRECTION.get(rotation)
    face_dir = r.u16(player_actor, ACTOR["face_dir"]) if r.valid_ptr(player_actor) else None
    grid_status = r.u32(player_grid, PLAYER_GRID["status"]) if r.valid_ptr(player_grid) else None
    grid_cmd = r.u32(player_grid, PLAYER_GRID["last_command"]) if r.valid_ptr(player_grid) else None
    ex_state = r.u32(player_state, PLAYER_STATE["ex_state"]) if r.valid_ptr(player_state) else None
    player_block = {
        "field_player": f"0x{(player or 0):08X}",
        "field_player_core": f"0x{(core or 0):08X}",
        "field_player_grid": f"0x{(player_grid or 0):08X}",
        "player_state": f"0x{(player_state or 0):08X}",
        "zone_id": r.u16(player_state, PLAYER_STATE["zone_id"]) if r.valid_ptr(player_state) else None,
        "actor": player_data,
        "orientation": {
            "face_dir_raw": face_dir,
            "facing": DIRECTIONS.get(face_dir, face_dir),
            "facing_zh": DIRECTION_ZH.get(face_dir, str(face_dir)),
            "rotation_angle_raw": rotation,
            "rotation_angle_hex": f"0x{rotation:04X}" if rotation is not None else None,
            "rotation_direction_raw": rotation_dir,
            "rotation_direction": DIRECTIONS.get(rotation_dir, rotation_dir),
            "sources_agree": face_dir in (0, 1, 2, 3) and rotation_dir == face_dir,
        },
        "action_status_raw": core_action,
        "action_status": ACTION_STATUS.get(core_action, core_action),
        "move_status_raw": core_move,
        "move_status": MOVE_STATUS.get(core_move, core_move),
        "grid_status_raw": grid_status,
        "grid_status": GRID_STATUS.get(grid_status, grid_status),
        "grid_last_command_raw": grid_cmd,
        "grid_last_command": GRID_COMMAND.get(grid_cmd, grid_cmd),
        "ex_state_raw": ex_state,
        "ex_state": EX_STATE.get(ex_state, ex_state),
        "key_move_dir_h": r.u16(core, PLAYER_CORE["key_move_dir_h"]) if r.valid_ptr(core) else None,
        "key_move_dir_v": r.u16(core, PLAYER_CORE["key_move_dir_v"]) if r.valid_ptr(core) else None,
        "special_sequence": r.u32(core, PLAYER_CORE["special_sequence"]) if r.valid_ptr(core) else None,
        "sex_raw": r.u32(core, PLAYER_CORE["sex"]) if r.valid_ptr(core) else None,
        "chunk_matches_gpos": chunk_consistency,
    }

    confidence = "probable"
    if not unique_margin or not chunk_consistency or not actor_data or not actor_data.get("structure_coherent"):
        confidence = "candidate"

    props = _decode_prop_system(r, mapper, mapper_data)
    zone_consensus = actor_data.get("zone_consensus") if actor_data else {
        "value": None, "confidence": "unresolved"
    }
    return {
        "format": "black2-runtime-field/v2",
        "status": "resolved" if confidence in {"probable", "verified"} else "candidate",
        "confidence": confidence,
        "frame": frame,
        "evidence_policy": {
            "absolute_addresses": "discovered from current RAM; never persisted as universal constants",
            "structure_source": "public Swan Gen-V layouts",
            "promotion_rule": "requires forward/back-pointer coherence plus matrix/player/actor consistency",
            "cross_snapshot_verified": False,
        },
        "root": {
            "field": f"0x{field:08X}",
            "game_system": f"0x{(field_candidate.get('game_system') or 0):08X}",
            "score": combined,
            "unique_margin": unique_margin,
            "checks": field_candidate["checks"],
        },
        "mapper": mapper_data,
        "player": player_block,
        "actors": actor_data,
        "props": props,
        "map_identity": {
            "zone_id_candidate": zone_consensus,
            "matrix_id": None,
            "map_header_id": None,
            "reason": "runtime mapper is resolved; ROM matrix/header binding is performed by MapTruthService",
        },
        "candidate_diagnostics": {
            "mapper_candidates": [
                {**item, "address": f"0x{int(item['address']):08X}"} for item in mappers[:8]
            ],
            "solution_count": len(solutions),
            "second_best_score": second_score,
        },
    }


async def read_main_ram(reader: MemoryReader, *, chunk_size: int = 0x20000) -> bytes:
    """Read Main RAM in bounded pieces for an explicit reverse-engineering request."""
    blocks = []
    for offset in range(0, MAIN_RAM_SIZE, chunk_size):
        size = min(chunk_size, MAIN_RAM_SIZE - offset)
        block = await reader.read_bytes(offset, size, "Main RAM")
        if len(block) != size:
            raise RuntimeError(
                f"Main RAM read truncated at 0x{offset:06X}: expected {size}, got {len(block)}"
            )
        blocks.append(bytes(block))
    return b"".join(blocks)


async def resolve_runtime_field(reader: MemoryReader) -> dict[str, Any]:
    ram = await read_main_ram(reader)
    return resolve_runtime_field_from_ram(ram)


def _result_bytes(result: dict[str, Any]) -> bytes:
    values = result.get("bytes") if isinstance(result, dict) else None
    if values is not None:
        return bytes(int(v) & 0xFF for v in values)
    try:
        return bytes.fromhex(str(result.get("hex", ""))) if isinstance(result, dict) else b""
    except ValueError:
        return b""


@dataclass
class RuntimeFieldLocator:
    """Discover the Field root once, then refresh live state with bounded reads.

    Discovery performs one explicit 4 MiB Main-RAM pass.  Subsequent samples
    read only the resolved structures and re-check their back pointers.  If a
    map lifecycle transition invalidates the cached addresses, discovery is
    automatically repeated instead of treating stale addresses as current.
    """

    addresses: dict[str, int] | None = None
    discovery_confidence: str = "unresolved"
    last_discovery_attempt: float = 0.0
    last_failure_reason: str = ""
    min_discovery_interval: float = 5.0

    def invalidate(self) -> None:
        self.addresses = None
        self.discovery_confidence = "unresolved"

    async def discover(self, reader: MemoryReader) -> dict[str, Any]:
        self.last_discovery_attempt = time.monotonic()
        ram = await read_main_ram(reader)
        result = resolve_runtime_field_from_ram(ram)
        if result.get("status") not in {"resolved", "candidate"}:
            self.last_failure_reason = str(result.get("reason", "runtime Field discovery failed"))
            self.invalidate()
            return result
        try:
            root = result["root"]
            player = result["player"]
            actor_system = result["actors"]
            mapper = result["mapper"]
            self.addresses = {
                "field": int(root["field"], 16),
                "mapper": int(mapper["address"], 16),
                "player": int(player["field_player"], 16),
                "core": int(player["field_player_core"], 16),
                "grid": int(player["field_player_grid"], 16),
                "state": int(player["player_state"], 16),
                "player_actor": int(player["actor"]["address"], 16),
                "actor_system": int(actor_system["address"], 16),
            }
        except (KeyError, TypeError, ValueError):
            self.invalidate()
            return {
                **result,
                "status": "unresolved",
                "confidence": "unresolved",
                "reason": "resolved structure could not be converted into a reusable locator",
            }
        self.discovery_confidence = str(result.get("confidence", "candidate"))
        self.last_failure_reason = ""
        return result

    async def _sample_cached(self, reader: MemoryReader) -> dict[str, Any] | None:
        if not self.addresses:
            return None
        a = self.addresses
        ranges = [
            {"id": "field", "addr": a["field"], "length": 0xA0},
            {"id": "mapper", "addr": a["mapper"], "length": 0x78},
            {"id": "player", "addr": a["player"], "length": 0x10},
            {"id": "core", "addr": a["core"], "length": 0x40},
            {"id": "grid", "addr": a["grid"], "length": 0x20},
            {"id": "state", "addr": a["state"], "length": 0x44},
            {"id": "actor", "addr": a["player_actor"], "length": ACTOR["stride"]},
            {"id": "actor_system", "addr": a["actor_system"], "length": 0x50},
        ]
        payload = await reader.read_batch_snapshot(ranges)
        results = payload.get("results", {}) if isinstance(payload, dict) else {}
        frame = int(payload.get("frame", 0)) if isinstance(payload, dict) else 0
        blobs = {key: _result_bytes(results.get(key, {})) for key in (
            "field", "mapper", "player", "core", "grid", "state", "actor", "actor_system"
        )}
        if any(not blob for blob in blobs.values()):
            return None

        def u8(name: str, off: int) -> int:
            return blobs[name][off]

        def s8(name: str, off: int) -> int:
            value = blobs[name][off]
            return value - 256 if value >= 128 else value

        def u16(name: str, off: int) -> int:
            return int.from_bytes(blobs[name][off:off + 2], "little")

        def s16(name: str, off: int) -> int:
            return int.from_bytes(blobs[name][off:off + 2], "little", signed=True)

        def u32(name: str, off: int) -> int:
            return int.from_bytes(blobs[name][off:off + 4], "little")

        def s32(name: str, off: int) -> int:
            return int.from_bytes(blobs[name][off:off + 4], "little", signed=True)

        coherent = all((
            u32("field", FIELD["g3d_mapper"]) == a["mapper"],
            u32("field", FIELD["player"]) == a["player"],
            u32("field", FIELD["actor_system"]) == a["actor_system"],
            u32("player", PLAYER["field"]) == a["field"],
            u32("player", PLAYER["core"]) == a["core"],
            u32("player", PLAYER["grid"]) == a["grid"],
            u32("core", PLAYER_CORE["field"]) == a["field"],
            u32("core", PLAYER_CORE["state"]) == a["state"],
            u32("core", PLAYER_CORE["actor"]) == a["player_actor"],
            u32("grid", PLAYER_GRID["core"]) == a["core"],
            u32("grid", PLAYER_GRID["field"]) == a["field"],
            u32("actor", ACTOR["actor_system"]) == a["actor_system"],
            u32("actor_system", ACTOR_SYSTEM["field"]) == a["field"],
            u32("actor_system", ACTOR_SYSTEM["g3d_mapper"]) == a["mapper"],
        ))
        if not coherent:
            return None

        width = u16("mapper", MAPPER["matrix_width"])
        height = u16("mapper", MAPPER["matrix_height"])
        count = u32("mapper", MAPPER["chunk_id_count"])
        player_chunk_index = s32("mapper", MAPPER["player_chunk_index"])
        chunk_span = s32("mapper", MAPPER["chunk_span"])
        gpos = {
            "x": u16("actor", ACTOR["gpos_x"]),
            "y": s16("actor", ACTOR["gpos_y"]),
            "z": u16("actor", ACTOR["gpos_z"]),
        }
        size = None
        if chunk_span > 0:
            tile_span = (chunk_span / FX32_ONE) / WORLD_UNITS_PER_TILE
            if tile_span.is_integer() and 1 <= tile_span <= 256:
                size = int(tile_span)
        chunk = None
        chunk_matches = False
        if width and 0 <= player_chunk_index < count:
            chunk = {
                "index": player_chunk_index,
                "x": player_chunk_index % width,
                "y": player_chunk_index // width,
            }
            if size:
                chunk_matches = gpos["x"] // size == chunk["x"] and gpos["z"] // size == chunk["y"]

        face = u16("actor", ACTOR["face_dir"])
        motion = u16("actor", ACTOR["motion_dir"])
        last_face = u16("actor", ACTOR["last_face_dir"])
        last_motion = u16("actor", ACTOR["last_motion_dir"])
        rotation = u16("state", PLAYER_STATE["rotation_angle"])
        rotation_dir = ROTATION_TO_DIRECTION.get(rotation)
        orientation_agrees = face in (0, 1, 2, 3) and rotation_dir == face

        move = u32("core", PLAYER_CORE["move_status"])
        action = u32("core", PLAYER_CORE["action_status"])
        grid_status = u32("grid", PLAYER_GRID["status"])
        grid_command = u32("grid", PLAYER_GRID["last_command"])
        ex_state = u32("state", PLAYER_STATE["ex_state"])

        if grid_status == 7 or grid_command == 9:
            phase = "Fall"
        elif grid_status in (4, 5, 6) or grid_command in (6, 7, 8):
            phase = GRID_STATUS.get(grid_status, GRID_COMMAND.get(grid_command, "Catwalk"))
        elif grid_status == 3 or grid_command == 4:
            phase = "Brake"
        elif move == 2 or grid_status == 2 or grid_command == 3:
            phase = "Turning"
        elif move == 1 or grid_status == 1 or grid_command == 2:
            phase = "Moving"
        elif move == 0 and grid_status == 0:
            phase = "Idle"
        else:
            phase = "Unknown"

        transport = EX_STATE.get(ex_state, f"Unknown({ex_state})")
        if transport == "Cycling":
            semantic_movement = "Cycling (骑行)" if phase == "Moving" else f"Cycling · {phase}"
        elif transport == "Surf":
            semantic_movement = "Surfing (冲浪)" if phase == "Moving" else f"Surf · {phase}"
        elif transport == "Dive":
            semantic_movement = "Diving (潜水)" if phase == "Moving" else f"Dive · {phase}"
        elif phase == "Idle":
            semantic_movement = "Idle (静止)"
        elif phase == "Turning":
            semantic_movement = "Turning (原地转向)"
        elif phase == "Moving":
            semantic_movement = "On-foot moving (步行/跑步待速度判定)"
        else:
            semantic_movement = phase

        world_fx = {
            "x": s32("actor", ACTOR["wpos_x"]),
            "y": s32("actor", ACTOR["wpos_y"]),
            "z": s32("actor", ACTOR["wpos_z"]),
        }
        return {
            "format": "black2-runtime-player-live/v3",
            "status": "resolved" if chunk_matches and orientation_agrees else "candidate",
            "confidence": self.discovery_confidence if chunk_matches and orientation_agrees else "candidate",
            "frame": frame,
            "root": {key: f"0x{value:08X}" for key, value in a.items()},
            "zone_id": u16("state", PLAYER_STATE["zone_id"]),
            "position": {
                "grid": gpos,
                "world_fx32": world_fx,
                "world": {axis: _fx32(value) for axis, value in world_fx.items()},
                "player_state_vec_fx32": {
                    "x": s32("state", PLAYER_STATE["vec_x"]),
                    "y": s32("state", PLAYER_STATE["vec_y"]),
                    "z": s32("state", PLAYER_STATE["vec_z"]),
                },
                "is_rail_position": bool(u8("state", PLAYER_STATE["is_pos_rail"])),
            },
            "orientation": {
                "verified": orientation_agrees,
                "primary_source": "FieldActor.FaceDir",
                "cross_check_source": "PlayerState.RotationAngle",
                "face_dir_raw": face,
                "facing": DIRECTIONS.get(face, face),
                "facing_zh": DIRECTION_ZH.get(face, str(face)),
                "rotation_angle_raw": rotation,
                "rotation_angle_hex": f"0x{rotation:04X}",
                "rotation_direction_raw": rotation_dir,
                "rotation_direction": DIRECTIONS.get(rotation_dir, rotation_dir),
                "sources_agree": orientation_agrees,
                "motion_dir_raw": motion,
                "motion_direction": DIRECTIONS.get(motion, motion),
                "last_face_dir_raw": last_face,
                "last_facing": DIRECTIONS.get(last_face, last_face),
                "last_motion_dir_raw": last_motion,
                "last_motion_direction": DIRECTIONS.get(last_motion, last_motion),
                "next_acmd_raw": u16("actor", ACTOR["next_acmd"]),
                "note": "MotionDir is animation/motion direction and is not used as the authoritative current facing.",
            },
            "locomotion": {
                "semantic_state": semantic_movement,
                "phase": phase,
                "transport_mode_raw": ex_state,
                "transport_mode": transport,
                "gait": "NotApplicable" if transport != "OnFoot" else ("Stationary" if phase != "Moving" else "UnresolvedWalkVsRun"),
                "gait_confidence": "unresolved" if transport == "OnFoot" and phase == "Moving" else "verified-enum",
                "move_status_raw": move,
                "move_status": MOVE_STATUS.get(move, move),
                "grid_status_raw": grid_status,
                "grid_status": GRID_STATUS.get(grid_status, grid_status),
                "grid_last_command_raw": grid_command,
                "grid_last_command": GRID_COMMAND.get(grid_command, grid_command),
                "action_status_raw": action,
                "action_status": ACTION_STATUS.get(action, action),
                "key_move_dir_h": u16("core", PLAYER_CORE["key_move_dir_h"]),
                "key_move_dir_v": u16("core", PLAYER_CORE["key_move_dir_v"]),
                "special_sequence": u32("core", PLAYER_CORE["special_sequence"]),
                "state_change_func_idx": u16("core", PLAYER_CORE["state_change_func_idx"]),
                "state_change_done": bool(u16("core", PLAYER_CORE["is_state_change_done"])),
                "grid_flags_raw": u32("grid", PLAYER_GRID["flags"]),
                "vertical_move_only": bool(u32("grid", PLAYER_GRID["vertical_move_only"])),
                "actor_movement_flags_raw": u32("actor", ACTOR["movement_flags"]),
            },
            "environment": {
                "tile_under": {
                    "class": u16("actor", ACTOR["tile_class"]),
                    "flags": u16("actor", ACTOR["tile_flags"]),
                },
                "tile_under_original_y": {
                    "class": u16("actor", ACTOR["tile_orig_y_class"]),
                    "flags": u16("actor", ACTOR["tile_orig_y_flags"]),
                },
                "collision_box": {
                    "width": u8("actor", ACTOR["collision_width"]),
                    "height": u8("actor", ACTOR["collision_height"]),
                },
                "model_position_offset": {
                    "x": s8("actor", ACTOR["model_pos_offset_x"]),
                    "y": s8("actor", ACTOR["model_pos_offset_y"]),
                    "z": s8("actor", ACTOR["model_pos_offset_z"]),
                },
            },
            "mapper": {
                "matrix_width": width,
                "matrix_height": height,
                "chunk_id_count": count,
                "player_chunk": chunk,
                "chunk_tile_size": size,
                "chunk_matches_gpos": chunk_matches,
                "load_diameter": {
                    "x": blobs["mapper"][MAPPER["load_diameter_x"]],
                    "z": blobs["mapper"][MAPPER["load_diameter_z"]],
                },
            },
            "evidence": {
                "structure_coherent": True,
                "orientation_crosscheck": orientation_agrees,
                "walk_run_status": "requires moving labeled samples or a separately verified gait field; not guessed from MotionDir",
            },
        }

    async def sample_player(self, reader: MemoryReader, *, allow_discovery: bool = False) -> dict[str, Any]:
        cached = await self._sample_cached(reader)
        if cached is not None:
            return cached
        self.invalidate()
        if not allow_discovery:
            return {
                "format": "black2-runtime-player-live/v2",
                "status": "unresolved",
                "confidence": "unresolved",
                "reason": "Field discovery is disabled for background sampling; run an explicit runtime discovery probe",
            }
        since = time.monotonic() - self.last_discovery_attempt
        if self.last_discovery_attempt and since < self.min_discovery_interval:
            return {
                "format": "black2-runtime-player-live/v2",
                "status": "unresolved",
                "confidence": "unresolved",
                "reason": self.last_failure_reason or "Field rediscovery is throttled after a recent failed/stale sample",
                "retry_after_seconds": max(0.0, self.min_discovery_interval - since),
            }
        discovery = await self.discover(reader)
        if discovery.get("status") not in {"resolved", "candidate"}:
            return {
                "format": "black2-runtime-player-live/v2",
                "status": "unresolved",
                "confidence": "unresolved",
                "reason": discovery.get("reason", "runtime Field discovery failed"),
            }
        cached = await self._sample_cached(reader)
        if cached is None:
            self.invalidate()
            return {
                "format": "black2-runtime-player-live/v2",
                "status": "unresolved",
                "confidence": "unresolved",
                "reason": "Field chain changed immediately after discovery",
            }
        cached["discovery"] = {
            "performed": True,
            "confidence": discovery.get("confidence"),
            "field": discovery.get("root", {}).get("field"),
        }
        return cached
