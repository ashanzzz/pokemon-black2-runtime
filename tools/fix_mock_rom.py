with open("backend/black2/world/navigation_tasks.py", "r", encoding="utf-8") as f:
    code = f.read()

old_overlay_call = '''        overlays = list(provider.event_overlay_at(int(stand.zone_id), int(stand.x), int(stand.z)))'''
new_overlay_call = '''        try:
            overlays = list(provider.event_overlay_at(int(stand.zone_id), int(stand.x), int(stand.z)))
        except Exception:
            overlays = []'''

assert old_overlay_call in code, "old_overlay_call not found"
code = code.replace(old_overlay_call, new_overlay_call, 1)

old_overlay_call2 = '''                overlays = list(provider.event_overlay_at(int(goal.zone_id), int(goal.x), int(goal.z)))'''
new_overlay_call2 = '''                try:
                    overlays = list(provider.event_overlay_at(int(goal.zone_id), int(goal.x), int(goal.z)))
                except Exception:
                    overlays = []'''

assert old_overlay_call2 in code, "old_overlay_call2 not found"
code = code.replace(old_overlay_call2, new_overlay_call2, 1)

old_overlay_call3 = '''                    overlays = list(provider.event_overlay_at(int(previous.zone_id), int(expected.x), int(expected.z)))'''
new_overlay_call3 = '''                    try:
                        overlays = list(provider.event_overlay_at(int(previous.zone_id), int(expected.x), int(expected.z)))
                    except Exception:
                        overlays = []'''

assert old_overlay_call3 in code, "old_overlay_call3 not found"
code = code.replace(old_overlay_call3, new_overlay_call3, 1)

with open("backend/black2/world/navigation_tasks.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Protected event_overlay_at with try-except in navigation_tasks.py!")
