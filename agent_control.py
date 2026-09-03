#!/usr/bin/env python3
"""Pokémon Black 2 Semantic Runtime - Interactive Control CLI.

Allows inspecting game status, logging in / continuing game, reading dialogue,
and executing actions against the running BizHawk instance via API.
"""

import sys
import time
import requests
import json

BASE_URL = "http://127.0.0.1:8765"


def get_status():
    try:
        r = requests.get(f"{BASE_URL}/api/bizhawk/status", timeout=2)
        return r.json()
    except Exception as e:
        return {"error": f"Failed to connect to backend at {BASE_URL}: {e}"}


def get_doctor():
    try:
        r = requests.get(f"{BASE_URL}/api/bizhawk/doctor", timeout=3)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def get_state():
    try:
        r = requests.get(f"{BASE_URL}/api/state", timeout=3)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def action_title_start():
    try:
        r = requests.post(f"{BASE_URL}/api/actions/title_start", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def action_continue():
    try:
        r = requests.post(f"{BASE_URL}/api/actions/continue_game", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def action_dialogue_advance(steps=1):
    try:
        r = requests.post(f"{BASE_URL}/api/actions/dialogue/advance?steps={steps}", timeout=10)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def scan_dialogue():
    try:
        r = requests.get(f"{BASE_URL}/api/actions/dialogue/scan_text", timeout=5)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def press_button(btn: str, frames: int = 4):
    try:
        r = requests.post(f"{BASE_URL}/api/actions/press", json={"button": btn, "frames": frames}, timeout=3)
        return r.json()
    except Exception as e:
        return {"error": str(e)}


def main():
    if len(sys.argv) < 2:
        print("Usage: python agent_control.py <command>")
        print("Commands:")
        print("  status            - Check BizHawk and Bridge connection status")
        print("  doctor            - Run full BizHawk Doctor diagnostics")
        print("  state             - Get current Semantic Game State")
        print("  title-start       - Press START / Touch on Title Screen")
        print("  continue          - Select 'Continue Game' on Main Menu")
        print("  dialogue-next     - Advance dialogue box (1 step)")
        print("  dialogue-auto <N> - Auto advance dialogue N steps (default 5)")
        print("  scan-text         - Scan memory for currently rendered/loaded dialogue text")
        print("  press <button>    - Press button (A, B, X, Y, Up, Down, Left, Right, Start, Select)")
        return

    cmd = sys.argv[1].lower()

    if cmd == "status":
        print(json.dumps(get_status(), indent=2, ensure_ascii=False))
    elif cmd == "doctor":
        print(json.dumps(get_doctor(), indent=2, ensure_ascii=False))
    elif cmd == "state":
        print(json.dumps(get_state(), indent=2, ensure_ascii=False))
    elif cmd == "title-start":
        print("Sending Title Screen Start action...")
        print(json.dumps(action_title_start(), indent=2, ensure_ascii=False))
    elif cmd == "continue":
        print("Sending Continue Game action...")
        print(json.dumps(action_continue(), indent=2, ensure_ascii=False))
    elif cmd == "dialogue-next":
        print("Advancing dialogue...")
        print(json.dumps(action_dialogue_advance(1), indent=2, ensure_ascii=False))
    elif cmd == "dialogue-auto":
        steps = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        print(f"Auto-advancing dialogue ({steps} steps)...")
        print(json.dumps(action_dialogue_advance(steps), indent=2, ensure_ascii=False))
    elif cmd == "scan-text":
        print("Scanning dialogue text in memory...")
        print(json.dumps(scan_dialogue(), indent=2, ensure_ascii=False))
    elif cmd == "press":
        btn = sys.argv[2] if len(sys.argv) > 2 else "A"
        print(f"Pressing {btn}...")
        print(json.dumps(press_button(btn), indent=2, ensure_ascii=False))
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()
