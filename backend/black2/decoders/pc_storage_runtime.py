"""Checksum-gated Gen V Pokemon Storage System (PC Box) decoder & operator.

This module provides full-fidelity decoders and atomic mutation primitives for
the 24 PC Boxes (720 slots) in Pokemon Black 2 (IREJ rev.1).

It resolves the SaveBlock via GameData (0x0223B570 -> PartyPtr - 0x18E00) and
enriches every stored BoxPokemon with DexStore catalog metadata (species,
abilities, held items, move slots, natures, IVs, EVs, shininess).
"""
from __future__ import annotations

import math
import struct
from typing import Any, Dict, List, Optional, Tuple

from ..dex.store import DexStore
from .party_runtime import (
    MAIN_RAM_START,
    MAIN_RAM_END,
    IREJ_REV1_GAME_DATA,
    GAME_DATA_PARTY_PTR,
    PARTY_POKEMON_SIZE,
    BOX_POKEMON_SIZE,
    BOX_ENCRYPTED_OFFSET,
    BOX_ENCRYPTED_SIZE,
    BLOCK_POSITION,
    _bytes,
    _pointer,
    _u16,
    _u32,
    _decrypt_words,
    _unshuffle_blocks,
)

GAME_DATA_BAG_PTR = 0x190
SAVE_BLOCK_PARTY_OFFSET = 0x18E00
SAVE_BLOCK_BAG_OFFSET = 0x18400
SAVE_BLOCK_BOX_NAMES_OFFSET = 0x00000
SAVE_BLOCK_BOXES_OFFSET = 0x00400

BOX_CAPACITY = 30
BOX_COUNT = 24
BOX_DATA_SIZE = BOX_CAPACITY * BOX_POKEMON_SIZE  # 30 * 136 = 4080 (0xFF0)
BOX_STRIDE = 0x1000  # 4096 bytes per box (0xFF0 payload + 0x10 metadata)

NATURE_NAMES_ZH = (
    "勤奋", "怕寂寞", "勇敢", "固执", "顽皮",
    "大胆", "坦率", "悠闲", "淘气", "乐天",
    "胆小", "急躁", "认真", "开朗", "天真",
    "内敛", "慢吞吞", "冷静", "害羞", "马虎",
    "温和", "温顺", "自大", "慎重", "浮躁",
)

NATURE_NAMES_EN = (
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty",
    "Bold", "Docile", "Relaxed", "Impish", "Lax",
    "Timid", "Hasty", "Serious", "Jolly", "Naive",
    "Modest", "Mild", "Quiet", "Bashful", "Rash",
    "Calm", "Gentle", "Sassy", "Careful", "Quirky",
)

# Nature stat modifications: (increased_stat_idx, decreased_stat_idx)
# Index: 0: Atk, 1: Def, 2: Spe, 3: SpA, 4: SpD
def get_nature_modifiers(nature_id: int) -> Tuple[Optional[int], Optional[int]]:
    if not (0 <= nature_id < 25):
        return None, None
    inc = nature_id // 5
    dec = nature_id % 5
    if inc == dec:
        return None, None
    return inc, dec


def _decode_ucs2_string(data: bytes, max_chars: int) -> str:
    """Decode a UCS-2LE byte slice into a clean string up to 0xFFFF or null."""
    chars = []
    for i in range(0, min(len(data), max_chars * 2), 2):
        if i + 2 > len(data):
            break
        code = int.from_bytes(data[i:i + 2], "little")
        if code in (0x0000, 0xFFFF):
            break
        chars.append(chr(code))
    return "".join(chars).strip()


def calculate_level_from_exp(exp: int) -> int:
    """Estimate Pokemon level from experience points (cubic default fallback)."""
    if exp <= 0:
        return 1
    lvl = int(round(exp ** (1.0 / 3.0)))
    if lvl > 100:
        return 100
    if lvl < 1:
        return 1
    while lvl < 100 and (lvl + 1) ** 3 <= exp:
        lvl += 1
    while lvl > 1 and lvl ** 3 > exp:
        lvl -= 1
    return max(1, min(100, lvl))


