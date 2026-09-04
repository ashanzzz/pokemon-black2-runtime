"""Pokémon Black 2 - Title / Login / Main Menu Screen State Decoder."""

from typing import Dict, Any, Optional, List, Union
from pydantic import BaseModel
from enum import Enum


class GameScene(str, Enum):
    UNKNOWN = "unknown"
    INTRO_LOGO = "intro_logo"
    TITLE_SCREEN = "title_screen"          # "Press START" / Title screen
    MAIN_MENU = "main_menu"                # "Continue" / "New Game" menu
    NEW_GAME_INTRO = "new_game_intro"      # Professor Juniper introduction
    OVERWORLD = "overworld"                # Normal field gameplay
    DIALOGUE = "dialogue"                  # Message box / NPC conversation active
    BATTLE = "battle"                      # Turn-based battle active
    BAG_MENU = "bag_menu"                  # Inventory screen
    PARTY_MENU = "party_menu"              # Pokémon party screen


MENU_OPTION_ID_MAP = {
    0x00: "继续游戏",
    0x01: "从最初开始",
    0x02: "不可思议的礼物",
    0x03: "战斗大会",
    0x04: "游戏同步",
    0x05: "Wi-Fi设定",
    0x06: "麦克风测试",
    0x08: "合众连接",
}


class TitleLoginState(BaseModel):
    scene: GameScene = GameScene.TITLE_SCREEN
    is_title_screen: bool = False
    is_main_menu: bool = False
    has_save_data: bool = False
    menu_cursor_index: int = 0
    menu_options: List[str] = []
    selected_option: Optional[str] = None
    confidence: float = 0.0
    raw_indicators: Dict[str, Any] = {}


class TitleLoginDecoder:
    """Decodes Title and Main Menu / Login Screen states from Main RAM (0x0223B630 ~ 0x0223B639)."""

    def __init__(self):
        pass

    def decode(self, menu_input: Union[List[int], Dict[str, Any]]) -> TitleLoginState:
        state = TitleLoginState()
        menu_bytes: List[int] = []
        if isinstance(menu_input, dict):
            menu_bytes = menu_input.get("bytes", [])
        elif isinstance(menu_input, list):
            menu_bytes = menu_input

        if not menu_bytes or len(menu_bytes) < 10:
            return state

        # 0x0223B630..34 contains active menu option IDs
        raw_options = menu_bytes[0:5]
        # Reject zeroed memory or non-menu arrays
        if len(set(raw_options)) < 2 or all(x == 0 for x in raw_options):
            state.scene = GameScene.UNKNOWN
            state.confidence = 0.0
            return state

        # Filter valid IDs
        valid_options = [MENU_OPTION_ID_MAP[opt_id] for opt_id in raw_options if opt_id in MENU_OPTION_ID_MAP]

        if len(valid_options) >= 3 and len(set(valid_options)) >= 3:
            state.scene = GameScene.MAIN_MENU
            state.is_main_menu = True
            state.menu_options = valid_options
            state.has_save_data = (0x00 in raw_options)

            cursor_idx = menu_bytes[9] if len(menu_bytes) > 9 else 0
            if 0 <= cursor_idx < len(valid_options):
                state.menu_cursor_index = cursor_idx
                state.selected_option = valid_options[cursor_idx]
            else:
                state.menu_cursor_index = 0
                state.selected_option = valid_options[0] if valid_options else None

            state.confidence = 1.0
            state.raw_indicators = {
                "option_ids": raw_options,
                "cursor_index": cursor_idx,
            }
        else:
            state.scene = GameScene.UNKNOWN
            state.confidence = 0.0

        return state

