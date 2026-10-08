import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfs = g._decode_zone_surfaces(448)

for z in range(655, 661):
    row = []
    for x in range(202, 217):
        matches = [s for s in surfs if s["x"] == x and s["z"] == z]
        is_water = any(m["surface"].get("material", {}).get("kind") == "water" for m in matches)
        row.append("W" if is_water else ".")
    print(f"z={z}: " + " ".join(row))
