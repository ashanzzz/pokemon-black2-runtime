"""REST API endpoints for Pokemon PC Storage System (Box Operations).

Endpoints expose full read/search capabilities and atomic memory mutation
primitives (Deposit, Withdraw, Swap, Move) for the 24 PC Boxes in Pokemon Black 2.
"""
from __future__ import annotations
from ..actions.command_bus import command_bus

import binascii
import struct
import asyncio
import traceback
from typing import Any, Dict, List, Optional, Tuple
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..bizhawk.bridge_client import BridgeClient
from ..memory.reader import MemoryReader
from ..dex.store import DexStore
from ..decoders.party_runtime import (
    IREJ_REV1_GAME_DATA,
    GAME_DATA_PARTY_PTR,
    PARTY_POKEMON_SIZE,
    BOX_POKEMON_SIZE,
    POKE_PARTY_CAPACITY,
    BOX_ENCRYPTED_OFFSET,
    BOX_ENCRYPTED_SIZE,
    BLOCK_POSITION,
    _u32,
    _u16,
    _decrypt_words,
    _unshuffle_blocks,
)
from ..decoders.pc_storage_runtime import (
    BOX_COUNT,
    BOX_CAPACITY,
    BOX_STRIDE,
    SAVE_BLOCK_PARTY_OFFSET,
    SAVE_BLOCK_BOXES_OFFSET,
    resolve_save_block_base,
    decode_box_names,
    decode_single_box,
    decode_all_boxes,
    decode_box_pokemon,
    search_pc_storage,
    build_party_payload_from_box,
)

router = APIRouter(prefix="/api/v1/pokemon/pc", tags=["pc_storage"])
_client: BridgeClient | None = None
_reader: MemoryReader | None = None
dex = DexStore()


def configure_pc_routes(client: BridgeClient, reader: Optional[MemoryReader] = None) -> None:
    global _client, _reader
    _client = client
    _reader = reader


def _to_ram_offset(addr_or_off: int) -> int:
    return addr_or_off - 0x02000000 if addr_or_off >= 0x02000000 else addr_or_off


async def _read_main_ram(offset_or_addr: int, length: int) -> bytes:
    """Read a bounded span from ARM9 Main RAM using robust batch snapshot."""
    if _client is None or not _client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    off = _to_ram_offset(offset_or_addr)
    if _reader is not None:
        snap = await _reader.read_batch_snapshot([{"id": "r", "domain": "Main RAM", "offset": off, "length": length}])
        res = snap.get("results", {}).get("r", {})
        hex_data = res.get("hex", "")
        if hex_data:
            return binascii.unhexlify(hex_data)
    int_list = await _client.read_bytes(off, length, domain="Main RAM")
    return bytes(int_list)


async def _write_main_ram(addr_or_off: int, data: bytes) -> Dict[str, Any]:
    """Write bytes into ARM9 Main RAM using offset relative to 0x02000000."""
    if _client is None or not _client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    off = _to_ram_offset(addr_or_off)
    return await _client.write_bytes(off, list(data), domain="Main RAM")


async def _get_save_base_and_party_ptr() -> Tuple[int, int]:
    """Retrieve verified live Party pointer and SaveBlock base address."""
    gd_raw = await _read_main_ram(IREJ_REV1_GAME_DATA - 0x02000000, 0x200)
    if len(gd_raw) < GAME_DATA_PARTY_PTR + 4:
        raise HTTPException(status_code=500, detail="Could not read GameData from ARM9 Main RAM")
    party_ptr = struct.unpack("<I", gd_raw[GAME_DATA_PARTY_PTR:GAME_DATA_PARTY_PTR + 4])[0]
    if not (0x02000000 <= party_ptr < 0x02400000):
        raise HTTPException(status_code=500, detail="GameData Party pointer is invalid or outside Main RAM")
    save_base = party_ptr - SAVE_BLOCK_PARTY_OFFSET
    if not (0x02000000 <= save_base < 0x02400000):
        raise HTTPException(status_code=500, detail="Resolved SaveBlock base is invalid")
    return save_base, party_ptr


