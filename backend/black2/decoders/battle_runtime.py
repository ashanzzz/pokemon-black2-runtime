"""Conservative Pokemon Black 2 IREJ rev.1 battle-presence decoder.

This module deliberately separates *observed bytes* from *battle semantics*.
The address chain below was recovered from supplied IREJ Main RAM captures
and matches the public SWAN ``GameData`` / ``FieldStatus`` layouts.  The
BusyFlag presence distinction is cross-state verified for this profile; the
battle kind, menu and legal actions remain unresolved.

Consequences:
- ``BusyFlag == 1`` may be reported as a high-confidence *candidate* battle.
- battle kind, format, command phase, legal moves, targets and execution stay
  unresolved until independent RAM evidence is verified.
- writes are never performed by this decoder.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..memory.reader import MemoryReader


MAIN_RAM_START = 0x02000000
MAIN_RAM_END = 0x02400000

# IREJ rev.1 candidate GameData base recovered from the supplied captures.
IREJ_REV1_GAME_DATA = 0x0223B570

# SWAN GameData layout offsets.
GAME_DATA_BAG_PTR = 0x190
GAME_DATA_PARTY_PTR = 0x194
GAME_DATA_FIELD_STATUS_PTR = 0x1B8
GAME_DATA_LAST_BATTLE_RESULT = 0x1BC
GAME_DATA_PAUSE_EVENTS = 0x1C0

# SWAN FieldStatus layout.
FIELD_STATUS_BUSY_FLAG = 0x11
FIELD_STATUS_SIZE = 0x18
FIELD_BUSY_NONE = 0
FIELD_BUSY_BATTLE = 1
FIELD_BUSY_LOADING = 2

# SWAN PokeParty header.  Slot bodies are encrypted/shuffled Gen V structures,
# so this first decoder exposes only the verified-looking header.
POKE_PARTY_HEADER_SIZE = 8
POKE_PARTY_EXPECTED_CAPACITY = 6

# Captures used by this recovery.  This number is evidence metadata only.
SUPPLIED_BATTLE_CAPTURE_COUNT = 5
SUPPLIED_OVERWORLD_CONTROL_COUNT = 3

# Dimensions deliberately kept explicit in the evidence payload.  The
# presence locator is useful to clients, but these values have no verified
# IREJ rev.1 offsets yet; naming them here prevents callers from mistaking a
# missing field for an inactive/zero-valued field.
UNRESOLVED_BATTLE_DIMENSIONS = {
    "battle_kind": "Battle kind (wild/trainer/link/etc.) is not decoded.",
    "battle_format": "Single/double/triple/rotation format is not decoded.",
    "phase": "Command/message/animation/result phase is not decoded.",
    "active_pokemon": "BattleMon pointer and active side/slot are not decoded.",
    "opponent_roster": "Opponent BattleMon roster is not decoded.",
    "move_legality": "Battle move usability, PP and target legality are not decoded.",
    "item_legality": "Battle item inventory/filtering and target legality are not decoded.",
    "battle_weather": "Battle weather/turn countdown is not decoded.",
    "field_effects": "Gen V field effects and side conditions are not decoded.",
    "battlefield_visual": "Battle background/terrain visual state is not decoded.",
    "overworld_time": "Overworld time-of-day is not established by battle RAM.",
    "overworld_season": "Overworld season is not established by battle RAM.",
    "overworld_weather": "Overworld zone weather is not established by battle RAM.",
}


def _bytes(row: Any) -> bytes:
    if not isinstance(row, dict):
        return b""
    values = row.get("bytes")
    if not isinstance(values, list):
        return b""
    try:
        return bytes(int(v) & 0xFF for v in values)
    except (TypeError, ValueError):
        return b""


def _le(data: bytes, size: int) -> int | None:
    if len(data) < size:
        return None
    return int.from_bytes(data[:size], "little", signed=False)


def _u8(row: Any) -> int | None:
    data = _bytes(row)
    return data[0] if data else None


def _u32(row: Any) -> int | None:
    return _le(_bytes(row), 4)


def _main_ram_pointer(value: Any) -> bool:
    return type(value) is int and MAIN_RAM_START <= value < MAIN_RAM_END


@dataclass(frozen=True)
class IrejBattleProfile:
    game_data: int = IREJ_REV1_GAME_DATA
    party_ptr_offset: int = GAME_DATA_PARTY_PTR
    field_status_ptr_offset: int = GAME_DATA_FIELD_STATUS_PTR
    field_busy_offset: int = FIELD_STATUS_BUSY_FLAG
    last_battle_result_offset: int = GAME_DATA_LAST_BATTLE_RESULT
    pause_events_offset: int = GAME_DATA_PAUSE_EVENTS
    source: str = "user battle RAM captures + ds-pokemon-hacking/swan structure layout"
    target: str = "Pokemon Black 2 IREJ rev.1"


# Kept as a public compatibility name for callers using the v1 decoder.
BattleEvidenceProfile = IrejBattleProfile


def _ram_u32(ram: bytes, address: int) -> int | None:
    offset = address - MAIN_RAM_START
    if offset < 0 or offset + 4 > len(ram):
        return None
    return int.from_bytes(ram[offset:offset + 4], "little")


def _ram_u8(ram: bytes, address: int) -> int | None:
    offset = address - MAIN_RAM_START
    return ram[offset] if 0 <= offset < len(ram) else None


def _raw_range(ram: bytes, address: int | None, length: int) -> dict[str, Any] | None:
    """Return a small, explicitly-addressed byte range from a RAM image.

    Battle semantics are intentionally not inferred from these bytes.  Keeping
    the exact bytes in the evidence payload makes a live capture auditable and
    lets a later decoder be tested against the same frame without guessing an
    offset again.  This helper is bounded by the supplied image and therefore
    cannot leak an out-of-range slice when a pointer candidate is malformed.
    """
    if not _main_ram_pointer(address):
        return None
    offset = address - MAIN_RAM_START
    if offset < 0 or offset + length > len(ram):
        return None
    return {
        "address": f"0x{address:08X}",
        "length": length,
        "hex": ram[offset:offset + length].hex(),
        "source": "Main RAM image at evidence frame",
    }


def decode_battle_evidence_from_ram(
    ram: bytes,
    *,
    frame: int = 0,
    profile: IrejBattleProfile = IrejBattleProfile(),
) -> dict[str, Any]:
    """Decode only the structurally supported battle-presence evidence.

    ``ram`` is a complete ARM9 Main RAM image beginning at ``0x02000000``.
    The same pure function is used by offline snapshot export and can be
    exercised without a bridge or emulator.
    """
    detector = {
        "profile": "IREJ-rev1",
        "presence_cross_state_verified": True,
        "positive_controls": SUPPLIED_BATTLE_CAPTURE_COUNT,
        "negative_controls": SUPPLIED_OVERWORLD_CONTROL_COUNT,
        "battle_kind_verified": False,
        "menu_verified": False,
    }
    base = profile.game_data
    party_ptr = _ram_u32(ram, base + profile.party_ptr_offset)
    field_ptr = _ram_u32(ram, base + profile.field_status_ptr_offset)
    last_result = _ram_u32(ram, base + profile.last_battle_result_offset)
    pause_events = _ram_u8(ram, base + profile.pause_events_offset)
    pointers_valid = _main_ram_pointer(party_ptr) and _main_ram_pointer(field_ptr)
    result: dict[str, Any] = {
        "format": "black2-battle-runtime-evidence/v1",
        "read_only": True,
        "mutation_policy": "decoder performs cache reads only; no emulator/RAM writes",
        "status": "unresolved",
        "active": None,
        "active_status": "unresolved",
        "field_busy": {"raw": None, "name": "unresolved"},
        "pointers": {
            "game_data": f"0x{base:08X}",
            "field_status": f"0x{field_ptr:08X}" if _main_ram_pointer(field_ptr) else None,
            "party": f"0x{party_ptr:08X}" if _main_ram_pointer(party_ptr) else None,
        },
        "party_header": {"status": "unresolved", "capacity": None, "count": None},
        "raw": {
            "field_status": None,
            "party_header": None,
            "policy": "raw bytes only; no unverified battle-field offsets are decoded",
        },
        "last_battle_result_raw": last_result,
        "pause_events_raw": pause_events,
        "frame": frame,
        "confidence": 0.0,
        "verified": False,
        "detector": detector,
        "unresolved_dimensions": dict(UNRESOLVED_BATTLE_DIMENSIONS),
        "evidence": {
            "target": profile.target,
            "profile": "IREJ-rev1",
            "positive_controls": SUPPLIED_BATTLE_CAPTURE_COUNT,
            "negative_controls": SUPPLIED_OVERWORLD_CONTROL_COUNT,
            "busy_flag_offset": f"0x{profile.field_busy_offset:X}",
        },
    }
    if not pointers_valid:
        result["status"] = "rejected_candidate"
        result["reason"] = "GameData candidate did not contain valid Main RAM pointers."
        return result

    field_offset = field_ptr - MAIN_RAM_START
    party_offset = party_ptr - MAIN_RAM_START
    if field_offset + profile.field_busy_offset >= len(ram) or party_offset + POKE_PARTY_HEADER_SIZE > len(ram):
        result["status"] = "rejected_candidate"
        result["reason"] = "Pointer targets are outside the supplied Main RAM image."
        return result
    busy = _ram_u8(ram, field_ptr + profile.field_busy_offset)
    capacity = int.from_bytes(ram[party_offset:party_offset + 4], "little")
    count = int.from_bytes(ram[party_offset + 4:party_offset + 8], "little")
    party_plausible = capacity == POKE_PARTY_EXPECTED_CAPACITY and 0 <= count <= POKE_PARTY_EXPECTED_CAPACITY
    result["party_header"] = {
        "status": "candidate" if party_plausible else "unresolved",
        "capacity": capacity if party_plausible else None,
        "count": count if party_plausible else None,
    }
    result["raw"] = {
        "field_status": _raw_range(ram, field_ptr, FIELD_STATUS_SIZE),
        "party_header": _raw_range(ram, party_ptr, POKE_PARTY_HEADER_SIZE),
        "policy": "raw bytes only; no unverified battle-field offsets are decoded",
    }
    result["field_busy"] = {
        "raw": busy,
        "name": {FIELD_BUSY_NONE: "none", FIELD_BUSY_BATTLE: "battle", FIELD_BUSY_LOADING: "loading"}.get(busy, "unknown"),
    }
    valid = party_plausible and busy in {FIELD_BUSY_NONE, FIELD_BUSY_BATTLE, FIELD_BUSY_LOADING}
    if not valid:
        result.update(status="rejected_candidate", reason="The recovered GameData chain failed structural plausibility checks.")
        return result
    if busy == FIELD_BUSY_BATTLE:
        result.update(active=True, active_status="candidate", status="candidate", confidence=0.95)
        result["reason"] = "IREJ rev.1 FieldStatus.BusyFlag=1; cross-state presence detector is verified for the supplied controls."
    elif busy == FIELD_BUSY_NONE:
        result.update(active=False, active_status="candidate", status="candidate", confidence=0.75)
        result["reason"] = "IREJ rev.1 FieldStatus.BusyFlag=0; cross-state presence detector is verified for the supplied controls."
    else:
        result.update(active=None, active_status="transition_candidate", status="candidate", confidence=0.8)
        result["reason"] = "FieldStatus.BusyFlag=2 indicates loading; battle presence is not asserted during transition."
    result["limitations"] = [
        "Battle kind/format/phase, opponent roster, move slots, target legality and menu cursor are unresolved.",
        "Party slot bodies remain encrypted/shuffled and are intentionally not guessed.",
        "Execution remains disabled until legal-action and post-action verification are implemented.",
    ]
    return result


class BattleRuntimeDecoder:
    """Read the smallest known IREJ battle-presence evidence chain."""

    def __init__(self, reader: MemoryReader | None = None):
        self.reader = reader
        self.profile = BattleEvidenceProfile()

    def configure(self, reader: MemoryReader | None) -> None:
        self.reader = reader

    @staticmethod
    def unresolved(reason: str = "Battle RAM reader is not configured.") -> dict[str, Any]:
        return {
            "format": "black2-battle-runtime-evidence/v1",
            "read_only": True,
            "mutation_policy": "decoder performs cache reads only; no emulator/RAM writes",
            "status": "unresolved",
            "active": None,
            "active_status": "unresolved",
            "field_busy": {"raw": None, "name": "unresolved"},
            "pointers": {"game_data": f"0x{IREJ_REV1_GAME_DATA:08X}", "field_status": None, "party": None},
            "party_header": {"status": "unresolved", "capacity": None, "count": None},
            "raw": {
                "field_status": None,
                "party_header": None,
                "policy": "raw bytes only; no unverified battle-field offsets are decoded",
            },
            "last_battle_result_raw": None,
            "pause_events_raw": None,
            "frame": None,
            "confidence": 0.0,
            "verified": False,
            "detector": {
                "profile": "IREJ-rev1",
                "presence_cross_state_verified": True,
                "positive_controls": SUPPLIED_BATTLE_CAPTURE_COUNT,
                "negative_controls": SUPPLIED_OVERWORLD_CONTROL_COUNT,
                "battle_kind_verified": False,
                "menu_verified": False,
            },
            "unresolved_dimensions": dict(UNRESOLVED_BATTLE_DIMENSIONS),
            "reason": reason,
            "limitations": [
                "Presence is cross-state verified for the supplied IREJ rev.1 controls.",
                "Battle kind/format/phase and command legality are not decoded by this locator.",
            ],
        }

    async def sample(self) -> dict[str, Any]:
        reader = self.reader
        if reader is None:
            return self.unresolved()

        try:
            header = await reader.read_batch_snapshot([
                {"id": "party_ptr", "addr": self.profile.game_data + self.profile.party_ptr_offset, "length": 4},
                {"id": "field_status_ptr", "addr": self.profile.game_data + self.profile.field_status_ptr_offset, "length": 4},
                {"id": "last_battle_result", "addr": self.profile.game_data + self.profile.last_battle_result_offset, "length": 4},
                {"id": "pause_events", "addr": self.profile.game_data + self.profile.pause_events_offset, "length": 1},
            ])
        except Exception as exc:
            return self.unresolved(f"GameData candidate read failed: {type(exc).__name__}: {exc}")

        rows = header.get("results") if isinstance(header, dict) else None
        rows = rows if isinstance(rows, dict) else {}
        party_ptr = _u32(rows.get("party_ptr"))
        field_status_ptr = _u32(rows.get("field_status_ptr"))
        last_result = _u32(rows.get("last_battle_result"))
        pause_events = _u8(rows.get("pause_events"))
        frame1 = header.get("frame") if isinstance(header, dict) else None

        pointer_evidence = _main_ram_pointer(party_ptr) and _main_ram_pointer(field_status_ptr)
        if not pointer_evidence:
            payload = self.unresolved("GameData candidate did not contain valid Main RAM pointers.")
            payload.update({
                "status": "rejected_candidate",
                "pointers": {
                    "game_data": f"0x{IREJ_REV1_GAME_DATA:08X}",
                    "field_status": f"0x{field_status_ptr:08X}" if type(field_status_ptr) is int else None,
                    "party": f"0x{party_ptr:08X}" if type(party_ptr) is int else None,
                },
                "last_battle_result_raw": last_result,
                "pause_events_raw": pause_events,
                "frame": frame1,
            })
            return payload

        try:
            detail = await reader.read_batch_snapshot([
                {"id": "field_status", "addr": field_status_ptr, "length": FIELD_STATUS_SIZE},
                {"id": "party_header", "addr": party_ptr, "length": POKE_PARTY_HEADER_SIZE},
            ])
        except Exception as exc:
            payload = self.unresolved(f"Pointer target read failed: {type(exc).__name__}: {exc}")
            payload.update({
                "pointers": {
                    "game_data": f"0x{IREJ_REV1_GAME_DATA:08X}",
                    "field_status": f"0x{field_status_ptr:08X}",
                    "party": f"0x{party_ptr:08X}",
                },
                "last_battle_result_raw": last_result,
                "pause_events_raw": pause_events,
                "frame": frame1,
            })
            return payload

        detail_rows = detail.get("results") if isinstance(detail, dict) else None
        detail_rows = detail_rows if isinstance(detail_rows, dict) else {}
        fs_raw = _bytes(detail_rows.get("field_status"))
        party_raw = _bytes(detail_rows.get("party_header"))
        busy = fs_raw[self.profile.field_busy_offset] if len(fs_raw) > self.profile.field_busy_offset else None
        capacity = int.from_bytes(party_raw[0:4], "little") if len(party_raw) >= 4 else None
        count = int.from_bytes(party_raw[4:8], "little") if len(party_raw) >= 8 else None
        party_plausible = (
            capacity == POKE_PARTY_EXPECTED_CAPACITY
            and type(count) is int
            and 0 <= count <= POKE_PARTY_EXPECTED_CAPACITY
        )
        chain_plausible = pointer_evidence and party_plausible and busy in {FIELD_BUSY_NONE, FIELD_BUSY_BATTLE, FIELD_BUSY_LOADING}

        names = {
            FIELD_BUSY_NONE: "none",
            FIELD_BUSY_BATTLE: "battle",
            FIELD_BUSY_LOADING: "loading",
        }
        if not chain_plausible:
            active = None
            active_status = "unresolved"
            status = "rejected_candidate"
            confidence = 0.0
            reason = "The recovered GameData chain failed one or more structural plausibility checks."
        elif busy == FIELD_BUSY_BATTLE:
            active = True
            active_status = "candidate"
            status = "candidate"
            # High confidence inside the supplied cross-state control set.
            confidence = 0.95
            reason = (
                "FieldStatus.BusyFlag is 1 (SWAN FLD_STATUS_BUSY_BATTLE) through the recovered IREJ rev.1 "
                "GameData pointer chain; this presence distinction is verified for the supplied cross-state controls."
            )
        elif busy == FIELD_BUSY_NONE:
            active = False
            active_status = "candidate"
            status = "candidate"
            confidence = 0.75
            reason = (
                "FieldStatus.BusyFlag is 0 through the recovered pointer chain. Negative-state behavior has not "
                "been validated by the supplied IREJ overworld negative controls."
            )
        else:
            active = None
            active_status = "transition_candidate"
            status = "candidate"
            confidence = 0.8
            reason = "FieldStatus.BusyFlag is 2 (loading); battle presence cannot be asserted during this transition."

        return {
            "format": "black2-battle-runtime-evidence/v1",
            "read_only": True,
            "mutation_policy": "decoder performs cache reads only; no emulator/RAM writes",
            "status": status,
            "active": active,
            "active_status": active_status,
            "field_busy": {"raw": busy, "name": names.get(busy, "unknown")},
            "pointers": {
                "game_data": f"0x{IREJ_REV1_GAME_DATA:08X}",
                "field_status": f"0x{field_status_ptr:08X}",
                "party": f"0x{party_ptr:08X}",
            },
            "party_header": {
                "status": "candidate" if party_plausible else "unresolved",
                "capacity": capacity if party_plausible else None,
                "count": count if party_plausible else None,
            },
            "raw": {
                "field_status": {
                    "address": f"0x{field_status_ptr:08X}",
                    "length": len(fs_raw),
                    "hex": fs_raw.hex(),
                    "source": "Main RAM live batch at evidence frame",
                },
                "party_header": {
                    "address": f"0x{party_ptr:08X}",
                    "length": len(party_raw),
                    "hex": party_raw.hex(),
                    "source": "Main RAM live batch at evidence frame",
                },
                "policy": "raw bytes only; no unverified battle-field offsets are decoded",
            },
            "last_battle_result_raw": last_result,
            "last_battle_result_semantics": "historical_or_transition_value_not_current_outcome",
            "pause_events_raw": pause_events,
            "frame": detail.get("frame") if isinstance(detail, dict) else frame1,
            "header_frame": frame1,
            "confidence": confidence,
            "verified": False,
            "detector": {
                "profile": "IREJ-rev1",
                "presence_cross_state_verified": True,
                "positive_controls": SUPPLIED_BATTLE_CAPTURE_COUNT,
                "negative_controls": SUPPLIED_OVERWORLD_CONTROL_COUNT,
                "battle_kind_verified": False,
                "menu_verified": False,
            },
            "unresolved_dimensions": dict(UNRESOLVED_BATTLE_DIMENSIONS),
            "reason": reason,
            "evidence": {
                "target": "Pokemon Black 2 IREJ rev.1",
                "supplied_battle_captures": SUPPLIED_BATTLE_CAPTURE_COUNT,
                "supplied_overworld_negative_controls": SUPPLIED_OVERWORLD_CONTROL_COUNT,
                "presence_cross_state_verified": True,
                "structure_reference": "ds-pokemon-hacking/swan: system/game_data.h + field/field_status.h + pml/poke_party.h",
                "observed_in_supplied_battle_captures": {
                    "game_data": "0x0223B570",
                    "party_ptr": "0x0221E624",
                    "field_status_ptr": "0x0224211C",
                    "busy_flag": 1,
                    "party_capacity": 6,
                    "party_count": 1,
                },
            },
            "limitations": [
                "Presence detector is cross-state verified for the supplied IREJ rev.1 profile; battle kind/phase/menu remain unresolved.",
                "Battle kind/format/phase, opponent roster, move slots, target legality and menu cursor are unresolved.",
                "Party slot bodies remain encrypted/shuffled and are intentionally not guessed here.",
                "Execution remains disabled until legal-action and post-action verification are implemented.",
            ],
        }
