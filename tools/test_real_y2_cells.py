import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph

nav = RomStaticNavigationGraph()
surfaces = nav._zone_surfaces(457)

# Filter surfaces for Y=2
y2_cells = set()
for s in surfaces:
    rel = (s.get("surface", {}).get("height") or {}).get("chunk_relative_world_y")
    if rel is not None and int(round(rel / 16.0)) == 2:
        y2_cells.add((s["x"], s["z"]))

print("Total real tiles on Layer Y=2:", len(y2_cells))
print("(11, 45) on real Layer Y=2:", (11, 45) in y2_cells)
print("(11, 46) on real Layer Y=2:", (11, 46) in y2_cells)
print("(10, 46) on real Layer Y=2:", (10, 46) in y2_cells)
print("(10, 44) on real Layer Y=2:", (10, 44) in y2_cells)
print("(15, 44) on real Layer Y=2:", (15, 44) in y2_cells)