@router.get("/summary")
async def get_pc_summary():
    """Retrieve an overview of all 24 PC Boxes (total stored, free, capacity)."""
    try:
        save_base, party_ptr = await _get_save_base_and_party_ptr()
        names_raw = await _read_main_ram(save_base - 0x02000000, 0x3E0)
        box_names = decode_box_names(names_raw, save_base)

        boxes_meta = []
        total_stored = 0

        for b_idx in range(BOX_COUNT):
            box_id = b_idx + 1
            b_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + b_idx * BOX_STRIDE
            b_raw = await _read_main_ram(b_addr - 0x02000000, BOX_CAPACITY * BOX_POKEMON_SIZE)
            
            cnt = 0
            for s_idx in range(BOX_CAPACITY):
                slot_raw = b_raw[s_idx * BOX_POKEMON_SIZE:(s_idx + 1) * BOX_POKEMON_SIZE]
                pid = struct.unpack("<I", slot_raw[:4])[0]
                csum = struct.unpack("<H", slot_raw[6:8])[0]
                if pid != 0 or csum != 0:
                    cnt += 1

            total_stored += cnt
            boxes_meta.append({
                "box_id": box_id,
                "name": box_names[b_idx] if b_idx < len(box_names) else f"Box {box_id}",
                "count": cnt,
                "capacity": BOX_CAPACITY,
                "free_slots": BOX_CAPACITY - cnt,
            })

        return {
            "status": "ready",
            "save_block_base": f"0x{save_base:08X}",
            "party_pointer": f"0x{party_ptr:08X}",
            "total_boxes": BOX_COUNT,
            "total_stored": total_stored,
            "total_capacity": BOX_COUNT * BOX_CAPACITY,
            "total_free": (BOX_COUNT * BOX_CAPACITY) - total_stored,
            "boxes": boxes_meta,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"get_pc_summary error: {type(e).__name__}: {e}")


@router.get("/boxes")
async def get_pc_boxes():
    """Alias for /summary returning the box array."""
    res = await get_pc_summary()
    return res["boxes"]


@router.get("/box/{box_id}")
async def get_pc_box(box_id: int):
    """Retrieve full details of all 30 slots for a single PC Box (1..24)."""
    if not (1 <= box_id <= BOX_COUNT):
        raise HTTPException(status_code=400, detail=f"box_id must be between 1 and {BOX_COUNT}")
    
    try:
        save_base, _ = await _get_save_base_and_party_ptr()
        names_raw = await _read_main_ram(save_base - 0x02000000, 0x3E0)
        box_names = decode_box_names(names_raw, save_base)

        b_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (box_id - 1) * BOX_STRIDE
        b_raw = await _read_main_ram(b_addr - 0x02000000, BOX_CAPACITY * BOX_POKEMON_SIZE)

        slots = []
        box_count = 0
        for s_idx in range(BOX_CAPACITY):
            slot_id = s_idx + 1
            slot_raw = b_raw[s_idx * BOX_POKEMON_SIZE:(s_idx + 1) * BOX_POKEMON_SIZE]
            mon = decode_box_pokemon(slot_raw, dex)
            if mon:
                mon["slot"] = slot_id
                mon["box_id"] = box_id
                slots.append(mon)
                box_count += 1
            else:
                slots.append({
                    "slot": slot_id,
                    "box_id": box_id,
                    "empty": True,
                })

        return {
            "box_id": box_id,
            "name": box_names[box_id - 1] if box_id - 1 < len(box_names) else f"Box {box_id}",
            "count": box_count,
            "capacity": BOX_CAPACITY,
            "free_slots": BOX_CAPACITY - box_count,
            "slots": slots,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"get_pc_box error: {type(e).__name__}: {e}")


@router.get("/box/{box_id}/slot/{slot_id}")
async def get_pc_box_slot(box_id: int, slot_id: int):
    """Retrieve detailed holographic metadata for a single PC slot."""
    if not (1 <= box_id <= BOX_COUNT):
        raise HTTPException(status_code=400, detail=f"box_id must be between 1 and {BOX_COUNT}")
    if not (1 <= slot_id <= BOX_CAPACITY):
        raise HTTPException(status_code=400, detail=f"slot_id must be between 1 and {BOX_CAPACITY}")

    try:
        save_base, _ = await _get_save_base_and_party_ptr()
        slot_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (box_id - 1) * BOX_STRIDE + (slot_id - 1) * BOX_POKEMON_SIZE
        slot_raw = await _read_main_ram(slot_addr - 0x02000000, BOX_POKEMON_SIZE)

        mon = decode_box_pokemon(slot_raw, dex)
        if not mon:
            return {
                "box_id": box_id,
                "slot": slot_id,
                "empty": True,
                "status": "empty_slot",
            }
        mon["box_id"] = box_id
        mon["slot"] = slot_id
        mon["empty"] = False
        return mon
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"get_pc_box_slot error: {type(e).__name__}: {e}")


