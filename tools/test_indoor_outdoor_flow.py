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

print("=== [TEST 3.2] Walk to Counter (7, 0, 12) -> Exit Mat (7, 0, 19) ===")
# 1. Walk from 19 to 12
print("1. Walking North to Counter (7, 0, 12)...")
t1 = api_post("/api/v1/navigation/tasks", {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 454, "x": 7, "y": 0, "z": 12}, "movement_mode": "auto"})
res1 = wait_task(t1.get("task_id") or t1.get("id"))
print(f"Step 1 status: {res1.get('status')}, steps={res1.get('progress', {}).get('completed_steps')}")

cur1 = api_get("/api/v1/player/runtime")
print(f"At counter: Zone={cur1['zone_id']}, Grid=({cur1['position']['grid']['x']}, {cur1['position']['grid']['z']}, Y={cur1['position']['grid']['y']})")

time.sleep(1.0)

# 2. Walk South from 12 to exit mat (7, 0, 19)
print("2. Walking South to Exit Mat (7, 0, 19)...")
t2 = api_post("/api/v1/navigation/tasks", {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 454, "x": 7, "y": 0, "z": 19}, "movement_mode": "auto"})
res2 = wait_task(t2.get("task_id") or t2.get("id"))
print(f"Step 2 status: {res2.get('status')}, transitions={res2.get('zone_transitions')}")

# Wait for fade transition
time.sleep(2.0)
cur2 = api_get("/api/v1/player/runtime")
print(f"Post-Exit: Zone={cur2.get('zone_id')}, Grid={cur2.get('position', {}).get('grid')}")
