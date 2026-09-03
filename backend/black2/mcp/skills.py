"""Pokémon Black 2 Semantic Runtime - High-Level AI Agent Skills & Tool Definitions.

Provides intuitive, human/AI-readable perception and decision interfaces.
"""

from typing import Dict, Any, List, Optional
import requests
import json

BASE_URL = "http://127.0.0.1:8765"


class PokemonAgentSkills:
    """High-level semantic tools for AI models to intuitively observe and control Pokémon Black 2."""

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url

    def observe(self) -> Dict[str, Any]:
        """[AI 核心感知] 直观获取游戏当前界面状态与可执行动作指导。
        无需自己读内存，直接返回当前在哪个界面、有什么选项、推荐按什么键。
        """
        try:
            r = requests.get(f"{self.base_url}/api/state", timeout=3)
            state = r.json()
            ctx = state.get("context", {})

            return {
                "ok": True,
                "当前界面 (current_screen)": ctx.get("screen_type", "TITLE_SCREEN"),
                "界面说明 (description)": ctx.get("screen_description", "正在读取界面..."),
                "当前所处地图 (location)": state.get("location", "未载入地图"),
                "地图是否已加载 (map_loaded)": state.get("map_loaded", False),
                "是否处于对话中 (is_dialogue)": ctx.get("is_dialogue_active", False),
                "当前对话内容 (dialogue_text)": ctx.get("dialogue_text", ""),
                "当前可选选项 (choices)": [c.get("label") for c in ctx.get("choices", [])],
                "角色能否自由移动 (can_move)": ctx.get("can_move_player", False),
                "同行宝可梦数量 (party_count)": state.get("party_count", 0),
                "玩家姓名 (player_name)": state.get("player_name", "未载入存档"),
                "推荐下一步操作 (recommended_action)": ctx.get("recommended_action", "按 A 键"),
                "推荐按键 (suggested_buttons)": state.get("suggested_buttons", ["A"]),
                "ready_for_input": True
            }
        except Exception as e:
            return {"ok": False, "error": f"Failed to observe game: {e}"}

    def press_button(self, button: str, hold_frames: int = 4) -> Dict[str, Any]:
        """[AI 动作操作] 按下指定的虚拟按键。
        参数:
            button: 'A' | 'B' | 'X' | 'Y' | 'Start' | 'Select' | 'Up' | 'Down' | 'Left' | 'Right'
            hold_frames: 按键持续帧数 (默认 4 帧)
        """
        try:
            r = requests.post(f"{self.base_url}/api/actions/press", json={"button": button, "frames": hold_frames}, timeout=3)
            return {"ok": True, "executed": f"Pressed {button}", "result": r.json()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def type_text(self, text: str) -> Dict[str, Any]:
        """[AI 命名软键盘] 在当前名字输入界面输入指定文本 (例如 'zero')。
        系统将自动映射 NDS 软键盘按键并确认。
        """
        try:
            r = requests.post(f"{self.base_url}/api/actions/type_text", json={"text": text}, timeout=10)
            return {"ok": True, "typed": text, "result": r.json()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def advance_dialogue(self, steps: int = 1) -> Dict[str, Any]:
        """[AI 对话推进] 推进当前剧情对话 (单步或连续推进多句)。"""
        try:
            r = requests.post(f"{self.base_url}/api/actions/dialogue/advance?steps={steps}", timeout=10)
            return {"ok": True, "advanced_steps": steps, "result": r.json()}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def select_menu_option(self, direction: str = "Down", confirm: bool = True) -> Dict[str, Any]:
        """[AI 菜单选择] 在选项/菜单界面上下移动光标并按 A 确认。"""
        try:
            # Move cursor then press A
            requests.post(f"{self.base_url}/api/actions/press", json={"button": direction, "frames": 4}, timeout=3)
            if confirm:
                import time
                time.sleep(0.2)
                requests.post(f"{self.base_url}/api/actions/press", json={"button": "A", "frames": 6}, timeout=3)
            return {"ok": True, "action": f"Moved {direction} and confirmed"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