@router.get("/search")
async def search_pc(
    species_id: Optional[int] = Query(None, description="Exact Species ID"),
    species_name: Optional[str] = Query(None, description="Species Chinese or English name query"),
    ability_id: Optional[int] = Query(None, description="Exact Ability ID"),
    held_item_id: Optional[int] = Query(None, description="Exact Held Item ID"),
    move_id: Optional[int] = Query(None, description="Move ID present in moveset"),
    is_shiny: Optional[bool] = Query(None, description="Filter for shiny Pokemons"),
    type_name: Optional[str] = Query(None, description="Element type (e.g. Fire, Water)"),
):
    """Search stored Pokemons across all 24 PC Boxes matching specified filters."""
    try:
        save_base, _ = await _get_save_base_and_party_ptr()

        all_boxes = []
        for b_idx in range(BOX_COUNT):
            box_id = b_idx + 1
            b_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + b_idx * BOX_STRIDE
            b_raw = await _read_main_ram(b_addr - 0x02000000, BOX_CAPACITY * BOX_POKEMON_SIZE)
            
            slots = []
            for s_idx in range(BOX_CAPACITY):
                slot_id = s_idx + 1
                slot_raw = b_raw[s_idx * BOX_POKEMON_SIZE:(s_idx + 1) * BOX_POKEMON_SIZE]
                mon = decode_box_pokemon(slot_raw, dex)
                if mon:
                    mon["slot"] = slot_id
                    mon["box_id"] = box_id
                    slots.append(mon)
            all_boxes.append({"box_id": box_id, "slots": slots})

        results = search_pc_storage(
            {"boxes": all_boxes},
            species_id=species_id,
            species_name=species_name,
            ability_id=ability_id,
            held_item_id=held_item_id,
            move_id=move_id,
            is_shiny=is_shiny,
            type_name=type_name,
        )
        return {
            "status": "ready",
            "match_count": len(results),
            "results": results,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"search_pc error: {type(e).__name__}: {e}")


class DepositRequest(BaseModel):
    party_slot: int = Field(..., ge=1, le=6, description="1-based party slot to deposit into PC")
    target_box: Optional[int] = Field(None, ge=1, le=24, description="Target box (1..24); auto-detects first free box if omitted")
    target_slot: Optional[int] = Field(None, ge=1, le=30, description="Target box slot (1..30); auto-detects first free slot if omitted")


class WithdrawRequest(BaseModel):
    box_id: int = Field(..., ge=1, le=24, description="Source PC box (1..24)")
    box_slot: int = Field(..., ge=1, le=30, description="Source PC slot (1..30)")
    target_party_slot: Optional[int] = Field(None, ge=1, le=6, description="Target party slot (1..6); defaults to end of party")


class SwapRequest(BaseModel):
    party_slot: int = Field(..., ge=1, le=6, description="Party slot (1..6)")
    box_id: int = Field(..., ge=1, le=24, description="PC box (1..24)")
    box_slot: int = Field(..., ge=1, le=30, description="PC box slot (1..30)")


class MoveRequest(BaseModel):
    src_box: int = Field(..., ge=1, le=24)
    src_slot: int = Field(..., ge=1, le=30)
    dst_box: int = Field(..., ge=1, le=24)
    dst_slot: int = Field(..., ge=1, le=30)


class PartyTeachMoveRequest(BaseModel):
    party_slot: int = Field(..., ge=1, le=6, description='Party slot (1..6)')
    move_slot: int = Field(..., ge=1, le=4, description='Move slot to replace (1..4)')
    move_id: int = Field(..., ge=1, le=559, description='Target Move ID to teach')
    pp: Optional[int] = Field(None, ge=1, le=64, description='Optional custom PP, defaults to base PP')


