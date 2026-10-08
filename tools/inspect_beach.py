import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)
by_coord = {(s["x"], s["z"], s.get("layer_index", 0)): s for s in surfaces}

print("=== Beach Area x=206..218, z=671..678 ===")
for z in range(671, 678):
    row = []
    for x in range(206, 218):
        s = by_coord.get((x, z, 0)) or by_coord.get((x, z, 1))
        if s:
            surf = s["surface"]
            kind = surf.get("material", {}).get("kind")
            blocked = surf.get("collision", {}).get("static_blocked")
            row.append(f"{kind[:3]}:{1 if blocked else 0}")
        else:
            row.append("---:-")
    print(f"z={z}: " + " ".join(row))
