import urllib.request, json, time

print("Testing live execution to catwalk (17, 2, 44)...")
body = {
    "destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 17, "y": 2, "z": 44},
    "movement_mode": "auto"
}
req = urllib.request.Request("http://127.0.0.1:8765/api/v1/navigation/tasks", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    task = json.loads(resp.read().decode("utf-8"))
task_id = task["task_id"]
print("Task ID:", task_id, task["status"])

for i in range(25):
    time.sleep(0.3)
    with urllib.request.urlopen(f"http://127.0.0.1:8765/api/v1/navigation/tasks/{task_id}") as r:
        tdata = json.loads(r.read().decode("utf-8"))
        st = tdata.get("status")
        prog = tdata.get("progress")
        print(f"Poll {i+1}: status={st}, progress={prog}")
        if st in ("completed", "succeeded", "failed", "cancelled"):
            print("Task Completed! Result:")
            print("  status:", st)
            print("  arrival:", tdata.get("arrival"))
            print("  stop_reason:", tdata.get("stop_reason"))
            break
