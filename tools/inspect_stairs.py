import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)

by_coord = {(s["x"], s["z"]): s for s in surfaces}

print("=== Area around x=200..208, z=640..645 ===")
for z in range(640, 646):
    row = []
    for x in range(200, 208):
        s = by_coord.get((x, z))
        if s:
            surf = s["surface"]
            h = surf.get("height", {})
            col = surf.get("collision", {})
            mat = surf.get("material", {})
            blocked = col.get("static_blocked")
            kind = mat.get("kind")
            sl = h.get("slope_index")
            rel_y = h.get("chunk_relative_world_y")
            row.append(f"({x},{z}):k={kind[:3]},b={1 if blocked else 0},y={rel_y:.0f}")
    print(" ".join(row))
