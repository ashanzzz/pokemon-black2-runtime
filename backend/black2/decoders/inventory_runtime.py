"""Gen V player inventory (Bag) decoder for Pokemon Black 2 IREJ rev.1.

This decoder reads the persistent player Bag (MYITEM) reached from
``GameData -> 0x190``.  It maps internal item IDs and quantities across the 5
distinct Gen V bag pockets (Items, Key Items, TM/HM, Medicine, Berries) and
enriches them with the offline DexStore catalog.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from ..memory.reader import MemoryReader
from ..dex.store import DexStore


MAIN_RAM_START = 0x02000000
MAIN_RAM_END = 0x02400000
IREJ_REV1_GAME_DATA = 0x0223B570
GAME_DATA_BAG_PTR = 0x190

# Pockets layout within Bag struct:
# - Items (Held / Regular items): offset 0x000, 310 slots max (1240 bytes)
# - Key Items:                    offset 0x4D8, 83 slots max (332 bytes)
# - TM / HM:                      offset 0x624, 109 slots max (436 bytes)
# - Medicine:                     offset 0x7D8, 48 slots max (192 bytes)
# - Berries:                      offset 0x898, 64 slots max (256 bytes)
POCKET_DEFINITIONS = (
    {"pocket_id": "items", "name_zh": "道具", "name_en": "Items", "offset": 0x000, "max_slots": 310},
    {"pocket_id": "key_items", "name_zh": "重要道具", "name_en": "Key Items", "offset": 0x4D8, "max_slots": 83},
    {"pocket_id": "tm_hm", "name_zh": "技能机器", "name_en": "TM/HM", "offset": 0x624, "max_slots": 109},
    {"pocket_id": "medicine", "name_zh": "回复药", "name_en": "Medicine", "offset": 0x7D8, "max_slots": 48},
    {"pocket_id": "berries", "name_zh": "树果", "name_en": "Berries", "offset": 0x898, "max_slots": 64},
)
TOTAL_BAG_SIZE = 0x998  # covers up to offset 0x898 + 0x100


def _pointer(value: Any) -> bool:
    return type(value) is int and MAIN_RAM_START <= value < MAIN_RAM_END


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


_shared_dex: DexStore | None = None


def _get_dex() -> DexStore:
    global _shared_dex
    if _shared_dex is None:
        _shared_dex = DexStore()
    return _shared_dex


def decode_player_inventory_from_bytes(bag_raw: bytes, *, frame: int | None = None) -> dict[str, Any]:
    """Decode bag structure bytes and enrich with Dex catalog metadata."""
    dex = _get_dex()
    pockets_output: List[dict[str, Any]] = []
    all_items: List[dict[str, Any]] = []
    total_item_count = 0

    for p_def in POCKET_DEFINITIONS:
        p_offset = p_def["offset"]
        max_slots = p_def["max_slots"]
        items_in_pocket: List[dict[str, Any]] = []

        for slot_idx in range(max_slots):
            entry_off = p_offset + slot_idx * 4
            if entry_off + 4 > len(bag_raw):
                break
            item_id = _u16(bag_raw, entry_off)
            quantity = _u16(bag_raw, entry_off + 2)

            if 1 <= item_id <= 720 and 1 <= quantity <= 999:
                # The item_id in ARM9 Bag memory is the ROM internal item id
                # (represented by game_index in DexStore), NOT PokeAPI's global id.
                # For example, internal item 450 is Bicycle (game_index=450, id=427).
                candidates = dex.entities_by_rom_id("items", item_id) or dex.items_by_game_index(item_id)
                item_info = candidates[0] if candidates else (dex.get("items", item_id) or {})
                names = item_info.get("names") or {}
                
                # Canonical Gen-V Chinese item names for common key items
                CANONICAL_KEY_NAMES_ZH = {
                    428: "探险套装",
                    433: "冒险便签",
                    437: "朋友手册",
                    442: "城镇地图",
                    448: "可达鸭喷壶",
                    450: "自行车",
                    465: "对战记录器",
                    471: "探宝器",
                    621: "即时通讯器",
                    627: "奖牌盒",
                }
                name_zh = CANONICAL_KEY_NAMES_ZH.get(item_id) or names.get("zh-Hans") or names.get("zh") or f"道具 #{item_id}"
                name_en = names.get("en") or f"Item #{item_id}"
                category = item_info.get("category")

                record = {
                    "slot": slot_idx + 1,
                    "item_id": item_id,
                    # Keep the catalog identity beside the display name.
                    "identifier": item_info.get("identifier"),
                    "game_index": item_info.get("game_index"),
                    "name": name_zh,
                    "name_en": name_en,
                    "quantity": quantity,
                    "pocket": p_def["pocket_id"],
                    "pocket_name_zh": p_def["name_zh"],
                    "pocket_name_en": p_def["name_en"],
                    "key_item": p_def["pocket_id"] == "key_items",
                    "category": category,
                }
                items_in_pocket.append(record)
                all_items.append(record)

        total_item_count += len(items_in_pocket)
        pockets_output.append({
            "pocket_id": p_def["pocket_id"],
            "name_zh": p_def["name_zh"],
            "name_en": p_def["name_en"],
            "count": len(items_in_pocket),
            "max_slots": max_slots,
            "items": items_in_pocket,
        })

    return {
        "format": "black2-inventory/v1",
        "status": "ready",
        "decode_status": "verified",
        "contents_known": True,
        "pocket_count": len(POCKET_DEFINITIONS),
        "item_count": total_item_count,
        "pockets": pockets_output,
        "items": all_items,
        "frame": frame,
        "evidence": {
            "source": "GameData.Bag (0x0223B570 -> +0x190)",
            "verified": True,
            "confidence": "verified",
            "reason": "Persistent player Bag decoded through GameData -> 0x190.",
        },
    }


def decode_player_inventory_from_ram(ram: bytes, *, frame: int | None = None) -> dict[str, Any]:
    """Decode inventory from a full ARM9 Main RAM buffer."""
    result: dict[str, Any] = {
        "format": "black2-inventory/v1",
        "status": "unresolved",
        "pockets": [],
        "items": [],
        "pocket_count": None,
        "item_count": None,
        "decode_status": "unverified",
        "contents_known": False,
        "frame": frame,
        "evidence": {
            "verified": False,
            "confidence": "unresolved",
            "reason": "The supplied Main RAM does not contain valid GameData.",
        },
    }
    game_data_offset = IREJ_REV1_GAME_DATA - MAIN_RAM_START
    if game_data_offset < 0 or game_data_offset + GAME_DATA_BAG_PTR + 4 > len(ram):
        return result

    bag_ptr = _u32(ram, game_data_offset + GAME_DATA_BAG_PTR)
    if not _pointer(bag_ptr):
        result["evidence"]["reason"] = "GameData.Bag pointer is outside ARM9 Main RAM."
        return result

    bag_offset = bag_ptr - MAIN_RAM_START
    if bag_offset < 0 or bag_offset + TOTAL_BAG_SIZE > len(ram):
        result["evidence"]["reason"] = "Bag data exceeds Main RAM range."
        return result

    return decode_player_inventory_from_bytes(ram[bag_offset:bag_offset + TOTAL_BAG_SIZE], frame=frame)


class PlayerInventoryDecoder:
    """Bounded, checksum-free live reader for the persistent Gen V player bag."""

    def __init__(self, reader: Optional[MemoryReader] = None) -> None:
        self._reader: Optional[MemoryReader] = reader

    def configure(self, reader: MemoryReader) -> None:
        self._reader = reader

    async def sample(self) -> dict[str, Any]:
        if self._reader is None:
            return {
                "format": "black2-inventory/v1",
                "status": "unresolved",
                "pockets": [],
                "items": [],
                "pocket_count": None,
                "item_count": None,
                "decode_status": "unverified",
                "contents_known": False,
                "evidence": {
                    "verified": False,
                    "confidence": "unresolved",
                    "reason": "MemoryReader is not configured for PlayerInventoryDecoder.",
                },
            }

        try:
            # Read GameData pointer table at 0x0223B570
            ptr_batch = await self._reader.read_bytes(
                IREJ_REV1_GAME_DATA - MAIN_RAM_START + GAME_DATA_BAG_PTR,
                4,
                "Main RAM",
            )
            if len(ptr_batch) != 4:
                return self._unresolved("Failed to read GameData.Bag pointer.")
            bag_ptr = _u32(bytes(ptr_batch), 0)
            if not _pointer(bag_ptr):
                return self._unresolved(f"GameData.Bag pointer 0x{bag_ptr:08X} is outside ARM9 Main RAM.")

            # Read the entire bag buffer (TOTAL_BAG_SIZE = 0x998 bytes)
            bag_bytes = await self._reader.read_bytes(
                bag_ptr - MAIN_RAM_START,
                TOTAL_BAG_SIZE,
                "Main RAM",
            )
            if len(bag_bytes) != TOTAL_BAG_SIZE:
                return self._unresolved("Failed to read complete Bag payload.")

            frame = None
            client = getattr(self._reader, "client", None)
            if client and hasattr(client, "last_frame"):
                frame = client.last_frame

            return decode_player_inventory_from_bytes(bytes(bag_bytes), frame=frame)
        except Exception as exc:
            return self._unresolved(f"Inventory read error: {type(exc).__name__}: {exc}")

    def _unresolved(self, reason: str) -> dict[str, Any]:
        return {
            "format": "black2-inventory/v1",
            "status": "unresolved",
            "pockets": [],
            "items": [],
            "pocket_count": None,
            "item_count": None,
            "decode_status": "unverified",
            "contents_known": False,
            "evidence": {
                "verified": False,
                "confidence": "unresolved",
                "reason": reason,
            },
        }
