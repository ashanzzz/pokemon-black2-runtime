"""Read-only battle identity candidates from the Black 2 battle heap.

The battle-presence decoder is intentionally small and stable, but it cannot
answer the useful question "what am I fighting?" by itself.  This module adds
the next evidence layer that was recovered from paired battle dumps:

* GFL heap blocks tagged ``btl_pokeparam.c`` are scanned in a bounded window.
* the BattlePokeParam species field at payload ``+0x18`` is mapped through the
  local, versioned Black 2 Dex.
* duplicate side/display objects are retained as raw evidence and grouped for
  presentation; no screenshot text is used.

This is deliberately a *candidate* decoder.  The species field and block
layout are strong repeated observations, but side/active/level semantics and
trainer causality still require independent controls.  In particular, a
single opposing species is not promoted to "trainer" merely because a
trainer-looking sprite was visible on a screenshot.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from ..dex.store import DexStore, dex_store
from ..memory.reader import MemoryReader

STAT_ZH = {
    "attack": "攻击", "defense": "防御", "special-attack": "特攻",
    "special-defense": "特防", "speed": "速度", "accuracy": "命中率", "evasion": "闪避率"
}
AILMENT_ZH = {
    "paralysis": "麻痹", "sleep": "睡眠", "burn": "灼伤",
    "poison": "中毒", "toxic": "剧毒", "freeze": "冰冻", "confusion": "混乱"
}
CATEGORY_ZH = {
    "damage": "直接伤害", "net-good-stats": "能力升降", "whole-field-effect": "天气/全场环境",
    "field-effect": "场地效果", "ailment": "异常状态", "damage+ailment": "攻击附带异常",
    "damage+lower": "攻击附带削弱", "damage+raise": "攻击附带强化", "heal": "回复生命"
}
SPECIAL_TURN_MOVES = {
    91: "双回合技能：第1回合钻入地下（无法被大部分技能命中），第2回合破土攻击",
    19: "双回合技能：第1回合飞向高空（无法被大部分技能命中），第2回合俯冲攻击",
    291: "双回合技能：第1回合潜入水底（无法被大部分技能命中），第2回合攻击",
    340: "双回合技能：第1回合跳上高空（无法被大部分技能命中），第2回合攻击",
    76: "双回合蓄力：第1回合吸收阳光，第2回合发射（大晴天下无需蓄力即时发动）",
    130: "双回合蓄力：第1回合缩入壳中提升1级防御，第2回合猛撞攻击",
    143: "双回合蓄力：第1回合全身聚集神鸟之光，第2回合高暴击攻击",
    240: "天气技能：召唤大雨持续5回合（水系威力+50%，火系威力-50%，打雷/暴风必中）",
    241: "天气技能：召唤大晴天持续5回合（火系威力+50%，水系威力-50%，日光束免蓄力）",
    201: "天气技能：召唤沙暴持续5回合（岩/地/钢以外每回合扣除1/16 HP，岩系特防+50%）",
    258: "天气技能：召唤冰雹持续5回合（冰系以外每回合扣除1/16 HP，暴风雪必中）",
}

def _annotate_move_mechanics(move_id: int, m_info: dict[str, Any]) -> dict[str, Any]:
    dmg_class_dict = m_info.get("damage_class") if isinstance(m_info, dict) and isinstance(m_info.get("damage_class"), dict) else {}
    dmg_class = dmg_class_dict.get("names", {}).get("zh-Hans") or dmg_class_dict.get("identifier") or "物理"
    meta = m_info.get("meta") if isinstance(m_info, dict) and isinstance(m_info.get("meta"), dict) else {}
    cat_id = meta.get("category", {}).get("identifier", "damage") if isinstance(meta.get("category"), dict) else "damage"
    cat_zh = CATEGORY_ZH.get(cat_id, cat_id)

    stat_changes = []
    for sc in meta.get("stat_changes") or []:
        stat_id = sc.get("stat", {}).get("identifier")
        chg = sc.get("change", 0)
        s_name = STAT_ZH.get(stat_id, stat_id)
        stat_changes.append({"stat": s_name, "change": chg, "description": f"{'提升' if chg > 0 else '降低'}目标 {s_name} {abs(chg)} 个等级"})

    ailment_id = meta.get("ailment", {}).get("identifier") if isinstance(meta.get("ailment"), dict) else None
    ailment_zh = AILMENT_ZH.get(ailment_id)
    ailment_chance = meta.get("ailment_chance", 0)
    prio = m_info.get("priority", 0)
    special_mech = SPECIAL_TURN_MOVES.get(move_id)

    effects = []
    if special_mech:
        effects.append(special_mech)
    elif stat_changes:
        effects.extend([sc["description"] for sc in stat_changes])
    elif ailment_zh:
        effects.append(f"使目标陷入【{ailment_zh}】状态" + (f"（几率: {ailment_chance}%）" if ailment_chance > 0 else ""))
    else:
        effect_obj = m_info.get("effect") if isinstance(m_info, dict) and isinstance(m_info.get("effect"), dict) else {}
        short_obj = effect_obj.get("short") if isinstance(effect_obj.get("short"), dict) else {}
        desc = short_obj.get("zh-Hans") or short_obj.get("en")
        if desc:
            if "Inflicts regular damage" in desc:
                desc = "造成常规攻击伤害，无额外追加效果。"
            effects.append(desc)

    return {
        "damage_class": dmg_class,
        "category": cat_zh,
        "priority": prio,
        "stat_changes": stat_changes,
        "ailment": ailment_zh,
        "special_mechanics": special_mech,
        "effect_summary": "；".join(effects) if effects else "普通攻击伤害，无追加效果。"
    }



MAIN_RAM_START = 0x02000000
MAIN_RAM_END = 0x02400000

# The first live captures put the active battle allocations here.  Keep this
# bounded: a whole-RAM scan belongs to explicit evidence export, not to every
# /battle/state poll.
BATTLE_POKE_HEAP_START = 0x0225B000
BATTLE_POKE_HEAP_LENGTH = 0x2800
# A live trainer battle allocates its setup object and UTF-16 string buffer in
# this bounded Main-RAM window.  This is intentionally a second, small read:
# it is not a whole-RAM text search and is sampled only while battle presence
# is active.
BATTLE_TRAINER_TEXT_HEAP_START = 0x0224B000
BATTLE_TRAINER_TEXT_HEAP_LENGTH = 0x2000
GFL_HEAP_MAGIC = b"\x44\x55\x00\x00"
GFL_HEAP_HEADER_SIZE = 0x20
BATTLE_POKE_SOURCE_PREFIX = "btl_pokeparam.c"
BATTLE_SETUP_SOURCE_TAG = "btl_setup.c"
BATTLE_TEXT_SOURCE_TAG = "strbuf.c"
BATTLE_POKE_PAYLOAD_SIZE_MIN = 0x40
BATTLE_POKE_SPECIES_OFFSET = 0x18
BATTLE_POKE_LEVEL_OR_EXP_OFFSET = 0x14
BATTLE_POKE_MAX_HP_OFFSET = 0x1A
BATTLE_POKE_CURRENT_HP_OFFSET = 0x1C


def _u16(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 2 > len(data):
        return None
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data: bytes, offset: int) -> int | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    return int.from_bytes(data[offset:offset + 4], "little")


def _address_in_main_ram(address: int) -> bool:
    return MAIN_RAM_START <= address < MAIN_RAM_END


def _hex_range(data: bytes, offset: int, length: int) -> str:
    if offset < 0 or offset >= len(data):
        return ""
    return data[offset:min(len(data), offset + length)].hex()


def _source_tag(data: bytes, header_offset: int) -> tuple[str, bytes]:
    # The observed allocator stores the C source tag across the nominal
    # 0x20-byte header boundary.  Reading 16 bytes here is intentional and is
    # why the parser does not reuse the older 8-byte heap summary helper.
    raw = data[header_offset + 0x14:header_offset + 0x24]
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace"), raw


def _is_battle_text_codepoint(value: int) -> bool:
    """Return whether a UTF-16 word can be part of a Gen-5 name.

    The filter is deliberately conservative.  It accepts the Japanese/CJK
    ranges used by the Chinese ROM and printable ASCII for English names, but
    excludes Hangul and allocator/control words.  The ROM catalog match made
    later in the identity decoder is the semantic gate; this function only
    finds bounded string-buffer candidates.
    """
    return (
        0x20 <= value <= 0x7E
        or 0x3040 <= value <= 0x30FF
        or 0x3400 <= value <= 0x4DBF
        or 0x4E00 <= value <= 0x9FFF
    )


def decode_battle_trainer_text_candidates_from_ram(
    ram: bytes,
    *,
    base_address: int = BATTLE_TRAINER_TEXT_HEAP_START,
    frame: int | None = None,
    known_names: set[str] | None = None,
) -> dict[str, Any]:
    """Decode current battle trainer-name candidates from GFL ``strbuf.c``.

    Paired live dumps show a ``btl_setup.c`` allocation immediately before a
    sibling ``strbuf.c`` allocation.  The current trainer name is a UTF-16LE
    sequence terminated by ``0xFFFF`` inside that string buffer.  We retain
    allocator headers and absolute addresses as evidence and never use the
    text alone to claim a verified trainer identity.
    """
    unresolved = {
        "format": "black2-battle-trainer-text-evidence/v1",
        "status": "unresolved",
        "verified": False,
        "frame": frame,
        "scan": {
            "base_address": f"0x{base_address:08X}",
            "length": len(ram) if isinstance(ram, (bytes, bytearray, memoryview)) else 0,
            "source_tags": [BATTLE_SETUP_SOURCE_TAG, BATTLE_TEXT_SOURCE_TAG],
            "allocator_header_size": GFL_HEAP_HEADER_SIZE,
        },
        "blocks": [],
        "text_candidates": [],
        "catalog_matches": [],
        "reason": "No battle setup/string-buffer text candidate was found in the bounded RAM window.",
    }
    if not isinstance(ram, (bytes, bytearray, memoryview)):
        unresolved["reason"] = "RAM payload is not bytes-like."
        return unresolved
    data = bytes(ram)
    unresolved["scan"]["length"] = len(data)
    if not _address_in_main_ram(base_address) or base_address + len(data) > MAIN_RAM_END:
        unresolved["reason"] = "RAM window is outside the 4 MiB ARM9 Main RAM domain."
        return unresolved

    blocks: list[dict[str, Any]] = []
    cursor = 0
    while True:
        relative = data.find(GFL_HEAP_MAGIC, cursor)
        if relative < 0:
            break
        cursor = relative + 4
        if relative + GFL_HEAP_HEADER_SIZE > len(data):
            continue
        block_size = _u32(data, relative + 0x04)
        if block_size is None or block_size < 0x20 or block_size > 0x4000:
            continue
        payload_relative = relative + GFL_HEAP_HEADER_SIZE
        payload_end = payload_relative + block_size
        if payload_end > len(data):
            continue
        source_tag, raw_tag = _source_tag(data, relative)
        if source_tag not in {BATTLE_SETUP_SOURCE_TAG, BATTLE_TEXT_SOURCE_TAG}:
            continue
        blocks.append({
            "header_address": f"0x{base_address + relative:08X}",
            "payload_address": f"0x{base_address + payload_relative:08X}",
            "relative_header": relative,
            "relative_payload": payload_relative,
            "block_size": block_size,
            "source_tag": source_tag,
            "source_tag_raw_hex": raw_tag.hex(),
            "payload_prefix_hex": data[payload_relative:min(payload_end, payload_relative + 0x100)].hex(),
        })

    setup_headers = [
        block["relative_header"]
        for block in blocks
        if block["source_tag"] == BATTLE_SETUP_SOURCE_TAG
    ]
    candidates: list[dict[str, Any]] = []
    for block in blocks:
        if block["source_tag"] != BATTLE_TEXT_SOURCE_TAG:
            continue
        relative = int(block["relative_payload"])
        end = min(len(data), relative + int(block["block_size"]))
        preceding_setup = [
            setup for setup in setup_headers
            if 0 < relative - setup <= 0x120
        ]
        association = {
            "status": "candidate" if preceding_setup else "unresolved",
            "setup_header_addresses": [f"0x{base_address + setup:08X}" for setup in preceding_setup],
            "reason": (
                "strbuf.c is adjacent to a btl_setup.c allocation in the same bounded frame."
                if preceding_setup else
                "strbuf.c was found, but no nearby btl_setup.c sibling was observed."
            ),
        }
        for cursor_word in range(relative, max(relative, end - 1), 2):
            value = _u16(data, cursor_word)
            if value is None or not _is_battle_text_codepoint(value):
                continue
            previous = _u16(data, cursor_word - 2) if cursor_word >= relative + 2 else None
            if previous is not None and _is_battle_text_codepoint(previous):
                continue
            words: list[int] = []
            end_word = cursor_word
            while end_word + 2 <= end:
                word = _u16(data, end_word)
                if word is None:
                    break
                if word == 0xFFFF:
                    break
                if not _is_battle_text_codepoint(word) or len(words) >= 24:
                    words = []
                    break
                words.append(word)
                end_word += 2
            terminator = _u16(data, end_word) if end_word + 2 <= end else None
            if not words or terminator != 0xFFFF:
                continue
            text = "".join(chr(word) for word in words)
            if known_names is not None and text not in known_names:
                continue
            address = base_address + cursor_word
            candidates.append({
                "text": text,
                "raw_words": [f"0x{word:04X}" for word in words],
                "terminator": "0xFFFF",
                "address": f"0x{address:08X}",
                "offset_in_window": cursor_word,
                "frame": frame,
                "source": "battle_btl_setup.strbuf_ram/v1",
                "setup_association": association,
                "confidence": "candidate",
            })

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        key = (str(candidate["text"]), str(candidate["address"]))
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    unresolved["blocks"] = blocks
    unresolved["text_candidates"] = deduped
    if deduped:
        unresolved["status"] = "candidate"
        unresolved["reason"] = (
            "A UTF-16LE, 0xFFFF-terminated name candidate was found in a battle strbuf.c allocation; "
            "ROM trainer-catalog and same-frame opponent matching are still required."
        )
    return unresolved


def _species_summary(store: DexStore, species_id: int) -> dict[str, Any] | None:
    try:
        entity = store.get("pokemon", species_id)
    except Exception:
        entity = None
    if not isinstance(entity, dict):
        return None
    names = entity.get("names") if isinstance(entity.get("names"), dict) else {}
    # The local Chinese names in some generated Dex revisions can contain
    # replacement characters.  English is therefore the stable display name;
    # preserve the full names map as provenance for a later ROM-text fix.
    return {
        "id": int(entity.get("id", species_id)),
        "pokedex_number": entity.get("pokedex_number"),
        "identifier": entity.get("identifier"),
        "name": names.get("en") or entity.get("identifier"),
        "names": names,
        "source": "black2-offline-dex/v1",
    }


def decode_battle_poke_candidates_from_ram(
    ram: bytes,
    *,
    base_address: int = BATTLE_POKE_HEAP_START,
    frame: int | None = None,
    store: DexStore | None = None,
) -> dict[str, Any]:
    """Decode tagged BattlePokeParam candidates from one bounded RAM image.

    ``ram`` may be either the bounded live window or a complete Main RAM
    image.  ``base_address`` identifies the first byte.  The return value
    always includes the raw allocator/header evidence so a future decoder can
    re-check this result without trusting the semantic projection.
    """
    if not isinstance(ram, (bytes, bytearray, memoryview)):
        return {
            "format": "black2-battle-identity-evidence/v1",
            "status": "unresolved",
            "reason": "RAM payload is not bytes-like.",
            "blocks": [],
            "species_candidates": [],
            "frame": frame,
        }
    data = bytes(ram)
    if not _address_in_main_ram(base_address) or base_address + len(data) > MAIN_RAM_END:
        return {
            "format": "black2-battle-identity-evidence/v1",
            "status": "unresolved",
            "reason": "RAM window is outside the 4 MiB ARM9 Main RAM domain.",
            "blocks": [],
            "species_candidates": [],
            "frame": frame,
        }

    dex = store or dex_store
    blocks: list[dict[str, Any]] = []
    cursor = 0
    while True:
        relative = data.find(GFL_HEAP_MAGIC, cursor)
        if relative < 0:
            break
        cursor = relative + 4
        if relative + GFL_HEAP_HEADER_SIZE > len(data):
            continue
        block_size = _u32(data, relative + 0x04)
        if block_size is None or block_size < BATTLE_POKE_PAYLOAD_SIZE_MIN or block_size > 0x4000:
            continue
        payload_relative = relative + GFL_HEAP_HEADER_SIZE
        # The semantic fields are at payload + offsets below.  We only need a
        # small prefix, but require it to be present in this exact frame.
        required_end = payload_relative + BATTLE_POKE_CURRENT_HP_OFFSET + 2
        if required_end > len(data):
            continue
        source_tag, raw_tag = _source_tag(data, relative)
        if not source_tag.startswith(BATTLE_POKE_SOURCE_PREFIX):
            continue
        species_id = _u16(data, payload_relative + BATTLE_POKE_SPECIES_OFFSET)
        raw_level_or_exp = _u32(data, payload_relative + BATTLE_POKE_LEVEL_OR_EXP_OFFSET)
        max_hp = _u16(data, payload_relative + BATTLE_POKE_MAX_HP_OFFSET)
        current_hp = _u16(data, payload_relative + BATTLE_POKE_CURRENT_HP_OFFSET)
        if species_id is None or not 1 <= species_id <= 649:
            continue
        header_address = base_address + relative
        payload_address = base_address + payload_relative
        # Decode in-battle combat level from payload +0x24 and gender from +0x25 if present
        level = None
        gender = None
        ability = None
        if payload_relative + 0x26 <= len(data):
            lvl_val = _u16(data, payload_relative + 0x24)
            if lvl_val is not None and 1 <= (lvl_val & 0xFF) <= 100:
                level = lvl_val & 0xFF
            g_val = data[payload_relative + 0x25]
            gender = "male" if g_val == 0 else ("female" if g_val == 1 else "genderless")
            ab_id = _u16(data, payload_relative + 0x22)
            if ab_id and ab_id > 0:
                ab_info = dex.get("abilities", ab_id) if dex else {}
                ab_names = ab_info.get("names") if isinstance(ab_info, dict) and isinstance(ab_info.get("names"), dict) else {}
                ability = {
                    "id": ab_id,
                    "name": ab_names.get("zh-Hans") or ab_names.get("zh") or ab_info.get("name") or f"Ability #{ab_id}",
                    "name_en": ab_info.get("name") or ab_info.get("identifier"),
                }

        # Decode in-battle 5 stats from payload +0xFA..+0x104 and stat stages from +0x108..+0x10E if present
        stats = None
        stat_stages = None
        if payload_relative + 0x104 <= len(data):
            stats = {
                "attack": _u16(data, payload_relative + 0xFA),
                "defense": _u16(data, payload_relative + 0xFC),
                "special_attack": _u16(data, payload_relative + 0xFE),
                "special_defense": _u16(data, payload_relative + 0x100),
                "speed": _u16(data, payload_relative + 0x102),
            }
        if payload_relative + 0x10F <= len(data):
            stage_keys = ["attack", "defense", "special_attack", "special_defense", "speed", "accuracy", "evasion"]
            stat_stages = {k: data[payload_relative + 0x108 + idx] - 6 for idx, k in enumerate(stage_keys)}

        # Decode in-battle move slots & current/max PP from payload +0x110 (4 slots x 14B)
        moves = []
        if payload_relative + 0x148 <= len(data):
            for slot_idx in range(4):
                m_off = payload_relative + 0x110 + slot_idx * 14
                move_id = _u16(data, m_off)
                if not move_id or not (1 <= move_id <= 649):
                    continue
                cur_pp = data[m_off + 2]
                max_pp = data[m_off + 3]
                m_info = dex.get("moves", move_id) if dex else {}
                names = m_info.get("names") if isinstance(m_info, dict) and isinstance(m_info.get("names"), dict) else {}
                move_name = names.get("zh-Hans") or names.get("zh") or m_info.get("name") or f"Move #{move_id}"
                m_type_dict = m_info.get("type") if isinstance(m_info, dict) and isinstance(m_info.get("type"), dict) else {}
                m_type = m_type_dict.get("names", {}).get("zh-Hans") or m_type_dict.get("identifier") or "一般"
                mech = _annotate_move_mechanics(move_id, m_info)
                moves.append({
                    "slot": slot_idx + 1,
                    "move_id": move_id,
                    "name": move_name,
                    "name_en": m_info.get("name") or m_info.get("identifier"),
                    "type": m_type,
                    "damage_class": mech["damage_class"],
                    "category": mech["category"],
                    "power": m_info.get("power"),
                    "accuracy": m_info.get("accuracy"),
                    "current_pp": cur_pp,
                    "max_pp": max_pp,
                    "priority": mech["priority"],
                    "stat_changes": mech["stat_changes"],
                    "ailment": mech["ailment"],
                    "special_mechanics": mech["special_mechanics"],
                    "effect_summary": mech["effect_summary"],
                })

        record = {
            "header_address": f"0x{header_address:08X}",
            "payload_address": f"0x{payload_address:08X}",
            "block_size": block_size,
            "source_tag": source_tag,
            "source_tag_raw_hex": raw_tag.hex(),
            "species_id": species_id,
            "species": _species_summary(dex, species_id),
            "level": level,
            "level_status": "verified" if level is not None else "unresolved",
            "level_or_exp_raw": raw_level_or_exp,
            "level_or_exp_semantics": "experience points in +0x14, exact level in +0x24",
            "current_hp": current_hp,
            "max_hp": max_hp,
            "gender": gender,
            "ability": ability,
            "stats": stats,
            "stat_stages": stat_stages,
            "moves": moves,
            "raw": {
                "header_hex": _hex_range(data, relative, GFL_HEAP_HEADER_SIZE),
                "payload_prefix_hex": _hex_range(data, payload_relative, min(0x214, block_size)),
                "ram_window": {
                    "base_address": f"0x{base_address:08X}",
                    "length": len(data),
                    "frame": frame,
                },
            },
            "confidence": "candidate",
        }
        blocks.append(record)

    groups: dict[tuple[int, int | None, int | None], list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        groups[(int(block["species_id"]), block.get("current_hp"), block.get("max_hp"))].append(block)
    species_candidates: list[dict[str, Any]] = []
    for (species_id, current_hp, max_hp), rows in groups.items():
        first = rows[0]
        species_candidates.append({
            "species_id": species_id,
            "species": first.get("species"),
            "occurrences": len(rows),
            "current_hp": current_hp,
            "max_hp": max_hp,
            "level": first.get("level"),
            "level_status": first.get("level_status", "unresolved"),
            "gender": first.get("gender"),
            "ability": first.get("ability"),
            "stats": first.get("stats"),
            "stat_stages": first.get("stat_stages"),
            "moves": first.get("moves", []),
            "level_or_exp_raw_values": [row.get("level_or_exp_raw") for row in rows],
            "objects": [
                {
                    "header_address": row["header_address"],
                    "payload_address": row["payload_address"],
                    "source_tag": row["source_tag"],
                }
                for row in rows
            ],
            "confidence": "candidate",
        })
    species_candidates.sort(key=lambda row: (int(row["species_id"]), str(row["objects"][0]["payload_address"])))
    status = "candidate" if species_candidates else "unresolved"
    reason = (
        "Tagged btl_pokeparam.c objects were found and species IDs were mapped through the local Dex."
        if species_candidates else
        "No valid btl_pokeparam.c object with a Gen V species ID was found in the bounded battle heap window."
    )
    return {
        "format": "black2-battle-identity-evidence/v1",
        "status": status,
        "verified": False,
        "frame": frame,
        "scan": {
            "base_address": f"0x{base_address:08X}",
            "length": len(data),
            "source_tag": BATTLE_POKE_SOURCE_PREFIX,
            "species_offset": f"0x{BATTLE_POKE_SPECIES_OFFSET:X}",
            "allocator_header_size": GFL_HEAP_HEADER_SIZE,
        },
        "blocks": blocks,
        "species_candidates": species_candidates,
        "reason": reason,
        "limitations": [
            "Species names are RAM species IDs mapped through the local Dex; this is not screenshot OCR.",
            "Repeated objects are grouped but active-side/party order is not independently verified.",
            "The +0x14 value is preserved as raw level-or-experience evidence; level semantics are unresolved.",
            "Trainer ID/name/class require battle-causal script/NPC/ROM evidence and are not inferred from species count.",
        ],
    }


def _party_species_ids(player_party: dict[str, Any] | None) -> set[int]:
    if not isinstance(player_party, dict):
        return set()
    slots = player_party.get("slots")
    if not isinstance(slots, list):
        return set()
    values: set[int] = set()
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        try:
            species_id = int(slot.get("species"))
        except (TypeError, ValueError):
            continue
        if 1 <= species_id <= 649:
            values.add(species_id)
    return values


def _unresolved_trainer(reason: str) -> dict[str, Any]:
    return {
        "status": "unresolved",
        "kind": None,
        "trainer_id": None,
        "name": None,
        "class": None,
        "source": None,
        "reason": reason,
    }


def _trainer_id_from_context(context: dict[str, Any] | None) -> int | None:
    """Extract an explicitly bound trainer id without guessing from species.

    The semantic runtime may eventually publish the id under one of the
    documented keys below.  Keeping this parser strict is important: a map
    id, NPC script id, or an arbitrary UI value must not be mistaken for a
    TRData member number.
    """
    if not isinstance(context, dict):
        return None
    candidates: list[Any] = [
        context.get("battle_trainer_id"),
        context.get("trainer_id"),
    ]
    for key in ("battle_trainer", "trainer", "encounter"):
        nested = context.get(key)
        if isinstance(nested, dict):
            candidates.extend((nested.get("trainer_id"), nested.get("battle_trainer_id")))
    for value in candidates:
        if isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= parsed <= 813:
            return parsed
    return None


def _zone_id_from_context(context: dict[str, Any] | None) -> int | None:
    """Read a live/current or pre-battle zone without treating map ids as trainers."""
    if not isinstance(context, dict):
        return None
    sources: list[dict[str, Any]] = [context]
    for key in ("battle_overworld", "overworld", "player"):
        nested = context.get(key)
        if isinstance(nested, dict):
            sources.append(nested)
    for source in sources:
        value = source.get("zone_id")
        if isinstance(value, bool):
            continue
        try:
            zone_id = int(value)
        except (TypeError, ValueError):
            continue
        if zone_id >= 0:
            return zone_id
    return None


def _causal_context_from_context(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the bounded actor evidence captured at the battle boundary."""
    if not isinstance(context, dict):
        return None
    sources: list[dict[str, Any]] = [context]
    battle_overworld = context.get("battle_overworld")
    if isinstance(battle_overworld, dict):
        sources.insert(0, battle_overworld)
    for source in sources:
        causal = source.get("causal_context")
        if not isinstance(causal, dict):
            continue
        actor = causal.get("actor")
        if not isinstance(actor, dict):
            continue
        # Exclude player actor: UID 255, distance 0, or Nate/Rosa models 231/232
        if (
            actor.get("is_player") is True
            or actor.get("actor_uid") == 255
            or actor.get("model_id") in {231, 232}
            or actor.get("distance_manhattan") == 0
            or causal.get("distance_manhattan") == 0
        ):
            continue
        if not any(isinstance(actor.get(key), int) and actor.get(key) > 0 for key in ("script_id", "event_type")):
            continue
        return causal
    return None


