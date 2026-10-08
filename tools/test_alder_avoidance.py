import sys; sys.path.insert(0, '.')
import requests, json

# 1. 检查雷达切片中阿戴克所在格 (111, 669, Y=2) 的瓦片数据
print('=== 1. Checking Radar Slice at Alder pos (111, 669, Y=2) ===')
r = requests.get('http://127.0.0.1:8765/api/v1/navigation/radar/slices?radius=4').json()
slices = r.get('slices', [])
found_cell = None
for s in slices:
    for row in s.get('grid', []):
        for c in row:
            if c.get('x') == 111 and c.get('z') == 669 and c.get('y') == 2:
                found_cell = c
                break
        if found_cell: break
    if found_cell: break

if found_cell:
    print('Found cell (111, 669, Y=2):')
    print(f"  symbol: {found_cell.get('symbol')}")
    print(f"  walkable: {found_cell.get('walkable')}")
    print(f"  kind: {found_cell.get('kind')}")
    print(f"  tile_class: {found_cell.get('tile_class')}")
    print(f"  has_actor: {found_cell.get('has_actor')}")
    print(f"  actor_model: {found_cell.get('actor_model')}")
else:
    print('Cell (111, 669, Y=2) not found in radius=4 slices (might be outside local viewport).')

# 2. 检查 A* 寻路是否成功避开阿戴克
print('\n=== 2. Testing A* Pathfinder Around Alder ===')
plan_res = requests.post('http://127.0.0.1:8765/api/v1/navigation/plans', json={
    'destination': {
        'type': 'grid',
        'space': 'gen5-field-grid-v1',
        'zone_id': 439,
        'x': 111,
        'z': 672,
        'y': 2
    },
    'movement_mode': 'run'
})
print('Plan status code:', plan_res.status_code)
d = plan_res.json()
print('Plan status:', d.get('status'))
if d.get('status') == 'success':
    nodes = d.get('route_detail', {}).get('nodes', [])
    print(f'Plan found route with {len(nodes)} nodes:')
    for idx, n in enumerate(nodes):
        print(f"  Step {idx}: ({n.get('x')}, {n.get('z')}, Y={n.get('y')})")
    
    # 验证路径中是否包含 (111, 669) - 严禁包含！
    contains_alder = any(n.get('x') == 111 and n.get('z') == 669 for n in nodes)
    print(f'Does planned path step on Alder (111, 669)? -> {contains_alder} (Expected: False, bypassed!)')
else:
    print('Plan detail:', d.get('error') or d.get('reason'))
