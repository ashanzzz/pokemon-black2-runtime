import requests, json

print('=== 1. Plan to ground level Y=1 (111, 1, 672) down stairs ===')
r1 = requests.post('http://127.0.0.1:8765/api/v1/navigation/plans', json={
    'destination': {'type': 'grid', 'space': 'gen5-field-grid-v1', 'zone_id': 439, 'x': 111, 'z': 672, 'y': 1},
    'movement_mode': 'run'
}).json()
print('Status 1:', r1.get('status'))
if r1.get('status') == 'success':
    nodes = r1.get('route_detail', {}).get('nodes', [])
    print(f'Route steps: {len(nodes)}')
    for idx, n in enumerate(nodes):
        print(f"  Step {idx}: ({n.get('x')}, {n.get('z')}, Y={n.get('y')})")
    has_alder = any(n.get('x') == 111 and n.get('z') == 669 for n in nodes)
    print(f'Does path hit Alder (111, 669)? -> {has_alder}')
else:
    print('Failed reason:', r1.get('error', {}).get('message') or r1.get('reason'))

print('\n=== 2. Plan to adjacent node on same high plateau Y=2 (107, 2, 662) ===')
r2 = requests.post('http://127.0.0.1:8765/api/v1/navigation/plans', json={
    'destination': {'type': 'grid', 'space': 'gen5-field-grid-v1', 'zone_id': 439, 'x': 107, 'z': 662, 'y': 2},
    'movement_mode': 'run'
}).json()
print('Status 2:', r2.get('status'))
if r2.get('status') == 'success':
    nodes2 = r2.get('route_detail', {}).get('nodes', [])
    print(f'Route steps: {len(nodes2)}')
    for idx, n in enumerate(nodes2[:6]):
        print(f"  Step {idx}: ({n.get('x')}, {n.get('z')}, Y={n.get('y')})")
    has_alder2 = any(n.get('x') == 111 and n.get('z') == 669 for n in nodes2)
    print(f'Does path hit Alder (111, 669)? -> {has_alder2}')
