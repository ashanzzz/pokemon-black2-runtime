"""Pokémon Black 2 - Pure Memory Gameplay Driver.

Executes autonomous game progression using 100% ARM9 RAM reads:
1. Active message & dialogue state tracking (0x022490A0, 0x0233605C).
2. Atomic coordinate verification (0x02143620).
3. Active actor and trigger state transitions.
"""

import time
import requests
import json
from typing import Dict, Any, List, Tuple, Optional

API_BASE = "http://127.0.0.1:8765"


def get_state() -> Dict[str, Any]:
    return requests.get(f"{API_BASE}/api/state", timeout=3).json()


def press_button(btn: str, frames: int = 8) -> Dict[str, Any]:
    return requests.post(
        f"{API_BASE}/api/actions/press",
        json={"button": btn, "frames": frames},
        timeout=3,
    ).json()


def advance_dialogue(steps: int = 1) -> Dict[str, Any]:
    return requests.post(
        f"{API_BASE}/api/actions/dialogue/advance?steps={steps}",
        timeout=5,
    ).json()


def select_choice(index: int = 0) -> Dict[str, Any]:
    return requests.post(
        f"{API_BASE}/api/actions/dialogue/choice",
        json={"index": index},
        timeout=5,
    ).json()


def run_memory_gameplay(max_iterations: int = 100):
    print("=" * 65)
    print("   Pokémon Black 2 - Pure Memory Gameplay Agent Active")
    print("=" * 65)

    last_dialogue = ""
    last_coords = None
    step_count = 0
    consecutive_idle = 0

    # Target path milestones in Aspertia City (桧扇市):
    # Start: Player house outdoor (47, 771)
    # Milestone 1: North road junction (47, 740)
    # Milestone 2: North town center / Hugh's house (47, 715)
    # Milestone 3: Northwest Lookout Point steps (28, 705)
    # Milestone 4: Lookout Point platform / Bianca (16, 705)
    WAYPOINTS = [
        {"x": 47, "y": 740, "desc": "桧扇市主干道 (中段)"},
        {"x": 47, "y": 715, "desc": "桧扇市北街区 / 劲敌家门口"},
        {"x": 28, "y": 705, "desc": "展望台西侧台阶路口"},
        {"x": 16, "y": 705, "desc": "展望台顶层平台 (寻找白露)"},
    ]
    current_waypoint_idx = 0

    for iteration in range(1, max_iterations + 1):
        time.sleep(0.3)
        st = get_state()
        ctx = st.get("context", {})
        st_type = ctx.get("screen_type", "OVERWORLD")
        is_dlg = ctx.get("is_dialogue_active", False)
        dlg_text = ctx.get("dialogue_text", "")
        speaker = ctx.get("speaker", "")
        coords = st.get("player_world_pos", {})
        cx = coords.get("x")
        cy = coords.get("y")
        cz = coords.get("z")

        print(f"\n[Turn #{iteration:03d} | Frame {st.get('frame')}] Scene: {st_type} | Coords: (X={cx}, Y={cy}, Elev={cz})")

        # Case 1: In Dialogue Choice (分支选择提示)
        if st_type == "DIALOGUE_CHOICE" or (is_dlg and ctx.get("choices")):
            print(f"  [RAM Event: Choice Prompt] Speaker: {speaker} | Text: {repr(dlg_text)}")
            choices = ctx.get("choices", [])
            print(f"  [RAM Action] Selecting default positive choice: {choices[0] if choices else 'Index 0'}")
            select_choice(0)
            time.sleep(0.5)
            continue

        # Case 2: In Active Dialogue / Cutscene (剧情对话)
        if is_dlg or st_type == "DIALOGUE_ACTIVE":
            if dlg_text != last_dialogue:
                print(f"  [RAM Event: Dialogue] 【{speaker}】: \"{dlg_text.replace(chr(10), ' ')}\"")
                last_dialogue = dlg_text
            advance_dialogue(1)
            time.sleep(0.4)
            continue

        # Case 3: Overworld Movement & Navigation (大地图自由移动)
        if cx is not None and cy is not None:
            if current_waypoint_idx < len(WAYPOINTS):
                target = WAYPOINTS[current_waypoint_idx]
                tx, ty, desc = target["x"], target["y"], target["desc"]
                dx = tx - cx
                dy = ty - cy

                # Check if reached current waypoint
                if abs(dx) <= 1 and abs(dy) <= 1:
                    print(f"  [RAM Navigation] Reached Waypoint #{current_waypoint_idx+1}: {desc} ({cx}, {cy})!")
                    current_waypoint_idx += 1
                    continue

                # Decide movement direction
                move_btn = None
                if dy < 0:
                    move_btn = "Up"
                elif dy > 0:
                    move_btn = "Down"
                elif dx < 0:
                    move_btn = "Left"
                elif dx > 0:
                    move_btn = "Right"

                if move_btn:
                    print(f"  [RAM Navigation] Heading towards {desc} ({tx}, {ty}). Step: {move_btn}...")
                    press_button(move_btn, frames=14)
                    time.sleep(0.3)

                    # Read new state to verify coordinate displacement
                    st_after = get_state()
                    new_c = st_after.get("player_world_pos", {})
                    nx, ny = new_c.get("x"), new_c.get("y")
                    if (nx, ny) != (cx, cy):
                        print(f"  [RAM Verified Move] Coords updated: ({cx}, {cy}) -> ({nx}, {ny}) [Success]")
                    else:
                        print(f"  [RAM Obstacle / Trigger Wait] Position unchanged ({cx}, {cy}). May have encountered NPC/Trigger.")
            else:
                print("  [RAM Navigation] All initial waypoints reached. Scanning for interactions...")
                press_button("A", frames=8)
                time.sleep(0.4)

    print("\n[Pure Memory Runner] Finished test cycle.")


if __name__ == "__main__":
    run_memory_gameplay(60)