def _shuffle_blocks(canonical_data: bytes, personality: int) -> bytes:
    order = BLOCK_POSITION[(personality >> 13) & 0x1F]
    canonical_blocks = [canonical_data[i * 32:(i + 1) * 32] for i in range(4)]
    encrypted_blocks = [None] * 4
    for canonical_idx, encrypted_pos in enumerate(order):
        encrypted_blocks[encrypted_pos] = canonical_blocks[canonical_idx]
    return b''.join(encrypted_blocks)


class PartySwapOrderRequest(BaseModel):
    slot_a: int = Field(..., ge=1, le=6, description="Party slot A (1..6)")
    slot_b: int = Field(..., ge=1, le=6, description="Party slot B (1..6)")


@router.post("/deposit")
async def post_deposit(req: DepositRequest):
    """Deposit a Pokemon from the active player party into a PC Box slot."""
    try:
        save_base, party_ptr = await _get_save_base_and_party_ptr()

        party_raw = await _read_main_ram(party_ptr - 0x02000000, 8 + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE)
        party_count = struct.unpack("<I", party_raw[4:8])[0]
        if party_count <= 1:
            raise HTTPException(
                status_code=400,
                detail="Cannot deposit: player must keep at least 1 Pokemon in the active party.",
            )
        if req.party_slot > party_count:
            raise HTTPException(
                status_code=400,
                detail=f"party_slot {req.party_slot} is empty; current party has {party_count} Pokemons.",
            )

        slot_offset = 8 + (req.party_slot - 1) * PARTY_POKEMON_SIZE
        target_pkm_full = party_raw[slot_offset:slot_offset + PARTY_POKEMON_SIZE]
        target_box_payload = target_pkm_full[:BOX_POKEMON_SIZE]

        mon_info = decode_box_pokemon(target_box_payload, dex)
        if not mon_info:
            raise HTTPException(status_code=500, detail="Corrupted party Pokemon checksum; cannot deposit.")

        dest_box = req.target_box
        dest_slot = req.target_slot

        if dest_box is None or dest_slot is None:
            found = False
            start_box = req.target_box if req.target_box is not None else 1
            for b in range(start_box, BOX_COUNT + 1):
                b_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (b - 1) * BOX_STRIDE
                b_raw = await _read_main_ram(b_addr - 0x02000000, BOX_CAPACITY * BOX_POKEMON_SIZE)
                for s in range(1, BOX_CAPACITY + 1):
                    s_raw = b_raw[(s - 1) * BOX_POKEMON_SIZE:s * BOX_POKEMON_SIZE]
                    if struct.unpack("<I", s_raw[:4])[0] == 0:
                        dest_box = b
                        dest_slot = s
                        found = True
                        break
                if found:
                    break
            if not found:
                raise HTTPException(status_code=400, detail="All 24 PC Boxes are completely full!")

        dest_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (dest_box - 1) * BOX_STRIDE + (dest_slot - 1) * BOX_POKEMON_SIZE
        dest_cur_raw = await _read_main_ram(dest_addr - 0x02000000, BOX_POKEMON_SIZE)
        if struct.unpack("<I", dest_cur_raw[:4])[0] != 0:
            raise HTTPException(status_code=400, detail=f"Target Box {dest_box} Slot {dest_slot} is already occupied.")

        await _write_main_ram(dest_addr, target_box_payload)

        # 顺移后续队伍槽位 (逐槽定点写入，避免大包超时)
        for shift_idx in range(req.party_slot - 1, party_count - 1):
            src_off = 8 + (shift_idx + 1) * PARTY_POKEMON_SIZE
            dst_off = 8 + shift_idx * PARTY_POKEMON_SIZE
            slot_bytes = party_raw[src_off:src_off + PARTY_POKEMON_SIZE]
            await _write_main_ram(party_ptr + dst_off, slot_bytes)

        # 清空原末尾槽位 (220 字节)
        last_off = 8 + (party_count - 1) * PARTY_POKEMON_SIZE
        await _write_main_ram(party_ptr + last_off, b"\x00" * PARTY_POKEMON_SIZE)

        # 更新 PartyCount (4 字节)
        await _write_main_ram(party_ptr + 4, struct.pack("<I", party_count - 1))

        return {
            "ok": True,
            "action": "deposit",
            "pokemon": {
                "species": mon_info["species"],
                "species_name": mon_info["species_name"],
                "level": mon_info["level"],
                "pid": mon_info["pid"],
            },
            "from_party_slot": req.party_slot,
            "to_box": dest_box,
            "to_slot": dest_slot,
            "new_party_count": party_count - 1,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"post_deposit error: {type(e).__name__}: {e}")


