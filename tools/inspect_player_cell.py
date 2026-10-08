import json
import urllib.request

# 1. 获取主角实时状态
with urllib.request.urlopen("http://localhost:8765/api/v1/player/runtime") as resp:
    player = json.loads(resp.read().decode('utf-8'))

pos = player.get("position", {}).get("grid", {})
zone_id = player.get("zone_id")
print(f"Player live: Zone={zone_id}, X={pos.get('x')}, Y={pos.get('y')}, Z={pos.get('z')}")

# 2. 获取雷达切片中主角所在格子的原始数据
with urllib.request.urlopen("http://localhost:8765/api/v1/navigation/radar/slices?radius=2") as resp:
    radar = json.loads(resp.read().decode('utf-8'))

for s in radar.get("slices", []):
    if s.get("floor_y") == pos.get("y"):
        for row in s.get("grid", []):
            for cell in row:
                if cell.get("x") == pos.get("x") and cell.get("z") == pos.get("z"):
                    print("Player cell in slice:")
                    print("  symbol:", repr(cell.get("symbol")))
                    print("  kind:", cell.get("kind"))
                    print("  tile_class:", cell.get("tile_class"), cell.get("tile_class_hex"))
                    print("  events:", cell.get("events"))
                    print("  interaction:", cell.get("interaction"))
                    print("  is_player:", cell.get("is_player"))
