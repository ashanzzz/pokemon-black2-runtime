import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode
from backend.black2.world.staircase_corridors import StaircaseCorridorService

nav = RomStaticNavigationGraph()

# Check what cells_for_layer produces for Y=2
surfaces = nav._zone_surfaces(457)
TILE_WORLD = 16.0

y2_filtered = set()
for item in surfaces:
    surf = item.get("surface") or {}
    rel = (surf.get("height") or {}).get("chunk_relative_world_y")
    if rel is not None and int(round(rel / TILE_WORLD)) == 2:
        y2_filtered.add((item["x"], item["z"]))

print("(11, 45) in filtered Y=2:", (11, 45) in y2_filtered)
print("(11, 46) in filtered Y=2:", (11, 46) in y2_filtered)
print("(10, 46) in filtered Y=2:", (10, 46) in y2_filtered)
print("(15, 44) in filtered Y=2:", (15, 44) in y2_filtered)

# Check staircase corridor handrails
scs = StaircaseCorridorService(nav)
corridors = scs.analyze_zone(457)
for c in corridors:
    print(f"Corridor {c.corridor_id}: axis={c.axis}, steps={[(s.x, s.z) for s in c.steps]}")
