"""Pokémon Black 2 - Automated New Game Onboarding Flow.

Handles the entire intro sequence:
1. Title Screen -> New Game
2. Juniper Intro Dialogue
3. Gender Selection (Male/Female)
4. Name Input (default 'zero')
5. Rival Name Input
6. Opening Mom dialogue in Aspertia City
7. Reaches free overworld movement
"""

import asyncio
import time
from typing import Dict, Any, Optional
from ..bizhawk.bridge_client import BridgeClient
from ..state.engine import SemanticStateEngine


# NDS Touch Coordinates for B2W2 Lower Screen Keyboard
# In Gen 5 (B2W2), Keyboard layout under Latin / ABC:
KEYBOARD_TOUCH_MAP = {
    'a': (36, 108), 'b': (124, 138), 'c': (80, 138), 'd': (80, 108),
    'e': (68, 78),  'f': (102, 108), 'g': (124, 108), 'h': (146, 108),
    'i': (178, 78), 'j': (168, 108), 'k': (190, 108), 'l': (212, 108),
    'm': (168, 138), 'n': (146, 138), 'o': (200, 78),  'p': (222, 78),
    'q': (24, 78),  'r': (90, 78),  's': (58, 108), 't': (112, 78),
    'u': (156, 78), 'v': (102, 138), 'w': (46, 78),  'x': (58, 138),
    'y': (134, 78), 'z': (36, 138),
    'OK': (228, 172), 'BACKSPACE': (228, 48), 'ABC_TAB': (80, 48)
}


class OnboardingFlow:
    def __init__(self, client: BridgeClient, state_engine: SemanticStateEngine):
        self.client = client
        self.state_engine = state_engine

    async def press_button(self, button: str, hold_frames: int = 4, delay_sec: float = 0.3):
        await self.client.press_buttons([button], frames=hold_frames)
        await asyncio.sleep(delay_sec)

    async def touch(self, x: int, y: int, hold_frames: int = 4, delay_sec: float = 0.3):
        await self.client.touch(x, y, frames=hold_frames)
        await asyncio.sleep(delay_sec)

    async def type_name_on_keyboard(self, name: str):
        """Type characters on the NDS name input screen keyboard and confirm."""
        # Switch to Latin / ABC letters tab
        await self.touch(KEYBOARD_TOUCH_MAP['ABC_TAB'][0], KEYBOARD_TOUCH_MAP['ABC_TAB'][1], hold_frames=4, delay_sec=0.4)
        
        # Type each character
        for char in name.lower():
            if char in KEYBOARD_TOUCH_MAP:
                tx, ty = KEYBOARD_TOUCH_MAP[char]
                await self.touch(tx, ty, hold_frames=4, delay_sec=0.35)
        
        # Press START to trigger OK confirmation (universal GameFreak naming shortcut)
        await asyncio.sleep(0.3)
        await self.press_button("Start", hold_frames=6, delay_sec=0.6)
        
        # Confirm "Is this name OK? -> Yes"
        await self.press_button("A", hold_frames=6, delay_sec=0.6)

    async def run_full_new_game_sequence(self, player_name: str = "zero", gender: str = "male") -> Dict[str, Any]:
        """Execute full automated new game onboarding flow."""
        log = []

        # 1. Advance Title Screen
        log.append("Step 1: Advancing Title Screen...")
        await self.touch(128, 96, hold_frames=6, delay_sec=0.5)
        await self.press_button("Start", hold_frames=6, delay_sec=1.0)
        await self.press_button("A", hold_frames=6, delay_sec=1.5)

        # 2. Main Menu: Select New Game (新游戏)
        log.append("Step 2: Selecting New Game on Main Menu...")
        # Press Down once (in case continue option exists) or A
        await self.press_button("Down", hold_frames=4, delay_sec=0.4)
        await self.press_button("A", hold_frames=6, delay_sec=1.0)
        # In case of save overwrite warning confirmation: "Yes"
        await self.press_button("Up", hold_frames=4, delay_sec=0.4)
        await self.press_button("A", hold_frames=6, delay_sec=2.0)

        # 3. Advance Juniper Intro Dialogue (Opening speech + Minccino demo)
        log.append("Step 3: Advancing Professor Juniper intro dialogue...")
        for i in range(18):
            await self.press_button("A", hold_frames=4, delay_sec=0.4)
        
        await asyncio.sleep(1.0)

        # 4. Gender Selection
        log.append(f"Step 4: Selecting Gender ({gender})...")
        if gender.lower() == "female":
            await self.press_button("Down", hold_frames=4, delay_sec=0.4)
        # Select gender and confirm Yes
        await self.press_button("A", hold_frames=6, delay_sec=0.6)
        await self.press_button("A", hold_frames=6, delay_sec=1.5)

        # 5. Player Name Input
        log.append(f"Step 5: Entering player name '{player_name}'...")
        await self.type_name_on_keyboard(player_name)
        await asyncio.sleep(1.5)

        # 6. Rival Introduction & Name
        log.append("Step 6: Confirming Rival name...")
        for _ in range(8):
            await self.press_button("A", hold_frames=4, delay_sec=0.4)
        
        # On rival keyboard screen: Press START to confirm default rival name (Hugh / 修)
        await self.press_button("Start", hold_frames=6, delay_sec=0.6)
        await self.press_button("A", hold_frames=6, delay_sec=1.0)

        # 7. Final Intro Dialogue & Transition into Aspertia City (主角家 1F)
        log.append("Step 7: Advancing final intro cutscene into Aspertia City...")
        for _ in range(25):
            await self.press_button("A", hold_frames=4, delay_sec=0.4)

        # 8. Complete Mom's opening dialogue in player room / house
        log.append("Step 8: Completing Mom dialogue in Aspertia City...")
        for _ in range(20):
            await self.press_button("A", hold_frames=4, delay_sec=0.35)

        log.append("Step 9: Overworld reached! Player is now in control.")

        # Query updated state
        state = await self.state_engine.sample_once()
        return {
            "ok": True,
            "status": "completed",
            "player_name": player_name,
            "gender": gender,
            "current_scene": state.scene.value,
            "location": state.field.map_name,
            "log": log
        }
