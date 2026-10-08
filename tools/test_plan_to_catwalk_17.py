import urllib.request, json

body = {
    "destination": {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 457, "x": 17, "y": 2, "z": 44},
    "movement_mode": "auto"
}
req = urllib.request.Request("http://127.0.0.1:8765/api/v1/navigation/plans", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    print("Plan to catwalk (17, 2, 44):")
    print("  Status:", data.get("status"))
    print("  Selected mode:", data.get("movement", {}).get("selected"))
    print("  Steps:", data.get("cost", {}).get("steps"))
    print("  Actions count:", len(data.get("route_detail", {}).get("actions", [])))
    for a in data.get("route_detail", {}).get("actions", []):
        print(f"    {a.get('direction')} {a.get('steps')} steps from ({a.get('from',{}).get('x')}, {a.get('from',{}).get('z')}, Y={a.get('from',{}).get('y')}) to ({a.get('to',{}).get('x')}, {a.get('to',{}).get('z')}, Y={a.get('to',{}).get('y')})")