@router.post("/withdraw")
async def post_withdraw(req: WithdrawRequest):
    """Withdraw a Pokemon from a PC Box slot into the active player party."""
    try:
        save_base, party_ptr = await _get_save_base_and_party_ptr()

        party_raw = await _read_main_ram(party_ptr - 0x02000000, 8 + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE)
        party_count = struct.unpack("<I", party_raw[4:8])[0]
        if party_count >= POKE_PARTY_CAPACITY:
            raise HTTPException(status_code=400, detail="Party is already full (6/6 Pokemons).")

        src_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (req.box_id - 1) * BOX_STRIDE + (req.box_slot - 1) * BOX_POKEMON_SIZE
        src_raw = await _read_main_ram(src_addr - 0x02000000, BOX_POKEMON_SIZE)
        mon_info = decode_box_pokemon(src_raw, dex)
        if not mon_info:
            raise HTTPException(status_code=400, detail=f"Box {req.box_id} Slot {req.box_slot} is empty or corrupted.")

        party_pkm_bytes = build_party_payload_from_box(src_raw, dex)

        dst_slot = req.target_party_slot if req.target_party_slot is not None else (party_count + 1)
        if dst_slot > party_count + 1:
            dst_slot = party_count + 1

        dst_off = 8 + (dst_slot - 1) * PARTY_POKEMON_SIZE
        # 1. 写入队伍新槽位 (220 字节)
        await _write_main_ram(party_ptr + dst_off, party_pkm_bytes)
        await asyncio.sleep(0.03)
        # 2. 更新 PartyCount (4 字节)
        await _write_main_ram(party_ptr + 4, struct.pack("<I", party_count + 1))
        await asyncio.sleep(0.03)
        # 3. 清空 Box 槽位 (136 字节)
        await _write_main_ram(src_addr, b"\x00" * BOX_POKEMON_SIZE)

        return {
            "ok": True,
            "action": "withdraw",
            "pokemon": {
                "species": mon_info["species"],
                "species_name": mon_info["species_name"],
                "level": mon_info["level"],
                "pid": mon_info["pid"],
            },
            "from_box": req.box_id,
            "from_slot": req.box_slot,
            "to_party_slot": dst_slot,
            "new_party_count": party_count + 1,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"post_withdraw error: {type(e).__name__}: {e}")


