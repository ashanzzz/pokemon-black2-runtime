import urllib.request, json, time

body = {
    "destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 16, "y": 0, "z": 47},
    "movement_mode": "auto"
}
req = urllib.request.Request("http://127.0.0.1:8765/api/v1/navigation/tasks", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    task = json.loads(resp.read().decode("utf-8"))

task_id = task.get("task_id")
print("Task ID:", task_id, task.get("status"))

for i in range(15):
    time.sleep(0.3)
    with urllib.request.urlopen(f"http://127.0.0.1:8765/api/v1/navigation/tasks/{task_id}") as r:
        tdata = json.loads(r.read().decode("utf-8"))
        st = tdata.get("status")
        print(f"Poll {i+1}: status={st}, stop_reason={tdata.get('stop_reason')}")
        if st in ("succeeded", "failed", "cancelled", "completed"):
            print("Full task response:")
            print(json.dumps(tdata, indent=2, ensure_ascii=False))
            break
