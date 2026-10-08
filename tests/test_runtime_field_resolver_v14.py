from __future__ import annotations

import struct

from backend.black2.world.runtime_field_resolver import (
    ACTOR,
    ACTOR_SYSTEM,
    ARM9_BASE,
    _Ram,
    _decode_actor_system,
)


def _put_u16(ram: bytearray, address: int, offset: int, value: int) -> None:
    struct.pack_into("<H", ram, address - ARM9_BASE + offset, value)


def _put_u32(ram: bytearray, address: int, offset: int, value: int) -> None:
    struct.pack_into("<I", ram, address - ARM9_BASE + offset, value)


def test_actor_system_count_is_not_active_slot_count() -> None:
    actor_system = ARM9_BASE + 0x1000
    field = ARM9_BASE + 0x2000
    mapper = ARM9_BASE + 0x3000
    heap = ARM9_BASE + 0x4000
    player_actor = heap + 4 * ACTOR["stride"]
    ram = bytearray(0x400000)

    _put_u16(ram, actor_system, ACTOR_SYSTEM["capacity"], 64)
    _put_u16(ram, actor_system, ACTOR_SYSTEM["count"], 59)
    _put_u32(ram, actor_system, ACTOR_SYSTEM["actor_heap"], heap)
    _put_u32(ram, actor_system, ACTOR_SYSTEM["g3d_mapper"], mapper)
    _put_u32(ram, actor_system, ACTOR_SYSTEM["field"], field)

    matching_slots = (0, 4, 7, 11, 15)
    for slot in matching_slots:
        actor = heap + slot * ACTOR["stride"]
        _put_u32(ram, actor, ACTOR["actor_system"], actor_system)
        _put_u16(ram, actor, ACTOR["uid"], slot + 100)
        _put_u16(ram, actor, ACTOR["zone_id"], 441)

    result = _decode_actor_system(_Ram(bytes(ram)), actor_system, field, mapper, player_actor)

    assert result["structure_coherent"] is True
    assert result["declared_count_raw"] == 59
    assert result["active_slot_count"] == 5
    assert result["resolved_count"] == 5
    assert result["count_semantics"]["relationship_verified"] is False
    assert result["slot_scan"]["matching_slots"] == list(matching_slots)
    assert result["player_slot"] == 4
    assert result["actors"][0]["is_player"] is False


def test_actor_system_does_not_assume_player_is_slot_zero() -> None:
    actor_system = ARM9_BASE + 0x1000
    field = ARM9_BASE + 0x2000
    mapper = ARM9_BASE + 0x3000
    heap = ARM9_BASE + 0x4000
    player_actor = heap + 8 * ACTOR["stride"]
    ram = bytearray(0x400000)

    _put_u16(ram, actor_system, ACTOR_SYSTEM["capacity"], 64)
    _put_u16(ram, actor_system, ACTOR_SYSTEM["count"], 63)
    _put_u32(ram, actor_system, ACTOR_SYSTEM["actor_heap"], heap)
    _put_u32(ram, actor_system, ACTOR_SYSTEM["g3d_mapper"], mapper)
    _put_u32(ram, actor_system, ACTOR_SYSTEM["field"], field)

    for slot in (0, 3, 8):
        actor = heap + slot * ACTOR["stride"]
        _put_u32(ram, actor, ACTOR["actor_system"], actor_system)

    result = _decode_actor_system(_Ram(bytes(ram)), actor_system, field, mapper, player_actor)

    assert result["structure_coherent"] is True
    assert result["player_slot"] == 8
    assert result["actors"][0]["slot"] == 0
    assert result["actors"][0]["is_player"] is False

