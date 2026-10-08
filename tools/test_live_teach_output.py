import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")
body = json.dumps({"party_slot": 2, "move_slot": 4, "move_id": 525}).encode("utf-8") # Teach TM82 龙尾
req = urllib.request.Request("http://127.0.0.1:8765/api/v1/game/party/teach-move", data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))
print("Status:", res.get("status"))
print("Summary:", res.get("summary_zh"))
print("Pokemon:", res.get("pokemon"))
print("Old Move:", res.get("old_move"))
print("New Move:", res.get("new_move"))
print("Checksum:", res.get("checksum"))
print("Current Moves:", res.get("current_moves"))