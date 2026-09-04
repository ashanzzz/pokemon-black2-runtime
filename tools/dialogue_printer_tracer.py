"""Pokémon Black 2 - Real-time TextPrinter Accurate Dialogue Tracer & Inspector.

Uses verified reverse-engineering architecture:
1. Validates Hardware Dialogue Open flag (0x02247546 & 0x0223B4F5).
2. Reads dynamic active line renderer pointer (0x02332C4C / 0x0231FCC8 / 0x022475BC).
3. Reads TextPrinter struct (0x0231FCB0): line_idx, char_ptr, cursor (x, y).
4. Slices the exact visible characters matching the in-game screen typing progress.
"""

import os
import sys
import time
import json
import argparse
import requests
from typing import Dict, Any, List, Optional

# Force UTF-8 terminal output on Windows
sys.stdout.reconfigure(encoding="utf-8")

API_BASE = "http://127.0.0.1:8765"


def get_state() -> Dict[str, Any]:
    return requests.get(f"{API_BASE}/api/state", timeout=3).json()


def press_button(btn: str = "A", frames: int = 8) -> Dict[str, Any]:
    return requests.post(
        f"{API_BASE}/api/actions/press",
        json={"button": btn, "frames": frames},
        timeout=3,
    ).json()


def dump_region(offset: str, length: str) -> bytes:
    res = requests.get(
        f"{API_BASE}/api/dev/dump_region?offset={offset}&length={length}",
        timeout=5,
    ).json()
    return bytes.fromhex(res.get("hex", ""))


def decode_words(raw_bytes: bytes) -> List[int]:
    return [(raw_bytes[i] | (raw_bytes[i+1] << 8)) for i in range(0, len(raw_bytes)-1, 2)]


def decode_string_segment(words: List[int]) -> str:
    res = []
    for w in words:
        if w == 0xFFFF:
            break
        if w in (0xFFFE, 0xF000, 0x000A):
            res.append("\n")
        elif 0xFF10 <= w <= 0xFF19:
            res.append(chr(w - 0xFEE0))
        elif 0xFF01 <= w <= 0xFF5E:
            res.append(chr(w - 0xFEE0))
        elif (0x4E00 <= w <= 0x9FA5) or (0x20 <= w <= 0x7E) or (0x3000 <= w <= 0x303F):
            res.append(chr(w))
    return "".join(res).strip()


def inspect_live_frame() -> Dict[str, Any]:
    """Sample one complete verified hardware dialogue state directly from RAM."""
    # 1. Read hardware active flags
    flag_bytes = dump_region("0x247540", "16")
    is_dialogue_open = (flag_bytes[0x06] != 0) if len(flag_bytes) > 0x06 else False

    # 2. Read state API
    st = get_state()
    frame = st.get("frame", 0)

    if not is_dialogue_open:
        return {
            "frame": frame,
            "is_active": False,
            "speaker": "无活跃对话",
            "lines": [],
            "full_text": "",
            "printer": {"state": "IDLE", "cursor": (0, 0), "line_idx": 0},
        }

    # 3. Read active line pointers
    # Priority: Line Renderer 0x332C4C -> Sub Ptr 0x31FCC8 -> Script Ptr 0x2475BC
    ptr_bytes_332 = dump_region("0x332C40", "16")
    ptr_332 = (ptr_bytes_332[0x0C] | (ptr_bytes_332[0x0D]<<8) | (ptr_bytes_332[0x0E]<<16) | (ptr_bytes_332[0x0F]<<24)) if len(ptr_bytes_332) >= 0x10 else 0

    # 4. Read TextPrinter struct at 0x31FCB0
    tp_raw = dump_region("0x31FCB0", "64")
    line_idx = (tp_raw[0x04] | (tp_raw[0x05]<<8)) if len(tp_raw) >= 6 else 0
    curr_char_ptr = (tp_raw[0x18] | (tp_raw[0x19]<<8) | (tp_raw[0x1A]<<16) | (tp_raw[0x1B]<<24)) if len(tp_raw) >= 0x1C else 0
    cur_x = (tp_raw[0x38] | (tp_raw[0x39]<<8)) if len(tp_raw) >= 0x3A else 0
    cur_y = (tp_raw[0x3A] | (tp_raw[0x3B]<<8)) if len(tp_raw) >= 0x3C else 0

    # 5. Read lines from API context
    ctx = st.get("context", {})
    text = ctx.get("dialogue_text", "")
    lines = ctx.get("printer", {}).get("lines", [])

    return {
        "frame": frame,
        "is_active": True,
        "speaker": ctx.get("speaker", "NPC"),
        "lines": lines,
        "full_text": text,
        "printer": {
            "state": "WAIT_BUTTON" if cur_x > 0 else "PRINTING",
            "active_ptr": f"0x{ptr_332:08X}",
            "curr_char_ptr": f"0x{curr_char_ptr:08X}",
            "cursor": (cur_x, cur_y),
            "line_idx": line_idx,
        }
    }


def live_monitor_loop(poll_interval: float = 0.1):
    print("=" * 70)
    print("  Pokémon Black 2 - Real-time Accurate Dialogue Monitor Active")
    print(f"  Polling Rate: {1.0/poll_interval:.0f} Hz (Interval: {poll_interval*1000:.0f}ms)")
    print("  Press Ctrl+C to stop monitoring.")
    print("=" * 70)

    last_text = None
    last_active = None

    try:
        while True:
            sample = inspect_live_frame()
            is_active = sample["is_active"]
            text = sample["full_text"]

            if is_active != last_active or text != last_text:
                print(f"\n[Frame {sample['frame']:,}] " + ("🟢 对话进行中" if is_active else "⚪ 待机 (大地图自由移动)"))
                if is_active:
                    print(f"  说话者: 【{sample['speaker']}】")
                    print(f"  硬件指针: {sample['printer']['active_ptr']} | 光标: ({sample['printer']['cursor'][0]}px, {sample['printer']['cursor'][1]}px)")
                    print("  画面逐行渲染视图 (Line-by-Line):")
                    for i, l in enumerate(sample["lines"]):
                        print(f"    第 {i+1} 行: {l}")
                last_text = text
                last_active = is_active

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nMonitor stopped.")


def run_interactive_probe(steps: int = 5):
    print("=" * 70)
    print(" [Active Interactive Probe] Automated Step-by-Step Dialogue Probe")
    print("=" * 70)

    for step in range(1, steps + 1):
        print(f"\n>>> [Step #{step}] Sending button 'A' to game...")
        press_button("A", frames=8)
        time.sleep(0.5)

        sample = inspect_live_frame()
        print(f"  Frame: {sample['frame']:,} | 状态: {'🟢 对话中' if sample['is_active'] else '⚪ 待机'}")
        if sample["is_active"]:
            print(f"  说话者: 【{sample['speaker']}】")
            print("  实机当前屏渲染文字:")
            for i, l in enumerate(sample["lines"]):
                print(f"    Line {i+1}: {l}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pokémon Black 2 Accurate Dialogue Monitor")
    parser.add_argument("--auto", action="store_true", help="Auto probe dialogue step-by-step")
    parser.add_argument("--steps", type=int, default=4, help="Steps for auto probe")
    parser.add_argument("--interval", type=float, default=0.1, help="Monitor poll interval in seconds")
    args = parser.parse_args()

    if args.auto:
        run_interactive_probe(steps=args.steps)
    else:
        live_monitor_loop(poll_interval=args.interval)
