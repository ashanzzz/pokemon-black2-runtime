import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode

nav = RomStaticNavigationGraph()
start = NavNode(457, 13, 0, 48)
goal = NavNode(457, 13, 2, 44)

res = nav.find_path(start, goal)
print("find_path cross-layer result:")
print("Reachable:", res.get("reachable"))
print("Cost:", res.get("cost"))
print("Staircase detected:", res.get("staircase_detected"))
print("Path length:", len(res.get("path", [])))
for i, n in enumerate(res.get("path", [])):
    print(f"  Step {i}: (X={n['x']}, Z={n['z']}, Y={n['y']})")
