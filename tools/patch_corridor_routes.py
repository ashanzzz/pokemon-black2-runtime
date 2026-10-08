with open('backend/black2/api/navigation_routes.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Add import
old_imp = "from ..world.fast_travel import fast_travel_service"
new_imp = """from ..world.fast_travel import fast_travel_service
from ..world.staircase_corridors import staircase_corridor_service"""

assert old_imp in text, "old_imp not found"
text = text.replace(old_imp, new_imp, 1)

# Upgrade staircase detection in navigation_radar_slices
old_detect = """    # Staircase and multi-level step geometry detection
    player_on_stair = False
    stair_runtime = None
    probe_x = px if x is not None else live_x
    probe_z = pz if z is not None else live_z
    if provider is not None and probe_x is not None and probe_z is not None:
        p_surf = provider.surface_at(resolved_zone, probe_x, probe_z, 0, allow_unverified_terrain=True)
        for s in (p_surf or {}).get("surfaces") or []:
            h = s.get("height") or {}
            sl = h.get("slope_index", 0)
            rel_y = h.get("chunk_relative_world_y")
            if sl > 0 and rel_y is not None:
                player_on_stair = True
                stair_slice = 1
                if len(major_floors) >= 2:
                    stair_slice = int(round((min(major_floors) + max(major_floors)) / 2.0))
                step_idx = 1 if rel_y < 16.0 else 2
                stair_runtime = {
                    "active": True,
                    "current_step": step_idx,
                    "total_steps": 2,
                    "step_world_y": round(rel_y, 2),
                    "stair_slice_y": stair_slice,
                    "slope_index": sl,
                    "height_index": h.get("height_index"),
                    "description": f"主角正处于双阶立体楼梯第 {step_idx} 阶 (标高 {rel_y:.1f})",
                }
                break"""

new_detect = """    # General staircase corridor clustering and transition layer flattening
    player_on_stair = False
    stair_runtime = None
    probe_x = px if x is not None else live_x
    probe_z = pz if z is not None else live_z
    if probe_x is not None and probe_z is not None:
        step_eval = staircase_corridor_service.evaluate_player_step(
            resolved_zone, int(probe_x), int(probe_z), live_world_y
        )
        if step_eval:
            player_on_stair = True
            stair_runtime = {
                "active": True,
                **step_eval,
                "stair_slice_y": step_eval["flattened_slice_y"],
                "description": step_eval["ai_guidance"],
            }"""

assert old_detect in text, "old_detect not found"
text = text.replace(old_detect, new_detect, 1)

with open('backend/black2/api/navigation_routes.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Updated navigation_routes.py with staircase_corridor_service successfully!")
