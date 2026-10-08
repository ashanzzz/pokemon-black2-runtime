import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode

nav = RomStaticNavigationGraph()
sample = {
    "zone_id": 457,
    "position": {
        "grid": {"x": 14, "y": 0, "z": 46},
        "world": {"x": 232.0, "y": 0.0, "z": 744.0}
    }
}
anchor = nav._anchor_from_sample(sample, 457)
print("Anchor:", anchor)
cells_anchored = nav._cells_for_layer(457, 2, anchor=anchor)
print("(11, 45) in cells_anchored:", (11, 45) in cells_anchored)
print("(11, 46) in cells_anchored:", (11, 46) in cells_anchored)
print("(10, 46) in cells_anchored:", (10, 46) in cells_anchored)
