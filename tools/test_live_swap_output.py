import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")
body = json.dumps({"slot_a": 1, "slot_b": 2}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:8765/api/v1/game/party/swap", data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))
print("Status:", res.get("status"))
print("Summary:", res.get("summary_zh"))
print("Swapped A:", res.get("swapped_a"))
print("Swapped B:", res.get("swapped_b"))
print("Lead Pokemon:", res.get("lead_pokemon"))
print("Latest Lineup:", res.get("latest_lineup"))