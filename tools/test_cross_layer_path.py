import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode
from backend.black2.world.staircase_corridors import StaircaseCorridorService

def test_cross_layer():
    nav = RomStaticNavigationGraph()
    scs = StaircaseCorridorService(nav)
    start = NavNode(457, 13, 0, 48)
    goal = NavNode(457, 13, 2, 44)
    corridors = scs.analyze_zone(int(start.zone_id))
    candidates = []
    for c in corridors:
        lp, up = c.lower_portal, c.upper_portal
        if lp and up:
            if lp.get("floor_y") == start.y and up.get("floor_y") == goal.y:
                candidates.append((c, lp, up, False))
            elif up.get("floor_y") == start.y and lp.get("floor_y") == goal.y:
                candidates.append((c, up, lp, True))

    for c, entry_p, exit_p, desc in candidates:
        entry_node = NavNode(start.zone_id, entry_p["x"], start.y, entry_p["z"])
        p1 = nav.find_path(start, entry_node)
        exit_node = NavNode(goal.zone_id, exit_p["x"], goal.y, exit_p["z"])
        p3 = nav.find_path(exit_node, goal)
        if p1.get("reachable") and p3.get("reachable"):
            stair_steps = []
            steps_ordered = list(c.steps)
            if desc: steps_ordered.reverse()
            for s in steps_ordered:
                stair_steps.append({"zone_id": int(start.zone_id), "x": int(s.x), "y": int(c.flattened_slice_y), "z": int(s.z)})
            full_path = list(p1.get("path", [])) + stair_steps + list(p3.get("path", []))
            print("SUCCESS! Stitched path length:", len(full_path))
            for i, node in enumerate(full_path):
                print(f"  Step {i}: (X={node['x']}, Z={node['z']}, Y={node['y']})")
            return

test_cross_layer()