def decode_box_pokemon(raw: bytes, dex: Optional[DexStore] = None) -> Optional[Dict[str, Any]]:
    """Decode a single 136-byte Gen V BoxPokemon structure."""
    if len(raw) < BOX_POKEMON_SIZE:
        return None
    raw = raw[:BOX_POKEMON_SIZE]

    pid = _u32(raw, 0)
    checksum = _u16(raw, 6)
    if pid == 0 and checksum == 0:
        return None

    decrypted_stored = _decrypt_words(
        raw[BOX_ENCRYPTED_OFFSET:BOX_ENCRYPTED_OFFSET + BOX_ENCRYPTED_SIZE],
        checksum,
    )
    checksum_actual = sum(
        _u16(decrypted_stored, offset) for offset in range(0, BOX_ENCRYPTED_SIZE, 2)
    ) & 0xFFFF
    if checksum_actual != checksum or checksum == 0:
        return None

    data = _unshuffle_blocks(decrypted_stored, pid)

    # Block A
    species_id = _u16(data, 0x00)
    held_item_id = _u16(data, 0x02)
    ot_tid = _u16(data, 0x04)
    ot_sid = _u16(data, 0x06)
    exp = _u32(data, 0x08)
    friendship = data[0x0C]
    ability_id = data[0x0D]
    markings = data[0x0E]
    language = data[0x0F]
    evs = {
        "hp": data[0x10],
        "attack": data[0x11],
        "defense": data[0x12],
        "speed": data[0x13],
        "sp_atk": data[0x14],
        "sp_def": data[0x15],
    }

    # Block B
    moves = []
    for move_slot in range(4):
        move_id = _u16(data, 0x20 + move_slot * 2)
        cur_pp = data[0x28 + move_slot]
        pp_up = (data[0x2C + move_slot] if 0x2C + move_slot < len(data) else 0) & 0x03
        move_info: Dict[str, Any] = {
            "slot": move_slot + 1,
            "move_id": move_id,
            "current_pp": cur_pp,
            "pp_up": pp_up,
        }
        if dex and move_id > 0:
            m_entity = dex.get("moves", move_id)
            if m_entity:
                move_info.update({
                    "name": m_entity.get("names", {}).get("zh-Hans") or m_entity.get("name_zh") or m_entity.get("identifier"),
                    "name_en": m_entity.get("names", {}).get("en") or m_entity.get("name_en") or m_entity.get("identifier"),
                    "type": m_entity.get("type"),
                    "power": m_entity.get("power"),
                    "accuracy": m_entity.get("accuracy"),
                    "max_pp": m_entity.get("pp"),
                })
        moves.append(move_info)

    iv32 = _u32(data, 0x30)
    ivs = {
        "hp": (iv32 >> 0) & 0x1F,
        "attack": (iv32 >> 5) & 0x1F,
        "defense": (iv32 >> 10) & 0x1F,
        "speed": (iv32 >> 15) & 0x1F,
        "sp_atk": (iv32 >> 20) & 0x1F,
        "sp_def": (iv32 >> 25) & 0x1F,
    }
    is_egg = bool((iv32 >> 30) & 1)
    is_nicknamed = bool((iv32 >> 31) & 1)

    gender_raw = (data[0x38] >> 1) & 0x03
    gender_map = {0: "M", 1: "F", 2: "Genderless"}
    gender = gender_map.get(gender_raw, "Unknown")
    form = data[0x38] >> 3
    nature_id = data[0x39]
    hidden_ability = bool(data[0x3A] & 1)

    # Block C
    nickname = _decode_ucs2_string(data[0x40:0x56], 11)
    origin_game = data[0x57] if len(data) > 0x57 else 0

    # Block D
    ot_name = _decode_ucs2_string(data[0x60:0x70], 8)
    met_location = _u16(data, 0x78) if len(data) >= 0x7A else 0
    ball_id = data[0x7B] if len(data) > 0x7B else 0
    met_level = data[0x7C] & 0x7F if len(data) > 0x7C else 0

    shiny_value = ot_tid ^ ot_sid ^ (pid >> 16) ^ (pid & 0xFFFF)
    is_shiny = (shiny_value < 8)

    level = calculate_level_from_exp(exp)
    nature_zh = NATURE_NAMES_ZH[nature_id] if 0 <= nature_id < len(NATURE_NAMES_ZH) else f"Nature#{nature_id}"
    nature_en = NATURE_NAMES_EN[nature_id] if 0 <= nature_id < len(NATURE_NAMES_EN) else f"Nature#{nature_id}"

    # Dex catalog enrichment
    species_name_zh = f"Pokemon#{species_id}"
    species_name_en = f"Pokemon#{species_id}"
    types = []
    base_stats = {}
    if dex and species_id > 0:
        sp_doc = dex.get("pokemon", species_id)
        if sp_doc:
            species_name_zh = sp_doc.get("names", {}).get("zh-Hans") or sp_doc.get("identifier") or species_name_zh
            species_name_en = sp_doc.get("names", {}).get("en") or sp_doc.get("identifier") or species_name_en
            for t in sp_doc.get("types", []):
                types.append({
                    "id": t.get("id"),
                    "name": t.get("names", {}).get("zh-Hans") or t.get("identifier"),
                    "name_en": t.get("names", {}).get("en") or t.get("identifier"),
                })
            for bs in sp_doc.get("base_stats", []):
                ident = bs.get("identifier", "").replace("-", "_")
                base_stats[ident] = bs.get("base_stat", 0)

    # Item catalog enrichment
    item_name_zh = "无携带"
    item_name_en = "None"
    if dex and held_item_id > 0:
        items = dex.items_by_game_index(held_item_id)
        if items:
            item_name_zh = items[0].get("name_zh") or items[0].get("identifier") or f"Item#{held_item_id}"
            item_name_en = items[0].get("name_en") or items[0].get("identifier") or f"Item#{held_item_id}"
        else:
            item_name_zh = f"Item#{held_item_id}"
            item_name_en = f"Item#{held_item_id}"

    # Ability catalog enrichment
    ability_name_zh = f"Ability#{ability_id}"
    ability_name_en = f"Ability#{ability_id}"
    if dex and ability_id > 0:
        ab_doc = dex.get("abilities", ability_id)
        if ab_doc:
            ability_name_zh = ab_doc.get("names", {}).get("zh-Hans") or ab_doc.get("name_zh") or ab_doc.get("identifier") or ability_name_zh
            ability_name_en = ab_doc.get("names", {}).get("en") or ab_doc.get("name_en") or ab_doc.get("identifier") or ability_name_en

    return {
        "pid": f"{pid:08X}",
        "species": species_id,
        "species_name": species_name_zh,
        "species_name_en": species_name_en,
        "nickname": nickname or species_name_zh,
        "level": level,
        "experience": exp,
        "held_item_id": held_item_id,
        "held_item_name": item_name_zh,
        "held_item_name_en": item_name_en,
        "ability_id": ability_id,
        "ability_name": ability_name_zh,
        "ability_name_en": ability_name_en,
        "hidden_ability": hidden_ability,
        "types": types,
        "nature": {
            "id": nature_id,
            "name": nature_zh,
            "name_en": nature_en,
        },
        "gender": gender,
        "form": form,
        "is_shiny": is_shiny,
        "is_egg": is_egg,
        "friendship": friendship,
        "ivs": ivs,
        "evs": evs,
        "base_stats": base_stats,
        "moves": moves,
        "trainer": {
            "tid": ot_tid,
            "sid": ot_sid,
            "name": ot_name,
        },
        "ball_id": ball_id,
        "met_level": met_level,
        "met_location": met_location,
        "raw_checksum": checksum,
    }


