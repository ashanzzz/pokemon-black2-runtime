import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

def get(path):
    req = urllib.request.Request("http://127.0.0.1:8765" + path)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

# 1. Observation
obs = get("/api/v1/agent/observation")
print("=== 1. Unified Agent Observation (GET /api/v1/agent/observation) ===")
print("Mode:", obs.get("mode"))
print("UI State:", obs.get("ui"))
print("Player:", obs.get("player"))
print("Available Actions:", obs.get("available_actions"))
b = obs.get("battle")
if b:
    print("\nBattle Active:", b.get("active"))
    print("Battle Phase:", b.get("phase"))
    print("Waiting for Player:", b.get("waiting_for_player"))
    print("Player Actor:", b.get("player_actor", {}).get("species_name"), f"HP: {b.get('player_actor', {}).get('hp')}")
    print("Opponent Actor:", b.get("opponent_actor", {}).get("species_name"), f"HP: {b.get('opponent_actor', {}).get('hp')}")
    print("Legal Actions Count:", len(b.get("legal_actions", [])))
    print("Sample Legal Actions:", [a.get("type") + ":" + str(a.get("move_name") or a.get("species_name") or a.get("legal")) for a in b.get("legal_actions", [])[:6]])

# 2. UI State
ui = get("/api/v1/ui/state")
print("\n=== 2. UI State (GET /api/v1/ui/state) ===")
print("Screen:", ui.get("screen"))
print("Phase:", ui.get("phase"))
print("Waiting for Input:", ui.get("waiting_for_input"))

# 3. Battle Request
breq = get("/api/v1/battle/request")
print("\n=== 3. Battle Request (GET /api/v1/battle/request) ===")
print("Status:", breq.get("status"))
print("Battle ID:", breq.get("battle_id"))
print("Phase:", breq.get("phase"))
print("Waiting for Player:", breq.get("waiting_for_player"))