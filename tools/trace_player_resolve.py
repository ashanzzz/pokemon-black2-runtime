import urllib.request, json
with urllib.request.urlopen('http://localhost:8765/api/v1/player/runtime') as r:
    d = json.loads(r.read().decode('utf-8'))
print(json.dumps(d, indent=2, ensure_ascii=False))
