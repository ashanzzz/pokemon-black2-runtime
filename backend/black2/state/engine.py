"""Pokémon Black 2 unified semantic state engine.

The engine consumes the shared PlayerRuntime cache for coordinates and binds
active dialogue objects with a cache-first, bounded runtime locator.  Loaded
message source and currently visible text remain separate facts.
"""
import time
import asyncio
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field
from enum import Enum

from ..memory.reader import MemoryReader
from ..decoders.dialogue import DialogueState, DialogueLogEntry, dialogue_timeline
from ..decoders.dialogue_runtime_decoder import RuntimeDialogueDecoder
from ..decoders.dialogue_object_resolver import DialogueRuntimeLocator
from ..decoders.title_login import TitleLoginDecoder
from ..decoders.field import get_map_name
from ..world.native_map import read_live_map_state
from ..world.runtime_player_state import player_runtime_service


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
    location: str = "实时 ARM9 地图（Map Section 未验证）"
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
    player_facing: str = "Unresolved"
    movement_state: str = "Unresolved"
    player_grid_pos: Dict[str, Any] = Field(default_factory=lambda: {"x": None, "y": None, "z": None})
    player_world_pos: Dict[str, Any] = Field(default_factory=lambda: {"x": None, "y": None, "z": None})
    player_position_verified: bool = False


