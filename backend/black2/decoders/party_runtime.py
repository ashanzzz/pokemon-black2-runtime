"""Checksum-gated Gen V player party decoder for Pokemon Black 2 IREJ rev.1."""
from __future__ import annotations

_shared_dex = None

def _get_dex():
    global _shared_dex
    if _shared_dex is None:
        try:
            from ..dex.store import DexStore
            _shared_dex = DexStore()
        except Exception:
            _shared_dex = None
    return _shared_dex


from typing import Any

from ..memory.reader import MemoryReader


MAIN_RAM_START = 0x02000000
MAIN_RAM_END = 0x02400000
IREJ_REV1_GAME_DATA = 0x0223B570
GAME_DATA_PARTY_PTR = 0x194
POKE_PARTY_HEADER_SIZE = 8
POKE_PARTY_CAPACITY = 6
PARTY_POKEMON_SIZE = 0xDC
BOX_POKEMON_SIZE = 0x88
BOX_ENCRYPTED_OFFSET = 8
BOX_ENCRYPTED_SIZE = 0x80

# The encrypted BoxPokemon payload is four 32-byte blocks. Gen IV/V use this
# exact 32-row table indexed by ``(PID >> 13) & 31``. The final eight rows are
# deliberate duplicates, not ``selector % 24`` over lexicographic permutations.
# This is the game-compatible BlockPosition table used by PKHeX's Shuffle45.
BLOCK_POSITION = (
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2),
    (0, 2, 3, 1), (0, 3, 2, 1), (1, 0, 2, 3), (1, 0, 3, 2),
    (2, 0, 1, 3), (3, 0, 1, 2), (2, 0, 3, 1), (3, 0, 2, 1),
    (1, 2, 0, 3), (1, 3, 0, 2), (2, 1, 0, 3), (3, 1, 0, 2),
    (2, 3, 0, 1), (3, 2, 0, 1), (1, 2, 3, 0), (1, 3, 2, 0),
    (2, 1, 3, 0), (3, 1, 2, 0), (2, 3, 1, 0), (3, 2, 1, 0),
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 3, 1, 2),
    (0, 2, 3, 1), (0, 3, 2, 1), (1, 0, 2, 3), (1, 0, 3, 2),
)


def _bytes(row: Any) -> bytes:
    if not isinstance(row, dict) or not isinstance(row.get("bytes"), list):
        return b""
    try:
        return bytes(int(value) & 0xFF for value in row["bytes"])
    except (TypeError, ValueError):
        return b""