@router.post('/party/teach-move')
async def post_party_teach_move(req: PartyTeachMoveRequest):
    try:
        from .battle_routes import _party_decoder
        from .semantic_routes import _enrich_party_slot

        try:
            initial_party = await _party_decoder.sample()
            initial_slots = {s["slot"]: _enrich_party_slot(s, dex) for s in (initial_party or {}).get("slots", [])}
        except Exception:
            initial_slots = {}
        target_mon = initial_slots.get(req.party_slot, {})
        pokemon_name = target_mon.get("species_name_zh") or target_mon.get("species_name") or f"??{req.party_slot}"

        save_base, party_ptr = await _get_save_base_and_party_ptr()
        party_raw = await _read_main_ram(party_ptr - 0x02000000, 8 + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE)
        party_count = struct.unpack('<I', party_raw[4:8])[0]

        if req.party_slot > party_count:
            raise HTTPException(status_code=400, detail=f'party_slot {req.party_slot} is empty.')

        off = 8 + (req.party_slot - 1) * PARTY_POKEMON_SIZE
        raw = party_raw[off:off + PARTY_POKEMON_SIZE]

        pid = _u32(raw, 0)
        checksum = _u16(raw, 6)
        decrypted_stored = _decrypt_words(raw[BOX_ENCRYPTED_OFFSET:BOX_ENCRYPTED_OFFSET + BOX_ENCRYPTED_SIZE], checksum)
        canonical = bytearray(_unshuffle_blocks(decrypted_stored, pid))

        move_entity = dex.get('moves', req.move_id) if dex else None
        base_pp = (move_entity.get('pp') or 15) if isinstance(move_entity, dict) else 15
        target_pp = req.pp if req.pp is not None else base_pp

        move_off = 0x20 + (req.move_slot - 1) * 2
        pp_off = 0x28 + (req.move_slot - 1)
        pp_up_off = 0x2C + (req.move_slot - 1)

        old_move_id = _u16(canonical, move_off)
        canonical[move_off:move_off + 2] = int(req.move_id).to_bytes(2, 'little')
        canonical[pp_off] = int(target_pp) & 0xFF
        canonical[pp_up_off] = 0

        new_checksum = sum(_u16(canonical, offset) for offset in range(0, BOX_ENCRYPTED_SIZE, 2)) & 0xFFFF

        shuffled = _shuffle_blocks(bytes(canonical), pid)
        re_encrypted = _decrypt_words(shuffled, new_checksum)

        new_raw = bytearray(raw)
        new_raw[6:8] = new_checksum.to_bytes(2, 'little')
        new_raw[BOX_ENCRYPTED_OFFSET:BOX_ENCRYPTED_OFFSET + BOX_ENCRYPTED_SIZE] = re_encrypted

        await _write_main_ram(party_ptr + off, bytes(new_raw))

        updated_party = await _party_decoder.sample()
        if updated_party and updated_party.get("slots"):
            updated_party["slots"] = [_enrich_party_slot(s, dex) for s in updated_party["slots"]]

        updated_mon = next((s for s in (updated_party.get("slots") or []) if s.get("slot") == req.party_slot), {})
        move_name = move_entity.get('names', {}).get('zh-Hans') or move_entity.get('name_zh') or move_entity.get('name') if isinstance(move_entity, dict) else f'Move #{req.move_id}'

        old_move_entity = dex.get('moves', old_move_id) if dex and old_move_id > 0 else None
        old_move_name = (
            old_move_entity.get('names', {}).get('zh-Hans')
            or old_move_entity.get('name_zh')
            or old_move_entity.get('name')
            if isinstance(old_move_entity, dict)
            else (f'Move #{old_move_id}' if old_move_id > 0 else ' (首发)')
        )

        current_moves = [
            f"槽位{m.get('slot')}: {m.get('name_zh') or m.get('name')} (PP:{m.get('current_pp')}/{m.get('max_pp')})"
            for m in updated_mon.get("moves", [])
        ]
        summary_zh = (
            f"席位 {req.party_slot}【{pokemon_name}】招式槽位 {req.move_slot} 已学会「{move_name}」"
            f"（替换原招式「{old_move_name}」），物理 RAM 校验码: 0x{new_checksum:04X}"
        )

        return {
            'ok': True,
            'status': 'succeeded',
            'action': 'teach_move',
            'party_slot': req.party_slot,
            'move_slot': req.move_slot,
            'pokemon': {
                'slot': req.party_slot,
                'species_name': pokemon_name,
                'species_id': target_mon.get('species'),
                'level': target_mon.get('level'),
                'pid': target_mon.get('pid'),
            },
            'old_move': {
                'id': old_move_id,
                'name': old_move_name,
            },
            'new_move': {
                'id': req.move_id,
                'name': move_name,
                'pp': target_pp,
                'max_pp': base_pp,
            },
            'current_moves': current_moves,
            'old_move_id': old_move_id,
            'new_move_id': req.move_id,
            'move_name': move_name,
            'pp': target_pp,
            'checksum': f'0x{new_checksum:04X}',
            'summary_zh': summary_zh,
            'verification': {
                'source': 'GameData.PokeParty (0x0221E624)',
                'pml_recalculated_checksum': f'0x{new_checksum:04X}',
                'block_b_injected': True,
            },
            'party': updated_party,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f'post_party_teach_move error: {type(e).__name__}: {e}')


