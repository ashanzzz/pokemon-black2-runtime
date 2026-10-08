import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)

by_coord = {(s["x"], s["z"]): s for s in surfaces}

for check_x in range(194, 215):
    for check_z in range(648, 660):
        s = by_coord.get((check_x, check_z))
        if s:
            surf = s.get("surface", {})
            mat = surf.get("material", {})
            col = surf.get("collision", {})
            kind = mat.get("kind")
            req = col.get("requires")
            blocked = col.get("static_blocked")
            if kind in ("water", "water_edge") or (req and "surf" in req):
                print(f"({check_x}, {check_z}): {kind} req={req} blocked={blocked}")
