import urllib.request, json, time

def post(url, data):
    req = urllib.request.Request(
        "http://localhost:8765" + url,
        data=json.dumps(data).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def capture(label):
    res = post("/api/dev/capture", {"label": label})
    print(f"[{label}] Capture: {res.get('capture_url')}")

print("Pressing X...")
post("/api/actions/press", {"button": "X", "frames": 16})
time.sleep(1.0)
capture("main_menu_open")