def _pointer(value: Any) -> bool:
    return type(value) is int and MAIN_RAM_START <= value < MAIN_RAM_END


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _decrypt_words(data: bytes, checksum: int) -> bytes:
    """Decrypt Gen V BoxPokemon words using its checksum-seeded LCG."""
    seed = checksum
    result = bytearray(len(data))
    for offset in range(0, len(data), 2):
        seed = (seed * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
        word = _u16(data, offset) ^ (seed >> 16)
        result[offset:offset + 2] = word.to_bytes(2, "little")
    return bytes(result)


def _unshuffle_blocks(encrypted_order: bytes, personality: int) -> bytes:
    """Return the normal A/B/C/D order from a decrypted stored payload."""
    order = BLOCK_POSITION[(personality >> 13) & 0x1F]
    blocks = [encrypted_order[index * 32:(index + 1) * 32] for index in range(4)]
    # Shuffle45 makes output block position ``i`` come from source block
    # ``BlockPosition[selector][i]``. Applying it after word decryption gives
    # the canonical A/B/C/D data order.
    return b"".join(blocks[source_index] for source_index in order)


def _slot(slot_index: int, raw: bytes) -> dict[str, Any] | None:
    if len(raw) != PARTY_POKEMON_SIZE:
        return None
    personality = _u32(raw, 0)
    checksum = _u16(raw, 6)
    decrypted_stored = _decrypt_words(raw[BOX_ENCRYPTED_OFFSET:BOX_ENCRYPTED_OFFSET + BOX_ENCRYPTED_SIZE], checksum)
    checksum_actual = sum(_u16(decrypted_stored, offset) for offset in range(0, BOX_ENCRYPTED_SIZE, 2)) & 0xFFFF
    if checksum_actual != checksum:
        return None
    data = _unshuffle_blocks(decrypted_stored, personality)
    # Party-only battle statistics use a second LCG stream seeded from the
    # personality value.  They are still persistent PartyPkm data, not a
    # BattleMon structure.
    party_data = _decrypt_words(raw[BOX_POKEMON_SIZE:], personality)
    # Block A (0x00) holds species/item/experience; block B (0x20) holds the
    # four move IDs and their current PP. Block B offset 0x2C holds PP Up counts.
    # Party-only values begin at 0x88.
    dex = _get_dex()
    moves = []
    for move_slot in range(4):
        move_id = _u16(data, 0x20 + move_slot * 2)
        cur_pp = int(data[0x28 + move_slot])
        pp_ups = int(data[0x2C + move_slot] & 0x03) if move_id > 0 else 0
        base_pp = 0
        if dex and move_id > 0:
            m_entity = dex.get("moves", move_id) or {}
            base_pp = int(m_entity.get("pp") or 0)
        max_pp = base_pp + (base_pp * pp_ups // 5) if base_pp > 0 else cur_pp
        moves.append({
            "slot": move_slot + 1,
            "move_id": move_id,
            "current_pp": cur_pp,
            "max_pp": max_pp,
            "base_pp": base_pp,
            "pp_ups": pp_ups,
            "usable": bool(cur_pp > 0 and move_id > 0),
        })
    return {
        "slot": slot_index + 1,
        "pid": f"{personality:08X}",
        "species": _u16(data, 0),
        "held_item_id": _u16(data, 2),
        "experience": _u32(data, 8),
        "level": party_data[4],
        "current_hp": _u16(party_data, 6),
        "max_hp": _u16(party_data, 8),
        "status_raw": _u32(party_data, 0),
        "moves": moves,
        "source": "GameData.PokeParty",
        "integrity": "checksum_verified",
        "confidence": "candidate",
    }


def decode_player_party_from_ram(ram: bytes, *, frame: int | None = None) -> dict[str, Any]:
    """Decode a complete ARM9 Main RAM image beginning at ``0x02000000``."""
    result: dict[str, Any] = {
        "format": "black2-player-party/v1",
        "status": "unresolved",
        "source": "GameData.PokeParty",
        "capacity": None,
        "count": None,
        "slots": [],
        "frame": frame,
        "reason": "GameData.PokeParty has not been structurally validated.",
    }
    game_data_offset = IREJ_REV1_GAME_DATA - MAIN_RAM_START
    if game_data_offset < 0 or game_data_offset + GAME_DATA_PARTY_PTR + 4 > len(ram):
        result["reason"] = "The supplied Main RAM image does not include GameData."
        return result
    party_ptr = _u32(ram, game_data_offset + GAME_DATA_PARTY_PTR)
    if not _pointer(party_ptr):
        result["reason"] = "GameData.PokeParty pointer is outside ARM9 Main RAM."
        return result
    party_offset = party_ptr - MAIN_RAM_START
    party_size = POKE_PARTY_HEADER_SIZE + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE
    if party_offset < 0 or party_offset + party_size > len(ram):
        result["reason"] = "GameData.PokeParty range is outside the supplied Main RAM image."
        return result
    capacity = _u32(ram, party_offset)
    count = _u32(ram, party_offset + 4)
    if capacity != POKE_PARTY_CAPACITY or count > POKE_PARTY_CAPACITY:
        result["reason"] = "GameData.PokeParty header failed capacity/count validation."
        return result
    slots: list[dict[str, Any]] = []
    for index in range(count):
        start = party_offset + POKE_PARTY_HEADER_SIZE + index * PARTY_POKEMON_SIZE
        slot = _slot(index, ram[start:start + PARTY_POKEMON_SIZE])
        if slot is None:
            result.update(
                capacity=capacity,
                count=count,
                reason=f"Party slot {index + 1} failed Gen V checksum validation.",
            )
            return result
        slots.append(slot)
    result.update(
        status="candidate",
        capacity=capacity,
        count=count,
        slots=slots,
        reason="Persistent player party decoded through GameData.PokeParty; not BattleMon state.",
    )
    return result


class PlayerPartyDecoder:
    """Read-only live wrapper around :func:`decode_player_party_from_ram`."""

    def __init__(self, reader: MemoryReader | None = None):
        self.reader = reader
        self.latest: dict[str, Any] | None = None

    def configure(self, reader: MemoryReader | None) -> None:
        self.reader = reader
        self.latest = None

    async def sample(self) -> dict[str, Any]:
        if self.reader is None:
            return decode_player_party_from_ram(b"")
        try:
            root = await self.reader.read_batch_snapshot([
                {"id": "party_ptr", "addr": IREJ_REV1_GAME_DATA + GAME_DATA_PARTY_PTR, "length": 4},
            ])
        except Exception as exc:
            payload = decode_player_party_from_ram(b"")
            payload["reason"] = f"GameData.PokeParty read failed: {type(exc).__name__}: {exc}"
            return payload
        rows = root.get("results") if isinstance(root, dict) else {}
        party_ptr = _u32(_bytes(rows.get("party_ptr")), 0) if isinstance(rows, dict) and len(_bytes(rows.get("party_ptr"))) >= 4 else None
        if not _pointer(party_ptr):
            payload = decode_player_party_from_ram(b"")
            payload["frame"] = root.get("frame") if isinstance(root, dict) else None
            payload["reason"] = "GameData.PokeParty pointer is outside ARM9 Main RAM."
            return payload
        try:
            snapshot = await self.reader.read_batch_snapshot([
                {"id": "party", "addr": party_ptr, "length": POKE_PARTY_HEADER_SIZE + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE},
            ])
        except Exception as exc:
            payload = decode_player_party_from_ram(b"")
            payload["reason"] = f"GameData.PokeParty body read failed: {type(exc).__name__}: {exc}"
            return payload
        party = _bytes((snapshot.get("results") or {}).get("party")) if isinstance(snapshot, dict) else b""
        # Reconstruct just the pieces required by the pure full-RAM decoder,
        # retaining exactly the same validation path for offline and live use.
        virtual_ram = bytearray(IREJ_REV1_GAME_DATA - MAIN_RAM_START + GAME_DATA_PARTY_PTR + 4)
        virtual_ram[IREJ_REV1_GAME_DATA - MAIN_RAM_START + GAME_DATA_PARTY_PTR:IREJ_REV1_GAME_DATA - MAIN_RAM_START + GAME_DATA_PARTY_PTR + 4] = party_ptr.to_bytes(4, "little")
        required = party_ptr - MAIN_RAM_START + len(party)
        if required > len(virtual_ram):
            virtual_ram.extend(b"\x00" * (required - len(virtual_ram)))
        virtual_ram[party_ptr - MAIN_RAM_START:party_ptr - MAIN_RAM_START + len(party)] = party
        res = decode_player_party_from_ram(bytes(virtual_ram), frame=snapshot.get("frame") if isinstance(snapshot, dict) else None)
        self.latest = res
        return res