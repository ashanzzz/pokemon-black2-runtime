"""Read-only battle menu cursor candidate decoder for Pokémon Black 2.

The first useful battle control primitive is not a button sequence.  It is a
same-frame readback of the menu object that accepted the button.  Controlled
BizHawk captures identified a ``btlv_input.c`` GFL allocation whose ``+0x84``
field changes with the menu and whose ``+0x94`` field changes with the move
cell.  This module exposes that observation without writing RAM or claiming
that all battle formats are decoded.

The allocator is transient, so the decoder deliberately discovers the object
from its source tag on every sample.  It rejects similarly tagged child
objects unless the GFL header and the observed ``0x388`` allocation size
match.  The result is a candidate/readback API; execution remains gated in the
HTTP layer until a post-action move/PP check is enabled.
"""
from __future__ import annotations

from typing import Any

from ..memory.reader import MemoryReader


MAIN_RAM_START = 0x02000000

# The object was observed in the battle-view allocator around this window.
# Keeping the scan bounded avoids a full 4 MiB scan on every battle poll and
# excludes stale field allocations seen in earlier captures.
BATTLE_INPUT_SCAN_START = 0x2B0000
BATTLE_INPUT_SCAN_SIZE = 0x10000

GFL_MAGIC = b"\x44\x55\x00\x00"
GFL_HEADER_SIZE = 0x20
BATTLE_INPUT_BLOCK_SIZE = 0x388
BATTLE_INPUT_SOURCE_TAG = b"btlv_input.c\x00"
BATTLE_INPUT_PHASE_OFFSET = 0x84
BATTLE_INPUT_CURSOR_OFFSET = 0x94

# These values are repeated in controlled move-menu transitions.  Some paths
# carry a transient/source bit in the same field: 0x4400 and 0x4620 were each
# semantically checked once after an otherwise-unresolved read, so they are
# retained as slot variants rather than rejected.  Slot 4 is readable, but it
# remains non-executable because no move occupies it.
CURSOR_SLOT_BY_RAW: dict[int, int] = {
    0x4200: 1,
    0x4400: 1,
    0x5E00: 1,
    0x4020: 2,
    0x4620: 2,
    0x4040: 3,
    0x4260: 4,
    # Live Zone446 wild-battle calibration: after the command-menu A edge,
    # the move menu opened with Water Gun (semantic slot 3) highlighted and
    # the same btlv_input.c +0x94 field read 0x00001E00.  The screenshot is
    # retained as calibration evidence; normal execution remains RAM-gated.
    0x1E00: 3,
}
PHASE_BY_RAW: dict[int, str] = {
    1: "command_menu",
    2: "move_menu",
}
GRID_BY_SLOT: dict[int, str] = {
    1: "top_left",
    2: "top_right",
    3: "bottom_left",
    4: "bottom_right",
}


