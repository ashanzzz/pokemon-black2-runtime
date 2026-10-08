# tools/advance_dialogue.py
"""CLI and API client for dialogue inspection and automated progression."""

import sys
import requests

sys.stdout.reconfigure(encoding="utf-8")
API = "http://127.0.0.1:8765"

def run(max_steps=20, button="A", step_delay_ms=250):
    url = f"{API}/api/v1/dialogue/skip"
    body = {
        "max_steps": max_steps,
        "button": button,
        "step_delay_ms": step_delay_ms,
        "stop_on_choice": True,
    }
    try:
        res = requests.post(url, json=body, timeout=15).json()
    except Exception as e:
        print(f"[Error] API request failed: {e}")
        return

    if res.get("dialogue_was_active"):
        print("=" * 60)
        print("【文字打印机实时读取内容】:")
        print(res.get("captured_text", ""))
        print("=" * 60)
        print(f"\n[成功] 对话已全部跳过！共推进 {res.get('steps_taken')} 步，已完全回落至大地图自由探索状态。")
        final = res.get("final_screen", {})
        print(f"当前状态: {final.get('screen_type')} | 可移动: {final.get('can_move_player')} | 描述: {final.get('screen_description')}")
    else:
        final = res.get("final_screen", {})
        print(f"[状态] 当前无活跃对话，主角已处于【{final.get('screen_type')}】自由探索态。")

if __name__ == "__main__":
    run()
