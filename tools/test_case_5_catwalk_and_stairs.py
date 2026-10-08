import urllib.request, json, time

def api_get(url):
    with urllib.request.urlopen("http://127.0.0.1:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def api_post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def wait_task(tid, max_polls=60):
    for i in range(max_polls):
        time.sleep(0.3)
        t = api_get(f"/api/v1/navigation/tasks/{tid}")
        st = t.get("status")
        prog = t.get("progress") or {}
        pos = t.get("current_node") or t.get("current", {}).get("position")
        print(f"  [Poll #{i+1}] status={st}, steps={prog.get('completed_steps')}/{prog.get('total_steps')}, pos={pos}")
        if st in ("completed", "succeeded", "failed", "cancelled"):
            return t
    return api_get(f"/api/v1/navigation/tasks/{tid}")

print("=== [TEST 5.2] Stairs Ascent (Y=0 -> Y=2) & Catwalk Traversal ===")
cur = api_get("/api/v1/player/runtime")
print(f"Start: Zone={cur['zone_id']}, Grid=({cur['position']['grid']['x']}, {cur['position']['grid']['z']}, Y={cur['position']['grid']['y']}), Mode={cur['locomotion']['transport_mode']}")

# Step 1: Navigate from (18, 0, 18) to (14, 0, 46) (stairs approach)
print("--- Step 1: Navigating to Stairs Approach (14, 0, 46) ---")
t1 = api_post("/api/v1/navigation/tasks", {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 14, "y": 0, "z": 46}, "movement_mode": "auto"})
res1 = wait_task(t1.get("task_id") or t1.get("id"))
print(f"Step 1 status: {res1.get('status')}")

# Step 2: Climb stairs onto high platform (10, 2, 44)
print("--- Step 2: Climbing Stairs onto High Platform (10, 2, 44) ---")
t2 = api_post("/api/v1/navigation/tasks", {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 10, "y": 2, "z": 44}, "movement_mode": "auto"})
res2 = wait_task(t2.get("task_id") or t2.get("id"))
print(f"Step 2 status: {res2.get('status')}")

cur2 = api_get("/api/v1/player/runtime")
print(f"On high platform: Zone={cur2['zone_id']}, Grid=({cur2['position']['grid']['x']}, {cur2['position']['grid']['z']}, Y={cur2['position']['grid']['y']})")

# Step 3: Navigate across high platform to catwalk (17, 2, 44)
# Route crosses catwalk entry (16, 2, 44) and reaches catwalk (17, 2, 44)
# System should automatically dismount bike and use run/walk mode
print("--- Step 3: Traversing across Catwalk Entry to (17, 2, 44) ---")
t3 = api_post("/api/v1/navigation/tasks", {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 17, "y": 2, "z": 44}, "movement_mode": "auto"})
res3 = wait_task(t3.get("task_id") or t3.get("id"))
print(f"Step 3 status: {res3.get('status')}, mode={res3.get('movement_mode')}")

cur3 = api_get("/api/v1/player/runtime")
print(f"Final state: Zone={cur3['zone_id']}, Grid=({cur3['position']['grid']['x']}, {cur3['position']['grid']['z']}, Y={cur3['position']['grid']['y']}), Mode={cur3['locomotion']['transport_mode']}")
