import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.world.static_navigation import NavNode

p = navigation_static_provider()
start = NavNode(454, 7, 0, 19)
goal = NavNode(454, 7, 0, 12)
print("Testing static path from (7, 0, 19) to (7, 0, 11) in Zone 454...")
res = p.find_path(start, goal)
print("Static result:", res.get("reachable"), res.get("reason"), "path length:", len(res.get("path", [])))

# Let's inspect surface for all tiles from 11 to 19 at x=7
for z in range(10, 21):
    surf = p.surface_at(454, 7, z, 0)
    print(f"Z={z}: walkable={surf.get('walkable')} kind={surf.get('kind')} flags={surf.get('flags')} class={hex(surf.get('tile_class', 0))}")