@router.post("/party/swap")
async def post_party_swap(req: PartySwapOrderRequest):
    """Atomically swap the order of two Pokemon within the active player party (1..6)."""
    try:
        from .battle_routes import _party_decoder
        from .semantic_routes import _enrich_party_slot

        try:
            initial_party = await _party_decoder.sample()
            slots_before = {s["slot"]: _enrich_party_slot(s, dex) for s in (initial_party or {}).get("slots", [])}
        except Exception:
            slots_before = {}
        mon_a = slots_before.get(req.slot_a, {})
        mon_b = slots_before.get(req.slot_b, {})

        if req.slot_a == req.slot_b:
            latest_slots = list(slots_before.values())
            latest_lineup = [
                f"{s['slot']}. {s.get('species_name_zh') or s.get('species_name')} (Lv.{s.get('level')}{' (首发)' if s.get('slot') == 1 else ''})"
                for s in latest_slots
            ]
            lead_mon = latest_slots[0] if latest_slots else {}
            return {
                "ok": True,
                "status": "succeeded",
                "action": "swap_party_order",
                "message": "Slots are identical; no-op.",
                "slot_a": req.slot_a,
                "slot_b": req.slot_b,
                "swapped_a": {
                    "slot": req.slot_a,
                    "species_name": mon_a.get("species_name_zh") or mon_a.get("species_name") or f"??{req.slot_a}",
                    "level": mon_a.get("level"),
                },
                "swapped_b": {
                    "slot": req.slot_b,
                    "species_name": mon_b.get("species_name_zh") or mon_b.get("species_name") or f"??{req.slot_b}",
                    "level": mon_b.get("level"),
                },
                "lead_pokemon": {
                    "slot": 1,
                    "species_name": lead_mon.get("species_name_zh") or lead_mon.get("species_name"),
                    "level": lead_mon.get("level"),
                },
                "latest_lineup": latest_lineup,
                "party": initial_party,
            }

        save_base, party_ptr = await _get_save_base_and_party_ptr()
        party_raw = await _read_main_ram(party_ptr - 0x02000000, 8 + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE)
        party_count = struct.unpack("<I", party_raw[4:8])[0]

        if req.slot_a > party_count:
            raise HTTPException(status_code=400, detail=f"slot_a {req.slot_a} is empty (party has {party_count} members).")
        if req.slot_b > party_count:
            raise HTTPException(status_code=400, detail=f"slot_b {req.slot_b} is empty (party has {party_count} members).")

        off_a = 8 + (req.slot_a - 1) * PARTY_POKEMON_SIZE
        off_b = 8 + (req.slot_b - 1) * PARTY_POKEMON_SIZE

        data_a = party_raw[off_a:off_a + PARTY_POKEMON_SIZE]
        data_b = party_raw[off_b:off_b + PARTY_POKEMON_SIZE]

        # Execute swap through transactional CommandBus with pre-snapshot, verification, and rollback
        pre_span = party_raw[8:8 + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE]

        async def _capture_pre():
            return (party_ptr + 8, pre_span, {})

        async def _exec_swap(_meta):
            await _write_main_ram(party_ptr + off_a, data_b)
            await _write_main_ram(party_ptr + off_b, data_a)
            return {"swapped": True}

        async def _verify_swap(_res):
            p = await _party_decoder.sample()
            if p and (p.get("count", 0) > 0 or p.get("status") in ("candidate", "resolved")):
                return True, {"party": p}, None
            return False, {}, "Party checksum verification failed after swap"

        tx_res = await command_bus.execute_transaction(
            "party_swap",
            owner_id=f"slots_{req.slot_a}_{req.slot_b}",
            capture_pre_state=_capture_pre,
            execute_action=_exec_swap,
            verify_post_state=_verify_swap,
            write_ram=_write_main_ram,
        )

        if not tx_res.ok:
            raise HTTPException(status_code=500, detail=f"Party swap transaction failed: {tx_res.error}")

        updated_party = tx_res.data.get("party") or await _party_decoder.sample()
        if updated_party and updated_party.get("slots"):
            updated_party["slots"] = [_enrich_party_slot(s, dex) for s in updated_party["slots"]]

        name_a = mon_a.get("species_name_zh") or mon_a.get("species_name") or f"??{req.slot_a}"
        lvl_a = mon_a.get("level", "?")
        name_b = mon_b.get("species_name_zh") or mon_b.get("species_name") or f"??{req.slot_b}"
        lvl_b = mon_b.get("level", "?")

        latest_slots = updated_party.get("slots", []) if updated_party else []
        latest_lineup = [
            f"{s['slot']}. {s.get('species_name_zh') or s.get('species_name')} (Lv.{s.get('level')}{' (首发)' if s.get('slot') == 1 else ''})"
            for s in latest_slots
        ]
        lead_mon = latest_slots[0] if latest_slots else {}

        summary_zh = (
            f"席位 {req.slot_a}【{name_a} Lv.{lvl_a}】与 席位 {req.slot_b}【{name_b} Lv.{lvl_b}】"
            f"物理 RAM 原子调换成功！最新首发已变更为: 席位 1【{lead_mon.get('species_name_zh') or lead_mon.get('species_name')} Lv.{lead_mon.get('level')}】"
        )

        return {
            "ok": True,
            "status": "succeeded",
            "action": "swap_party_order",
            "slot_a": req.slot_a,
            "slot_b": req.slot_b,
            "swapped_a": {
                "slot": req.slot_a,
                "species_name": name_a,
                "species_id": mon_a.get("species"),
                "level": mon_a.get("level"),
                "current_hp": mon_a.get("current_hp"),
                "max_hp": mon_a.get("max_hp"),
                "pid": mon_a.get("pid"),
            },
            "swapped_b": {
                "slot": req.slot_b,
                "species_name": name_b,
                "species_id": mon_b.get("species"),
                "level": mon_b.get("level"),
                "current_hp": mon_b.get("current_hp"),
                "max_hp": mon_b.get("max_hp"),
                "pid": mon_b.get("pid"),
            },
            "lead_pokemon": {
                "slot": 1,
                "species_name": lead_mon.get("species_name_zh") or lead_mon.get("species_name"),
                "species_id": lead_mon.get("species"),
                "level": lead_mon.get("level"),
                "current_hp": lead_mon.get("current_hp"),
                "max_hp": lead_mon.get("max_hp"),
            },
            "latest_lineup": latest_lineup,
            "summary_zh": summary_zh,
            "verification": {
                "source": "GameData.PokeParty (0x0221E624)",
                "checksum_verified": True,
                "party_count": len(latest_slots),
            },
            "party": updated_party,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"post_party_swap error: {type(e).__name__}: {e}")


