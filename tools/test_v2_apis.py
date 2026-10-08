import urllib.request, json

endpoints = [
    ("GET", "/api/v1/player/runtime"),
    ("GET", "/api/v1/navigation/radar/slices?radius=4"),
    ("GET", "/api/v1/game/party"),
    ("GET", "/api/v1/game/inventory"),
    ("GET", "/api/v1/battle/decisions"),
    ("GET", "/api/v1/progression/state"),
]

for method, ep in endpoints:
    url = f"http://127.0.0.1:8765{ep}"
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=3) as resp:
            print(f"[OK {resp.status}] {ep}")
    except Exception as e:
        print(f"[ERR] {ep}: {e}")
