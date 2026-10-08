from __future__ import annotations

import asyncio

from backend.black2.progression.state import (
    EVENT_WORK_PTR_OFFSET,
    GAME_DATA,
    SAVE_CONTROL_PTR_OFFSET,
    ProgressionStateService,
    summarize_event_work,
)


class FakeReader:
    def __init__(self) -> None:
        self.ranges = {}

    async def read_bytes(self, address: int, length: int):
        for base, data in self.ranges.items():
            if base <= address < base + len(data):
                offset = address - base
                return list(data[offset:offset + length].ljust(length, b"\x00"))
        return [0] * length


def test_event_work_summary_does_not_promote_raw_bytes_to_story_flags():
    raw = bytearray(431 * 2 + 383 + 1)
    raw[13 * 2] = 0x34
    raw[431 * 2 + 4] = 0x80
    result = summarize_event_work(bytes(raw), address=0x02225724, frame=99)

    assert result["status"] == "candidate"
    assert result["works"]["nonzero_indices"] == [13]
    assert result["flag_bytes"]["nonzero_indices"] == [4]
    assert result["semantic_status"].startswith("raw EventWork")


def test_progression_service_resolves_gamedata_eventwork_pointer_without_guessing_flags():
    reader = FakeReader()
    game_data = bytearray(0x1B0)
    event_work_ptr = 0x02225724
    game_data[EVENT_WORK_PTR_OFFSET:EVENT_WORK_PTR_OFFSET + 4] = event_work_ptr.to_bytes(4, "little")
    game_data[0x158:0x15C] = (0x0223B9C8).to_bytes(4, "little")
    game_data[0x190:0x194] = (0x0221DC24).to_bytes(4, "little")
    game_data[0x194:0x198] = (0x0221E624).to_bytes(4, "little")
    reader.ranges[GAME_DATA] = bytes(game_data)
    event_work = bytearray(431 * 2 + 383 + 1)
    event_work[13 * 2] = 209
    reader.ranges[event_work_ptr] = bytes(event_work)

    service = ProgressionStateService(reader=reader, runtime_provider=type("Hub", (), {"latest": {"frame": 123}})())
    result = asyncio.run(service.sample())

    assert result["status"] == "partial"
    assert result["game_data"]["event_work_ptr"] == "0x02225724"
    assert result["event_work"]["works"]["nonzero_indices"] == [13]
    assert result["badges"]["status"] == "unresolved"
    assert result["story_flags"]["status"] == "unresolved"
    assert result["evidence"]["writes_performed"] is False


def test_progression_service_decodes_verified_badges_and_money_from_block52():
    reader = FakeReader()
    game_data = bytearray(0x1B0)
    save_control_ptr = 0x022051EC
    event_work_ptr = 0x02225724
    game_data[SAVE_CONTROL_PTR_OFFSET:SAVE_CONTROL_PTR_OFFSET + 4] = save_control_ptr.to_bytes(4, "little")
    game_data[EVENT_WORK_PTR_OFFSET:EVENT_WORK_PTR_OFFSET + 4] = event_work_ptr.to_bytes(4, "little")
    reader.ranges[GAME_DATA] = bytes(game_data)

    save_control = bytearray(0x20)
    save_data_ptr = 0x02205284
    save_control[0x10:0x14] = save_data_ptr.to_bytes(4, "little")
    reader.ranges[save_control_ptr] = bytes(save_control)

    save_data = bytearray(0x40)
    r5_ptr = 0x02205444
    base_buf_ptr = 0x02205824
    save_data[0x2C:0x30] = r5_ptr.to_bytes(4, "little")
    save_data[0x34:0x38] = base_buf_ptr.to_bytes(4, "little")
    reader.ranges[save_data_ptr] = bytes(save_data)

    r5_data = bytearray(0x20)
    table_ptr = 0x0220548C
    r5_data[0x04:0x08] = (73).to_bytes(4, "little")
    r5_data[0x14:0x18] = table_ptr.to_bytes(4, "little")
    reader.ranges[r5_ptr] = bytes(r5_data)

    table_data = bytearray(73 * 12)
    rel_offset = 0x21100
    table_data[52 * 12 + 4:52 * 12 + 8] = (0xF0).to_bytes(4, "little")
    table_data[52 * 12 + 8:52 * 12 + 12] = rel_offset.to_bytes(4, "little")
    reader.ranges[table_ptr] = bytes(table_data)

    block52_addr = base_buf_ptr + rel_offset
    block52_data = bytearray(0xF0)
    block52_data[0x00:0x04] = (294722).to_bytes(4, "little")
    block52_data[0x04] = 0x3F  # 6 badges (bits 0..5)
    reader.ranges[block52_addr] = bytes(block52_data)

    event_work = bytearray(431 * 2 + 383 + 1)
    reader.ranges[event_work_ptr] = bytes(event_work)

    service = ProgressionStateService(reader=reader, runtime_provider=type("Hub", (), {"latest": {"frame": 456}})())
    result = asyncio.run(service.sample())

    assert result["status"] == "verified"
    assert result["contents_known"] is True
    assert result["money"]["status"] == "verified"
    assert result["money"]["amount"] == 294722
    assert result["badges"]["status"] == "verified"
    assert result["badges"]["count"] == 6
    assert result["badges"]["mask"] == 0x3F
    assert result["badges"]["next_target"]["name_en"] == "Freeze Badge"
    assert result["badges"]["next_target"]["city_en"] == "Opelucid City"
    assert len(result["gates"]) == 12
    unlocked_gates = [g for g in result["gates"] if g["unlocked"]]
    locked_gates = [g for g in result["gates"] if not g["unlocked"]]
    assert len(unlocked_gates) == 8
    assert len(locked_gates) == 4
    assert locked_gates[0]["gate_id"] == "gate_marine_tube_humilau"
    assert locked_gates[1]["gate_id"] == "gate_seaside_cave_humilau"
    assert locked_gates[2]["gate_id"] == "gate_victory_road_badges"
    assert locked_gates[3]["gate_id"] == "gate_pokemon_league_champion" 
