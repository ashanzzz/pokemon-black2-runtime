"""Pokémon Black 2 - Action Engine (Section 88, 89, 90).

Provides high-level semantic actions for Title Screen, Login / Continue, Dialogue navigation, etc.
"""

import asyncio
from typing import Dict, Any, Optional, List
from ..bizhawk.bridge_client import BridgeClient
from ..state.engine import SemanticStateEngine
from ..decoders.text import extract_printable_strings, decode_gen5_string


class ActionEngine:
    def __init__(self, client: BridgeClient, state_engine: SemanticStateEngine):
        self.client = client
        self.state_engine = state_engine

    async def press_button(self, button: str, hold_frames: int = 4, wait_frames: int = 15) -> Dict[str, Any]:
        """Press a single button, wait for hold frames, and advance emulator frames."""
        res = await self.client.press_buttons([button], frames=hold_frames)
        # Advance frames if needed or wait
        await asyncio.sleep(0.05 * (hold_frames + wait_frames) / 10.0)
        return res

    async def touch_screen(self, x: int = 128, y: int = 96, hold_frames: int = 4) -> Dict[str, Any]:
        """Simulate NDS touch at coordinate (x, y)."""
        return await self.client.touch(x, y, frames=hold_frames)

    async def handle_title_screen_start(self) -> Dict[str, Any]:
        """Advance past the initial GameFreak / Title screen ("Press START" / Touch)."""
        # In Gen 5, touching the lower screen or pressing START / A starts the title screen
        await self.touch_screen(128, 96, hold_frames=6)
        await asyncio.sleep(0.3)
        await self.press_button("A", hold_frames=4, wait_frames=20)
        await asyncio.sleep(0.3)
        await self.press_button("Start", hold_frames=4, wait_frames=20)
        return {"action": "title_screen_start", "status": "executed"}

    async def handle_continue_game(self) -> Dict[str, Any]:
        """Select 'Continue Game' (继续游戏) on the main menu save file selection."""
        # Top option is Continue Game -> Press A
        await self.press_button("A", hold_frames=6, wait_frames=30)
        await asyncio.sleep(0.5)
        await self.press_button("A", hold_frames=6, wait_frames=30)
        return {"action": "continue_game", "status": "executed"}

    async def handle_new_game(self) -> Dict[str, Any]:
        """Select 'New Game' on the main menu."""
        # Check current RAM cursor at 0x0223B639
        try:
            curr_cursor = await self.client.read_u8(0x23B639, "Main RAM")
            # If cursor is not at 0 or 1 (depending on save file), move to New Game (ID 0x01)
            raw_options = await self.client.read_bytes(0x23B630, 5, "Main RAM")
            target_idx = raw_options.index(0x01) if 0x01 in raw_options else 0
            delta = target_idx - curr_cursor
            btn = "Down" if delta > 0 else "Up"
            for _ in range(abs(delta)):
                await self.press_button(btn, hold_frames=4, wait_frames=8)
                await asyncio.sleep(0.15)
        except Exception:
            pass

        await self.press_button("A", hold_frames=6, wait_frames=30)
        return {"action": "new_game", "status": "executed"}

    async def select_menu_option(self, target_idx: int) -> Dict[str, Any]:
        """Directly navigate to and select a main menu option by index using verified RAM cursor."""
        try:
            curr_cursor = await self.client.read_u8(0x23B639, "Main RAM")
            delta = target_idx - curr_cursor
            if delta != 0:
                btn = "Down" if delta > 0 else "Up"
                for _ in range(abs(delta)):
                    await self.press_button(btn, hold_frames=4, wait_frames=8)
                    await asyncio.sleep(0.2)

            await asyncio.sleep(0.2)
            await self.press_button("A", hold_frames=6, wait_frames=30)
            return {"action": "select_menu_option", "index": target_idx, "status": "executed"}
        except Exception as e:
            return {"action": "select_menu_option", "error": str(e)}

    async def enter_name_on_keyboard(self, name: str = "zero") -> Dict[str, Any]:
        """Directly enter player/rival name on the NDS naming screen and confirm."""
        # 1. Clear any existing characters (touch '清' at x=228, y=118)
        await self.touch_screen(228, 118, hold_frames=6)
        await asyncio.sleep(0.3)

        # 2. Touch 'ABC' tab at (62, 185) or D-Pad navigation
        await self.touch_screen(62, 185, hold_frames=6)
        await asyncio.sleep(0.3)

        # 3. Type each character using keyboard coordinates
        key_coords = {
            'q': (30, 118), 'w': (48, 118), 'e': (66, 118), 'r': (84, 118), 't': (102, 118),
            'y': (120, 118), 'u': (138, 118), 'i': (156, 118), 'o': (174, 118), 'p': (192, 118),
            'a': (48, 138), 's': (66, 138), 'd': (84, 138), 'f': (102, 138), 'g': (120, 138),
            'h': (138, 138), 'j': (156, 138), 'k': (174, 138), 'l': (192, 138),
            'z': (66, 158), 'x': (84, 158), 'c': (102, 158), 'v': (120, 158), 'b': (138, 158),
            'n': (156, 158), 'm': (174, 158)
        }

        for char in name.lower()[:5]: # Max 5 characters
            if char in key_coords:
                kx, ky = key_coords[char]
                await self.touch_screen(kx, ky, hold_frames=4)
                await asyncio.sleep(0.25)

        # 4. Press START or touch '完毕' (220, 182) to confirm name
        await asyncio.sleep(0.3)
        await self.press_button("Start", hold_frames=6, wait_frames=30)
        await asyncio.sleep(0.5)

        # 5. Confirm "Is this name OK? -> Yes" (Press A)
        await self.press_button("A", hold_frames=6, wait_frames=30)

        return {"action": "enter_name", "name": name, "status": "confirmed"}

    async def select_dialogue_choice(self, choice_index: int) -> Dict[str, Any]:
        """Select a dialogue choice (e.g. 0 for Yes/Top, 1 for No/Bottom) and confirm with A."""
        # Top option is index 0
        if choice_index > 0:
            for _ in range(choice_index):
                await self.press_button("Down", hold_frames=4, wait_frames=8)
                await asyncio.sleep(0.15)
        else:
            await self.press_button("Up", hold_frames=4, wait_frames=8)
            await asyncio.sleep(0.15)

        await self.press_button("A", hold_frames=6, wait_frames=20)
        return {"action": "dialogue_choice", "choice_index": choice_index, "status": "executed"}

    async def advance_dialogue_once(self) -> Dict[str, Any]:
        """Press A or B to advance the current dialogue box."""
        await self.press_button("A", hold_frames=3, wait_frames=10)
        return {"action": "advance_dialogue_once", "status": "executed"}

    async def auto_advance_dialogue(self, max_steps: int = 20, delay: float = 0.4) -> List[Dict[str, Any]]:
        """Repeatedly advance dialogue until text completes."""
        steps = []
        for i in range(max_steps):
            res = await self.advance_dialogue_once()
            steps.append({"step": i + 1, "result": res})
            await asyncio.sleep(delay)
        return steps

    async def scan_current_dialogue_text(self, search_size: int = 0x400000) -> List[Dict[str, Any]]:
        """Scan candidate RAM areas to extract actively loaded or displayed dialogue text."""
        results = []
        chunk_size = 0x20000
        try:
            for start in range(0, 0x400000, chunk_size):
                bytes_data = await self.client.read_bytes(start, chunk_size, "Main RAM")
                if not bytes_data:
                    continue
                strings = extract_printable_strings(bytes_data, min_len=2)
                for rel_off, s in strings:
                    abs_ram_off = start + rel_off
                    arm9_addr = 0x02000000 + abs_ram_off
                    # Only collect if has Chinese or meaningful text
                    chinese_count = sum(1 for c in s if 0x4E00 <= ord(c) <= 0x9FA5)
                    if chinese_count >= 2:
                        results.append({
                            "address": f"0x{arm9_addr:08X}",
                            "offset": f"0x{abs_ram_off:06X}",
                            "text": s,
                            "len": len(s)
                        })
            return results
        except Exception as e:
            return [{"error": str(e)}]
