import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph

nav = RomStaticNavigationGraph()
surfaces = nav._zone_surfaces(457)
for s in surfaces:
    if s["x"] in (11, 12) and s["z"] == 46:
        rel = (s.get("surface", {}).get("height") or {}).get("chunk_relative_world_y")
        cy = int(round(rel / 16.0))
        print(f"({s['x']}, 46): rel={rel}, round(rel/16)={cy}")
