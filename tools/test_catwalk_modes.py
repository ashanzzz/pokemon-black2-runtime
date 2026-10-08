import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode

nav = RomStaticNavigationGraph()
start = NavNode(457, 13, 2, 44)
goal = NavNode(457, 17, 2, 44)

for mode in ("walk", "run", "bike"):
    res = nav.find_path(start, goal, movement_mode=mode)
    print(f"Mode={mode}: reachable={res.get('reachable')}, reason={res.get('reason')}, blocker={res.get('movement_blocker')}")
