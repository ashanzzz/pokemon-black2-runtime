import urllib.request, json

for r in [4, 7, 10, 15]:
    ep = f"/api/v1/navigation/radar/slices?radius={r}"
    url = f"http://127.0.0.1:8765{ep}"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"[OK 200] radius={r} -> {len(data.get('slices', []))} slices, width={data.get('bounds', {}).get('width')}, height={data.get('bounds', {}).get('height')}")
    except Exception as e:
        print(f"[ERR] radius={r}: {e}")
