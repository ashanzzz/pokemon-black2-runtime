import sys, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8")

req = urllib.request.Request("http://127.0.0.1:8765/api/v1/game/party")
with urllib.request.urlopen(req) as resp:
    party = json.loads(resp.read().decode("utf-8"))

slot1 = party["slots"][0]
print("=== Persistent Party Slot 1 Move PP Details ===")
print("Mon:", slot1.get("species_name_zh"), f"Lv.{slot1.get('level')}")
for m in slot1.get("moves", []):
    print(f"  Slot {m.get('slot')}: 【{m.get('name') or m.get('move_id')}】 PP: {m.get('current_pp')}/{m.get('max_pp')} (Base: {m.get('base_pp')}, PP Ups: {m.get('pp_ups')}, Usable: {m.get('usable')})")