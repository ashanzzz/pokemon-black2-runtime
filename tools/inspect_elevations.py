import sys, os
sys.path.insert(0, os.getcwd())

from backend.black2.world.static_navigation import RomStaticNavigationGraph

g = RomStaticNavigationGraph()
surfaces = g._decode_zone_surfaces(448)

by_coord = {(s["x"], s["z"]): s for s in surfaces}

print("=== Bridge vs Water ===")
for x in range(194, 215):
    s = by_coord.get((x, 651))
    if s:
        surf = s["surface"]
        h = surf.get("height", {})
        col = surf.get("collision", {})
        mat = surf.get("material", {})
        print(f"({x}, 651): height={h.get('height_index')} rel_y={h.get('chunk_relative_world_y')} kind={mat.get('kind')} blocked={col.get('static_blocked')}")
