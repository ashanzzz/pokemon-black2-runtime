import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode

nav = RomStaticNavigationGraph()
cells = nav._planning_cells(457, 2)
for x in range(13, 19):
    c = cells.get((x, 44))
    if c:
        print(f"X={x}, Z=44, Y=2: in cells=True, static_blocked={c.static_blocked}, flags={c.flags}, tc={hex(c.tile_class)}, blocked_dirs={c.blocked_directions}, mat={c.material}")
    else:
        print(f"X={x}, Z=44, Y=2: in cells=False")
