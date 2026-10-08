"""ROM-backed item catalog and field item resolver for Pokémon Black 2.

Decodes official item names (a/0/0/2[64]), descriptions (a/0/0/2[63]), base
attributes/prices (a/0/2/4), and field pickup / hidden item LUTs (a/0/7/8[30]).
Combines with offline DexStore for English identifiers and category semantics.
"""
from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..decoders.trainer_rom import _default_rom_path, decode_gen5_message_file
from ..dex.store import DexStore, dex_store
from ..world.rom_reader import NitroRom

_FULLWIDTH = {
    "\uff10": "0", "\uff11": "1", "\uff12": "2", "\uff13": "3", "\uff14": "4",
    "\uff15": "5", "\uff16": "6", "\uff17": "7", "\uff18": "8", "\uff19": "9",
    "\uff0d": "-", "\uff30": "P", "\uff37": "W", "\uff34": "T", "\uff2e": "N",
    "\uff27": "G", "\uff25": "E", "\uff21": "A", "\uff32": "R", "\uff28": "H",
    "\uff0c": "，", "\uff0e": "。", "\uff1f": "？", "\uff01": "！",
}

POCKET_NAMES_ZH = {
    "items": "道具",
    "medicine": "回复药",
    "tm_hm": "技能机器",
    "berries": "树果",
    "key_items": "重要道具",
}


import re

def _clean_text(value: str) -> str:
    def _repl(m: re.Match) -> str:
        code = int(m.group(1), 16)
        if 0xFF10 <= code <= 0xFF19:
            return chr(code - 0xFF10 + ord("0"))
        if 0xFF21 <= code <= 0xFF3A:
            return chr(code - 0xFF21 + ord("A"))
        if 0xFF41 <= code <= 0xFF5A:
            return chr(code - 0xFF41 + ord("a"))
        if code == 0xFF0C:
            return "，"
        if code == 0xFF0E:
            return "。"
        if code == 0xFF01:
            return "！"
        if code == 0xFF1F:
            return "？"
        return chr(code)

    value = re.sub(r"\\x([0-9A-Fa-f]{4})", _repl, value)
    for source, target in _FULLWIDTH.items():
        value = value.replace(source, target)
    return value.strip()


