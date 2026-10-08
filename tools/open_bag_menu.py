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

# Select Bag: Down, then A
print("Selecting Bag...")
post("/api/actions/press", {"button": "Down", "frames": 16})
time.sleep(0.5)

post("/api/actions/press", {"button": "A", "frames": 16})
time.sleep(1.5)

capture("inside_bag")
