"""Pokémon Black 2 - Unified Semantic State Engine with Active RAM Message Tracking."""

import time
import asyncio
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from enum import Enum

from ..memory.reader import MemoryReader
from ..decoders.dialogue import DialogueDecoder, DialogueState, DialogueLogEntry, dialogue_timeline
from ..decoders.title_login import TitleLoginDecoder, TitleLoginState, GameScene
from ..decoders.field import get_map_name
from ..world.native_map import read_live_map_state


class GameScreenType(str, Enum):
    RUNTIME_UNRESOLVED = "RUNTIME_UNRESOLVED"
    TITLE_SCREEN = "TITLE_SCREEN"
    MAIN_MENU = "MAIN_MENU"
    INTRO_SPEECH = "INTRO_SPEECH"
    GENDER_SELECTION = "GENDER_SELECTION"
    NAME_INPUT = "NAME_INPUT"
    RIVAL_NAME_INPUT = "RIVAL_NAME_INPUT"
    OVERWORLD = "OVERWORLD"
    DIALOGUE_ACTIVE = "DIALOGUE_ACTIVE"
    DIALOGUE_CHOICE = "DIALOGUE_CHOICE"
    BATTLE = "BATTLE"
    BAG_MENU = "BAG_MENU"
    PARTY_MENU = "PARTY_MENU"


class ChoiceOption(BaseModel):
    index: int
    label: str
    selected: bool = False


class SemanticScreenContext(BaseModel):
    screen_type: GameScreenType = GameScreenType.OVERWORLD
    screen_description: str = "【大地图自由探索】主角可自由移动"
    available_actions: List[str] = ["方向键移动", "按 A 键互动", "按 X 键打开菜单"]
    can_move_player: bool = True
    is_dialogue_active: bool = False
    dialogue_text: str = ""
    full_dialogue_text: str = ""
    loaded_dialogue_text: str = ""
    speaker: str = "无活跃对话"
    speaker_category: str = "IDLE"
    active_pointer: Optional[str] = None
    dialogue_start_time: Optional[str] = None
    dialogue_duration_sec: float = 0.0
    printer: Dict[str, Any] = Field(default_factory=dict)
    choices: List[ChoiceOption] = Field(default_factory=list)
    recommended_action: str = "方向键移动"
    dialogue_history: List[DialogueLogEntry] = Field(default_factory=list)


class SemanticGameState(BaseModel):
    timestamp: float
    frame: int
    context: SemanticScreenContext = SemanticScreenContext()
    location: str = "桧扇市 主角家 (1F 室内)"
    map_loaded: bool = True
    player_name: str = "zero"
    rival_name: str = "NO"
    gender: str = "男孩子 (Male)"
    party_count: int = 0
    money: int = 3000
    badges: int = 0
    ready_for_input: bool = True
    suggested_buttons: List[str] = ["A"]
    map_section_id: Optional[int] = None
    player_facing: str = "South"
    movement_state: str = "Idle (静止)"
    player_world_pos: Dict[str, Any] = Field(
        default_factory=lambda: {"x": None, "y": None, "z": None}
    )
    player_position_verified: bool = False


