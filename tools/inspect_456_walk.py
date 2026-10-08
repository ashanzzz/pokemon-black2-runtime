import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfs = g._decode_zone_surfaces(456)

by_c = {(s["x"], s["z"]): s for s in surfs}

print("=== Zone 456 grid around (209, 673) ===")
for z in range(670, 686):
    row = []
    for x in range(204, 218):
        s = by_c.get((x, z))
        if s:
            k = s["surface"].get("material", {}).get("kind")
            b = s["surface"].get("collision", {}).get("static_blocked")
            row.append("." if not b else "#")
        else:
            row.append(" ")
    print(f"z={z:3d}: " + "".join(row))
