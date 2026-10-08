import urllib.request, json, time

def api_get(url):
    with urllib.request.urlopen("http://127.0.0.1:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def api_post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

print("==========================================================================")
print("   AUTONOMOUS GLOBAL NAVIGATION PIPELINE: RETURN TO ASPERTIA CITY (HOME)  ")
print("==========================================================================")

cur = api_get("/api/v1/player/runtime")
print(f"Start Position: Zone {cur['zone_id']} | Grid ({cur['position']['grid']['x']}, {cur['position']['grid']['z']}, Y={cur['position']['grid']['y']}) | Mode={cur['locomotion']['transport_mode']}")

# Dispatch global task
req_body = {
    "goal_zone": 427,
    "poi": "home",
    "movement_mode": "auto",
    "flee_wild_battles": True
}
print(f"\nDispatching POST /api/v1/navigation/global/tasks: {req_body}...")
gtask = api_post("/api/v1/navigation/global/tasks", req_body)
gtask_id = gtask["task_id"]
print(f"Global Task ID: {gtask_id}")
print(f"Macro Zone Path: {gtask.get('zone_path')}")
print(f"POI Target: {gtask.get('poi')} -> {gtask.get('poi_description')} {gtask.get('final_destination')}\n")

t0 = time.time()
last_step = -1
last_micro = None
while time.time() - t0 < 300:  # 5 minutes max
    time.sleep(1.0)
    info = api_get(f"/api/v1/navigation/global/tasks/{gtask_id}")
    st = info.get("status")
    step_idx = info.get("current_step_index")
    micro_id = info.get("current_micro_task_id")
    
    cur_p = api_get("/api/v1/player/runtime")
    z = cur_p.get("zone_id")
    g = cur_p.get("position", {}).get("grid") or {}
    mode = cur_p.get("locomotion", {}).get("transport_mode")

    if step_idx != last_step or micro_id != last_micro:
        last_step = step_idx
        last_micro = micro_id
        print(f"[{time.time()-t0:.1f}s] [Pipeline Step {step_idx}/{info.get('total_steps')}] Active Micro Task: {micro_id}")
    
    print(f"    [{time.time()-t0:.1f}s] Status={st} | Live: Zone {z} Grid=({g.get('x')}, {g.get('z')}, Y={g.get('y')}) | Mode={mode}")

    if st in ("completed", "succeeded"):
        print(f"\n[SUCCESS] Global journey completed in {time.time()-t0:.2f}s!")
        print("Final Arrival:", info.get("arrival"))
        break
    elif st in ("failed", "cancelled"):
        print(f"\n[STOPPED] Global journey stopped with status={st}!")
        print("Error details:", info.get("error"))
        break

final_cur = api_get("/api/v1/player/runtime")
print(f"\nFinal Ground Truth: Zone {final_cur.get('zone_id')} | Grid {final_cur.get('position', {}).get('grid')}")
