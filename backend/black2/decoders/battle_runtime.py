"""Conservative Pokemon Black 2 IREJ rev.1 battle-presence decoder.

This module deliberately separates *observed bytes* from *battle semantics*.
The address chain below was recovered from six user-supplied battle Main RAM
captures and matches the public SWAN ``GameData`` / ``FieldStatus`` layouts.
It is strong candidate evidence, not a cross-state verification: no paired
IREJ overworld capture was supplied with this patch.

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
SUPPLIED_BATTLE_CAPTURE_COUNT = 6


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
class BattleEvidenceProfile:
    game_data: int = IREJ_REV1_GAME_DATA
    source: str = "user battle RAM captures + ds-pokemon-hacking/swan structure layout"
    target: str = "Pokemon Black 2 IREJ rev.1"


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
            "status": "unresolved",
            "active": None,
            "active_status": "unresolved",
            "field_busy": {"raw": None, "name": "unresolved"},
            "pointers": {"game_data": f"0x{IREJ_REV1_GAME_DATA:08X}", "field_status": None, "party": None},
            "party_header": {"status": "unresolved", "capacity": None, "count": None},
            "last_battle_result_raw": None,
            "pause_events_raw": None,
            "frame": None,
            "confidence": 0.0,
            "verified": False,
            "reason": reason,
            "limitations": [
                "No paired IREJ overworld negative capture is included in the supplied evidence set.",
                "Battle kind/format/phase and command legality are not decoded by this locator.",
            ],
        }

    async def sample(self) -> dict[str, Any]:
        reader = self.reader
        if reader is None:
            return self.unresolved()

        try:
            header = await reader.read_batch_snapshot([
                {"id": "party_ptr", "addr": IREJ_REV1_GAME_DATA + GAME_DATA_PARTY_PTR, "length": 4},
                {"id": "field_status_ptr", "addr": IREJ_REV1_GAME_DATA + GAME_DATA_FIELD_STATUS_PTR, "length": 4},
                {"id": "last_battle_result", "addr": IREJ_REV1_GAME_DATA + GAME_DATA_LAST_BATTLE_RESULT, "length": 4},
                {"id": "pause_events", "addr": IREJ_REV1_GAME_DATA + GAME_DATA_PAUSE_EVENTS, "length": 1},
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
        busy = fs_raw[FIELD_STATUS_BUSY_FLAG] if len(fs_raw) > FIELD_STATUS_BUSY_FLAG else None
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
            # High confidence inside the supplied battle evidence set, but not
            # called verified until an independent negative/control set exists.
            confidence = 0.95
            reason = (
                "FieldStatus.BusyFlag is 1 (SWAN FLD_STATUS_BUSY_BATTLE) through the recovered IREJ rev.1 "
                "GameData pointer chain. This is candidate evidence, not a verified cross-state detector."
            )
        elif busy == FIELD_BUSY_NONE:
            active = False
            active_status = "candidate"
            status = "candidate"
            confidence = 0.75
            reason = (
                "FieldStatus.BusyFlag is 0 through the recovered pointer chain. Negative-state behavior has not "
                "yet been validated against supplied IREJ overworld captures."
            )
        else:
            active = None
            active_status = "transition_candidate"
            status = "candidate"
            confidence = 0.8
            reason = "FieldStatus.BusyFlag is 2 (loading); battle presence cannot be asserted during this transition."

        return {
            "format": "black2-battle-runtime-evidence/v1",
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
            "last_battle_result_raw": last_result,
            "last_battle_result_semantics": "historical_or_transition_value_not_current_outcome",
            "pause_events_raw": pause_events,
            "frame": detail.get("frame") if isinstance(detail, dict) else frame1,
            "header_frame": frame1,
            "confidence": confidence,
            "verified": False,
            "reason": reason,
            "evidence": {
                "target": "Pokemon Black 2 IREJ rev.1",
                "supplied_battle_captures": SUPPLIED_BATTLE_CAPTURE_COUNT,
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
                "No paired IREJ overworld negative capture is included in the supplied evidence set.",
                "Battle kind/format/phase, opponent roster, move slots, target legality and menu cursor are unresolved.",
                "Party slot bodies remain encrypted/shuffled and are intentionally not guessed here.",
                "Execution remains disabled until legal-action and post-action verification are implemented.",
            ],
        }
