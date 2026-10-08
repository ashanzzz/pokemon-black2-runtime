import sys, os
sys.path.insert(0, os.getcwd())

from collections import deque
from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()

# Search across Zone 456 and Zone 448 water surfaces
water_tiles = set()
for zid in [456, 448]:
    surfs = g._decode_zone_surfaces(zid)
    for s in surfs:
        k = s["surface"].get("material", {}).get("kind")
        req = s["surface"].get("collision", {}).get("requires") or []
        if k in ("water", "water_edge") or "surf" in req:
            water_tiles.add((s["x"], s["z"]))

print(f"Total water tiles across 456 & 448: {len(water_tiles)}")

start = (216, 678)
queue = deque([(start, [start])])
visited = {start}
farthest = start
max_dist = 0
all_reachable = []

while queue:
    curr, path = queue.popleft()
    if len(path) > max_dist:
        max_dist = len(path)
        farthest = curr
    all_reachable.append((curr, len(path)))
    cx, cz = curr
    for dx, dz in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        nxt = (cx + dx, cz + dz)
        if nxt in water_tiles and nxt not in visited:
            visited.add(nxt)
            queue.append((nxt, path + [nxt]))

print(f"Reachable water tiles from {start}: {len(visited)}")
print(f"Farthest water tile: {farthest} (distance: {max_dist} steps)")

# Let's show some destinations
all_reachable.sort(key=lambda x: x[1], reverse=True)
print("Top 10 distant water destinations:")
for pt, d in all_reachable[:10]:
    print(f"  Tile {pt}: {d} steps away")
