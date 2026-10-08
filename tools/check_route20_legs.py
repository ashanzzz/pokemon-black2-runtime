import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.world.static_navigation import NavNode

p = navigation_static_provider()
legs = [
    (NavNode(446, 184, 0, 650), NavNode(446, 177, 0, 653), 'Leg A (Y=0 road)'),
    (NavNode(446, 177, 0, 653), NavNode(446, 174, -1, 654), 'Leg B (Y=0 -> Y=-1 step)'),
    (NavNode(446, 174, -1, 654), NavNode(446, 170, -1, 652), 'Leg C1 (Y=-1 road)'),
    (NavNode(446, 170, -1, 652), NavNode(446, 168, -2, 652), 'Leg C2 (Y=-1 -> Y=-2 step)'),
    (NavNode(446, 168, -2, 652), NavNode(446, 160, -2, 650), 'Leg D1 (Y=-2 road)'),
    (NavNode(446, 160, -2, 650), NavNode(446, 160, 2, 646), 'Leg D2 (Y=-2 -> Y=2 slope)'),
    (NavNode(446, 160, 2, 646), NavNode(446, 128, 2, 662), 'Leg E (Y=2 road to bridge)'),
]

for n1, n2, name in legs:
    r = p.find_path(n1, n2)
    print(name, "reachable:", r.get("reachable"), "steps:", len(r.get("path", [])), "reason:", r.get("reason"))
