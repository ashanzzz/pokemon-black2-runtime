import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph

nav = RomStaticNavigationGraph()
surfaces = nav._zone_surfaces(457)
sample = {
    "zone_id": 457,
    "position": {
        "grid": {"x": 14, "y": 0, "z": 46},
        "world": {"x": 232.0, "y": 0.0, "z": 744.0}
    }
}
anchor = nav._anchor_from_sample(sample, 457)
anchor_rel = nav._anchor_relative_height(surfaces, anchor)
print("anchor_rel:", anchor_rel)

for s in surfaces:
    if s["x"] == 11 and s["z"] == 45:
        surf = s["surface"]
        h = surf.get("height", {})
        rel = h.get("chunk_relative_world_y")
        print("Tile (11, 45): layer_index:", s.get("layer_index"), "rel:", rel)
        if anchor_rel is not None and rel is not None:
            aligned_world_y = 0.0 + (rel - anchor_rel)
            cand_y = 0 + int(round((aligned_world_y - 0.0) / 16.0))
            print("  cand_y:", cand_y)
