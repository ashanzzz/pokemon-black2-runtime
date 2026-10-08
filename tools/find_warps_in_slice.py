import urllib.request, json, sys
sys.stdout.reconfigure(encoding='utf-8')
url = 'http://127.0.0.1:8765/api/navigation/radar/slices?radius=15'
# wait, endpoint is /api/v1/navigation/radar/slices
url = 'http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=15'
with urllib.request.urlopen(url) as resp:
    data = json.loads(resp.read())
for s in data['slices']:
    for row in s['grid']:
        for c in row:
            if c.get('has_warp') or c.get('symbol') == 'D':
                print(f"Warp at ({c['x']}, {c['z']}, Y={c['y']}) sym={c['symbol']} is_doorstep={c.get('is_doorstep')} kind={c['kind']}")
