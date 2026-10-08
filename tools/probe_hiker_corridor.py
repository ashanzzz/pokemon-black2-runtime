import sys, time, requests
sys.stdout.reconfigure(encoding="utf-8")
BASE_URL = "http://127.0.0.1:8765"

def get_player():
    r = requests.get(f"{BASE_URL}/api/v1/player/runtime").json()
    return {
        "zone_id": r.get("zone_id"),
        "grid": r.get("position", {}).get("grid") or r.get("grid"),
        "facing": r.get("orientation", {}).get("facing"),
    }

def get_dialogue():
    r = requests.get(f"{BASE_URL}/api/v1/dialogue/current").json()
    return {
        "active": r.get("is_active"),
        "screen": r.get("screen_type"),
        "text": r.get("text", ""),
    }

def press(btn, frames=8, delay=0.5):
    requests.post(f"{BASE_URL}/api/actions/press", json={"button": btn, "frames": frames})
    time.sleep(delay)

print("=" * 60)
print("  Zone 446 登山大叔 (159, 645) 阻挡触发器现场实测")
print("=" * 60)

p0 = get_player()
d0 = get_dialogue()
print(f"起点位置: {p0['grid']} | 朝向: {p0['facing']} | 对话: {d0['active']}")

# Test 1: Step North from (158, 645) -> (158, 644)
print("\n--- 测试 1: 从 (158, 645) 向北按 [Up] 迈入 Z=644 ---")
press("Up", frames=8, delay=0.8)
p1 = get_player()
d1 = get_dialogue()
print(f"落脚点: {p1['grid']} | 朝向: {p1['facing']} | 对话: {d1['active']}")
if d1["active"]:
    print(f"  触发拦截对话: {d1['text'][:60]}")
    # Dismiss dialogue by pressing B repeatedly
    for _ in range(8):
        press("B", frames=4, delay=0.25)
    time.sleep(0.5)
    p1_after = get_player()
    print(f"  对话结束后主角位置: {p1_after['grid']}")

