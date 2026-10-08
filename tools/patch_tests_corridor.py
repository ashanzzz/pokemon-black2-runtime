with open('tests/test_locomotion_terrain_v24.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Add import
old_imp = "from backend.black2.world.staircase_corridors import staircase_corridor_service"
if old_imp not in text:
    text = text.replace(
        "from backend.black2.world.fast_travel import fast_travel_service",
        "from backend.black2.world.fast_travel import fast_travel_service\nfrom backend.black2.world.staircase_corridors import staircase_corridor_service"
    )

# Add new tests
new_tests = """

# TC-10: General N-step staircase corridor clustering & flattening
def test_staircase_corridor_clustering():
    corridors = staircase_corridor_service.analyze_zone(457)
    assert len(corridors) == 4, f"Zone 457 must cluster into exactly 4 corridors, got {len(corridors)}"

    # Check 2-step stairs
    c_2step = next((c for c in corridors if c.corridor_id == "stair:457:12_11_46"), None)
    assert c_2step is not None
    assert c_2step.total_steps == 2
    assert c_2step.axis == "east_west"
    assert c_2step.rising_direction == "West"
    assert c_2step.lower_portal["x"] == 13 and c_2step.lower_portal["z"] == 46
    assert c_2step.upper_portal["x"] == 10 and c_2step.upper_portal["z"] == 46
    assert c_2step.flattened_slice_y == 1

    # Check 3-step trench stairs
    c_3step = next((c for c in corridors if c.total_steps == 3), None)
    assert c_3step is not None
    assert len(c_3step.steps) == 3

    # Check 5-step gentle ramp
    c_5step = next((c for c in corridors if c.total_steps == 5), None)
    assert c_5step is not None
    assert len(c_5step.steps) == 5


# TC-11: Player step evaluation within corridor
def test_player_step_evaluation():
    # Step 1
    s1 = staircase_corridor_service.evaluate_player_step(457, 12, 46, 7.99)
    assert s1 is not None
    assert s1["in_staircase_corridor"] is True
    assert s1["current_step"] == 1
    assert s1["total_steps"] == 2
    assert s1["flattened_slice_y"] == 1

    # Step 2
    s2 = staircase_corridor_service.evaluate_player_step(457, 11, 46, 23.99)
    assert s2 is not None
    assert s2["current_step"] == 2
    assert s2["total_steps"] == 2

    # Non-stair tile returns None
    s_none = staircase_corridor_service.evaluate_player_step(457, 13, 46, 0.0)
    assert s_none is None
"""

text += new_tests

with open('tests/test_locomotion_terrain_v24.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated tests/test_locomotion_terrain_v24.py with TC-10 and TC-11!")