class BattleIdentityDecoder:
    """Sample BattlePokeParam candidates only while presence is active."""

    def __init__(
        self,
        reader: MemoryReader | None = None,
        store: DexStore | None = None,
        trainer_catalog: Any | None = None,
    ):
        self.reader = reader
        self.store = store or dex_store
        self.trainer_catalog = trainer_catalog

    def configure(self, reader: MemoryReader | None) -> None:
        self.reader = reader

    def configure_trainer_catalog(self, catalog: Any | None) -> None:
        self.trainer_catalog = catalog

    @staticmethod
    def unresolved(reason: str = "Battle identity reader is not configured.", *, frame: int | None = None) -> dict[str, Any]:
        return {
            "format": "black2-battle-identity/v1",
            "status": "unresolved",
            "verified": False,
            "battle_kind": {
                "status": "unresolved",
                "value": None,
                "confidence": 0.0,
                "reason": "No trainer/wild causal evidence is bound to the battle frame.",
            },
            "trainer": _unresolved_trainer("Trainer ID/name/class decoder is not bound to a battle-causing script or NPC."),
            "player": {"status": "unresolved", "active": None, "party": []},
            "opponent": {"status": "unresolved", "active": None, "party": []},
            "evidence": {"status": "unresolved", "frame": frame, "blocks": [], "species_candidates": []},
            "reason": reason,
            "limitations": [
                "No battle kind or trainer identity is guessed when RAM evidence is absent.",
                "Read-only decoder; no input or RAM writes are performed.",
            ],
        }

    def _project_group(self, group: dict[str, Any]) -> dict[str, Any]:
        return {
            "species_id": group.get("species_id"),
            "species": group.get("species"),
            "current_hp": group.get("current_hp"),
            "max_hp": group.get("max_hp"),
            "level": group.get("level"),
            "level_status": group.get("level_status", "unresolved"),
            "gender": group.get("gender"),
            "ability": group.get("ability"),
            "stats": group.get("stats"),
            "stat_stages": group.get("stat_stages"),
            "moves": group.get("moves", []),
            "level_or_exp_raw_values": group.get("level_or_exp_raw_values", []),
            "object_count": group.get("occurrences", 0),
            "objects": group.get("objects", []),
            "confidence": "candidate",
        }

    async def sample(
        self,
        *,
        presence: dict[str, Any] | None = None,
        player_party: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        trainer_catalog: Any | None = None,
    ) -> dict[str, Any]:
        reader = self.reader
        if trainer_catalog is not None:
            self.trainer_catalog = trainer_catalog
        frame = presence.get("frame") if isinstance(presence, dict) else None
        if reader is None:
            return self.unresolved(frame=frame)
        if not isinstance(presence, dict) or presence.get("active") is not True:
            payload = self.unresolved(
                "Battle presence is not active on this sample; tagged blocks are not promoted from stale heap bytes.",
                frame=frame,
            )
            payload["evidence"]["presence"] = presence or {}
            return payload
        try:
            snapshot = await reader.read_batch_snapshot([{
                "id": "battle_poke_heap",
                "addr": BATTLE_POKE_HEAP_START,
                "length": BATTLE_POKE_HEAP_LENGTH,
            }, {
                "id": "battle_trainer_text_heap",
                "addr": BATTLE_TRAINER_TEXT_HEAP_START,
                "length": BATTLE_TRAINER_TEXT_HEAP_LENGTH,
            }])
        except Exception as exc:
            return self.unresolved(f"BattlePokeParam heap read failed: {type(exc).__name__}: {exc}", frame=frame)
        rows = snapshot.get("results") if isinstance(snapshot, dict) else None
        rows = rows if isinstance(rows, dict) else {}
        row = rows.get("battle_poke_heap")
        raw = bytes(int(value) & 0xFF for value in row.get("bytes", [])) if isinstance(row, dict) and isinstance(row.get("bytes"), list) else b""
        text_row = rows.get("battle_trainer_text_heap")
        trainer_text_raw = (
            bytes(int(value) & 0xFF for value in text_row.get("bytes", []))
            if isinstance(text_row, dict) and isinstance(text_row.get("bytes"), list)
            else b""
        )
        heap_frame = snapshot.get("frame") if isinstance(snapshot, dict) else frame
        evidence = decode_battle_poke_candidates_from_ram(
            raw,
            base_address=BATTLE_POKE_HEAP_START,
            frame=heap_frame,
            store=self.store,
        )
        trainer_text = decode_battle_trainer_text_candidates_from_ram(
            trainer_text_raw,
            base_address=BATTLE_TRAINER_TEXT_HEAP_START,
            frame=heap_frame,
        )
        groups = evidence.get("species_candidates") if isinstance(evidence.get("species_candidates"), list) else []
        player_ids = _party_species_ids(player_party)
        player_slots = player_party.get("slots", []) if isinstance(player_party, dict) else []
        player_lead_species = player_slots[0].get("species") if player_slots else None

        player_groups = [group for group in groups if group.get("species_id") in player_ids]
        opponent_groups = [group for group in groups if group.get("species_id") not in player_ids]

        if opponent_groups:
            # Clean separation: species not in player's party are opponents
            opponent_party = [self._project_group(g) for g in opponent_groups]
            opponent_active = opponent_party[0]
            player_active_group = next((g for g in player_groups if g.get("species_id") == player_lead_species), None) or (player_groups[0] if player_groups else None)
            player_active = self._project_group(player_active_group) if player_active_group else None
            side_status = "candidate"
        elif player_lead_species is not None and len(groups) >= 2:
            # Fallback when opponent species happens to be in player's party
            player_lead_group = next((g for g in groups if g.get("species_id") == player_lead_species), None)
            other_groups = [g for g in groups if g is not player_lead_group]
            if player_lead_group is not None and other_groups:
                player_active = self._project_group(player_lead_group)
                opponent_party = [self._project_group(g) for g in other_groups]
                opponent_active = opponent_party[0] if opponent_party else None
                side_status = "candidate"
            else:
                player_active = None
                opponent_party = []
                opponent_active = None
                side_status = "unresolved"
        else:
            player_active = None
            opponent_party = []
            opponent_active = None
            side_status = "unresolved"
        context = context if isinstance(context, dict) else {}
        causal_context = _causal_context_from_context(context)
        trainer_id = _trainer_id_from_context(context)
        trainer = _unresolved_trainer(
            "No battle-causing trainer script/NPC binding is currently available; species/zone alone cannot identify a trainer."
        )
        if causal_context is not None:
            # The actor binding proves a nearby scripted/event actor was
            # present at the battle transition.  It does not yet prove the
            # TRData member number, so keep the name/id unresolved.
            trainer["kind"] = "trainer"
            trainer["source"] = "runtime_actor_overlay_at_battle_transition"
            trainer["causal_context"] = causal_context
            trainer["reason"] = (
                "A same-scene scripted/event FieldActor was captured at the battle boundary; "
                "direct trainer-id/script trigger decoding is still required for the exact name."
            )
        trainer_party_match: dict[str, Any] | None = None
        zone_script_catalog: dict[str, Any] | None = None
        zone_trainer_candidates: list[dict[str, Any]] = []
        text_catalog_rows: list[dict[str, Any]] = []
        text_catalog_matches: list[dict[str, Any]] = []
        if self.trainer_catalog is not None:
            find_by_name = getattr(self.trainer_catalog, "find_trainer_ids_by_name", None)
            if callable(find_by_name):
                seen_text_matches: set[tuple[str, int]] = set()
                for text_candidate in trainer_text.get("text_candidates", []):
                    if not isinstance(text_candidate, dict):
                        continue
                    text = text_candidate.get("text")
                    if not isinstance(text, str) or not text:
                        continue
                    try:
                        trainer_ids = tuple(int(value) for value in find_by_name(text))
                    except Exception:
                        trainer_ids = ()
                    for candidate_id in trainer_ids:
                        key = (text, candidate_id)
                        if key in seen_text_matches:
                            continue
                        seen_text_matches.add(key)
                        try:
                            catalog_record = self.trainer_catalog.get(candidate_id)
                        except Exception:
                            catalog_record = None
                        if not isinstance(catalog_record, dict):
                            continue
                        catalog_species = sorted({
                            int(pokemon.get("species_id"))
                            for pokemon in catalog_record.get("party", [])
                            if isinstance(pokemon, dict) and isinstance(pokemon.get("species_id"), int)
                        })
                        ram_species = sorted({
                            int(group.get("species_id"))
                            for group in opponent_groups
                            if isinstance(group.get("species_id"), int)
                        })
                        matched_species = sorted(set(catalog_species).intersection(ram_species))
                        row = {
                            "text": text,
                            "trainer_id": candidate_id,
                            "name": catalog_record.get("name"),
                            "catalog_species_ids": catalog_species,
                            "ram_species_ids": ram_species,
                            "matched_species_ids": matched_species,
                            "text_evidence": text_candidate,
                            "catalog": catalog_record,
                        }
                        text_catalog_rows.append(row)
                        text_catalog_matches.append({
                            "text": text,
                            "trainer_id": candidate_id,
                            "name": catalog_record.get("name"),
                            "catalog_species_ids": catalog_species,
                            "ram_species_ids": ram_species,
                            "matched_species_ids": matched_species,
                            "text_evidence": text_candidate,
                        })
        trainer_text["catalog_matches"] = text_catalog_matches
        text_trainer_row = next(
            (
                row
                for row in text_catalog_rows
                if row.get("matched_species_ids") and isinstance(row.get("catalog"), dict)
            ),
            None,
        )
        matched_text_rows = [
            row for row in text_catalog_rows
            if row.get("matched_species_ids") and isinstance(row.get("catalog"), dict)
        ]
        # A name string can occur in multiple ROM records.  Only bind it when
        # exactly one catalog record also matches the live opponent species.
        if len(matched_text_rows) != 1:
            text_trainer_row = None
        if trainer_id is not None and self.trainer_catalog is not None:
            try:
                catalog_record = self.trainer_catalog.get(trainer_id)
            except Exception as exc:
                catalog_record = None
                trainer = _unresolved_trainer(
                    f"Trainer id {trainer_id} was supplied by runtime context, but ROM catalog lookup failed: {type(exc).__name__}: {exc}"
                )
            if isinstance(catalog_record, dict):
                trainer_class = catalog_record.get("trainer_class") if isinstance(catalog_record.get("trainer_class"), dict) else {}
                gym_candidate = trainer_class.get("is_gym_leader_class_candidate") is True
                catalog_species = [
                    int(row.get("species_id"))
                    for row in catalog_record.get("party", [])
                    if isinstance(row, dict) and isinstance(row.get("species_id"), int)
                ]
                ram_species = [
                    int(group.get("species_id"))
                    for group in opponent_groups
                    if isinstance(group.get("species_id"), int)
                ]
                matched = sorted(set(ram_species).intersection(catalog_species))
                trainer_party_match = {
                    "status": "candidate" if matched else "unresolved",
                    "ram_species_ids": sorted(set(ram_species)),
                    "catalog_species_ids": sorted(set(catalog_species)),
                    "matched_species_ids": matched,
                    "reason": "RAM opponent species intersect the ROM trainer party." if matched else "No opponent species intersection was observed in this frame.",
                }
                trainer = {
                    "status": "candidate",
                    "kind": "gym_trainer" if gym_candidate else "trainer",
                    "trainer_id": trainer_id,
                    "name": catalog_record.get("name"),
                    "class": trainer_class,
                    "source": "runtime_context_trainer_id->black2-trainer-catalog/v1",
                    "catalog": catalog_record,
                    "party_match": trainer_party_match,
                    "reason": "A trainer id was explicitly supplied, but same-frame causal binding still needs live script/NPC verification.",
                }
                if causal_context is not None:
                    trainer["causal_context"] = causal_context
        elif text_trainer_row is not None:
            catalog_record = text_trainer_row["catalog"]
            trainer_class = catalog_record.get("trainer_class") if isinstance(catalog_record.get("trainer_class"), dict) else {}
            gym_candidate = trainer_class.get("is_gym_leader_class_candidate") is True
            trainer_party_match = {
                "status": "candidate",
                "ram_species_ids": text_trainer_row.get("ram_species_ids", []),
                "catalog_species_ids": text_trainer_row.get("catalog_species_ids", []),
                "matched_species_ids": text_trainer_row.get("matched_species_ids", []),
                "reason": "Current btl_setup/strbuf trainer-name evidence maps to one ROM trainer whose party intersects the live opponent species.",
            }
            trainer = {
                "status": "candidate",
                "kind": "gym_trainer" if gym_candidate else "trainer",
                "trainer_id": text_trainer_row.get("trainer_id"),
                "name": catalog_record.get("name"),
                "class": trainer_class,
                "source": "battle_btl_setup.strbuf_ram->black2-trainer-catalog/v1",
                "catalog": catalog_record,
                "party_match": trainer_party_match,
                "runtime_text_evidence": text_trainer_row.get("text_evidence"),
                "reason": "The current battle setup string buffer contains the trainer name; exact ROM ID and party match are candidate evidence until an independent runtime trainer-id pointer is decoded.",
            }
            if causal_context is not None:
                trainer["causal_context"] = causal_context
        elif self.trainer_catalog is not None and opponent_groups:
            # When the runtime has no direct trainer-id pointer, use the
            # session's pre-battle zone as a bounded ROM-script index.  A
            # species match is still only a candidate: several trainers can
            # share a species, and a static script can be gated by flags.
            zone_id = _zone_id_from_context(context)
            if zone_id is not None:
                try:
                    zone_script_catalog = self.trainer_catalog.zone_trainer_candidates(zone_id)
                except Exception as exc:
                    zone_script_catalog = {
                        "status": "unresolved",
                        "zone_id": zone_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                raw_species = {
                    int(group.get("species_id"))
                    for group in opponent_groups
                    if isinstance(group.get("species_id"), int)
                }
                for candidate in (zone_script_catalog.get("candidates", []) if isinstance(zone_script_catalog, dict) else []):
                    if not isinstance(candidate, dict):
                        continue
                    catalog_record = candidate.get("catalog")
                    if not isinstance(catalog_record, dict):
                        continue
                    catalog_species = {
                        int(row.get("species_id"))
                        for row in catalog_record.get("party", [])
                        if isinstance(row, dict) and isinstance(row.get("species_id"), int)
                    }
                    matched = sorted(raw_species.intersection(catalog_species))
                    if not matched:
                        continue
                    trainer_class = catalog_record.get("trainer_class") if isinstance(catalog_record.get("trainer_class"), dict) else {}
                    candidate_view = {
                        "trainer_id": candidate.get("trainer_id"),
                        "raw_trainer_id": candidate.get("raw_trainer_id"),
                        "id_encoding": candidate.get("id_encoding"),
                        "opcode": candidate.get("opcode"),
                        "opcode_name": candidate.get("opcode_name"),
                        "script_indices": candidate.get("script_indices", []),
                        "catalog": catalog_record,
                        "matched_species_ids": matched,
                        "confidence": "zone_script_species_match",
                    }
                    zone_trainer_candidates.append(candidate_view)
                if len(zone_trainer_candidates) == 1:
                    candidate = zone_trainer_candidates[0]
                    catalog_record = candidate["catalog"]
                    trainer_class = catalog_record.get("trainer_class") if isinstance(catalog_record.get("trainer_class"), dict) else {}
                    gym_candidate = trainer_class.get("is_gym_leader_class_candidate") is True
                    trainer_party_match = {
                        "status": "candidate",
                        "ram_species_ids": sorted({int(group.get("species_id")) for group in opponent_groups if isinstance(group.get("species_id"), int)}),
                        "catalog_species_ids": sorted({int(row.get("species_id")) for row in catalog_record.get("party", []) if isinstance(row, dict) and isinstance(row.get("species_id"), int)}),
                        "matched_species_ids": candidate.get("matched_species_ids", []),
                        "reason": "Current/pre-battle zone script candidate and live opponent species intersect; flag/trigger execution still needs same-frame verification.",
                    }
                    trainer = {
                        "status": "candidate",
                        "kind": "gym_trainer" if gym_candidate else "trainer",
                        "trainer_id": candidate.get("trainer_id"),
                        "name": catalog_record.get("name"),
                        "class": trainer_class,
                        "source": "battle_overworld_zone->black2-zone-trainer-script-catalog/v1",
                        "catalog": catalog_record,
                        "script_binding": candidate,
                        "party_match": trainer_party_match,
                        "reason": "One ROM TrainerBattle candidate in the same session zone matches the live opponent species; not promoted to verified without a runtime trainer-id/trigger readback.",
                    }
                    if causal_context is not None:
                        trainer["causal_context"] = causal_context
                elif len(zone_trainer_candidates) > 1:
                    trainer = _unresolved_trainer(
                        "Multiple same-zone TrainerBattle candidates match the live opponent species; trainer name is intentionally not guessed."
                    )
                    trainer["candidates"] = zone_trainer_candidates
                    if causal_context is not None:
                        trainer["kind"] = "trainer"
                        trainer["source"] = "runtime_actor_overlay_at_battle_transition"
                        trainer["causal_context"] = causal_context
        if opponent_active is not None and trainer.get("status") == "candidate":
            battle_kind = {
                "status": "candidate",
                "value": trainer.get("kind"),
                "confidence": 0.75 if trainer_party_match and trainer_party_match.get("matched_species_ids") else 0.55,
                "reason": "Trainer catalog and opponent species are available; runtime trainer-id causal binding remains candidate.",
                "alternatives": ["wild"],
            }
        elif opponent_active is not None and causal_context is not None:
            battle_overworld = context.get("battle_overworld") if isinstance(context.get("battle_overworld"), dict) else {}
            has_dialogue = bool(battle_overworld.get("dialogue_text")) or battle_overworld.get("screen_type") == "DIALOGUE_ACTIVE"
            battle_kind = {
                "status": "candidate",
                "value": "trainer" if has_dialogue else "wild",
                "confidence": 0.90 if has_dialogue else 0.78,
                "reason": "A same-scene scripted/event FieldActor was captured at the battle transition; exact trainer ID/name is still unresolved.",
                "alternatives": ["wild", "gym_trainer"],
            }
        elif opponent_active is not None and zone_trainer_candidates:
            battle_kind = {
                "status": "unresolved",
                "value": None,
                "confidence": 0.0,
                "reason": "More than one same-zone ROM TrainerBattle candidate matches the live opponent species; battle kind/name is not guessed.",
                "alternatives": ["trainer", "gym_trainer", "wild"],
            }
        elif opponent_active is not None:
            zone_reason = (
                "No same-zone ROM TrainerBattle candidate matched the live opponent species; wild remains a candidate."
                if zone_script_catalog is not None and len(zone_trainer_candidates) == 0
                else "A non-player BattlePokeParam group is present, but no trainer-causing NPC/script evidence is bound to this frame; wild is a hypothesis only."
            )
            battle_kind = {
                "status": "candidate",
                "value": "wild",
                "confidence": 0.60 if zone_script_catalog is not None and len(zone_trainer_candidates) == 0 else 0.35,
                "reason": zone_reason,
                "alternatives": ["trainer", "gym_trainer"],
            }
        else:
            battle_kind = {
                "status": "unresolved",
                "value": None,
                "confidence": 0.0,
                "reason": "BattlePokeParam objects were found, but player-side matching did not uniquely separate the opponent.",
            }
        return {
            "format": "black2-battle-identity/v1",
            "status": "candidate" if evidence.get("status") == "candidate" else "unresolved",
            "verified": False,
            "battle_kind": battle_kind,
            "trainer": trainer,
            "player": {
                "status": side_status,
                "active": player_active,
                "party": [self._project_group(group) for group in player_groups],
                "party_species_ids_from_persistent_party": sorted(player_ids),
            },
            "opponent": {
                "status": side_status,
                "active": opponent_active,
                "party": opponent_party,
            },
            "evidence": evidence,
            "trainer_text": trainer_text,
            "context": context,
            "causal_context": causal_context,
            "zone_script_catalog": zone_script_catalog,
            "zone_trainer_candidates": zone_trainer_candidates,
            "trainer_id_hint": trainer_id,
            "reason": evidence.get("reason"),
            "limitations": evidence.get("limitations", []),
        }


__all__ = [
    "BATTLE_POKE_HEAP_START",
    "BATTLE_POKE_HEAP_LENGTH",
    "BATTLE_TRAINER_TEXT_HEAP_START",
    "BATTLE_TRAINER_TEXT_HEAP_LENGTH",
    "BattleIdentityDecoder",
    "decode_battle_poke_candidates_from_ram",
    "decode_battle_trainer_text_candidates_from_ram",
]