def build_party_payload_from_box(box_bytes: bytes, dex: Optional[DexStore] = None) -> bytes:
    """Calculate 84-byte battle stats and encrypt them with PID to form a 220-byte PartyMon."""
    if len(box_bytes) < BOX_POKEMON_SIZE:
        raise ValueError("Invalid BoxPokemon size")
    box_bytes = box_bytes[:BOX_POKEMON_SIZE]

    pid = _u32(box_bytes, 0)
    checksum = _u16(box_bytes, 6)
    decrypted_stored = _decrypt_words(
        box_bytes[BOX_ENCRYPTED_OFFSET:BOX_ENCRYPTED_OFFSET + BOX_ENCRYPTED_SIZE],
        checksum,
    )
    data = _unshuffle_blocks(decrypted_stored, pid)

    species_id = _u16(data, 0x00)
    exp = _u32(data, 0x08)
    nature_id = data[0x39]
    iv32 = _u32(data, 0x30)

    iv_hp = (iv32 >> 0) & 0x1F
    iv_atk = (iv32 >> 5) & 0x1F
    iv_def = (iv32 >> 10) & 0x1F
    iv_spe = (iv32 >> 15) & 0x1F
    iv_spa = (iv32 >> 20) & 0x1F
    iv_spd = (iv32 >> 25) & 0x1F

    ev_hp = data[0x10]
    ev_atk = data[0x11]
    ev_def = data[0x12]
    ev_spe = data[0x13]
    ev_spa = data[0x14]
    ev_spd = data[0x15]

    level = calculate_level_from_exp(exp)

    # Base stats default
    base_hp, base_atk, base_def, base_spe, base_spa, base_spd = 50, 50, 50, 50, 50, 50
    if dex and species_id > 0:
        sp_doc = dex.get("pokemon", species_id)
        if sp_doc:
            b_dict = {bs.get("identifier"): bs.get("base_stat") for bs in sp_doc.get("base_stats", [])}
            base_hp = b_dict.get("hp", 50)
            base_atk = b_dict.get("attack", 50)
            base_def = b_dict.get("defense", 50)
            base_spe = b_dict.get("speed", 50)
            base_spa = b_dict.get("special-attack", 50)
            base_spd = b_dict.get("special-defense", 50)

    # HP Calculation
    if base_hp == 1:
        max_hp = 1
    else:
        max_hp = (((iv_hp + 2 * base_hp + (ev_hp // 4) + 100) * level) // 100) + 10
    cur_hp = max_hp

    # 5 Stats: Atk, Def, Spe, SpA, SpD
    inc_stat, dec_stat = get_nature_modifiers(nature_id)
    raw_stats = [
        (((iv_atk + 2 * base_atk + (ev_atk // 4)) * level) // 100) + 5,
        (((iv_def + 2 * base_def + (ev_def // 4)) * level) // 100) + 5,
        (((iv_spe + 2 * base_spe + (ev_spe // 4)) * level) // 100) + 5,
        (((iv_spa + 2 * base_spa + (ev_spa // 4)) * level) // 100) + 5,
        (((iv_spd + 2 * base_spd + (ev_spd // 4)) * level) // 100) + 5,
    ]

    final_stats = []
    for idx, raw_val in enumerate(raw_stats):
        val = raw_val
        if inc_stat is not None and idx == inc_stat:
            val = (val * 110) // 100
        elif dec_stat is not None and idx == dec_stat:
            val = (val * 90) // 100
        final_stats.append(max(1, min(65535, val)))

    atk, defense, spe, spa, spd = final_stats

    # Construct 84 plaintext bytes
    payload = bytearray(84)
    struct.pack_into("<I", payload, 0x00, 0)        # StatusCond = HEALTHY
    payload[0x04] = level & 0xFF                   # Level
    payload[0x05] = 0                              # field_8D
    struct.pack_into("<H", payload, 0x06, cur_hp)  # NowHP
    struct.pack_into("<H", payload, 0x08, max_hp)  # MaxHP
    struct.pack_into("<H", payload, 0x0A, atk)     # ATK
    struct.pack_into("<H", payload, 0x0C, defense) # DEF
    struct.pack_into("<H", payload, 0x0E, spe)     # SPE
    struct.pack_into("<H", payload, 0x10, spa)     # SPA
    struct.pack_into("<H", payload, 0x12, spd)     # SPD

    # Encrypt 84 bytes using PID-seeded LCG
    encrypted_payload = _decrypt_words(bytes(payload), pid)

    return box_bytes + encrypted_payload


def resolve_save_block_base(ram: bytes) -> Optional[int]:
    """Resolve ARM9 SaveBlock base address by subtracting party offset from GameData."""
    game_data_offset = IREJ_REV1_GAME_DATA - MAIN_RAM_START
    if game_data_offset < 0 or game_data_offset + GAME_DATA_PARTY_PTR + 4 > len(ram):
        return None
    party_ptr = _u32(ram, game_data_offset + GAME_DATA_PARTY_PTR)
    if not _pointer(party_ptr):
        return None
    base = party_ptr - SAVE_BLOCK_PARTY_OFFSET
    if not _pointer(base):
        return None
    return base


def decode_box_names(ram: bytes, save_base: int) -> List[str]:
    """Decode the 24 user-visible box names from SaveBlock + 0x00000."""
    names_offset = save_base - MAIN_RAM_START + SAVE_BLOCK_BOX_NAMES_OFFSET
    names = []
    for b in range(BOX_COUNT):
        slot_addr = names_offset + b * 40
        if slot_addr + 40 > len(ram):
            names.append(f"Box {b + 1}")
            continue
        name_str = _decode_ucs2_string(ram[slot_addr + 4:slot_addr + 40], 17)
        names.append(name_str if name_str else f"Box {b + 1}")
    return names


def decode_single_box(ram: bytes, box_id: int, dex: Optional[DexStore] = None) -> Optional[Dict[str, Any]]:
    """Decode all 30 slots for a single box (1..24)."""
    if not (1 <= box_id <= BOX_COUNT):
        return None
    save_base = resolve_save_block_base(ram)
    if save_base is None:
        return None

    names = decode_box_names(ram, save_base)
    box_name = names[box_id - 1] if box_id - 1 < len(names) else f"Box {box_id}"

    box_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + (box_id - 1) * BOX_STRIDE
    box_ram_off = box_addr - MAIN_RAM_START
    if box_ram_off < 0 or box_ram_off + BOX_DATA_SIZE > len(ram):
        return None

    box_raw = ram[box_ram_off:box_ram_off + BOX_DATA_SIZE]
    slots = []
    box_count = 0
    for s_idx in range(BOX_CAPACITY):
        slot_id = s_idx + 1
        slot_raw = box_raw[s_idx * BOX_POKEMON_SIZE:(s_idx + 1) * BOX_POKEMON_SIZE]
        pokemon = decode_box_pokemon(slot_raw, dex)
        if pokemon:
            pokemon["slot"] = slot_id
            pokemon["box_id"] = box_id
            slots.append(pokemon)
            box_count += 1
        else:
            slots.append({
                "slot": slot_id,
                "box_id": box_id,
                "empty": True,
            })

    return {
        "box_id": box_id,
        "name": box_name,
        "count": box_count,
        "capacity": BOX_CAPACITY,
        "free_slots": BOX_CAPACITY - box_count,
        "slots": slots,
    }


def decode_all_boxes(ram: bytes, dex: Optional[DexStore] = None) -> Dict[str, Any]:
    """Decode all 24 PC Boxes and return a structured warehouse overview."""
    save_base = resolve_save_block_base(ram)
    if save_base is None:
        return {
            "status": "unresolved",
            "reason": "Could not resolve SaveBlock base address from GameData.",
            "total_stored": 0,
            "total_capacity": BOX_COUNT * BOX_CAPACITY,
            "boxes": [],
        }

    box_names = decode_box_names(ram, save_base)
    boxes = []
    total_stored = 0

    for b_idx in range(BOX_COUNT):
        box_id = b_idx + 1
        box_addr = save_base + SAVE_BLOCK_BOXES_OFFSET + b_idx * BOX_STRIDE
        box_ram_off = box_addr - MAIN_RAM_START
        if box_ram_off < 0 or box_ram_off + BOX_DATA_SIZE > len(ram):
            boxes.append({
                "box_id": box_id,
                "name": box_names[b_idx],
                "count": 0,
                "capacity": BOX_CAPACITY,
                "free_slots": BOX_CAPACITY,
                "slots": [],
            })
            continue

        box_raw = ram[box_ram_off:box_ram_off + BOX_DATA_SIZE]
        slots = []
        box_count = 0
        for s_idx in range(BOX_CAPACITY):
            slot_id = s_idx + 1
            slot_raw = box_raw[s_idx * BOX_POKEMON_SIZE:(s_idx + 1) * BOX_POKEMON_SIZE]
            pokemon = decode_box_pokemon(slot_raw, dex)
            if pokemon:
                pokemon["slot"] = slot_id
                pokemon["box_id"] = box_id
                slots.append(pokemon)
                box_count += 1

        total_stored += box_count
        boxes.append({
            "box_id": box_id,
            "name": box_names[b_idx],
            "count": box_count,
            "capacity": BOX_CAPACITY,
            "free_slots": BOX_CAPACITY - box_count,
            "slots": slots,
        })

    return {
        "status": "ready",
        "save_block_base": f"0x{save_base:08X}",
        "total_stored": total_stored,
        "total_capacity": BOX_COUNT * BOX_CAPACITY,
        "total_free": (BOX_COUNT * BOX_CAPACITY) - total_stored,
        "boxes": boxes,
    }


def search_pc_storage(
    boxes_data: Dict[str, Any],
    *,
    species_id: Optional[int] = None,
    species_name: Optional[str] = None,
    ability_id: Optional[int] = None,
    held_item_id: Optional[int] = None,
    move_id: Optional[int] = None,
    is_shiny: Optional[bool] = None,
    type_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Filter stored PC Pokemons across all 24 boxes by arbitrary criteria."""
    results = []
    for box in boxes_data.get("boxes", []):
        for mon in box.get("slots", []):
            if mon.get("empty"):
                continue
            if species_id is not None and mon.get("species") != species_id:
                continue
            if species_name is not None:
                q = species_name.lower()
                if q not in mon.get("species_name", "").lower() and q not in mon.get("species_name_en", "").lower():
                    continue
            if ability_id is not None and mon.get("ability_id") != ability_id:
                continue
            if held_item_id is not None and mon.get("held_item_id") != held_item_id:
                continue
            if move_id is not None:
                if not any(m.get("move_id") == move_id for m in mon.get("moves", [])):
                    continue
            if is_shiny is not None and mon.get("is_shiny") != is_shiny:
                continue
            if type_name is not None:
                q = type_name.lower()
                if not any(q in t.get("name", "").lower() or q in t.get("name_en", "").lower() for t in mon.get("types", [])):
                    continue
            results.append(mon)
    return results
