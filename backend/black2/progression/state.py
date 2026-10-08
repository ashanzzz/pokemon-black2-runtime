"""Evidence-bounded progression state for Black 2 IREJ rev.1.

Decodes:
1. GameData -> SaveControl -> SaveData -> Block 52 (Misc / TrainerCard block):
   - Offset +0x00 (u32): Player Money / Cash
   - Offset +0x04 (u8):  Badges 8-bit bitmask (0..7)
2. GameData + 0x1AC -> EventWorkSave:
   - u16 Works[431] (862 bytes)
   - u8 FlagBytes[383] (383 bytes)
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from ..memory.reader import MemoryReader
from .story_gate import evaluate_story_gates

GAME_DATA = 0x0223B570
SAVE_CONTROL_PTR_OFFSET = 0x000
EVENT_WORK_PTR_OFFSET = 0x1AC
PLAYER_SAVE_PTR_OFFSET = 0x158
BAG_PTR_OFFSET = 0x190
PARTY_PTR_OFFSET = 0x194

EVENT_WORK_U16_COUNT = 431
EVENT_WORK_FLAG_BYTE_COUNT = 383
EVENT_WORK_SIZE = EVENT_WORK_U16_COUNT * 2 + EVENT_WORK_FLAG_BYTE_COUNT + 1

# Block 52 in B2W2 SaveBlockAccessor is Misc (Badge Flags, Money, Trainer Sayings)
BLOCK_52_ID = 52
BLOCK_52_MAX_EXPECTED = 73

UNOVA_BADGES = [
    {
        "index": 0,
        "badge_id": 1,
        "name_zh": "基础徽章",
        "name_en": "Basic Badge",
        "leader_zh": "黑连",
        "leader_en": "Cheren",
        "type": "Normal",
        "city_zh": "桧扇市",
        "city_en": "Aspertia City",
    },
    {
        "index": 1,
        "badge_id": 2,
        "name_zh": "毒性徽章",
        "name_en": "Toxic Badge",
        "leader_zh": "霍米加",
        "leader_en": "Roxie",
        "type": "Poison",
        "city_zh": "立涌市",
        "city_en": "Virbank City",
    },
    {
        "index": 2,
        "badge_id": 3,
        "name_zh": "甲虫徽章",
        "name_en": "Insect Badge",
        "leader_zh": "亚堤",
        "leader_en": "Burgh",
        "type": "Bug",
        "city_zh": "飞云市",
        "city_en": "Castelia City",
    },
    {
        "index": 3,
        "badge_id": 4,
        "name_zh": "伏特徽章",
        "name_en": "Bolt Badge",
        "leader_zh": "小菊儿",
        "leader_en": "Elesa",
        "type": "Electric",
        "city_zh": "雷文市",
        "city_en": "Nimbasa City",
    },
    {
        "index": 4,
        "badge_id": 5,
        "name_zh": "震动徽章",
        "name_en": "Quake Badge",
        "leader_zh": "菊老大",
        "leader_en": "Clay",
        "type": "Ground",
        "city_zh": "帆巴市",
        "city_en": "Driftveil City",
    },
    {
        "index": 5,
        "badge_id": 6,
        "name_zh": "飞翼徽章",
        "name_en": "Jet Badge",
        "leader_zh": "风露",
        "leader_en": "Skyla",
        "type": "Flying",
        "city_zh": "吹寄市",
        "city_en": "Mistralton City",
    },
    {
        "index": 6,
        "badge_id": 7,
        "name_zh": "冰冻徽章",
        "name_en": "Freeze Badge",
        "leader_zh": "夏卡",
        "leader_en": "Drayden",
        "type": "Dragon",
        "city_zh": "双龙市",
        "city_en": "Opelucid City",
    },
    {
        "index": 7,
        "badge_id": 8,
        "name_zh": "海浪徽章",
        "name_en": "Wave Badge",
        "leader_zh": "西子伊",
        "leader_en": "Marlon",
        "type": "Water",
        "city_zh": "青海波市",
        "city_en": "Humilau City",
    },
]

SYMBOL_TARGETS = {
    "isBadgeObtained": 0x0200C97D,
    "addBadge": 0x0200C991,
    "getBadgeCount": 0x0200C9A1,
    "getCash": 0x0200C9BD,
    "addCashToTotal": 0x0200C9C1,
    "subCashFromTotal": 0x0200C9E5,
    "SaveControl_GetBlockPtr": 0x02007449,
    "SaveData_GetBlock": 0x0203AA65,
    "EventWork_FlagGet": 0x020191D9,
    "EventWork_GetFlagBytePtr": 0x02019279,
    "GameData_GetEventWork": 0x02017395,
    "GameData_GetPlayerState": 0x020171F5,
}


def _u32(raw: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(raw):
        return 0
    return int.from_bytes(raw[offset:offset + 4], "little")


def _is_main_ram_pointer(value: int) -> bool:
    return isinstance(value, int) and 0x02000000 <= value < 0x02400000


def _nonzero_indices(raw: bytes) -> list[int]:
    return [index for index, value in enumerate(raw) if value]


def resolve_trainer_defeat_flag(script_id: int | None) -> int | None:
    """Resolve the B2W2 IREJ engine EventWork defeat flag from an NPC's script_id.

    B2W2 ARM9 event_trainer_eye.c / 0x02154998 & 0x02154A10 formula:
      - If 3000 <= script_id < 5000:
          flag_id = (script_id - 3000) + 0x5F0 (1520) = script_id - 1480
      - If script_id >= 5000:
          flag_id = (script_id - 5000) + 0x5F0 (1520) = script_id - 3480
      - If script_id < 3000:
          returns None (not a standard line-of-sight trainer script)
    """
    if script_id is None:
        return None
    try:
        sc = int(script_id)
    except (TypeError, ValueError):
        return None
    if 3000 <= sc < 5000:
        return sc - 1480
    elif sc >= 5000:
        return sc - 3480
    return None


def is_event_flag_set(flag_id: int | None, flag_bytes: bytes | None) -> bool:
    """Check if a 1-bit event flag is set in the 383-byte EventWork bitfield."""
    if flag_id is None or not flag_bytes:
        return False
    byte_idx = flag_id >> 3
    if byte_idx < 0 or byte_idx >= len(flag_bytes):
        return False
    bit_idx = flag_id & 7
    return bool((flag_bytes[byte_idx] >> bit_idx) & 1)


def event_flag_ids_set(flag_bytes: bytes | None) -> list[int]:
    """Return raw EventWork bit IDs that are set in the current live sample.

    These IDs are intentionally raw/semantic-neutral.  They are not story
    labels and must never be treated as a guessed quest meaning.
    """
    if not flag_bytes:
        return []
    return [
        byte_index * 8 + bit
        for byte_index, value in enumerate(flag_bytes)
        if value
        for bit in range(8)
        if value & (1 << bit)
    ]


def summarize_event_work(raw: bytes, *, address: int, frame: int | None = None) -> dict[str, Any]:
    works_size = EVENT_WORK_U16_COUNT * 2
    works = raw[:works_size]
    flag_bytes = raw[works_size:works_size + EVENT_WORK_FLAG_BYTE_COUNT]
    return {
        "status": "candidate" if len(raw) >= works_size else "unresolved",
        "address": f"0x{address:08X}",
        "size": len(raw),
        "expected_size": EVENT_WORK_SIZE,
        "works": {
            "count": min(len(works) // 2, EVENT_WORK_U16_COUNT),
            "nonzero_indices": [
                index for index in range(min(len(works) // 2, EVENT_WORK_U16_COUNT))
                if int.from_bytes(works[index * 2:index * 2 + 2], "little") != 0
            ],
        },
        "flag_bytes": {
            "count": len(flag_bytes),
            "nonzero_indices": _nonzero_indices(flag_bytes),
            "raw_hex": flag_bytes.hex() if flag_bytes else None,
            "set_flag_ids": event_flag_ids_set(flag_bytes),
            "sha256": hashlib.sha256(flag_bytes).hexdigest() if flag_bytes else None,
        },
        "frame": frame,
        "semantic_status": "raw EventWork bytes only; individual flag semantic IDs require paired probes",
    }


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


@dataclass
class ProgressionStateService:
    reader: MemoryReader | None = None
    runtime_provider: Any | None = None
    latest: dict[str, Any] | None = None
    _raw_flag_bytes: bytes | None = None
    _raw_works_u16: tuple[int, ...] | None = None

    def configure(self, reader: MemoryReader, runtime_provider: Any | None = None) -> None:
        self.reader = reader
        self.runtime_provider = runtime_provider

    async def _resolve_block52(self) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Traverse GameData -> SaveControl -> SaveData -> Block 52 descriptor."""
        chain_info: dict[str, Any] = {}
        if self.reader is None:
            return None, chain_info

        gd_bytes = bytes(await self.reader.read_bytes(GAME_DATA, 0x10))
        save_control = _u32(gd_bytes, SAVE_CONTROL_PTR_OFFSET)
        chain_info["save_control_ptr"] = f"0x{save_control:08X}" if _is_main_ram_pointer(save_control) else None
        if not _is_main_ram_pointer(save_control):
            return None, chain_info

        sc_bytes = bytes(await self.reader.read_bytes(save_control, 0x20))
        save_data = _u32(sc_bytes, 0x10)
        chain_info["save_data_ptr"] = f"0x{save_data:08X}" if _is_main_ram_pointer(save_data) else None
        if not _is_main_ram_pointer(save_data):
            return None, chain_info

        sd_bytes = bytes(await self.reader.read_bytes(save_data, 0x40))
        r5 = _u32(sd_bytes, 0x2C)
        base_buf = _u32(sd_bytes, 0x34)
        chain_info["block_table_ptr"] = f"0x{r5:08X}" if _is_main_ram_pointer(r5) else None
        chain_info["base_buf_ptr"] = f"0x{base_buf:08X}" if _is_main_ram_pointer(base_buf) else None
        if not _is_main_ram_pointer(r5) or not _is_main_ram_pointer(base_buf):
            return None, chain_info

        r5_bytes = bytes(await self.reader.read_bytes(r5, 0x20))
        max_blocks = _u32(r5_bytes, 0x04)
        table_ptr = _u32(r5_bytes, 0x14)
        chain_info["max_blocks"] = max_blocks
        chain_info["table_ptr"] = f"0x{table_ptr:08X}" if _is_main_ram_pointer(table_ptr) else None
        if not _is_main_ram_pointer(table_ptr) or max_blocks <= BLOCK_52_ID:
            return None, chain_info

        desc_bytes = bytes(await self.reader.read_bytes(table_ptr + BLOCK_52_ID * 12, 12))
        block_size = _u32(desc_bytes, 4)
        rel_offset = _u32(desc_bytes, 8)
        block52_addr = base_buf + rel_offset
        chain_info["block52_addr"] = f"0x{block52_addr:08X}"
        chain_info["block52_size"] = block_size

        b52_raw = bytes(await self.reader.read_bytes(block52_addr, min(block_size, 0xF0)))
        if len(b52_raw) < 0x08:
            return None, chain_info

        money = _u32(b52_raw, 0x00)
        badge_mask = b52_raw[0x04]

        badges_detail = []
        for badge_def in UNOVA_BADGES:
            idx = badge_def["index"]
            obtained = bool(badge_mask & (1 << idx))
            badges_detail.append({**badge_def, "obtained": obtained})

        badge_count = sum(1 for b in badges_detail if b["obtained"])
        next_target = next((b for b in badges_detail if not b["obtained"]), None)

        return {
            "address": f"0x{block52_addr:08X}",
            "size": block_size,
            "money": {
                "status": "verified",
                "amount": money,
                "formatted": f"${money:,}",
            },
            "badges": {
                "status": "verified",
                "count": badge_count,
                "mask": badge_mask,
                "mask_hex": f"0x{badge_mask:02X}",
                "individual": badges_detail,
                "next_target": next_target,
            },
        }, chain_info

    async def sample(self) -> dict[str, Any]:
        if self.reader is None:
            result = self.unresolved("Progression memory reader is not configured.")
            self.latest = result
            return result

        try:
            header = bytes(await self.reader.read_bytes(GAME_DATA, 0x1B0))
            event_work_ptr = _u32(header, EVENT_WORK_PTR_OFFSET)
            player_save_ptr = _u32(header, PLAYER_SAVE_PTR_OFFSET)
            bag_ptr = _u32(header, BAG_PTR_OFFSET)
            party_ptr = _u32(header, PARTY_PTR_OFFSET)

            frame = None
            if self.runtime_provider is not None:
                latest = getattr(self.runtime_provider, "latest", None)
                if isinstance(latest, dict):
                    frame = latest.get("frame")

            block52_decoded, chain_info = await self._resolve_block52()

            event_work_summary: dict[str, Any] = {
                "status": "unresolved", "reason": "EventWork pointer invalid",
            }
            if _is_main_ram_pointer(event_work_ptr):
                event_work_raw = bytes(await self.reader.read_bytes(event_work_ptr, EVENT_WORK_SIZE))
                works_size = EVENT_WORK_U16_COUNT * 2
                self._raw_works_u16 = tuple(_u16(event_work_raw, i * 2) for i in range(EVENT_WORK_U16_COUNT))
                self._raw_flag_bytes = event_work_raw[works_size:works_size + EVENT_WORK_FLAG_BYTE_COUNT]
                event_work_summary = summarize_event_work(event_work_raw, address=event_work_ptr, frame=frame)

            badges_info = (
                block52_decoded["badges"]
                if block52_decoded
                else {
                    "status": "unresolved",
                    "count": None,
                    "individual": [],
                    "reason": "Block 52 (Misc / TrainerCard) pointer chain is unresolved.",
                }
            )

            money_info = (
                block52_decoded["money"]
                if block52_decoded
                else {
                    "status": "unresolved",
                    "amount": None,
                    "reason": "Block 52 (Misc / TrainerCard) pointer chain is unresolved.",
                }
            )

            is_verified = (
                block52_decoded is not None
                and badges_info.get("status") == "verified"
                and money_info.get("status") == "verified"
            )

            evaluated_gates = []
            if is_verified:
                evaluated_gates = evaluate_story_gates(
                    badge_mask=badges_info.get("mask", 0),
                    badge_count=badges_info.get("count", 0),
                )

            result = {
                "format": "black2-progression-state/v1",
                "status": "verified" if is_verified else "partial",
                "confidence": "verified" if is_verified else "candidate",
                "contents_known": is_verified,
                "game_data": {
                    "address": f"0x{GAME_DATA:08X}",
                    "event_work_ptr": f"0x{event_work_ptr:08X}" if _is_main_ram_pointer(event_work_ptr) else None,
                    "player_save_ptr": f"0x{player_save_ptr:08X}" if _is_main_ram_pointer(player_save_ptr) else None,
                    "bag_ptr": f"0x{bag_ptr:08X}" if _is_main_ram_pointer(bag_ptr) else None,
                    "party_ptr": f"0x{party_ptr:08X}" if _is_main_ram_pointer(party_ptr) else None,
                    "block52_chain": chain_info,
                },
                "money": money_info,
                "badges": badges_info,
                "story_flags": {
                    "status": "unresolved",
                    "values": event_work_summary.get("flag_bytes", {}).get("set_flag_ids", []),
                    "semantic_status": "raw_bit_ids_only",
                    "reason": "EventWork raw flag bytes available; individual semantic flags being promoted incrementally.",
                },
                "event_work": event_work_summary,
                "gates": evaluated_gates,
                "reverse_engineering_targets": {
                    "symbols": {name: f"0x{address:08X}" for name, address in SYMBOL_TARGETS.items()},
                    "block52_verified": is_verified,
                },
                "evidence": {
                    "source": "GameData -> SaveControl -> SaveData -> Block 52; GameData + 0x1AC -> EventWorkSave",
                    "verified_pointer_chain": True,
                    "confidence": "verified" if is_verified else "candidate",
                    "frame": frame,
                    "writes_performed": False,
                },
            }
            self.latest = result
            return result
        except Exception as exc:
            result = self.unresolved(f"{type(exc).__name__}: {exc}")
            self.latest = result
            return result

    @staticmethod
    def unresolved(reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "format": "black2-progression-state/v1",
            "status": "unresolved",
            "confidence": "unresolved",
            "contents_known": False,
            "money": {"status": "unresolved", "amount": None},
            "badges": {"status": "unresolved", "count": None, "individual": []},
            "story_flags": {"status": "unresolved", "values": []},
            "gates": [],
            "reason": reason,
            **extra,
        }


    def get_flag_bytes(self) -> bytes | None:
        return self._raw_flag_bytes

    def get_works_u16(self) -> tuple[int, ...] | None:
        return self._raw_works_u16

    def get_event_var(self, var_id: int) -> int | None:
        if self._raw_works_u16 is None:
            return None
        idx = var_id - 0x4000 if var_id >= 0x4000 else var_id
        if 0 <= idx < len(self._raw_works_u16):
            return self._raw_works_u16[idx]
        return None

    def is_flag_set(self, flag_id: int) -> bool:
        return is_event_flag_set(flag_id, self._raw_flag_bytes)

    def is_trainer_defeated(self, script_id: int) -> bool:
        flag_id = resolve_trainer_defeat_flag(script_id)
        if flag_id is None:
            return False
        return self.is_flag_set(flag_id)


progression_state_service = ProgressionStateService()
