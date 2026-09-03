"""Pokémon Gen 5 (Black 2 / White 2) Map & Field State Decoder."""

from typing import Dict, Any, Optional
from pydantic import BaseModel


# Unova Map ID / Map Section to Location Name Dictionary (B2W2)
UNOVA_MAP_NAMES: Dict[int, str] = {
    0x0000: "未知区域 / 初始界面 (Intro / Title)",
    0x002A: "19号道路 (Route 19)",
    42: "19号道路 (Route 19)",
    0x015E: "桧扇市 (Aspertia City - 主角家乡)",
    0x0160: "桧扇市 主角家 (Player's House 1F)",
    0x0161: "桧扇市 主角房间 (Player's Room 2F)",
    0x0162: "桧扇市 劲敌家 (Hugh's House)",
    0x0165: "桧扇市 宝可梦训练家学校 (Trainer School)",
    0x0168: "桧扇道馆 (Aspertia Gym)",
    0x016A: "桧扇市 展望台 (Lookout Point)",
    0x0170: "19号道路 (Route 19)",
    0x0178: "算木镇 (Floccesy Town)",
    0x0179: "阿戴克的家 (Alder's House)",
    0x0180: "20号道路 (Route 20)",
    0x0188: "算木牧场 (Floccesy Ranch)",
    0x0190: "立涌市 (Virbank City)",
    0x0194: "立涌道馆 (Virbank Gym)",
    0x0198: "立涌联合工业区 (Virbank Complex)",
    0x01A0: "飞云市 (Castelia City)",
    0x01C0: "雷文市 (Nimbasa City)",
    0x01E0: "帆巴市 (Driftveil City)",
    0x0200: "吹寄市 (Mistralton City)",
    0x0220: "雪花市 (Icirrus City)",
    0x0240: "双龙市 (Opelucid City)",
    0x0260: "青海波市 (Humilau City)",
    0x0280: "宝可梦联盟 (Pokémon League)"
}


class FieldState(BaseModel):
    map_loaded: bool = False
    map_id: int = 0
    map_name: str = "未加载地图 (Title / Intro Screen)"
    player_x: int = 0
    player_y: int = 0
    player_z: int = 0
    facing: str = "South"  # "North", "South", "East", "West"
    movement_state: str = "Idle"  # "Idle", "Walking", "Running", "Biking", "Surfing", "Locked"
    is_indoor: bool = False
    wild_encounter_rate: int = 0


def get_map_name(map_id: int) -> str:
    return UNOVA_MAP_NAMES.get(map_id, f"合众地区未知区域 (Map ID: 0x{map_id:04X})")
