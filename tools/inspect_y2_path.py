import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode

nav = RomStaticNavigationGraph()
start = NavNode(457, 10, 2, 46)
goal = NavNode(457, 15, 2, 44)
res = nav.find_path(start, goal)
print("Path on Y=2 from (10, 46) to (15, 44):")
print("Reachable:", res.get("reachable"))
print("Length:", len(res.get("path", [])))
for i, n in enumerate(res.get("path", [])):
    print(f"  Node {i}: (X={n['x']}, Z={n['z']}, Y={n['y']})")
