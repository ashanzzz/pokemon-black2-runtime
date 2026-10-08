import requests, json

r2 = requests.post('http://127.0.0.1:8765/api/v1/navigation/plans', json={
    'destination': {'type': 'grid', 'space': 'gen5-field-grid-v1', 'zone_id': 439, 'x': 107, 'z': 662, 'y': 2},
    'movement_mode': 'run'
}).json()

nodes = r2.get('route_detail', {}).get('nodes') or r2.get('segments', [{}])[0].get('path', [])
print(f'Route steps to Alder House doorstep: {len(nodes)}')
for idx, n in enumerate(nodes):
    print(f"  Step {idx}: ({n.get('x')}, {n.get('z')}, Y={n.get('y')})")
has_alder = any(n.get('x') == 111 and n.get('z') == 669 for n in nodes)
print(f'Does path touch Alder pos (111, 669)? -> {has_alder} (Expected: False)')
