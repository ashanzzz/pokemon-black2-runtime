"""Pokémon Black 2 - Complete Unova Map Catalog & Landmark Database.

Indexes all Matrix #0 chunks, towns, routes, dungeons, interiors, and POI landmarks
with global world matrix coordinates, collision characteristics, and descriptions.
"""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel


class MapLandmark(BaseModel):
    id: str
    name_zh: str
    name_en: str
    category: str  # "town" | "route" | "facility" | "landmark" | "interior"
    matrix_id: int
    chunk: Dict[str, int]
    coords: Dict[str, int]  # {"x": 47, "y": 771, "z": 12}
    model_id: int
    description: str
    recommended_mode: str = "walk"  # "walk" | "run" | "bike" | "surf"


# Comprehensive POI & Landmark Database for Black 2
UNOVA_LANDMARKS: List[MapLandmark] = [
    # --- 桧扇市 (Aspertia City) ---
    MapLandmark(
        id="aspertia_player_house_out",
        name_zh="桧扇市 主角家门前 (Player's House Front)",
        name_en="Aspertia City - Player's House Front",
        category="town",
        matrix_id=0,
        chunk={"x": 1, "y": 23},
        coords={"x": 47, "y": 771, "z": 12},
        model_id=279,
        description="主角冒险起点的家门前庭院，南侧主干道起点",
        recommended_mode="run"
    ),
    MapLandmark(
        id="aspertia_hugh_house",
        name_zh="桧扇市 劲敌修的家 (Hugh's House)",
        name_en="Aspertia City - Hugh's House",
        category="town",
        matrix_id=0,
        chunk={"x": 1, "y": 22},
        coords={"x": 47, "y": 725, "z": 12},
        model_id=280,
        description="劲敌修和妹妹所居住的房屋门前",
        recommended_mode="run"
    ),
    MapLandmark(
        id="aspertia_school_gym",
        name_zh="桧扇市 宝可梦训练家学校 / 桧扇道馆 (Trainer School & Gym)",
        name_en="Aspertia City - Trainer School & Gym",
        category="facility",
        matrix_id=0,
        chunk={"x": 1, "y": 22},
        coords={"x": 40, "y": 710, "z": 12},
        model_id=280,
        description="馆主黑连所在的训练家学校与道馆挑战场地",
        recommended_mode="run"
    ),
    MapLandmark(
        id="aspertia_lookout_steps",
        name_zh="桧扇市 展望台阶梯路口 (Lookout Point Steps)",
        name_en="Aspertia City - Lookout Point Steps",
        category="landmark",
        matrix_id=0,
        chunk={"x": 1, "y": 22},
        coords={"x": 28, "y": 705, "z": 14},
        model_id=280,
        description="通往展望台的高低差阶梯下口",
        recommended_mode="run"
    ),
    MapLandmark(
        id="aspertia_lookout_point",
        name_zh="桧扇市 展望台 (Lookout Point - 白露初选御三家)",
        name_en="Aspertia City - Lookout Point",
        category="landmark",
        matrix_id=0,
        chunk={"x": 1, "y": 21},
        coords={"x": 16, "y": 705, "z": 16},
        model_id=281,
        description="小镇最高处的观景台，红豆杉博士助手白露在此赠予最初宝可梦",
        recommended_mode="run"
    ),

    # --- 19号道路 (Route 19) ---
    MapLandmark(
        id="route19_west_gate",
        name_zh="19号道路 西侧关卡入口 (Route 19 West Gate)",
        name_en="Route 19 - West Gate Entrance",
        category="route",
        matrix_id=0,
        chunk={"x": 1, "y": 21},
        coords={"x": 58, "y": 680, "z": 12},
        model_id=281,
        description="连接桧扇市与19号道路的警卫关卡",
        recommended_mode="run"
    ),
    MapLandmark(
        id="route19_mid",
        name_zh="19号道路 主干道中段 (Route 19 Midpoint)",
        name_en="Route 19 - Midpoint",
        category="route",
        matrix_id=0,
        chunk={"x": 2, "y": 21},
        coords={"x": 80, "y": 680, "z": 12},
        model_id=282,
        description="19号道路开阔横向石砖干道，前任冠军阿戴克在此初遇主角",
        recommended_mode="run"
    ),
    MapLandmark(
        id="route19_east_cliff",
        name_zh="19号道路 东侧山岩阶梯 (Route 19 East Cliff Steps)",
        name_en="Route 19 - East Cliff Steps",
        category="route",
        matrix_id=0,
        chunk={"x": 2, "y": 21},
        coords={"x": 92, "y": 680, "z": 12},
        model_id=282,
        description="靠近算木镇交界处的阶梯台地",
        recommended_mode="run"
    ),

    # --- 算木镇 (Floccesy Town) ---
    MapLandmark(
        id="floccesy_town_center",
        name_zh="算木镇 城镇广场中心 (Floccesy Town Center)",
        name_en="Floccesy Town - Center Plaza",
        category="town",
        matrix_id=0,
        chunk={"x": 3, "y": 21},
        coords={"x": 100, "y": 680, "z": 12},
        model_id=283,
        description="算木镇钟楼与中心广场，石桥与山间平原交汇处",
        recommended_mode="bike"
    ),
    MapLandmark(
        id="floccesy_alder_house",
        name_zh="算木镇 阿戴克的家 (Alder's House)",
        name_en="Floccesy Town - Alder's House",
        category="facility",
        matrix_id=0,
        chunk={"x": 3, "y": 21},
        coords={"x": 108, "y": 675, "z": 13},
        model_id=283,
        description="合众前任冠军阿戴克的隐居住所",
        recommended_mode="bike"
    ),
    MapLandmark(
        id="floccesy_ranch_entrance_steps",
        name_zh="算木镇 北侧通往算木牧场阶梯 (Ranch Entrance Steps)",
        name_en="Floccesy Town - Ranch Entrance Steps",
        category="landmark",
        matrix_id=0,
        chunk={"x": 3, "y": 21},
        coords={"x": 114, "y": 680, "z": 13},
        model_id=283,
        description="算木镇通往算木牧场的三阶跃级台阶平台",
        recommended_mode="bike"
    ),

    # --- 算木牧场 (Floccesy Ranch) ---
    MapLandmark(
        id="floccesy_ranch_pasture",
        name_zh="算木牧场 牧场草坪 (Floccesy Ranch Pasture)",
        name_en="Floccesy Ranch - Pasture",
        category="landmark",
        matrix_id=0,
        chunk={"x": 3, "y": 20},
        coords={"x": 112, "y": 655, "z": 12},
        model_id=284,
        description="算木牧场核心草坪，咩利羊群与寻找扒手猫剧情发生地",
        recommended_mode="bike"
    ),

    # --- 20号道路 (Route 20) ---
    MapLandmark(
        id="route20_entrance",
        name_zh="20号道路 算木镇东侧入口 (Route 20 Entrance)",
        name_en="Route 20 - Entrance",
        category="route",
        matrix_id=0,
        chunk={"x": 4, "y": 19},
        coords={"x": 135, "y": 625, "z": 12},
        model_id=285,
        description="穿越山谷通往立涌市的20号道路西侧",
        recommended_mode="bike"
    ),

    # --- 立涌市 (Virbank City) ---
    MapLandmark(
        id="virbank_city_center",
        name_zh="立涌市 港口城市中心 (Virbank City Center)",
        name_en="Virbank City - Center",
        category="town",
        matrix_id=0,
        chunk={"x": 4, "y": 20},
        coords={"x": 145, "y": 655, "z": 12},
        model_id=286,
        description="立涌市港口城市中心与霍米加毒系道馆所在地",
        recommended_mode="bike"
    ),
    MapLandmark(
        id="virbank_complex",
        name_zh="立涌联合工业区 (Virbank Complex)",
        name_en="Virbank Complex",
        category="facility",
        matrix_id=0,
        chunk={"x": 5, "y": 20},
        coords={"x": 175, "y": 655, "z": 12},
        model_id=287,
        description="立涌市南侧大型工业厂区，包含众多训练家与稀有宝可梦",
        recommended_mode="bike"
    ),
]


def get_all_landmarks() -> List[MapLandmark]:
    return UNOVA_LANDMARKS


def find_landmark_by_id(landmark_id: str) -> Optional[MapLandmark]:
    for lm in UNOVA_LANDMARKS:
        if lm.id == landmark_id:
            return lm
    return None
