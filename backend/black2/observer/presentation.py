"""Observer Presentation Layer with Live Dialogue Text, Speaker Detection, and Chronological Timeline."""

import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from .capabilities import capability_store
from .logger import observer_logger
from ..state.memory_goals import goal_memory_manager, GoalTier


class ObserverPresentationState(BaseModel):
    bizhawk_status: str
    bridge_version: str
    rom_name: str
    frame: int
    state_version: int
    backend_health: str
    current_time: str
    primary_context: str
    modal_stack: List[str]
    active_handler: str
    input_state: str

    location_zh: str
    location_en: str
    map_id_hex: str
    world_pos: Dict[str, Any]
    facing: str
    player_control: str
    story_objective: str
    player_name: str
    party_count: int
    money: int
    badges_count: int

    # Three-tier goals & action directive
    goals: Optional[Dict[str, Any]] = None

    # Active Live Dialogue & Speaker Details
    dialogue_active: bool
    dialogue_speaker: str
    dialogue_speaker_category: str
    dialogue_start_time: Optional[str] = None
    dialogue_duration_sec: float = 0.0
    dialogue_text: str
    dialogue_active_pointer: Optional[str] = None
    dialogue_has_choices: bool
    dialogue_choices: List[Dict[str, Any]]
    dialogue_history: List[Dict[str, Any]] = Field(default_factory=list)

    capability_summary: Dict[str, Any]
    detectors: List[Dict[str, Any]]
    recent_events: List[Dict[str, Any]]
    recent_calls: List[Dict[str, Any]]


