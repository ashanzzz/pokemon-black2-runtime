import urllib.request, json, time

def api_get(url):
    with urllib.request.urlopen("http://127.0.0.1:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def api_post(url, data):
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:8765" + url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def exec_nav(zone_id, x, y, z, name):
    print(f"\n>>> [DISPATCH] {name} -> Zone {zone_id} ({x}, {y}, {z})...")
    payload = {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": zone_id, "x": x, "y": y, "z": z}, "movement_mode": "auto"}
    t = api_post("/api/v1/navigation/tasks", payload)
    tid = t["task_id"]
    t0 = time.time()
    for _ in range(40):
        time.sleep(0.4)
        info = api_get(f"/api/v1/navigation/tasks/{tid}")
        st = info.get("status")
        pos = info.get("current_node") or info.get("current", {}).get("position")
        if st in ("completed", "succeeded"):
            print(f"    [PASS] {name} reached in {time.time()-t0:.1f}s! Landed at {pos}")
            time.sleep(1.0)
            return True
        elif st in ("failed", "cancelled"):
            print(f"    [FAIL] {name} stopped: {info.get('stop_reason')}")
            return False
    return False

print("==========================================================================")
print("   AUTONOMOUS HOMECOMING TEST: MULTI-ZONE PIPELINE TO ASPERTIA CITY       ")
print("==========================================================================")
cur = api_get("/api/v1/player/runtime")
print(f"Current Position: Zone {cur['zone_id']} Grid ({cur['position']['grid']['x']}, {cur['position']['grid']['z']}, Y={cur['position']['grid']['y']})")

legs = [
    (446, 128, 2, 662, "Leg 4: Cruise West across Route 20 to Floccesy Bridge"),
    (439, 96, 1, 694, "Leg 5: Cruise West across Floccesy Town to Route 19"),
    (437, 52, 1, 702, "Leg 6: Cruise South down Route 19 to Aspertia Gate"),
    (438, 4, 0, 14, "Leg 7: Walk through Gate 438 to South Exit Mat"),
    (427, 59, 1, 724, "Leg 8: Arrive at Protagonist Home in Aspertia City"),
]

for zid, x, y, z, label in legs:
    ok = exec_nav(zid, x, y, z, label)
    if not ok:
        print(f"\n[BLOCKED] Pipeline paused at {label}.")
        break

final_cur = api_get("/api/v1/player/runtime")
print(f"\n==========================================================================")
print(f"Final Ground Truth: Zone {final_cur.get('zone_id')} Grid {final_cur.get('position', {}).get('grid')}")
print(f"==========================================================================")
