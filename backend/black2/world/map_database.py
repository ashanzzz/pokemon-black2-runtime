"""Map Database & 3D Definition Provider for Pokémon Black 2 (Gen 5)."""

from typing import Dict, Any, List, Optional
from pydantic import BaseModel


class Map3DDefinition(BaseModel):
    map_id: int
    name_zh: str
    name_en: str
    map_type: str  # "indoor" | "outdoor" | "cave" | "gym" | "building"
    matrix_id: int
    matrix_dim: List[int] = [1, 1]  # [width, height]
    model_id: str
    default_camera: Dict[str, Any] = {
        "yaw": -45.0,
        "pitch": 35.0,
        "fit_distance": 22.0
    }
    bounds: Dict[str, Any] = {
        "min": [-10, 0, -10],
        "max": [10, 10, 10]
    }
    warps: List[Dict[str, Any]] = []
    npcs: List[Dict[str, Any]] = []
    triggers: List[Dict[str, Any]] = []


# Complete Map Definitions for B2W2
MAP_DEFINITIONS: Dict[int, Map3DDefinition] = {
    # 1. Aspertia City Player's Room 2F (室内 - 主角房间)
    0x0161: Map3DDefinition(
        map_id=0x0161,
        name_zh="桧扇市 主角房间 (Player's Room 2F)",
        name_en="Aspertia City - Player's Room 2F",
        map_type="indoor",
        matrix_id=101,
        matrix_dim=[1, 1],
        model_id="indoor_aspertia_house_2f",
        default_camera={"yaw": -45.0, "pitch": 35.0, "fit_distance": 18.0},
        bounds={"min": [-8, 0, -8], "max": [8, 6, 8]},
        warps=[
            {"id": 1, "x": -4.0, "y": 0.0, "z": -2.0, "target_map": 0x0160, "label": "楼梯通往 1F (Stairs to 1F)"}
        ],
        npcs=[],
        triggers=[]
    ),

    # 2. Aspertia City Player's House 1F (室内 - 主角家 1F)
    0x0160: Map3DDefinition(
        map_id=0x0160,
        name_zh="桧扇市 主角家 (Player's House 1F)",
        name_en="Aspertia City - Player's House 1F",
        map_type="indoor",
        matrix_id=100,
        matrix_dim=[1, 1],
        model_id="indoor_aspertia_house_1f",
        default_camera={"yaw": -45.0, "pitch": 35.0, "fit_distance": 20.0},
        bounds={"min": [-10, 0, -10], "max": [10, 6, 10]},
        warps=[
            {"id": 1, "x": -4.0, "y": 0.0, "z": -3.0, "target_map": 0x0161, "label": "楼梯通往 2F (Stairs to 2F)"},
            {"id": 2, "x": 0.0, "y": 0.0, "z": 6.0, "target_map": 0x015E, "label": "大门通往 桧扇市 (Exit to Town)"}
        ],
        npcs=[
            {"id": 1, "name": "妈妈 (Mom)", "x": 2.0, "y": 0.0, "z": 0.0, "facing": "South", "sprite": "mom", "role": "story"}
        ]
    ),

    # 3. Aspertia City (室外大型城市 - 桧扇市)
    0x015E: Map3DDefinition(
        map_id=0x015E,
        name_zh="桧扇市 (Aspertia City)",
        name_en="Aspertia City",
        map_type="outdoor",
        matrix_id=20,
        matrix_dim=[3, 3],
        model_id="outdoor_aspertia_city",
        default_camera={"yaw": -45.0, "pitch": 35.0, "fit_distance": 55.0},
        bounds={"min": [-35, 0, -35], "max": [35, 20, 35]},
        warps=[
            {"id": 1, "x": 0.0, "y": 0.0, "z": 12.0, "target_map": 0x0160, "label": "主角家 (Player's House)"},
            {"id": 2, "x": 14.0, "y": 0.0, "z": 8.0, "target_map": 0x0162, "label": "劲敌家 (Hugh's House)"},
            {"id": 3, "x": -16.0, "y": 0.0, "z": -10.0, "target_map": 0x0165, "label": "训练家学校 (Trainer School)"},
            {"id": 4, "x": 0.0, "y": 8.0, "z": -24.0, "target_map": 0x016A, "label": "展望台 (Lookout Point - 白露初选御三家)"}
        ],
        npcs=[
            {"id": 1, "name": "劲敌 修 (Hugh)", "x": 2.0, "y": 0.0, "z": 16.0, "facing": "North", "sprite": "hugh", "role": "rival"},
            {"id": 2, "name": "妹妹 (Sister)", "x": 5.0, "y": 0.0, "z": 16.0, "facing": "North", "sprite": "girl", "role": "npc"},
            {"id": 3, "name": "白露 (Bianca)", "x": 0.0, "y": 8.0, "z": -26.0, "facing": "South", "sprite": "bianca", "role": "story_starter"}
        ]
    ),

    # 4. Aspertia Lookout Point (桧扇市 展望台 - 选御三家场地)
    0x016A: Map3DDefinition(
        map_id=0x016A,
        name_zh="桧扇市 展望台 (Aspertia Lookout Point)",
        name_en="Aspertia Lookout Point",
        map_type="outdoor",
        matrix_id=22,
        matrix_dim=[1, 1],
        model_id="outdoor_aspertia_lookout",
        default_camera={"yaw": -45.0, "pitch": 32.0, "fit_distance": 25.0},
        bounds={"min": [-12, 0, -12], "max": [12, 10, 12]},
        warps=[
            {"id": 1, "x": 0.0, "y": 0.0, "z": 8.0, "target_map": 0x015E, "label": "通往 桧扇市主街 (Back to Town)"}
        ],
        npcs=[
            {"id": 1, "name": "白露 (Bianca) · 御三家箱子", "x": 0.0, "y": 0.0, "z": -2.0, "facing": "South", "sprite": "bianca", "role": "starter_box"}
        ]
    ),

    # 5. Virbank Complex (立涌联合工业区)
    0x0198: Map3DDefinition(
        map_id=0x0198,
        name_zh="立涌联合工业区 (Virbank Complex)",
        name_en="Virbank Complex",
        map_type="outdoor",
        matrix_id=42,
        matrix_dim=[3, 3],
        model_id="outdoor_virbank_complex",
        default_camera={"yaw": -45.0, "pitch": 35.0, "fit_distance": 65.0},
        bounds={"min": [-40, 0, -40], "max": [40, 25, 40]}
    )
}


def get_map_3d_definition(map_id: int) -> Map3DDefinition:
    if map_id in MAP_DEFINITIONS:
        return MAP_DEFINITIONS[map_id]
    
    # Generic fallback definition
    name = f"未知区域 (Map ID: 0x{map_id:04X})"
    return Map3DDefinition(
        map_id=map_id,
        name_zh=name,
        name_en=name,
        map_type="indoor" if (map_id & 0x0001) else "outdoor",
        matrix_id=map_id,
        model_id=f"map_model_{map_id:04X}",
        default_camera={"yaw": -45.0, "pitch": 35.0, "fit_distance": 30.0}
    )
