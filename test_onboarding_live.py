"""Live Onboarding Runner: From New Game to Choosing Snivy (Grass Starter)."""

import asyncio
import time
import requests

BASE_URL = "http://127.0.0.1:8765"


def press_button(btn: str, frames: int = 8, delay: float = 0.3):
    requests.post(f"{BASE_URL}/api/actions/press", json={"button": btn, "frames": frames}, timeout=5)
    time.sleep(delay)


def touch_screen(x: int, y: int, frames: int = 8, delay: float = 0.35):
    requests.post(f"{BASE_URL}/api/actions/touch", json={"x": x, "y": y, "frames": frames}, timeout=5)
    time.sleep(delay)


# NDS on-screen keyboard touch coordinates for Gen 5 (B2W2)
KEYBOARD_TOUCH_MAP = {
    'a': (36, 108), 'b': (124, 138), 'c': (80, 138), 'd': (80, 108),
    'e': (68, 78),  'f': (102, 108), 'g': (124, 108), 'h': (146, 108),
    'i': (178, 78), 'j': (168, 108), 'k': (190, 108), 'l': (212, 108),
    'm': (168, 138), 'n': (146, 138), 'o': (200, 78),  'p': (222, 78),
    'q': (24, 78),  'r': (90, 78),  's': (58, 108), 't': (112, 78),
    'u': (156, 78), 'v': (102, 138), 'w': (46, 78),  'x': (58, 138),
    'y': (134, 78), 'z': (36, 138),
    'ABC_TAB': (80, 48), 'OK': (228, 172)
}


def run_sequence():
    print("=== Step 1: 确认 '从最初开始' (New Game) ===")
    press_button("A", frames=8, delay=1.5)
    # If warning popup appears, confirm Yes
    press_button("Up", frames=6, delay=0.4)
    press_button("A", frames=8, delay=2.5)

    print("=== Step 2: 推进红豆杉博士开场剧情 (Intro Dialogue) ===")
    for i in range(18):
        press_button("A", frames=6, delay=0.38)
    time.sleep(1.0)

    print("=== Step 3: 选择性别 - 男孩 (Male) 并确认 ===")
    press_button("A", frames=8, delay=0.8) # Select Boy
    press_button("A", frames=8, delay=1.8) # Confirm "你是男孩子，对吧？" -> 是

    print("=== Step 4: 软键盘键入名字 'zero' 并确认 ===")
    # Touch ABC mode tab
    touch_screen(KEYBOARD_TOUCH_MAP['ABC_TAB'][0], KEYBOARD_TOUCH_MAP['ABC_TAB'][1], frames=8, delay=0.5)
    
    # Touch z, e, r, o
    for c in ['z', 'e', 'r', 'o']:
        tx, ty = KEYBOARD_TOUCH_MAP[c]
        print(f"  Typing '{c}' at ({tx}, {ty})")
        touch_screen(tx, ty, frames=8, delay=0.4)

    time.sleep(0.5)
    # Press START on keyboard screen to trigger OK
    press_button("Start", frames=8, delay=0.8)
    # Confirm "你的名字是 zero，对吧？" -> 是
    press_button("A", frames=8, delay=1.5)

    print("=== Step 5: 劲敌介绍与起名确认 ===")
    for _ in range(8):
        press_button("A", frames=6, delay=0.4)
    time.sleep(0.5)
    # On rival keyboard: Press START to keep default name (修 / Hugh)
    press_button("Start", frames=8, delay=0.8)
    press_button("A", frames=8, delay=1.5)

    print("=== Step 6: 最终开场转场与进入主角家 1F ===")
    for _ in range(25):
        press_button("A", frames=6, delay=0.38)

    print("=== Step 7: 推进主角家妈妈开场对话 ===")
    for _ in range(22):
        press_button("A", frames=6, delay=0.35)

    print("=== 流程推进完毕！===")


if __name__ == "__main__":
    run_sequence()
