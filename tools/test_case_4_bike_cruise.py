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

print("=== [TEST 4] Auto-Mount Bike Long-Distance Cruising ===")
cur = api_get("/api/v1/player/runtime")
print(f"Start: Zone={cur['zone_id']}, Grid=({cur['position']['grid']['x']}, {cur['position']['grid']['z']}, Y={cur['position']['grid']['y']}), Mode={cur['locomotion']['transport_mode']}")

# Cruise South from 649 to 665
dest = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 448, "x": 210, "y": 0, "z": 665}
print(f"Navigating South to (210, 0, 665)...")
t = api_post("/api/v1/navigation/tasks", {"destination": dest, "movement_mode": "auto"})
tid = t.get("task_id") or t.get("id")
print("Task ID:", tid, "Mode selected:", t.get("movement_mode") or t.get("movement", {}).get("selected"))

t0 = time.time()
for i in range(40):
    time.sleep(0.3)
    tdata = api_get(f"/api/v1/navigation/tasks/{tid}")
    st = tdata.get("status")
    prog = tdata.get("progress") or {}
    cur_pos = tdata.get("current_node") or tdata.get("current", {}).get("position")
    print(f"[{time.time()-t0:.1f}s] Poll #{i+1}: status={st}, steps={prog.get('completed_steps')}/{prog.get('total_steps')}, current={cur_pos}")
    if st in ("completed", "succeeded", "failed", "cancelled"):
        print(f"Task finished in {time.time()-t0:.2f}s with status={st}")
        break

post_cur = api_get("/api/v1/player/runtime")
print(f"Arrival: Zone={post_cur['zone_id']}, Grid=({post_cur['position']['grid']['x']}, {post_cur['position']['grid']['z']}, Y={post_cur['position']['grid']['y']}), Mode={post_cur['locomotion']['transport_mode']}")