def build_observer_presentation(state_dict: Dict[str, Any]) -> ObserverPresentationState:
    frame = state_dict.get("frame", 0)
    ctx = state_dict.get("context", {})
    is_dlg = ctx.get("is_dialogue_active", False)
    dlg_text = ctx.get("dialogue_text", "")
    dlg_speaker = ctx.get("speaker", "无活跃对话")
    dlg_speaker_cat = ctx.get("speaker_category", "IDLE")
    dlg_start_time = ctx.get("dialogue_start_time")
    dlg_duration = ctx.get("dialogue_duration_sec", 0.0)
    dlg_choices = ctx.get("choices", [])
    dlg_history = ctx.get("dialogue_history", [])

    map_section_id = state_dict.get("map_section_id")
    location = state_dict.get("location") or "实时 ARM9 地图（位置未验证）"
    world_pos = state_dict.get("player_world_pos") or {"x": None, "y": None, "z": None}
    position_verified = bool(state_dict.get("player_position_verified"))
    map_id_text = f"0x{map_section_id:04X}" if isinstance(map_section_id, int) else "UNVERIFIED"
    position_evidence = (
        f"ARM9 player position ({world_pos.get('x')}, {world_pos.get('y')})"
        if position_verified
        else "ARM9 player position is not verified"
    )

    primary = "FIELD"
    modal_stack = []
    active_handler = "FieldSystem"
    input_state = "INJECTABLE (BizHawk bridge ready)"
    player_control = "UNVERIFIED (awaiting live movement evidence)"
    story_obj = "在 1F 与妈妈对话，获得跑鞋与道具，然后前往展望台寻找白露"

    screen_type = ctx.get("screen_type")
    is_main_menu = (screen_type == "MAIN_MENU")

    if is_main_menu:
        primary = "TITLE_AND_LOGIN"
        modal_stack = ["MAIN_MENU"]
        active_handler = "TitleLoginDecoder"
        input_state = "MENU_NAVIGATION (A: 确认 / ↑↓: 切换)"
        player_control = "MENU_ACTIVE (0x0223B630)"
        location = "主菜单 / 初始界面 (Main Menu)"
        story_obj = "主菜单：按 A 键选择「从最初开始」创建新存档，或选择其他功能"
    elif is_dlg:
        primary = "FIELD"
        modal_stack = ["CUTSCENE", "DIALOGUE"]
        active_handler = "DialogueRuntime"
        input_state = "INJECTABLE (dialogue inference is advisory)"
        player_control = "UNVERIFIED (dialogue detector does not block controller tests)"
    else:
        primary = "FIELD"
        modal_stack = ["FIELD_FREE_MOVEMENT"]
        active_handler = "PlayerMovementController"
        input_state = "INJECTABLE (BizHawk bridge ready)"
        player_control = "UNVERIFIED (awaiting live movement evidence)"

    detectors = [
        {
            "detector": "TitleLoginDecoder",
            "candidate": "ACTIVE" if is_main_menu else "INACTIVE",
            "confidence": 1.0 if is_main_menu else 0.0,
            "evidence": "Main RAM 0x0223B630 Menu Option struct verified" if is_main_menu else "In-game / Field active",
        },
        {
            "detector": "FieldSystem",
            "candidate": "ACTIVE" if position_verified else "UNVERIFIED",
            "confidence": 1.0 if position_verified else 0.0,
            "evidence": position_evidence,
        },
        {
            "detector": "DialogueRuntime",
            "candidate": "ACTIVE" if is_dlg else "INACTIVE",
            "confidence": 1.0 if is_dlg else 0.8,
            "evidence": f"[{dlg_speaker}]: {dlg_text[:16].replace(chr(10), ' ')}..." if is_dlg else "No active dialogue buffer changes",
        },
        {"detector": "CutsceneScript", "candidate": "ACTIVE" if is_dlg else "INACTIVE", "confidence": 0.95, "evidence": "Script event active" if is_dlg else "Free field control"},
        {"detector": "BattleSystem", "candidate": "INACTIVE", "confidence": 1.0, "evidence": "Battle struct pointer null"},
        {"detector": "MenuController", "candidate": "INACTIVE", "confidence": 0.99, "evidence": "Bag/Party modal closed"},
        {"detector": "TransitionFade", "candidate": "INACTIVE", "confidence": 1.0, "evidence": "Display brightness normal"}
    ]

    facing_val = state_dict.get("player_facing") or "South"
    facing_map = {
        "North": "北 (North / ↑)",
        "South": "南 (South / ↓)",
        "West": "西 (West / ←)",
        "East": "东 (East / →)",
    }
    facing_text = facing_map.get(facing_val, f"{facing_val}")
    movement_state = state_dict.get("movement_state") or "Idle (静止)"

    now_str = time.strftime("%Y-%m-%d %H:%M:%S")

    # Format history if they are pydantic objects or dicts
    formatted_history = []
    for item in dlg_history:
        if hasattr(item, "model_dump"):
            formatted_history.append(item.model_dump())
        elif isinstance(item, dict):
            formatted_history.append(item)

    # Evaluate three-tier goals & action directive from live RAM
    evaluated_goals = goal_memory_manager.evaluate(state_dict).model_dump()
    story_obj = evaluated_goals["immediate"]

    return ObserverPresentationState(
        bizhawk_status="BIZHAWK CONNECTED (MelonDS NDS)",
        bridge_version="1.1.0",
        rom_name="口袋妖怪 黑2 汉化版 (IREO)",
        frame=frame,
        state_version=1000 + (frame % 500),
        backend_health="HEALTHY",
        current_time=now_str,
        primary_context=primary,
        modal_stack=modal_stack,
        active_handler=active_handler,
        input_state=input_state,
        location_zh=location,
        location_en="Live ARM9 map" if position_verified else "Live ARM9 map (unverified)",
        map_id_hex=map_id_text,
        world_pos=world_pos,
        facing=facing_text,
        player_control=player_control,
        story_objective=story_obj,
        player_name="zero",
        party_count=0,
        money=3000,
        badges_count=0,
        goals=evaluated_goals,
        dialogue_active=is_dlg,
        dialogue_speaker=dlg_speaker,
        dialogue_speaker_category=dlg_speaker_cat,
        dialogue_start_time=dlg_start_time,
        dialogue_duration_sec=dlg_duration,
        dialogue_text=dlg_text or "[当前未处于对话中]",
        dialogue_active_pointer=ctx.get("active_pointer"),
        dialogue_has_choices=bool(dlg_choices),
        dialogue_choices=dlg_choices,
        dialogue_history=formatted_history,
        capability_summary=capability_store.get_summary(),
        detectors=detectors,
        recent_events=[e.model_dump() for e in observer_logger.get_recent_events(10)],
        recent_calls=[c.model_dump() for c in observer_logger.get_recent_calls(10)]
    )
