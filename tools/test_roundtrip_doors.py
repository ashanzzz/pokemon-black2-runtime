import urllib.request, json, time

def api_get(url):
    with urllib.request.urlopen("http://127.0.0.1:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def api_post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def wait_task(tid, max_polls=35):
    for i in range(max_polls):
        time.sleep(0.3)
        t = api_get(f"/api/v1/navigation/tasks/{tid}")
        st = t.get("status")
        if st in ("completed", "succeeded", "failed", "cancelled"):
            return t
    return api_get(f"/api/v1/navigation/tasks/{tid}")

print("=== [ROUND-TRIP TEST] Seamless Exterior <-> Interior Warp ===")
cur = api_get("/api/v1/player/runtime")
print(f"Initial: Zone={cur['zone_id']}, Grid=({cur['position']['grid']['x']}, {cur['position']['grid']['z']}, Y={cur['position']['grid']['y']})")

# Step A: Enter Pokemon Center (from 210, 0, 649 -> door 210, 0, 648)
print("--- Step A: Entering Pokemon Center ---")
tA = api_post("/api/v1/navigation/tasks", {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 448, "x": 210, "y": 0, "z": 648}, "movement_mode": "auto"})
resA = wait_task(tA.get("task_id") or tA.get("id"))
print(f"Step A result: status={resA.get('status')}, transitions={resA.get('zone_transitions')}")

curA = api_get("/api/v1/player/runtime")
print(f"After Step A: Zone={curA['zone_id']}, Grid=({curA['position']['grid']['x']}, {curA['position']['grid']['z']}, Y={curA['position']['grid']['y']})")

time.sleep(0.5)

# Step B: Walk to counter (7, 0, 12)
print("--- Step B: Walking to Nurse Joy Counter ---")
tB = api_post("/api/v1/navigation/tasks", {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 454, "x": 7, "y": 0, "z": 12}, "movement_mode": "auto"})
resB = wait_task(tB.get("task_id") or tB.get("id"))
print(f"Step B result: status={resB.get('status')}, steps={resB.get('progress', {}).get('completed_steps')}")

time.sleep(0.5)

# Step C: Exit Pokemon Center by navigating to mat (7, 0, 19)
print("--- Step C: Exiting Pokemon Center via Mat ---")
tC = api_post("/api/v1/navigation/tasks", {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 454, "x": 7, "y": 0, "z": 19}, "movement_mode": "auto"})
resC = wait_task(tC.get("task_id") or tC.get("id"))
print(f"Step C result: status={resC.get('status')}, transitions={resC.get('zone_transitions')}")

curC = api_get("/api/v1/player/runtime")
print(f"Final: Zone={curC['zone_id']}, Grid=({curC['position']['grid']['x']}, {curC['position']['grid']['z']}, Y={curC['position']['grid']['y']})")