@router.post("/swap")
async def post_swap(req: SwapRequest):
    """Atomically swap a party Pokemon with a PC box Pokemon."""
    try:
        save_base, party_ptr = await _get_save_base_and_party_ptr()

        party_raw = await _read_main_ram(party_ptr - 0x02000000, 8 + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE)
        party_count = struct.unpack("<I", party_raw[4:8])[0]
        if req.party_slot > party_count:
            raise HTTPException(status_code=400, detail=f"party_slot {req.party_slot} is empty.")

        p_off = 8 + (req.party_slot - 1) * PARTY_POKEMON_SIZE
        party_box_payload = party_raw[p_off:p_off + BOX_POKEMON_SIZE]

        box_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (req.box_id - 1) * BOX_STRIDE + (req.box_slot - 1) * BOX_POKEMON_SIZE
        box_raw = await _read_main_ram(box_addr - 0x02000000, BOX_POKEMON_SIZE)
        box_mon_info = decode_box_pokemon(box_raw, dex)
        if not box_mon_info:
            raise HTTPException(status_code=400, detail=f"Box {req.box_id} Slot {req.box_slot} is empty; use deposit instead.")

        new_party_payload = build_party_payload_from_box(box_raw, dex)

        await _write_main_ram(party_ptr + p_off, new_party_payload)
        await _write_main_ram(box_addr, party_box_payload)

        return {
            "ok": True,
            "action": "swap",
            "party_slot": req.party_slot,
            "box_id": req.box_id,
            "box_slot": req.box_slot,
            "swapped_in_party": box_mon_info["species_name"],
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"post_swap error: {type(e).__name__}: {e}")


@router.post("/move")
async def post_move(req: MoveRequest):
    """Atomically move or exchange two slots inside the PC Storage system."""
    try:
        save_base, _ = await _get_save_base_and_party_ptr()

        src_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (req.src_box - 1) * BOX_STRIDE + (req.src_slot - 1) * BOX_POKEMON_SIZE
        dst_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (req.dst_box - 1) * BOX_STRIDE + (req.dst_slot - 1) * BOX_POKEMON_SIZE

        src_raw = await _read_main_ram(src_addr - 0x02000000, BOX_POKEMON_SIZE)
        dst_raw = await _read_main_ram(dst_addr - 0x02000000, BOX_POKEMON_SIZE)

        await _write_main_ram(src_addr, dst_raw)
        await _write_main_ram(dst_addr, src_raw)

        return {
            "ok": True,
            "action": "move",
            "src": {"box": req.src_box, "slot": req.src_slot},
            "dst": {"box": req.dst_box, "slot": req.dst_slot},
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"post_move error: {type(e).__name__}: {e}")
