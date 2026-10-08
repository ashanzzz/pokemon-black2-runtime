import sys; sys.path.insert(0, '.')
import requests, json

r = requests.get('http://127.0.0.1:8765/api/v1/map/v6/actors/live').json()
actors = r.get('actors', [])
print(f'Total live actors in Main RAM: {len(actors)}')
for a in actors:
    grid = a.get('grid', {})
    world = a.get('world', {})
    print(f"Actor obj={a.get('obj_code')}, model={a.get('model_id')}, name={a.get('name_zh') or a.get('name')}, grid=({grid.get('x')}, {grid.get('z')}, Y={grid.get('y')}), state={a.get('state')}, facing={a.get('facing')}")
