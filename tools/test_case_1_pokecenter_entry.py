import urllib.request, json, time

def api_get(url):
    with urllib.request.urlopen("http://127.0.0.1:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def api_post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

print("=== [TEST 1] Exterior Zone 448 -> Pokemon Center Door (210, 0, 648) ===")
cur = api_get("/api/v1/player/runtime")
print(f"Current State: Zone={cur['zone_id']}, Grid=({cur['position']['grid']['x']}, {cur['position']['grid']['z']}, Y={cur['position']['grid']['y']}), Mode={cur['locomotion']['transport_mode']}")

dest = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 448, "x": 210, "y": 0, "z": 648}
print(f"Submitting navigation task to Pokemon Center door: {dest}...")
task = api_post("/api/v1/navigation/tasks", {"destination": dest, "movement_mode": "auto"})
task_id = task.get("task_id") or task.get("id")
print(f"Task dispatched: ID={task_id}, status={task.get('status')}")

t0 = time.time()
for i in range(40):
    time.sleep(0.3)
    tdata = api_get(f"/api/v1/navigation/tasks/{task_id}")
    st = tdata.get("status")
    cur_pos = tdata.get("current_node") or tdata.get("current", {}).get("position")
    prog = tdata.get("progress") or {}
    print(f"[{time.time()-t0:.1f}s] Poll #{i+1}: status={st}, steps={prog.get('completed_steps')}/{prog.get('total_steps')}, current={cur_pos}")
    if st in ("completed", "succeeded", "failed", "cancelled"):
        print(f"Task completed in {time.time()-t0:.2f}s with status={st}")
        print("Detail:", json.dumps(tdata, indent=2, ensure_ascii=False))
        break

post_cur = api_get("/api/v1/player/runtime")
print(f"Post-Test State: Zone={post_cur['zone_id']}, Grid=({post_cur['position']['grid']['x']}, {post_cur['position']['grid']['z']}, Y={post_cur['position']['grid']['y']}), Mode={post_cur['locomotion']['transport_mode']}")
