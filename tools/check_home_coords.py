import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider

p = navigation_static_provider()
for dx in (-1, 0, 1):
    for dz in (-1, 0, 1):
        x, z = 43 + dx, 714 + dz
        w = p.surface_at(427, x, z, 0).get('walkable')
        k = p.surface_at(427, x, z, 0).get('kind')
        print(f"({x}, {z}): walkable={w} kind={k}")
