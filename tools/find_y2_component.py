import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode

nav = RomStaticNavigationGraph()
cells = nav._cells_for_layer(457, 2)
print("Connected walkable tiles from (10, 46) on Layer Y=2:")
visited = set()
queue = [(10, 46)]
visited.add((10, 46))
while queue:
    cx, cz = queue.pop(0)
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nx, nz = cx + dx, cz + dz
        if (nx, nz) in cells and (nx, nz) not in visited:
            visited.add((nx, nz))
            queue.append((nx, nz))

print(f"Total reachable tiles on Y=2 component: {len(visited)}")
for x, z in sorted(visited):
    print(f"  ({x}, {z})")
