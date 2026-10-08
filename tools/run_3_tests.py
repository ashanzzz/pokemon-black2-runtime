import urllib.request, json

def plan(dest):
    body = {"destination": dest, "movement_mode": "auto"}
    req = urllib.request.Request("http://127.0.0.1:8765/api/v1/navigation/plans", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"Plan to ({dest['x']}, {dest['z']}, Y={dest['y']}): OK {data.get('status')}, steps={data.get('cost', {}).get('steps')}")
            for a in data.get("route_detail", {}).get("actions", []):
                print(f"   Action: {a.get('direction')} {a.get('steps')} steps from ({a.get('from', {}).get('x')}, {a.get('from', {}).get('z')}, Y={a.get('from', {}).get('y')}) to ({a.get('to', {}).get('x')}, {a.get('to', {}).get('z')}, Y={a.get('to', {}).get('y')})")
    except urllib.error.HTTPError as e:
        err = json.loads(e.read().decode("utf-8"))
        print(f"Plan to ({dest['x']}, {dest['z']}, Y={dest['y']}): REJECTED {err.get('error', {}).get('code')}: {err.get('error', {}).get('message')}")

print("--- RUNNING 3 TEST CASES ---")
# Test 1: (11, 2, 45) - invalid air tile
plan({"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 11, "y": 2, "z": 45})

# Test 2: (11, 0, 45) - valid ground tile
plan({"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 11, "y": 0, "z": 45})

# Test 3: (15, 2, 44) - valid platform tile
plan({"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 15, "y": 2, "z": 44})