class RomItemCatalog:
    """Authoritative item catalog sourced directly from the Black 2 ROM."""

    def __init__(self, rom: NitroRom | None = None, rom_path: str | Path | None = None, store: DexStore | None = None):
        if rom is None:
            if hasattr(rom_path, "rom"):
                rom = rom_path.rom
            else:
                selected = str(rom_path) if rom_path else _default_rom_path()
                if not selected:
                    raise FileNotFoundError("Black 2 ROM path is not available for RomItemCatalog")
                rom = NitroRom.shared(selected)
        elif hasattr(rom, "rom"):
            rom = rom.rom
        self.rom = rom
        self.store = store or dex_store
        self._names: list[str] | None = None
        self._descriptions: list[str] | None = None
        self._field_lut: tuple[int, ...] | None = None
        self._item_data_cache: dict[int, dict[str, Any]] = {}

    def _ensure_loaded(self) -> None:
        if self._names is not None:
            return
        msg_archive = self.rom.archive("a/0/0/2")
        raw_names = decode_gen5_message_file(msg_archive.files[64])[0]
        self._names = [_clean_text(str(entry.get("text") or "")) for entry in raw_names]

        raw_descs = decode_gen5_message_file(msg_archive.files[63])[0]
        self._descriptions = [_clean_text(str(entry.get("text") or "")) for entry in raw_descs]

        scr_archive = self.rom.archive("a/0/7/8")
        lut_bytes = scr_archive.files[30]
        self._field_lut = struct.unpack(f"<{len(lut_bytes) // 2}H", lut_bytes)

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        if not isinstance(item_id, int) or item_id <= 0:
            return None
        self._ensure_loaded()
        assert self._names is not None
        assert self._descriptions is not None
        if item_id >= len(self._names):
            return None

        cached = self._item_data_cache.get(item_id)
        if cached is not None:
            return cached

        name_zh = self._names[item_id]
        desc_zh = self._descriptions[item_id] if item_id < len(self._descriptions) else ""

        # Price and attributes from a/0/2/4
        price = 0
        try:
            item_archive = self.rom.archive("a/0/2/4")
            if item_id < len(item_archive.files):
                raw = item_archive.files[item_id]
                if len(raw) >= 2:
                    price_base = struct.unpack("<H", raw[:2])[0]
                    price = price_base * 10
        except Exception:
            price = 0

        # DexStore metadata for pocket and identifier
        dex_meta = self._get_dex_meta(item_id)

        record = {
            "item_id": item_id,
            "name_zh": name_zh,
            "name_en": dex_meta.get("name_en") or name_zh,
            "identifier": dex_meta.get("identifier") or f"item_{item_id}",
            "price": price,
            "pocket_id": dex_meta.get("pocket_id") or "items",
            "pocket_zh": POCKET_NAMES_ZH.get(dex_meta.get("pocket_id") or "items", "道具"),
            "description_zh": desc_zh,
            "category": dex_meta.get("category"),
            "status": "resolved",
            "source": "ROM:a/0/0/2[64]+a/0/2/4",
        }
        self._item_data_cache[item_id] = record
        return record

    def _get_dex_meta(self, item_id: int) -> dict[str, Any]:
        try:
            items = self.store.items_by_game_index(item_id)
            if items:
                first = items[0]
                names = first.get("names") or {}
                pocket = first.get("pocket") or {}
                category = first.get("category") or {}
                pocket_id_raw = pocket.get("identifier") or "items"
                # Map PokeAPI pocket to Gen 5 standard pocket
                pocket_map = {
                    "pokeballs": "items",
                    "standard-balls": "items",
                    "medicine": "medicine",
                    "machines": "tm_hm",
                    "berries": "berries",
                    "key-items": "key_items",
                }
                return {
                    "name_en": names.get("en"),
                    "identifier": first.get("identifier"),
                    "pocket_id": pocket_map.get(pocket_id_raw, "items"),
                    "category": category.get("identifier"),
                }
        except Exception:
            pass
        return {}

    def resolve_field_item(self, script_id: int, flag_id: int = 0) -> dict[str, Any]:
        self._ensure_loaded()
        assert self._field_lut is not None
        index: int | None = None
        if 7000 <= script_id < 7000 + len(self._field_lut):
            index = script_id - 7000
        elif 1100 <= flag_id < 1100 + len(self._field_lut):
            index = flag_id - 1100

        if index is not None and 0 <= index < len(self._field_lut):
            item_id = self._field_lut[index]
            item = self.get_item(item_id)
            if item:
                return {
                    "item_id": item_id,
                    "name_zh": item["name_zh"],
                    "name_en": item["name_en"],
                    "count": 1,
                    "price": item["price"],
                    "pocket_id": item["pocket_id"],
                    "pocket_zh": item["pocket_zh"],
                    "description_zh": item["description_zh"],
                    "lut_index": index,
                    "status": "resolved",
                    "confidence": "verified_rom_lut",
                }

        return {
            "item_id": None,
            "name_zh": "道具球",
            "name_en": "Item Ball",
            "count": 1,
            "price": 0,
            "pocket_id": "items",
            "pocket_zh": "道具",
            "description_zh": "",
            "lut_index": index,
            "status": "candidate",
            "confidence": "candidate_registry",
        }

    def resolve_hidden_item(self, script_id: int, flag_id: int = 0) -> dict[str, Any]:
        self._ensure_loaded()
        assert self._field_lut is not None
        index: int | None = None
        if 8000 <= script_id < 8000 + len(self._field_lut):
            index = script_id - 8000
        elif 1100 <= flag_id < 1100 + len(self._field_lut):
            index = flag_id - 1100

        if index is not None and 0 <= index < len(self._field_lut):
            item_id = self._field_lut[index]
            item = self.get_item(item_id)
            if item:
                return {
                    "item_id": item_id,
                    "name_zh": item["name_zh"],
                    "name_en": item["name_en"],
                    "count": 1,
                    "price": item["price"],
                    "pocket_id": item["pocket_id"],
                    "pocket_zh": item["pocket_zh"],
                    "description_zh": item["description_zh"],
                    "lut_index": index,
                    "status": "resolved",
                    "confidence": "verified_rom_lut",
                }

        return {
            "item_id": None,
            "name_zh": "隐藏道具",
            "name_en": "Hidden Item",
            "count": 1,
            "price": 0,
            "pocket_id": "items",
            "pocket_zh": "道具",
            "description_zh": "",
            "lut_index": index,
            "status": "candidate",
            "confidence": "candidate_registry",
        }


_default_item_catalog: RomItemCatalog | None = None


def default_item_catalog(rom: Any = None) -> RomItemCatalog:
    global _default_item_catalog
    if _default_item_catalog is None or (rom is not None and _default_item_catalog.rom != getattr(rom, "rom", rom)):
        _default_item_catalog = RomItemCatalog(rom)
    return _default_item_catalog
