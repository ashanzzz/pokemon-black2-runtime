with open('backend/black2/api/navigation_routes.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 1. Update lines 4355-4370
idx_major = -1
for i, line in enumerate(lines):
    if "Major functional floors have real flat walkable road tiles" in line:
        idx_major = i
        break

assert idx_major != -1, "major floors comment not found"

# Find sorted_layers = all_active_floors
idx_sorted = -1
for i in range(idx_major, idx_major + 20):
    if "sorted_layers = all_active_floors" in lines[i]:
        idx_sorted = i
        break

assert idx_sorted != -1, "sorted_layers line not found"

stair_detection_code = [
    '    # Staircase and multi-level step geometry detection\n',
    '    player_on_stair = False\n',
    '    stair_runtime = None\n',
    '    if provider is not None and live_x is not None and live_z is not None:\n',
    '        p_surf = provider.surface_at(resolved_zone, live_x, live_z, 0, allow_unverified_terrain=True)\n',
    '        for s in (p_surf or {}).get("surfaces") or []:\n',
    '            h = s.get("height") or {}\n',
    '            sl = h.get("slope_index", 0)\n',
    '            rel_y = h.get("chunk_relative_world_y")\n',
    '            if sl > 0 and rel_y is not None:\n',
    '                player_on_stair = True\n',
    '                stair_slice = 1\n',
    '                if len(major_floors) >= 2:\n',
    '                    stair_slice = int(round((min(major_floors) + max(major_floors)) / 2.0))\n',
    '                step_idx = 1 if rel_y < 16.0 else 2\n',
    '                stair_runtime = {\n',
    '                    "active": True,\n',
    '                    "current_step": step_idx,\n',
    '                    "total_steps": 2,\n',
    '                    "step_world_y": round(rel_y, 2),\n',
    '                    "stair_slice_y": stair_slice,\n',
    '                    "slope_index": sl,\n',
    '                    "height_index": h.get("height_index"),\n',
    '                    "description": f"主角正处于双阶立体楼梯第 {step_idx} 阶 (标高 {rel_y:.1f})",\n',
    '                }\n',
    '                break\n',
    '\n',
    '    if y is None and player_on_stair and stair_runtime is not None:\n',
    '        py = stair_runtime["stair_slice_y"]\n',
    '\n',
    '    all_active_floors = list(major_floors)\n',
    '    if player_on_stair and stair_runtime is not None and stair_runtime["stair_slice_y"] not in all_active_floors:\n',
    '        all_active_floors.append(stair_runtime["stair_slice_y"])\n',
    '    elif py not in all_active_floors:\n',
    '        all_active_floors.append(py)\n',
    '    all_active_floors.sort(reverse=True)\n',
    '\n',
]

# Replace lines between major_floors = ... and sorted_layers = ...
idx_major_assign = -1
for i in range(idx_major, idx_sorted):
    if "major_floors = sorted(" in lines[i]:
        idx_major_assign = i + (2 if "if not major_floors" in lines[i+1] else 1)
        break

# Let's verify
lines[idx_major+4 : idx_sorted] = stair_detection_code

# Re-search for slices_data label
for i, line in enumerate(lines):
    if 'layer_label = f"Floor Y={floor_y:+d}' in line:
        lines[i] = (
            '        layer_label = f"Floor Y={floor_y:+d} (标高: {floor_y * 16.0:.1f})"\n'
            '        if stair_runtime and stair_runtime.get("active") and floor_y == stair_runtime.get("stair_slice_y"):\n'
            '            step_i = stair_runtime.get("current_step", 1)\n'
            '            step_y = stair_runtime.get("step_world_y", 8.0)\n'
            '            layer_label = f"Floor Y={floor_y:+d} (标高: {floor_y * 16.0:.1f}) ★楼梯跨层中段 [踩踏第 {step_i} 阶 · 标高 {step_y:.1f}]"\n'
            '        elif is_player_layer:\n'
            '            layer_label += " ★当前所在层"\n'
        )
        lines[i+1] = ''
        lines[i+2] = ''
        break

# Re-search for response
for i, line in enumerate(lines):
    if '"catwalk_runtime": catwalk_runtime,' in line:
        lines.insert(i+1, '        "stair_runtime": stair_runtime,\n')
        break

# Re-search for if len(sorted_layers) == 2:
for i, line in enumerate(lines):
    if 'if len(sorted_layers) == 2:' in line:
        lines[i] = '        if len(sorted_layers) == 2 or (len(sorted_layers) == 3 and stair_runtime and stair_runtime.get("active")):\n'
        for j in range(i+1, i+15):
            if 'top_y, bot_y = sorted_layers[0], sorted_layers[1]' in lines[j]:
                lines[j] = '            top_y, bot_y = (sorted_layers[0], sorted_layers[-1]) if len(sorted_layers) == 3 else (sorted_layers[0], sorted_layers[1])\n'
            if 'star_top = ' in lines[j]:
                lines[j] = '            star_top = " ★主角所在层" if top_y == py and not player_on_stair else ""\n'
            if 'star_bot = ' in lines[j]:
                lines[j] = '            star_bot = " ★主角所在层" if bot_y == py and not player_on_stair else ""\n'
        for j in range(i+15, i+30):
            if 'rendered_blocks = "\\n".join(body_lines)' in lines[j]:
                lines.insert(j, '            if stair_runtime and stair_runtime.get("active"):\n                step_num = stair_runtime["current_step"]\n                step_h = stair_runtime["step_world_y"]\n                body_lines.append(f">>> [▲▼] 楼梯跨层中段踏面: 主角踩踏第 {step_num} 阶 (共 2 阶 · 标高 {step_h:.1f}) | 西向登高台 (Y=+2) | 东向达地面 (Y=0) <<<")\n')
                break
        break

with open('backend/black2/api/navigation_routes.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("Patch applied successfully!")
