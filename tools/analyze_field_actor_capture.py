#!/usr/bin/env python3
"""Summarize a bridge-owned ActorSystem / ActorHeap A-edge capture.

The output deliberately calls the decoded offsets SWAN hypotheses.  It detects
which *actor slots* changed after an input edge, but never promotes a slot to
the player or a ScriptWork target to the speaking NPC on that basis alone.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ARM9_BASE = 0x02000000
ACTOR_SYSTEM = 0x0223DB68
ACTOR_HEAP_FALLBACK = 0x0223DBE4
ACTOR_STRIDE = 0x100
SCRIPT_WORK_CANDIDATE = 0x0224758C
GAME_SYSTEM_CANDIDATE = 0x0223B4C0
FIELD_CANDIDATE = 0x02263520


def _raw(item: dict[str, Any]) -> bytes:
    if item.get("bytes") is not None:
        return bytes(int(value) & 0xFF for value in item["bytes"])
    return bytes.fromhex(str(item.get("hex", "")))


def _base(item: dict[str, Any]) -> int:
    offset = int(item.get("offset", 0))
    return offset if offset >= ARM9_BASE else ARM9_BASE + offset


def _contains(item: dict[str, Any], address: int, length: int) -> bool:
    base = _base(item)
    return base <= address and address + length <= base + len(_raw(item))


def _at(item: dict[str, Any], address: int, length: int) -> bytes | None:
    if not _contains(item, address, length):
        return None
    start = address - _base(item)
    return _raw(item)[start:start + length]


def _u16(data: bytes | None) -> int | None:
    return int.from_bytes(data, "little") if data is not None and len(data) == 2 else None


def _s16(data: bytes | None) -> int | None:
    return int.from_bytes(data, "little", signed=True) if data is not None and len(data) == 2 else None


def _u32(data: bytes | None) -> int | None:
    return int.from_bytes(data, "little") if data is not None and len(data) == 4 else None


def _hex(value: int | None) -> str | None:
    return f"0x{value:08X}" if value is not None else None


def _range_map(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("id")): item
        for item in sample.get("ranges", [])
        if isinstance(item, dict) and item.get("id")
    }


def _actor_system_observation(ranges: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = ranges.get("actor_system_candidate", {})
    packed = _u32(_at(item, ACTOR_SYSTEM + 0x04, 4))
    capacity = packed & 0xFFFF if packed is not None else None
    count = (packed >> 16) & 0xFFFF if packed is not None else None
    return {
        "address": _hex(ACTOR_SYSTEM),
        "capacity_u16_swan_hypothesis": capacity,
        "count_u16_swan_hypothesis": count,
        "actor_heap_pointer_swan_hypothesis": _hex(_u32(_at(item, ACTOR_SYSTEM + 0x1C, 4))),
        "link_plus_40_unknown": _hex(_u32(_at(item, ACTOR_SYSTEM + 0x40, 4))),
    }


def _actor_row(item: dict[str, Any], address: int, index: int) -> dict[str, Any] | None:
    if not _contains(item, address, ACTOR_STRIDE):
        return None
    uid_zone = _u32(_at(item, address + 0x08, 4))
    model_move = _u32(_at(item, address + 0x0C, 4))
    scrid_default_dir = _u32(_at(item, address + 0x14, 4))
    face_motion = _u32(_at(item, address + 0x18, 4))
    return {
        "index": index,
        "address": _hex(address),
        "raw_flags": _hex(_u32(_at(item, address, 4))),
        "raw_movement_flags": _hex(_u32(_at(item, address + 0x04, 4))),
        "uid_u16_swan_hypothesis": uid_zone & 0xFFFF if uid_zone is not None else None,
        "zone_id_u16_swan_hypothesis": uid_zone >> 16 if uid_zone is not None else None,
        "model_id_u16_swan_hypothesis": model_move & 0xFFFF if model_move is not None else None,
        "scrid_u16_swan_hypothesis": scrid_default_dir & 0xFFFF if scrid_default_dir is not None else None,
        "default_dir_u16_swan_hypothesis": scrid_default_dir >> 16 if scrid_default_dir is not None else None,
        "face_dir_u16_swan_hypothesis": face_motion & 0xFFFF if face_motion is not None else None,
        "motion_dir_u16_swan_hypothesis": face_motion >> 16 if face_motion is not None else None,
        "gpos_swan_hypothesis": {
            "x": _u16(_at(item, address + 0x3C, 2)),
            "y": _s16(_at(item, address + 0x3E, 2)),
            "z": _u16(_at(item, address + 0x40, 2)),
        },
        "wpos_raw": {
            "x": _hex(_u32(_at(item, address + 0x44, 4))),
            "y": _hex(_u32(_at(item, address + 0x48, 4))),
            "z": _hex(_u32(_at(item, address + 0x4C, 4))),
        },
        "actor_system_backref_swan_hypothesis": _hex(_u32(_at(item, address + 0x88, 4))),
    }


def _actor_rows(ranges: dict[str, dict[str, Any]], system: dict[str, Any]) -> list[dict[str, Any]]:
    item = ranges.get("actor_heap_candidate", {})
    heap_value = system.get("actor_heap_pointer_swan_hypothesis")
    heap = int(heap_value, 16) if heap_value else ACTOR_HEAP_FALLBACK
    count = system.get("count_u16_swan_hypothesis")
    available = max(0, (_base(item) + len(_raw(item)) - heap) // ACTOR_STRIDE)
    requested = min(int(count or 0), 64, available)
    return [row for index in range(requested) if (row := _actor_row(item, heap + index * ACTOR_STRIDE, index))]


def _object_chain(ranges: dict[str, dict[str, Any]]) -> dict[str, Any]:
    script_item = ranges.get("script_work_context", {})
    game_item = ranges.get("game_system_candidate_via_scriptwork", {})
    field_item = ranges.get("field_candidate_via_scriptwork", {})
    return {
        "script_work_candidate": {
            "address": _hex(SCRIPT_WORK_CANDIDATE),
            "active_scalar_plus_00": _hex(_u32(_at(script_item, SCRIPT_WORK_CANDIDATE, 4))),
            "scrid_like_plus_04": _hex(_u32(_at(script_item, SCRIPT_WORK_CANDIDATE + 0x04, 4))),
            "parent_actor_candidate_plus_08": _hex(_u32(_at(script_item, SCRIPT_WORK_CANDIDATE + 0x08, 4))),
            "game_system_candidate_plus_10": _hex(_u32(_at(script_item, SCRIPT_WORK_CANDIDATE + 0x10, 4))),
            "field_candidate_plus_1c": _hex(_u32(_at(script_item, SCRIPT_WORK_CANDIDATE + 0x1C, 4))),
        },
        "game_system_candidate": {
            "address": _hex(GAME_SYSTEM_CANDIDATE),
            "field_pointer_plus_20_swan_hypothesis": _hex(_u32(_at(game_item, GAME_SYSTEM_CANDIDATE + 0x20, 4))),
        },
        "field_candidate": {
            "address": _hex(FIELD_CANDIDATE),
            "game_system_backref_plus_04_swan_hypothesis": _hex(_u32(_at(field_item, FIELD_CANDIDATE + 0x04, 4))),
            "actor_system_plus_40_swan_hypothesis": _hex(_u32(_at(field_item, FIELD_CANDIDATE + 0x40, 4))),
            "player_plus_94_swan_hypothesis": _hex(_u32(_at(field_item, FIELD_CANDIDATE + 0x94, 4))),
        },
    }


def _changed_fields(previous: dict[str, Any], current: dict[str, Any]) -> list[str]:
    return [
        key for key in current
        if key not in {"index", "address"} and previous.get(key) != current.get(key)
    ]


def analyze(capture: dict[str, Any]) -> dict[str, Any]:
    samples = capture.get("capture", {}).get("samples", [])
    observation_rows: list[dict[str, Any]] = []
    previous_by_slot: dict[int, dict[str, Any]] = {}
    changed_slots: dict[int, list[dict[str, Any]]] = {}

    for sample_index, sample in enumerate(samples):
        ranges = _range_map(sample)
        system = _actor_system_observation(ranges)
        actors = _actor_rows(ranges, system)
        for actor in actors:
            previous = previous_by_slot.get(actor["index"])
            if previous is not None:
                fields = _changed_fields(previous, actor)
                if fields:
                    changed_slots.setdefault(actor["index"], []).append({
                        "frame": sample.get("frame"),
                        "phase": sample.get("phase"),
                        "changed_fields": fields,
                        "before": previous,
                        "after": actor,
                    })
            previous_by_slot[actor["index"]] = actor
        observation_rows.append({
            "sample_index": sample_index,
            "phase": sample.get("phase"),
            "frame": sample.get("frame"),
            "actor_system": system,
            "object_chain_candidates": _object_chain(ranges),
            "actors_swan_hypothesis": actors,
        })

    return {
        "schema": "field_actor_capture_analysis/v1",
        "source_capture": capture.get("capture", {}).get("started_at") or "bridge-owned A-edge capture",
        "method": "read-only analysis of raw bridge frames; SWAN field names are hypotheses",
        "guardrails": [
            "A slot that changes after a directional input is not automatically the player.",
            "A ScriptWork parent-actor candidate is not automatically the speaking NPC.",
            "No current actor position is inferred from static spawn resources.",
        ],
        "samples": observation_rows,
        "changed_actor_slots": {str(index): entries for index, entries in changed_slots.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    result = analyze(capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
