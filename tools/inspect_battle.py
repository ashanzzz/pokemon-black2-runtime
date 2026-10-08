import urllib.request, json

def get(url):
    with urllib.request.urlopen("http://localhost:8765" + url) as resp:
        return json.loads(resp.read().decode("utf-8"))

b_state = get("/api/v1/battle/state")
print("=== BATTLE STATE ===")
print("Kind:", b_state.get("kind"), "Phase:", b_state.get("phase"))

ident = get("/api/v1/battle/identity")
print("\n=== BATTLE IDENTITY ===")
for side in ("player", "opponent"):
    p = ident.get(side) or {}
    print(f"[{side.upper()}] Species: {p.get('species_name')} (ID:{p.get('species_id')}) Lv.{p.get('level')} HP:{p.get('current_hp')}/{p.get('max_hp')}")

eval_res = get("/api/v1/battle/capture-eval")
print("\n=== CAPTURE PROBABILITY EVALUATION ===")
print(json.dumps(eval_res, indent=2, ensure_ascii=False))
