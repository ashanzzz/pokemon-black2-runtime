import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)

by_coord = {(s["x"], s["z"]): s for s in surfaces}

print("=== City tiles x=208..225, z=645..655 ===")
for z in range(645, 656):
    row = []
    for x in range(208, 222):
        s = by_coord.get((x, z))
        if s:
            surf = s["surface"]
            h = surf.get("height", {})
            col = surf.get("collision", {})
            mat = surf.get("material", {})
            blocked = col.get("static_blocked")
            kind = mat.get("kind")
            rel_y = h.get("chunk_relative_world_y")
            row.append(f"{kind[:3]}:{1 if blocked else 0}")
        else:
            row.append("---:-")
    print(f"z={z}: " + " ".join(row))
