import urllib.request, json, time

def api_get(url):
    with urllib.request.urlopen("http://127.0.0.1:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def api_post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

cur = api_get("/api/v1/player/runtime")
print("Start position:", cur["position"]["grid"])
print("Transport:", cur["locomotion"]["transport_mode"])

# Move 1 tile south on bike: from 650 to 651
dest = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 448, "x": 210, "y": 0, "z": 651}
task = api_post("/api/v1/navigation/tasks", {"destination": dest, "movement_mode": "bike"})
tid = task.get("task_id") or task.get("id")
print("Task 1 tile:", tid)
for _ in range(15):
    time.sleep(0.2)
    t = api_get(f"/api/v1/navigation/tasks/{tid}")
    if t.get("status") in ("completed", "failed"):
        print("Result 1 tile:", t.get("status"), t.get("continuous_segments"))
        break

# Now move 2 tiles south on bike: from 651 to 653
cur = api_get("/api/v1/player/runtime")
print("Now at:", cur["position"]["grid"])
dest = {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 448, "x": 210, "y": 0, "z": 653}
task = api_post("/api/v1/navigation/tasks", {"destination": dest, "movement_mode": "bike"})
tid = task.get("task_id") or task.get("id")
print("Task 2 tiles:", tid)
for _ in range(15):
    time.sleep(0.2)
    t = api_get(f"/api/v1/navigation/tasks/{tid}")
    if t.get("status") in ("completed", "failed"):
        print("Result 2 tiles:", t.get("status"), t.get("continuous_segments"))
        break
