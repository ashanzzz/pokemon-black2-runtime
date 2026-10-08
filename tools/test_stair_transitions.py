import sys, os
sys.path.insert(0, os.path.abspath("."))
from backend.black2.world.static_navigation import RomStaticNavigationGraph, NavNode
from backend.black2.world.staircase_corridors import StaircaseCorridorService

def test_has_candidate(start, goal):
    nav = RomStaticNavigationGraph()
    if abs(start.x - goal.x) + abs(start.z - goal.z) != 1:
        return False
    scs = StaircaseCorridorService(nav)
    for c in scs.analyze_zone(int(start.zone_id)):
        lp, up, steps = c.lower_portal, c.upper_portal, c.steps
        if not lp or not up or not steps:
            continue
        if ((start.x, start.z) == (lp["x"], lp["z"]) and (goal.x, goal.z) == (steps[0].x, steps[0].z)) or \
           ((goal.x, goal.z) == (lp["x"], lp["z"]) and (start.x, start.z) == (steps[0].x, steps[0].z)):
            return True
        if ((start.x, start.z) == (steps[-1].x, steps[-1].z) and (goal.x, goal.z) == (up["x"], up["z"])) or \
           ((goal.x, goal.z) == (steps[-1].x, steps[-1].z) and (start.x, start.z) == (up["x"], up["z"])):
            return True
        for i in range(len(steps) - 1):
            if ((start.x, start.z) == (steps[i].x, steps[i].z) and (goal.x, goal.z) == (steps[i+1].x, steps[i+1].z)) or \
               ((goal.x, goal.z) == (steps[i].x, steps[i].z) and (start.x, start.z) == (steps[i+1].x, steps[i+1].z)):
                return True
    return False

# Test (11, 46) -> (12, 46)
print("(11, 46) -> (12, 46) is stair edge:", test_has_candidate(NavNode(457, 11, 1, 46), NavNode(457, 12, 1, 46)))
# Test (10, 46) -> (11, 46)
print("(10, 46) -> (11, 46) is stair edge:", test_has_candidate(NavNode(457, 10, 2, 46), NavNode(457, 11, 1, 46)))
# Test (12, 46) -> (13, 46)
print("(12, 46) -> (13, 46) is stair edge:", test_has_candidate(NavNode(457, 12, 1, 46), NavNode(457, 13, 0, 46)))
