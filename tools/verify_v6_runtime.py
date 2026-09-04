from __future__ import annotations
import json
import sys
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

BASE = sys.argv[1].rstrip('/') if len(sys.argv) > 1 else 'http://127.0.0.1:8765'
ENDPOINTS = [
    '/api/v1/map/v6/status',
    '/api/v1/map/v6/player/live',
    '/api/v1/map/v6/scene/current',
]

for path in ENDPOINTS:
    try:
        with urlopen(BASE + path, timeout=45) as r:
            data = json.load(r)
        print(f'PASS {path}')
        if path.endswith('/player/live'):
            print('  zone=', data.get('zone_id'), 'world=', data.get('world'), 'facing=', (data.get('orientation') or {}).get('facing'))
        if path.endswith('/scene/current'):
            print('  scene_key=', data.get('scene_key'), 'env=', data.get('environment'), 'origin=', data.get('scene_origin'))
    except (URLError, HTTPError, ValueError) as e:
        print(f'FAIL {path}: {e}')