class SemanticStateEngine:
    def __init__(self, memory_reader: MemoryReader):
        self.reader = memory_reader
        self.dialogue_decoder = DialogueDecoder()
        self.title_login_decoder = TitleLoginDecoder()
        self.current_state: Optional[SemanticGameState] = None
        self.listeners: List[Any] = []

    def on_user_advance(self):
        pass

    async def sample_once(self) -> SemanticGameState:
        """Sample active RAM message buffer & state directly from BizHawk."""
        frame = 0

        # 2. Read live map state to know location & movement state
        live_map = None
        try:
            live_map = await read_live_map_state(self.reader)
        except Exception:
            pass

        map_section_id = live_map.map_id if live_map else None
        location = (
            get_map_name(map_section_id)
            if map_section_id is not None
            else "实时 ARM9 地图（Map Section 未验证）"
        )
        # Do not leak a candidate map reader's coordinates into the semantic
        # API.  In particular, the old fixed 0x0223DE00 route was rejected;
        # position, facing, and motion require a verified FieldPlayer chain.
        has_verified_player = bool(live_map and live_map.verified)
        player_world_pos = {
            "x": live_map.x if has_verified_player else None,
            "y": live_map.y if has_verified_player else None,
            "z": live_map.elevation if has_verified_player else None,
        }
        movement_str = live_map.movement_state if has_verified_player else "Unresolved"
        is_moving = has_verified_player and any(
            kw in movement_str
            for kw in ("Walking", "Running", "Biking", "Surfing", "移动", "行走", "奔跑", "骑行")
        )

        # 3. One bridge-frame batch for the dialogue flag, message stream, and
        # observed ``tcbl.c`` control allocation.  In particular, do not read a
        # continuation pointer on a different frame and call its future text
        # visible.  The surface is read as byte evidence, never as a screenshot.
        sample_specs = [
            {"id": "script_message_active", "offset": 0x247546, "length": 1},
            {"id": "active_dialogue_target", "offset": 0x2490A0, "length": 256},
            {"id": "msg_printer_buffer", "offset": 0x2490A0, "length": 1024},
            {"id": "dialogue_tcb", "offset": 0x332C20, "length": 0x80},
            {"id": "dialogue_heap_active", "offset": 0x32B800, "length": 0x400},
            {"id": "dialogue_bitmap_surface", "offset": 0x335380, "length": 0x1000},
            {"id": "text_printer_struct", "offset": 0x31FCB0, "length": 64},
            {"id": "main_menu_struct", "offset": 0x23B630, "length": 16}
        ]

        batch_res = {}
        batch_read_error: Optional[str] = None
        try:
            batch_payload = await self.reader.read_batch_snapshot(sample_specs)
            batch_res = batch_payload.get("results", {})
            frame = int(batch_payload.get("frame", frame))
            if not batch_res:
                batch_read_error = "bridge returned an empty dialogue batch"
        except Exception as exc:
            # A failed read is not an observed zero.  In particular it must not
            # become a false overworld / dialogue-ended report.
            batch_read_error = f"{type(exc).__name__}: {exc}"

        if batch_read_error:
            ctx = SemanticScreenContext(
                screen_type=GameScreenType.RUNTIME_UNRESOLVED,
                screen_description="【运行时状态未解析】本帧对话 RAM 读取失败；未将其解释为自由移动或对话结束。",
                available_actions=[],
                can_move_player=False,
                is_dialogue_active=False,
                speaker="未解析（RAM 读取失败）",
                speaker_category="UNRESOLVED",
                recommended_action="等待 BizHawk Bridge 恢复后重新读取 RAM",
            )
            state = SemanticGameState(
                timestamp=time.time(),
                frame=frame,
                context=ctx,
                location="实时 ARM9 状态未解析",
                map_loaded=False,
                ready_for_input=False,
                suggested_buttons=[],
                player_facing="Unresolved",
                movement_state="Unresolved",
                player_world_pos={"x": None, "y": None, "z": None},
                player_position_verified=False,
            )
            self.current_state = state
            return state

        flag_item = batch_res.get("script_message_active", {})
        flag_bytes = flag_item.get("bytes", []) if isinstance(flag_item, dict) else []
        has_active_ptr = bool(flag_bytes and flag_bytes[0] != 0)

        # Check for Main Menu / Title Screen from RAM (0x0223B630)
        title_login_state = self.title_login_decoder.decode(batch_res.get("main_menu_struct", []))

        ctx = SemanticScreenContext()

        if title_login_state.is_main_menu:
            ctx.screen_type = GameScreenType.MAIN_MENU
            ctx.is_dialogue_active = False
            ctx.can_move_player = False
            ctx.speaker = "系统主菜单"
            ctx.speaker_category = "SYSTEM"
            selected_opt = title_login_state.selected_option or "从最初开始"
            ctx.screen_description = f"【主菜单 / 初始界面】当前光标选中：「{selected_opt}」"
            ctx.available_actions = ["方向键上下切换菜单", "按 A 键确认选中项"]
            ctx.recommended_action = f"按 A 键进入「{selected_opt}」"
            ctx.choices = [
                ChoiceOption(index=i, label=opt, selected=(i == title_login_state.menu_cursor_index))
                for i, opt in enumerate(title_login_state.menu_options)
            ]
            location = "主菜单 / 初始界面 (Main Menu)"
            suggested_buttons = ["A", "Up", "Down"]
        else:
            dialogue_state = self.dialogue_decoder.decode(
                batch_res,
                frame=frame,
                location=location,
                map_section_id=map_section_id,
                is_player_moving=is_moving,
                has_active_ptr=has_active_ptr,
            )

            ctx.dialogue_history = dialogue_state.recent_history
            ctx.active_pointer = dialogue_state.active_pointer
            ctx.full_dialogue_text = dialogue_state.full_dialogue_text
            ctx.loaded_dialogue_text = dialogue_state.loaded_text
            ctx.printer = dialogue_state.printer.model_dump()
            if dialogue_state.active:
                ctx.screen_type = GameScreenType.DIALOGUE_CHOICE if dialogue_state.has_choices else GameScreenType.DIALOGUE_ACTIVE
                ctx.is_dialogue_active = True
                ctx.dialogue_text = dialogue_state.current_text
                ctx.speaker = dialogue_state.speaker
                ctx.speaker_category = dialogue_state.speaker_category
                ctx.dialogue_start_time = dialogue_state.start_time
                ctx.dialogue_duration_sec = dialogue_state.duration_seconds
                ctx.choices = [
                    ChoiceOption(index=choice.index, label=choice.text, selected=choice.selected)
                    for choice in dialogue_state.choices
                ]
                ctx.screen_description = "【对话中】可见文字、等待按键状态和说话 Actor 尚未由 RAM 结构验证"
                ctx.available_actions = ["方向键选择选项", "按 A 键确认选项"] if dialogue_state.has_choices else ["按 A 键继续对话", "按 B 键加速文字"]
                ctx.recommended_action = "手动观察后按 A；当前 RAM 解析器不会自动判定已可翻页"
                ctx.can_move_player = False
                suggested_buttons = ["A"]
            else:
                ctx.screen_type = GameScreenType.OVERWORLD
                ctx.is_dialogue_active = False
                ctx.dialogue_text = dialogue_state.current_text
                ctx.speaker = dialogue_state.speaker
                ctx.speaker_category = dialogue_state.speaker_category
                ctx.dialogue_start_time = dialogue_state.start_time
                ctx.dialogue_duration_sec = dialogue_state.duration_seconds
                ctx.screen_description = "【大地图自由探索】主角可自由移动"
                ctx.available_actions = ["方向键移动", "按 A 键互动", "按 X 键打开菜单"]
                ctx.recommended_action = "自由探索 / 向目标前进"
                ctx.can_move_player = True
                suggested_buttons = ["Up", "Down", "Left", "Right"]

        state = SemanticGameState(
            timestamp=time.time(),
            frame=frame,
            context=ctx,
            location=location,
            map_loaded=(not title_login_state.is_main_menu),
            player_name="zero",
            gender="男孩子 (Male)",
            party_count=0,
            money=3000,
            badges=0,
            ready_for_input=not ctx.is_dialogue_active,
            suggested_buttons=suggested_buttons,
            map_section_id=map_section_id if not title_login_state.is_main_menu else None,
            player_facing=(
                live_map.facing
                if (has_verified_player and not title_login_state.is_main_menu)
                else "Unresolved"
            ),
            movement_state=(
                live_map.movement_state
                if (has_verified_player and not title_login_state.is_main_menu)
                else ("Menu Idle (菜单静止)" if title_login_state.is_main_menu else "Unresolved")
            ),
            player_world_pos=player_world_pos if not title_login_state.is_main_menu else {"x": None, "y": None, "z": None},
            player_position_verified=bool(has_verified_player and not title_login_state.is_main_menu),
        )

        self.current_state = state
        return state

    async def start_sampling(self, interval_sec: float = 0.2):
        while True:
            try:
                if self.reader.client.is_connected:
                    state = await self.sample_once()
                    for cb in list(self.listeners):
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                await cb(state)
                            else:
                                cb(state)
                        except Exception:
                            pass
            except Exception:
                pass
            await asyncio.sleep(interval_sec)