class SemanticStateEngine:
    def __init__(self, memory_reader: MemoryReader):
        self.reader = memory_reader
        self.dialogue_decoder = RuntimeDialogueDecoder()
        self.dialogue_runtime = DialogueRuntimeLocator()
        self.title_login_decoder = TitleLoginDecoder()
        self.current_state: Optional[SemanticGameState] = None
        self.listeners: List[Any] = []

    def on_user_advance(self):
        pass

    @staticmethod
    def _dialogue_base_specs() -> list[dict[str, Any]]:
        return [
            {"id": "script_message_active", "offset": 0x247546, "length": 1},
            # The tag is located inside this bounded ScriptWork neighborhood;
            # the payload address is resolved from the allocation itself.
            {"id": "script_work_context", "offset": 0x247400, "length": 0x800},
            # Legacy source window remains present for compatibility.  Once a
            # dynamic StrBuf is bound, RuntimeDialogueDecoder uses that range.
            {"id": "msg_printer_buffer", "offset": 0x2490A0, "length": 1024},
            {"id": "dialogue_tcb", "offset": 0x332C20, "length": 0x80},
            {"id": "dialogue_heap_active", "offset": 0x32B800, "length": 0x400},
            {"id": "dialogue_bitmap_surface", "offset": 0x335380, "length": 0x1000},
            {"id": "text_printer_struct", "offset": 0x31FCB0, "length": 64},
            {"id": "main_menu_struct", "offset": 0x23B630, "length": 16},
        ]

    async def _sample_dialogue_batch(self) -> tuple[Dict[str, Any], int]:
        specs = self._dialogue_base_specs() + self.dialogue_runtime.sample_specs()
        payload = await self.reader.read_batch_snapshot(specs)
        return payload.get("results", {}), int(payload.get("frame", 0))

    @staticmethod
    def _active_flag(batch_res: Dict[str, Any]) -> bool:
        item = batch_res.get("script_message_active", {})
        values = item.get("bytes", []) if isinstance(item, dict) else []
        return bool(values and int(values[0]) != 0)

    async def _bind_dialogue_if_needed(
        self, batch_res: Dict[str, Any], frame: int
    ) -> tuple[Dict[str, Any], int, bool]:
        active = self._active_flag(batch_res)
        if not active:
            self.dialogue_runtime.invalidate()
            return batch_res, frame, False

        cached = self.dialogue_runtime.resolve_cached_batch(batch_res)
        if not cached.valid:
            # Discovery may read several small bounded ranges, but those reads
            # are locator evidence only.  Visible text is never assembled from
            # them.  After success we resample source + TCBL + pixels atomically.
            await self.dialogue_runtime.discover(self.reader, batch_res)
            if self.dialogue_runtime.cached and self.dialogue_runtime.cached.valid:
                batch_res, frame = await self._sample_dialogue_batch()
                active = self._active_flag(batch_res)
                if not active:
                    self.dialogue_runtime.invalidate()
        return batch_res, frame, active

    async def sample_once(self) -> SemanticGameState:
        """Sample RAM state without turning candidates into product truth."""
        frame = 0

        # PlayerRuntime is the canonical player source. read_live_map_state
        # performs the shared cache update; it is not used to rename GPos as WPos.
        live_map = None
        try:
            live_map = await read_live_map_state(self.reader, force_sample=True)
        except Exception:
            pass
        player_sample = player_runtime_service.latest or {}
        ppos = player_sample.get("position") or {}
        pgrid = ppos.get("grid") or {}
        pworld = ppos.get("world") or {}
        porient = player_sample.get("orientation") or {}
        ploco = player_sample.get("locomotion") or {}
        player_status = player_sample.get("status")
        has_player_position = (
            player_status in {"resolved", "candidate"}
            and all(isinstance(pworld.get(k), (int, float)) for k in ("x", "y", "z"))
            and all(isinstance(pgrid.get(k), int) for k in ("x", "y", "z"))
        )
        has_verified_player = bool(player_status == "resolved" and has_player_position and porient.get("verified"))
        player_world_pos = {
            "x": pworld.get("x") if has_player_position else None,
            "y": pworld.get("y") if has_player_position else None,
            "z": pworld.get("z") if has_player_position else None,
        }
        player_grid_pos = {
            "x": pgrid.get("x") if has_player_position else None,
            "y": pgrid.get("y") if has_player_position else None,
            "z": pgrid.get("z") if has_player_position else None,
        }
        movement_str = str(ploco.get("semantic_state") or "Unresolved")
        is_moving = ploco.get("phase") == "Moving"

        map_section_id = live_map.map_id if live_map else None
        location = get_map_name(map_section_id) if map_section_id is not None else "实时 ARM9 地图（Map Section 未验证）"

        batch_res: Dict[str, Any] = {}
        batch_read_error: Optional[str] = None
        try:
            batch_res, frame = await self._sample_dialogue_batch()
            if not batch_res:
                batch_read_error = "bridge returned an empty dialogue batch"
            else:
                batch_res, frame, has_active_ptr = await self._bind_dialogue_if_needed(batch_res, frame)
        except Exception as exc:
            batch_read_error = f"{type(exc).__name__}: {exc}"
            has_active_ptr = False

        if batch_read_error:
            ctx = SemanticScreenContext(
                screen_type=GameScreenType.RUNTIME_UNRESOLVED,
                screen_description="【运行时状态未解析】本帧对话 RAM 读取失败；未将其解释为自由移动或对话结束。",
                available_actions=[], can_move_player=False, is_dialogue_active=False,
                speaker="未解析（RAM 读取失败）", speaker_category="UNRESOLVED",
                recommended_action="等待 BizHawk Bridge 恢复后重新读取 RAM",
            )
            state = SemanticGameState(
                timestamp=time.time(), frame=frame, context=ctx,
                location="实时 ARM9 状态未解析", map_loaded=False,
                ready_for_input=False, suggested_buttons=[],
                player_facing=porient.get("facing", "Unresolved") if has_player_position else "Unresolved",
                movement_state=movement_str, player_grid_pos=player_grid_pos,
                player_world_pos=player_world_pos,
                player_position_verified=has_verified_player,
            )
            self.current_state = state
            return state

        title_login_state = self.title_login_decoder.decode(batch_res.get("main_menu_struct", []))
        ctx = SemanticScreenContext()

        if title_login_state.is_main_menu:
            self.dialogue_runtime.invalidate()
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
            ready_for_input = True
        else:
            dialogue_state: DialogueState = self.dialogue_decoder.decode(
                batch_res, frame=frame, location=location, map_section_id=map_section_id,
                is_player_moving=is_moving, has_active_ptr=has_active_ptr,
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
                ctx.choices = [ChoiceOption(index=x.index, label=x.text, selected=x.selected) for x in dialogue_state.choices]
                if dialogue_state.printer.renderer_kind == "runtime_bound_token_stream":
                    ctx.screen_description = "【对话中】当前可见文字由 live TextPrinter/Window RAM 状态重建；Loaded Stream 单独保留。"
                else:
                    ctx.screen_description = "【对话中】脚本处于消息状态，但当前 TextPrinter/Window 仍未解析。"
                if dialogue_state.has_choices:
                    ctx.available_actions = ["方向键选择选项", "按 A 键确认选项"]
                else:
                    ctx.available_actions = ["按 A 键继续对话", "按 B 键加速文字"]
                ctx.recommended_action = (
                    "按 A 键继续" if dialogue_state.awaiting_input
                    else "等待当前文字打印完成"
                )
                ctx.can_move_player = False
                suggested_buttons = ["A"] if dialogue_state.awaiting_input else []
                ready_for_input = bool(dialogue_state.awaiting_input)
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
                ready_for_input = True

        state = SemanticGameState(
            timestamp=time.time(), frame=frame, context=ctx, location=location,
            map_loaded=not title_login_state.is_main_menu,
            player_name="zero", gender="男孩子 (Male)", party_count=0, money=3000, badges=0,
            ready_for_input=ready_for_input, suggested_buttons=suggested_buttons,
            map_section_id=map_section_id if not title_login_state.is_main_menu else None,
            player_facing=(porient.get("facing", "Unresolved") if has_player_position and not title_login_state.is_main_menu else "Unresolved"),
            movement_state=(movement_str if not title_login_state.is_main_menu else "Menu Idle (菜单静止)"),
            player_grid_pos=(player_grid_pos if not title_login_state.is_main_menu else {"x": None, "y": None, "z": None}),
            player_world_pos=(player_world_pos if not title_login_state.is_main_menu else {"x": None, "y": None, "z": None}),
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
