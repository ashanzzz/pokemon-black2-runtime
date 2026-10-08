import urllib.request, json, time

def api_get(url):
    with urllib.request.urlopen("http://127.0.0.1:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

def api_post(url, data):
    body = json.dumps(data).encode("utf-8")
    for attempt in range(12):
        req = urllib.request.Request("http://127.0.0.1:8765" + url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 409 and attempt < 11:
                time.sleep(0.8)
                continue
            raise

def exec_nav(zone_id, x, y, z, name):
    print(f"\n>>> [DISPATCH] {name} -> Zone {zone_id} ({x}, {y}, {z})...")
    payload = {"destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": zone_id, "x": x, "y": y, "z": z}, "movement_mode": "auto"}
    for attempt in range(8):
        t = api_post("/api/v1/navigation/tasks", payload)
        tid = t["task_id"]
        t0 = time.time()
        for _ in range(50):
            time.sleep(0.4)
            info = api_get(f"/api/v1/navigation/tasks/{tid}")
            st = info.get("status")
            pos = info.get("current_node") or info.get("current", {}).get("position")
            if st in ("completed", "succeeded"):
                print(f"    [PASS] {name} reached in {time.time()-t0:.1f}s! Landed at {pos}")
                time.sleep(0.8)
                return True
            elif st in ("failed", "cancelled"):
                code = (info.get('stop_reason') or {}).get('code')
                if code == 'NAV_INTERRUPTED_BY_DIALOGUE':
                    print(f"    [DIALOGUE] Interrupted by field dialogue ({pos}); advancing dialogue...")
                    try:
                        api_post("/api/v1/dialogue/advance", {})
                    except Exception:
                        pass
                    time.sleep(0.8)
                    break
                elif code in ('NAV_PARTIAL_SEGMENT', 'NAV_LANDING_UNSETTLED', 'NAV_GRAPH_REVISION_CHANGED', 'NAV_NOT_CONTROLLABLE'):
                    print(f"    [PARTIAL] Progress made ({pos}); settling and retrying...")
                    time.sleep(0.6)
                    break
                else:
                    print(f"    [FAIL] {name} stopped: {info.get('stop_reason')}")
                    return False
    return False

print("==========================================================================")
print("   GRAND EXPEDITION: GYM INSPECTION -> HIGH PEAK LOOKOUT -> ALDER DOJO    ")
print("==========================================================================")
cur = api_get("/api/v1/player/runtime")
print(f"Start Position: Zone {cur['zone_id']} Grid ({cur['position']['grid']['x']}, {cur['position']['grid']['z']}, Y={cur['position']['grid']['y']})")

zid = int(cur['zone_id'])

# Stage 1: If in 489 (Gym), exit to 436 (Trainer School)
if zid == 489:
    print("\n1. Exiting Gym Battlefield to Trainer School...")
    exec_nav(489, 15, 0, 24, "Gym Exit Mat")
    api_post("/api/actions/press", {"buttons": ["Down"], "frames": 12})
    time.sleep(2.0)
    cur = api_get("/api/v1/player/runtime")
    zid = int(cur['zone_id'])

# Stage 2: If in 436 (Trainer School), exit to 427 (Aspertia City)
if zid == 436:
    print("\n2. Exiting Trainer School to Aspertia City...")
    exec_nav(436, 9, 0, 24, "School Exit Mat")
    api_post("/api/actions/press", {"buttons": ["Down"], "frames": 12})
    time.sleep(2.0)
    cur = api_get("/api/v1/player/runtime")
    zid = int(cur['zone_id'])

# Stage 3: In 427, Climb High Peak Lookout (Y=1 -> Y=5!)
if zid == 427:
    gx = int(cur['position']['grid']['x'])
    gz = int(cur['position']['grid']['z'])
    gy = int(cur['position']['grid'].get('y', 1))
    if gy < 5 and gz > 715:
        print("\n3. Climbing Grand Stone Stairs to High Peak Lookout (Y=5!)...")
        exec_nav(427, 36, 5, 724, "Lookout Peak (Altitude Y=+5)")
        time.sleep(1.0)

    # Stage 4: Descend Lookout Peak (Y=5 -> Y=1) to Aspertia Gate
    print("\n4. Descending Lookout Peak to Aspertia Gate...")
    exec_nav(427, 52, 1, 711, "Aspertia Gate Doorstep")
    api_post("/api/actions/press", {"buttons": ["Up"], "frames": 14})
    time.sleep(2.5)
    cur = api_get("/api/v1/player/runtime")
    zid = int(cur['zone_id'])

# Stage 5: In Gate 438, walk to Route 19 north exit
if zid == 438:
    print("\n5. Walking through Gate 438 to Route 19...")
    exec_nav(438, 4, 0, 1, "Gate North Mat")
    api_post("/api/actions/press", {"buttons": ["Up"], "frames": 14})
    time.sleep(2.5)
    cur = api_get("/api/v1/player/runtime")
    zid = int(cur['zone_id'])

# Stage 6: Cruise Route 19 to Floccesy Town
if zid == 437:
    print("\n6. Cruising Route 19 Highway to Floccesy Town...")
    exec_nav(437, 95, 1, 694, "Route 19 to Floccesy Entrance")
    exec_nav(439, 96, 1, 694, "Enter Floccesy Town")
    cur = api_get("/api/v1/player/runtime")
    zid = int(cur['zone_id'])

# Stage 7: Across Floccesy Town to Alder's Estate
if zid == 439:
    print("\n7. Cruising Floccesy Town to Champion Alder's Estate...")
    exec_nav(439, 112, 2, 668, "Champion Alder Meeting Point (Y=2)")
    # Approach Alder House Doorstep
    ok = exec_nav(439, 107, 2, 662, "Alder House Doorstep")
    if ok:
        # Stage 8: Enter Alder House (Zone 440)
        print("\n8. Entering Alder's Training Dojo...")
        api_post("/api/actions/press", {"buttons": ["Up"], "frames": 14})
        time.sleep(2.5)
        cur = api_get("/api/v1/player/runtime")
        zid = int(cur['zone_id'])
        if zid == 440:
            exec_nav(440, 5, 0, 5, "Alder Dojo Center Mat")
    else:
        print("\n8. Met with Champion Alder outside estate; story gate engaged.")

final_cur = api_get("/api/v1/player/runtime")
print(f"\n==========================================================================")
print(f"Grand Expedition Arrival: Zone {final_cur.get('zone_id')} Grid {final_cur.get('position', {}).get('grid')}")
print(f"==========================================================================")
