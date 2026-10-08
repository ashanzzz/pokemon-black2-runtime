import sys, os
sys.path.insert(0, os.getcwd())

from collections import deque
from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(456)
walk_set = set()
for s in surfaces:
    col = s["surface"].get("collision", {})
    if not col.get("static_blocked"):
        walk_set.add((s["x"], s["z"]))

start = (209, 672)
goal = (213, 673)

queue = deque([(start, [start])])
visited = {start}
found_path = None

while queue:
    curr, path = queue.popleft()
    if curr == goal:
        found_path = path
        break
    cx, cz = curr
    for dx, dz in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nx, nz = cx + dx, cz + dz
        if (nx, nz) in walk_set and (nx, nz) not in visited:
            visited.add((nx, nz))
            queue.append(((nx, nz), path + [(nx, nz)]))

print("Path from (209, 672) to (213, 673):", found_path)
