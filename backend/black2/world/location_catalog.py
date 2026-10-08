"""ROM-backed location and environment catalog for Gen-5 maps.

The location-name table is stored in the Chinese B2/W2 ROM message archive.
This module keeps the raw ZoneHeader IDs and exposes a stable label plus an
explicit environment classification; it never turns a guessed string into a
navigation fact.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..decoders.trainer_rom import decode_gen5_message_file


_FULLWIDTH = {
    "\uff10": "0", "\uff11": "1", "\uff12": "2", "\uff13": "3", "\uff14": "4",
    "\uff15": "5", "\uff16": "6", "\uff17": "7", "\uff18": "8", "\uff19": "9",
    "\uff0d": "-", "\uff30": "P", "\uff37": "W", "\uff34": "T", "\uff2e": "N",
    "\uff27": "G", "\uff25": "E", "\uff21": "A", "\uff32": "R",
}
_CAVE_KEYWORDS = ("洞穴", "穴", "山", "岩洞", "秘道", "遗迹", "地下", "之间", "森", "林", "殿", "空洞")


import re

def _clean(value: str) -> str:
    def _repl(m: re.Match) -> str:
        code = int(m.group(1), 16)
        if 0xFF10 <= code <= 0xFF19:
            return chr(code - 0xFF10 + ord("0"))
        if 0xFF21 <= code <= 0xFF3A:
            return chr(code - 0xFF21 + ord("A"))
        if 0xFF41 <= code <= 0xFF5A:
            return chr(code - 0xFF41 + ord("a"))
        return chr(code)

    value = re.sub(r"\\x([0-9A-Fa-f]{4})", _repl, value)
    for source, target in _FULLWIDTH.items():
        value = value.replace(source, target)
    return value.strip()


@dataclass(frozen=True)
class ZoneLabel:
    zone_id: int
    name_zh: str
    parent_name_zh: str | None
    environment: str
    environment_zh: str
    source: str
    confidence: str

    @property
    def display_name(self) -> str:
        full = f"{self.parent_name_zh} {self.name_zh}" if self.parent_name_zh and self.parent_name_zh != self.name_zh else self.name_zh
        return f"{full} [{self.environment_zh} / {self.environment}]"

    def as_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "name_zh": self.name_zh,
            "parent_name_zh": self.parent_name_zh,
            "display_name": self.display_name,
            "environment": self.environment,
            "environment_zh": self.environment_zh,
            "source": self.source,
            "confidence": self.confidence,
        }


class RomLocationCatalog:
    """Lazy, process-local decoder for the official ROM location-name table."""

    def __init__(self, rom: Any):
        self.rom = rom
        self._names: list[str] | None = None
        self._cache: dict[int, ZoneLabel] = {}

    def _load_names(self) -> list[str]:
        if self._names is not None:
            return self._names
        archive = self.rom.rom.archive("a/0/0/2")
        # Message file 109 is the location-name table for the supplied B2/W2 ROM.
        entries = decode_gen5_message_file(archive.files[109])[0]
        self._names = [_clean(str(entry.get("text") or "")) for entry in entries]
        return self._names

    def _name(self, location_name_id: int | None) -> str | None:
        if not isinstance(location_name_id, int):
            return None
        names = self._load_names()
        if 0 <= location_name_id < len(names) and names[location_name_id]:
            return names[location_name_id]
        return None

    def zone_label(self, zone_id: int) -> ZoneLabel:
        zid = int(zone_id)
        cached = self._cache.get(zid)
        if cached is not None:
            return cached
        zone = self.rom.zone(zid)
        name = self._name(getattr(zone, "location_name_id", None)) or f"Zone {zid}"
        parent_name = None
        parent_id = getattr(zone, "parent_zone_id", None)
        if isinstance(parent_id, int) and parent_id != zid:
            try:
                parent_name = self._name(getattr(self.rom.zone(parent_id), "location_name_id", None))
            except Exception:
                parent_name = None
        area = self.rom.area(int(zone.area_id))
        is_exterior = bool(getattr(area, "is_exterior", False))
        if zid in {443, 352, 401} or "中心" in name or "Center" in name:
            environment, environment_zh = "pokemon_center", "宝可梦中心"
        elif "大门" in name or "Gate" in name:
            environment, environment_zh = "gate", "通道大门"
        elif is_exterior:
            environment, environment_zh = "outdoor", "室外"
        elif any(word in name for word in _CAVE_KEYWORDS) or (parent_name and any(word in parent_name for word in _CAVE_KEYWORDS)) or int(getattr(zone, "map_type", -1)) == 0x10:
            environment, environment_zh = "cave", "洞穴内"
        else:
            environment, environment_zh = "interior", "室内"
        label = ZoneLabel(zid, name, parent_name, environment, environment_zh, "ROM:a/0/0/2[109]+ZoneHeader", "verified_rom_text")
        self._cache[zid] = label
        return label

    def display_name(self, zone_id: int) -> str:
        return self.zone_label(zone_id).display_name


__all__ = ["RomLocationCatalog", "ZoneLabel"]



KNOWN_POI_COORDINATES: dict[tuple[int, str], dict[str, Any]] = {
    (427, "home"): {"x": 47, "y": 1, "z": 762, "description": "桧扇市真正主角家门前待命格 (True Protagonist Home Doorstep -> Zone 428)"},
    (427, "civilian_house_se"): {"x": 59, "y": 1, "z": 724, "description": "桧扇市东南民居待命格 (Civilian House Doorstep -> Zone 433)"},
    (427, "pokemon_center"): {"x": 47, "y": 1, "z": 738, "description": "桧扇市宝可梦中心门前待命格 (Aspertia Pokemon Center Doorstep -> Zone 435)"},
    (427, "center"): {"x": 47, "y": 1, "z": 738, "description": "桧扇市宝可梦中心门前待命格 (Aspertia Pokemon Center Doorstep -> Zone 435)"},
    (427, "gym"): {"x": 39, "y": 1, "z": 739, "description": "桧扇道馆/训练家学校门前待命格 (Aspertia Gym Doorstep)"},
    (427, "rival"): {"x": 48, "y": 1, "z": 740, "description": "劲敌家门前待命格 (Rival Home Doorstep)"},
    (427, "lookout"): {"x": 52, "y": 1, "z": 711, "description": "桧扇市高台展望台入口待命格 (Aspertia Lookout Doorstep)"},
    (457, "north_gate"): {"x": 18, "y": 0, "z": 17, "description": "立涌工业园区北门跨区门垫 (Virbank Complex North Gate)"},
    (448, "gate"): {"x": 193, "y": 0, "z": 650, "description": "立涌市大门入口 (Virbank City Gate)"},
    (448, "gym"): {"x": 213, "y": 0, "z": 663, "description": "立涌道馆门前待命格 (Virbank Gym Doorstep)"},
    (448, "pokemon_center"): {"x": 210, "y": 0, "z": 649, "description": "立涌市宝可梦中心门前待命格 (Virbank Pokemon Center Doorstep)"},
    (448, "center"): {"x": 210, "y": 0, "z": 649, "description": "立涌市宝可梦中心门前待命格 (Virbank Pokemon Center Doorstep)"},
    (120, "gym"): {"x": 405, "y": 0, "z": 145, "description": "双龙道馆门前待命格 (Opelucid Gym Doorstep -> Zone 121)"},
    (120, "pokemon_center"): {"x": 425, "y": 0, "z": 173, "description": "双龙市宝可梦中心门前待命格 (Opelucid Pokemon Center Doorstep -> Zone 122)"},
    (120, "center"): {"x": 425, "y": 0, "z": 173, "description": "双龙市宝可梦中心门前待命格 (Opelucid Pokemon Center Doorstep -> Zone 122)"},
    (121, "leader"): {"x": 15, "y": 0, "z": 14, "description": "双龙道馆馆主夏卡面前对战席位 (Drayden Battle Position in Opelucid Gym)"},
}

def resolve_zone_poi(zone_id: int, poi: str, rom: Any | None = None) -> dict[str, Any] | None:
    """Resolve a semantic POI label to concrete grid coordinates in a zone."""
    zid = int(zone_id)
    norm_poi = poi.strip().lower().replace("-", "_").replace(" ", "_")

    # 1. Check known landmarks
    known = KNOWN_POI_COORDINATES.get((zid, norm_poi))
    if known:
        return {"zone_id": zid, "x": known["x"], "y": known.get("y", 0), "z": known["z"], "poi": norm_poi, "description": known["description"]}

    # 2. Check Pokemon Center via FastTravelService
    if norm_poi in {"pokemon_center", "center", "nurse", "heal", "pokecenter"}:
        try:
            from .fast_travel import FastTravelService
            fts = FastTravelService(rom)
            for dst in fts.get_destinations():
                if dst.get("zone_id") == zid:
                    grid = dst.get("landing_grid") or {}
                    return {
                        "zone_id": zid,
                        "x": grid.get("x"),
                        "y": grid.get("y", 0),
                        "z": grid.get("z"),
                        "poi": "pokemon_center",
                        "description": dst.get("landing_description") or "宝可梦中心门前待命格",
                    }
        except Exception:
            pass

    # 3. Check Gym
    if norm_poi in {"gym", "leader", "gym_leader"}:
        from .gym_catalog import GYM_DEFINITIONS
        for g in GYM_DEFINITIONS:
            if g.get("city_zone_id") == zid or g.get("zone_id") == zid:
                door = g.get("entrance_doorstep") or g.get("doorstep")
                if door:
                    return {"zone_id": zid, "x": door[0], "y": 0, "z": door[1], "poi": "gym", "description": f"{g.get('gym_name_zh')}入口待命格"}

    return None
