import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.world.static_navigation import NavNode

p = navigation_static_provider()
print("=== PREFLIGHT VALIDATION FOR GRAND EXPEDITION ===")

# Test 1: Exit Home 428 to 427 (5, 0, 10) -> (47, 1, 762)
print("1. Doorstep outside home in 427:", p.surface_at(427, 47, 762, 1).get('walkable'))

# Test 2: Walk to Trainer School (39, 1, 740)
p_school = p.find_path(NavNode(427, 47, 1, 762), NavNode(427, 39, 1, 740))
print("2. Path to Trainer School (39, 1, 740):", p_school.get('reachable'), "steps:", len(p_school.get('path', [])))

# Test 3: Inside School 436 to Gym Door (9, 0, 2)
p_gym_door = p.find_path(NavNode(436, 9, 0, 24), NavNode(436, 9, 0, 2))
print("3. School to Gym back door (436):", p_gym_door.get('reachable'), "steps:", len(p_gym_door.get('path', [])))

# Test 4: Inside Gym 489 to Cheren (14, 0, 23) -> (14, 0, 5)
p_cheren = p.find_path(NavNode(489, 14, 0, 23), NavNode(489, 14, 0, 5))
print("4. Battlefield to Cheren (489):", p_cheren.get('reachable'), "steps:", len(p_cheren.get('path', [])))

# Test 5: School back to Aspertia Gate (52, 1, 711)
p_gate = p.find_path(NavNode(427, 39, 1, 740), NavNode(427, 52, 1, 711))
print("5. School to Aspertia Gate (427):", p_gate.get('reachable'), "steps:", len(p_gate.get('path', [])))

# Test 6: Gate 438 interior (4, 0, 14) -> (4, 0, 2)
p_gate_room = p.find_path(NavNode(438, 4, 0, 14), NavNode(438, 4, 0, 2))
print("6. Gate 438 traversal:", p_gate_room.get('reachable'), "steps:", len(p_gate_room.get('path', [])))

# Test 7: Route 19 to Floccesy Town (52, 1, 702) -> (95, 1, 694)
p_r19 = p.find_path(NavNode(437, 52, 1, 702), NavNode(437, 95, 1, 694))
print("7. Route 19 highway cruise:", p_r19.get('reachable'), "steps:", len(p_r19.get('path', [])))

# Test 8: Floccesy Town (126, 2, 662) to Alder House (107, 2, 662)
p_alder = p.find_path(NavNode(439, 96, 1, 694), NavNode(439, 107, 2, 662))
print("8. Floccesy Town to Alder House doorstep:", p_alder.get('reachable'), "steps:", len(p_alder.get('path', [])))

# Test 9: Inside Alder House 440 to Alder Dojo center (5, 0, 12) -> (5, 0, 5)
p_alder_room = p.find_path(NavNode(440, 5, 0, 12), NavNode(440, 5, 0, 5))
print("9. Alder House dojo traversal:", p_alder_room.get('reachable'), "steps:", len(p_alder_room.get('path', [])))
