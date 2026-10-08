import urllib.request, json, sys
sys.stdout.reconfigure(encoding="utf-8")
body = json.dumps({"slot_a": 1, "slot_b": 2}).encode("utf-8")
req = urllib.request.Request("http://127.0.0.1:8765/api/v1/game/party/swap", data=body, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode("utf-8"))
print("==== 队伍调序实机执行结果 ====")
print(res.get("summary_zh"))
print("调换 A:", res.get("swapped_a"))
print("调换 B:", res.get("swapped_b"))
print("当前首发:", res.get("lead_pokemon"))
print("最新全队排布:", res.get("latest_lineup"))