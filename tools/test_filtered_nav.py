import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode

nav = RomStaticNavigationGraph()
start = NavNode(457, 14, 0, 46)
goal = NavNode(457, 15, 2, 44)

# Test with properly filtered candidate Y
surfaces = nav._zone_surfaces(457)
for s in surfaces:
    if s["x"] == 11 and s["z"] == 45:
        rel = (s.get("surface", {}).get("height") or {}).get("chunk_relative_world_y")
        cy = int(round(rel / 16.0))
        print(f"Tile (11, 45): rel={rel}, cy={cy} (on layer 2? {cy == 2})")

res = nav.find_path(start, goal)
print("\nNew stitched path:")
print("Reachable:", res.get("reachable"))
for i, n in enumerate(res.get("path", [])):
    print(f"  Step {i}: (X={n['x']}, Z={n['z']}, Y={n['y']})")
