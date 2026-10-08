import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode

nav = RomStaticNavigationGraph()

# Build true Layer Y=2 cells
surfaces = nav._zone_surfaces(457)
y2_cells = {}
for s in surfaces:
    rel = (s.get("surface", {}).get("height") or {}).get("chunk_relative_world_y")
    if rel is not None and int(round(rel / 16.0)) == 2:
        y2_cells[(s["x"], s["z"])] = s

# Run BFS from (10, 46) to (15, 44) on real Y=2 cells
start = (10, 46)
goal = (15, 44)
queue = [[start]]
visited = {start}
found_path = None
while queue:
    path = queue.pop(0)
    curr = path[-1]
    if curr == goal:
        found_path = path
        break
    cx, cz = curr
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        nxt = (cx + dx, cz + dz)
        if nxt in y2_cells and nxt not in visited:
            visited.add(nxt)
            queue.append(path + [nxt])

print("True path on Layer Y=2 from (10, 46) to (15, 44):")
if found_path:
    print(f"Path length: {len(found_path)} nodes, {len(found_path) - 1} steps:")
    for i, (x, z) in enumerate(found_path):
        print(f"  Node {i}: ({x}, {z}, Y=2)")
else:
    print("No path found!")