def _u32(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    return int.from_bytes(data[offset:offset + 4], "little", signed=False)


def _hex_address(address: int | None) -> str | None:
    return f"0x{address:08X}" if isinstance(address, int) else None


def _source_tag(data: bytes) -> str:
    if len(data) <= 0x14:
        return ""
    raw = data[0x14:0x34]
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _mapping_payload() -> dict[str, Any]:
    return {
        "status": "candidate",
        "encoding": "little_endian_u32",
        "raw_to_slot": {f"0x{raw:08X}": slot for raw, slot in CURSOR_SLOT_BY_RAW.items()},
        "slot_to_grid": {f"slot_{slot}": grid for slot, grid in GRID_BY_SLOT.items()},
        "slot_4": "candidate_readback_only_empty_cell",
        "evidence": {
            "slot_1": "controlled move-menu baseline/slot_2->slot_1 Left readback; 0x4400 and stable move-menu 0x5E00 semantic checks",
            "slot_2": "controlled move-menu slot_1->slot_2 Right readback; 0x4620 semantic check after slot_4->slot_2 Up",
            "slot_3": "controlled move-menu slot_1->slot_3 Down readback; live Zone446 0x1E00 semantic screenshot check",
            "slot_4": "controlled move-menu slot_2->slot_4 Down readback plus one semantic screenshot check",
        },
    }


def decode_battle_ui_input_object(
    data: bytes,
    *,
    header_address: int,
    frame: int | None = None,
) -> dict[str, Any]:
    """Decode one candidate ``btlv_input.c`` allocation from raw bytes.

    This pure function is intentionally strict and is used by unit tests as
    well as the live reader.  A valid GFL object may still have an unresolved
    phase or cursor value; in that case the raw value is preserved and no slot
    is guessed.
    """

    block_size = _u32(data, 0x04)
    source_tag = _source_tag(data)
    phase_raw = _u32(data, BATTLE_INPUT_PHASE_OFFSET)
    cursor_raw = _u32(data, BATTLE_INPUT_CURSOR_OFFSET)
    header_ok = data[:4] == GFL_MAGIC
    size_ok = block_size == BATTLE_INPUT_BLOCK_SIZE
    source_ok = source_tag == BATTLE_INPUT_SOURCE_TAG.rstrip(b"\x00").decode("ascii")
    object_valid = header_ok and size_ok and source_ok

    phase_name = PHASE_BY_RAW.get(phase_raw) if isinstance(phase_raw, int) else None
    slot = CURSOR_SLOT_BY_RAW.get(cursor_raw) if isinstance(cursor_raw, int) else None
    cursor_status = "candidate" if slot is not None else "unresolved"
    phase_status = "candidate" if phase_name is not None else "unresolved"

    field_address = header_address + BATTLE_INPUT_CURSOR_OFFSET
    return {
        "status": "candidate" if object_valid else "rejected_candidate",
        "verified": False,
        "frame": frame,
        "object": {
            "header_address": _hex_address(header_address),
            "payload_address": _hex_address(header_address + GFL_HEADER_SIZE),
            "block_size": block_size,
            "expected_block_size": BATTLE_INPUT_BLOCK_SIZE,
            "source_tag": source_tag or None,
            "source_tag_expected": BATTLE_INPUT_SOURCE_TAG.rstrip(b"\x00").decode("ascii"),
            "header_magic": data[:4].hex() if len(data) >= 4 else None,
            "header_ok": header_ok,
            "size_ok": size_ok,
            "source_ok": source_ok,
        },
        "phase": {
            "status": phase_status if object_valid else "unresolved",
            "raw_u32": phase_raw,
            "value": phase_name,
            "field": {"offset": f"+0x{BATTLE_INPUT_PHASE_OFFSET:X}", "address": _hex_address(header_address + BATTLE_INPUT_PHASE_OFFSET)},
        },
        "cursor": {
            "status": cursor_status if object_valid else "unresolved",
            "slot": slot,
            "grid": GRID_BY_SLOT.get(slot) if slot is not None else None,
            "raw_u32": cursor_raw,
            "field": {"offset": f"+0x{BATTLE_INPUT_CURSOR_OFFSET:X}", "address": _hex_address(field_address)},
        },
        "memory": {
            "domain": "Main RAM",
            "source": "GFL btlv_input.c allocation",
            "header_address": _hex_address(header_address),
            "cursor_field_address": _hex_address(field_address),
            "cursor_field_offset": f"+0x{BATTLE_INPUT_CURSOR_OFFSET:X}",
        },
        "mapping": _mapping_payload(),
    }


class BattleUiCursorDecoder:
    """Discover and read the active battle input object without mutation."""

    def __init__(self, reader: MemoryReader | None = None):
        self.reader = reader

    def configure(self, reader: MemoryReader | None) -> None:
        self.reader = reader

    @staticmethod
    def unresolved(reason: str = "Battle UI cursor reader is not configured.") -> dict[str, Any]:
        return {
            "format": "black2-battle-ui-cursor/v1",
            "status": "unresolved",
            "verified": False,
            "read_only": True,
            "writes_performed": False,
            "frame": None,
            "scan": {
                "domain": "Main RAM",
                "start": BATTLE_INPUT_SCAN_START,
                "size": BATTLE_INPUT_SCAN_SIZE,
                "source_tag": BATTLE_INPUT_SOURCE_TAG.rstrip(b"\x00").decode("ascii"),
            },
            "phase": {"status": "unresolved", "raw_u32": None, "value": None},
            "cursor": {"status": "unresolved", "slot": None, "grid": None, "raw_u32": None},
            "mapping": _mapping_payload(),
            "candidates": [],
            "reason": reason,
        }

    async def sample(self) -> dict[str, Any]:
        reader = self.reader
        if reader is None:
            return self.unresolved()

        try:
            scan = await reader.scan_pattern_snapshot(
                list(BATTLE_INPUT_SOURCE_TAG),
                start=BATTLE_INPUT_SCAN_START,
                size=BATTLE_INPUT_SCAN_SIZE,
                limit=32,
            )
        except Exception as exc:
            return self.unresolved(f"battle input tag scan failed: {type(exc).__name__}: {exc}")

        matches = scan.get("matches") if isinstance(scan, dict) else None
        matches = matches if isinstance(matches, list) else []
        headers: list[tuple[str, int]] = []
        seen: set[int] = set()
        for raw_match in matches:
            if not isinstance(raw_match, int):
                continue
            header_offset = raw_match - 0x14
            if header_offset < BATTLE_INPUT_SCAN_START or header_offset + BATTLE_INPUT_BLOCK_SIZE > BATTLE_INPUT_SCAN_START + BATTLE_INPUT_SCAN_SIZE:
                continue
            header_address = MAIN_RAM_START + header_offset
            if header_address in seen:
                continue
            seen.add(header_address)
            headers.append((f"input_{header_address:08X}", header_address))

        if not headers:
            return self.unresolved("no btlv_input.c tag was found in the bounded battle allocator window")

        try:
            detail = await reader.read_batch_snapshot([
                {"id": item_id, "addr": address, "length": BATTLE_INPUT_BLOCK_SIZE}
                for item_id, address in headers
            ])
        except Exception as exc:
            return self.unresolved(f"battle input object read failed: {type(exc).__name__}: {exc}")

        detail_rows = detail.get("results") if isinstance(detail, dict) else None
        detail_rows = detail_rows if isinstance(detail_rows, dict) else {}
        frame = detail.get("frame") if isinstance(detail, dict) else scan.get("frame")
        candidates: list[dict[str, Any]] = []
        for item_id, address in headers:
            row = detail_rows.get(item_id)
            values = row.get("bytes") if isinstance(row, dict) else None
            if not isinstance(values, list):
                continue
            try:
                raw = bytes(int(value) & 0xFF for value in values)
            except (TypeError, ValueError):
                continue
            candidates.append(decode_battle_ui_input_object(raw, header_address=address, frame=frame))

        valid = [row for row in candidates if row.get("status") == "candidate"]
        if not valid:
            result = self.unresolved("tagged btlv_input.c allocations were found, but no GFL 0x388 object passed validation")
            result["frame"] = frame
            result["candidates"] = candidates
            return result

        # Prefer a valid object with a known phase/cursor.  If allocator churn
        # leaves more than one full object, keep all evidence but never merge
        # fields from different allocations.
        selected = next(
            (row for row in valid if row.get("phase", {}).get("status") == "candidate" and row.get("cursor", {}).get("status") == "candidate"),
            valid[0],
        )
        result = {
            "format": "black2-battle-ui-cursor/v1",
            "status": "candidate",
            "verified": False,
            "read_only": True,
            "writes_performed": False,
            "frame": frame,
            "scan": {
                "domain": "Main RAM",
                "start": BATTLE_INPUT_SCAN_START,
                "size": BATTLE_INPUT_SCAN_SIZE,
                "source_tag": BATTLE_INPUT_SOURCE_TAG.rstrip(b"\x00").decode("ascii"),
                "tag_matches": [_hex_address(MAIN_RAM_START + int(value)) for value in matches if isinstance(value, int)],
            },
            "phase": selected.get("phase"),
            "cursor": selected.get("cursor"),
            "object": selected.get("object"),
            "memory": selected.get("memory"),
            "mapping": selected.get("mapping", _mapping_payload()),
            "candidates": [
                {
                    "status": row.get("status"),
                    "header_address": row.get("object", {}).get("header_address"),
                    "block_size": row.get("object", {}).get("block_size"),
                    "phase": row.get("phase"),
                    "cursor": row.get("cursor"),
                }
                for row in candidates
            ],
            "reason": "A bounded btlv_input.c tag scan selected a GFL 0x388 allocation; phase/cursor values are read back from the same frame.",
        }
        return result


__all__ = [
    "BATTLE_INPUT_BLOCK_SIZE",
    "BATTLE_INPUT_CURSOR_OFFSET",
    "BATTLE_INPUT_PHASE_OFFSET",
    "BATTLE_INPUT_SCAN_SIZE",
    "BATTLE_INPUT_SCAN_START",
    "BATTLE_INPUT_SOURCE_TAG",
    "BattleUiCursorDecoder",
    "CURSOR_SLOT_BY_RAW",
    "decode_battle_ui_input_object",
]
