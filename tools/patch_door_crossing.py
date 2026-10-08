with open("backend/black2/world/navigation_tasks.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. 注入 _resolve_and_push_door 函数
push_door_def = '''    async def _resolve_and_push_door(self, record: dict[str, Any], stand: NavNode) -> dict[str, Any] | None:
        """Resolve building door portal at doorstep and execute directional push-through."""
        provider = self.planner._resolve_static_provider() if hasattr(self.planner, "_resolve_static_provider") else None
        if provider is None or not hasattr(provider, "event_overlay_at"):
            return None
        overlays = list(provider.event_overlay_at(int(stand.zone_id), int(stand.x), int(stand.z)))
        warp = next((it for it in overlays if it.get("kind") == "warp"), None)
        if warp is None:
            return None
        geom = warp.get("door_geometry") or {}
        door_type = geom.get("type")
        target_zone = warp.get("target_zone_id_candidate")
        entry_dir = geom.get("entry_direction")
        
        # 若是建筑嵌入门 (building_portal)，向 entry_direction 顶门并等待切图
        if door_type == "building_portal" and entry_dir and target_zone is not None:
            button_map = {"North": "Up", "South": "Down", "West": "Left", "East": "Right"}
            push_button = button_map.get(entry_dir)
            if not push_button:
                return None
            
            self._emit("navigation.door.push_through", task_id=record["task_id"],
                       summary=f"Approached doorstep; pushing {entry_dir} ({push_button}) towards Zone {target_zone}.")
            await self.client.press_buttons([push_button], frames=12)
            
            # 等待转场切图 (最多 6 秒)
            deadline = asyncio.get_running_loop().time() + 6.0
            while asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.2)
                if self.live_player_sampler is not None:
                    try:
                        res = self.live_player_sampler()
                        if inspect.isawaitable(res):
                            await res
                    except Exception:
                        pass
                cur_node, _ = self._current_node()
                if cur_node and int(cur_node.zone_id) == int(target_zone):
                    return {"transited": True, "target_zone": int(target_zone), "landing": cur_node.public()}
        return None

    async def _turn_to_facing('''

assert "async def _turn_to_facing(" in code, "_turn_to_facing not found"
code = code.replace("    async def _turn_to_facing(", push_door_def, 1)

# 2. 在 arrived 终点处触发自动顶门与跨区转场验证
old_arrived_block = '''        arrived, arrival_player = self._current_node()
        goal = path[-1]
        if not self._same_spatial_node(arrived, goal):
            return _stop("NAV_POSITION_DIVERGED", "Final Matrix-global GPos does not satisfy destination.",
                         expected=goal.public(), observed=arrived.public() if arrived else None)'''

new_arrived_block = '''        arrived, arrival_player = self._current_node()
        goal = path[-1]

        # 检查是否抵达门前待命格并执行自动顶门 (Push-Through)
        door_warp = await self._resolve_and_push_door(record, goal)
        if door_warp and door_warp.get("transited"):
            arrived, arrival_player = self._current_node()
            record["zone_transitions"].append({
                "from_zone": int(goal.zone_id),
                "to_zone": int(door_warp["target_zone"]),
                "landing": door_warp.get("landing"),
            })

        # 检查是否成功完成跨区 Warp 传送
        zone_switched_to_target = False
        if arrived and int(arrived.zone_id) != int(goal.zone_id):
            provider = self.planner._resolve_static_provider() if hasattr(self.planner, "_resolve_static_provider") else None
            if provider and hasattr(provider, "event_overlay_at"):
                overlays = list(provider.event_overlay_at(int(goal.zone_id), int(goal.x), int(goal.z)))
                warp_on_goal = next((it for it in overlays if it.get("kind") == "warp"), None)
                if warp_on_goal and int(arrived.zone_id) == int(warp_on_goal.get("target_zone_id_candidate", -1)):
                    zone_switched_to_target = True

        if not self._same_spatial_node(arrived, goal) and not zone_switched_to_target and not (door_warp and door_warp.get("transited")):
            return _stop("NAV_POSITION_DIVERGED", "Final Matrix-global GPos does not satisfy destination.",
                         expected=goal.public(), observed=arrived.public() if arrived else None)'''

assert old_arrived_block in code, "old_arrived_block not found"
code = code.replace(old_arrived_block, new_arrived_block, 1)

# 3. 在 _wait_for_landing 中增加 Warp 门垫切图容差
old_divergent_check = '''            if node is None or not self._in_spatial_set(node, allowed):
                return await finish("divergent")'''

new_divergent_check = '''            if node and previous and int(node.zone_id) != int(previous.zone_id):
                # 检查当前步是否踩上地面跨区门垫 (walkable_mat warp)
                provider = self.planner._resolve_static_provider() if hasattr(self.planner, "_resolve_static_provider") else None
                if provider and hasattr(provider, "event_overlay_at"):
                    overlays = list(provider.event_overlay_at(int(previous.zone_id), int(expected.x), int(expected.z)))
                    warp = next((it for it in overlays if it.get("kind") == "warp"), None)
                    if warp and int(node.zone_id) == int(warp.get("target_zone_id_candidate", -1)):
                        return await finish("expected")

            if node is None or not self._in_spatial_set(node, allowed):
                return await finish("divergent")'''

assert old_divergent_check in code, "old_divergent_check not found"
code = code.replace(old_divergent_check, new_divergent_check, 1)

with open("backend/black2/world/navigation_tasks.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Applied door push-through & warp transition engine successfully!")
