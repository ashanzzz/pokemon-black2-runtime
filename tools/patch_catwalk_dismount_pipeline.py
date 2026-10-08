# 1. Patch tile_semantics.py: 0xBF catwalk_entry blocks_cycling=False
with open("backend/black2/world/tile_semantics.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace(
    '0xBF: _entry("catwalk_entry", "Catwalk entry", blocks_cycling=True),',
    '0xBF: _entry("catwalk_entry", "Catwalk entry"),'
)

with open("backend/black2/world/tile_semantics.py", "w", encoding="utf-8") as f:
    f.write(text)
print("1. tile_semantics.py patched: 0xBF catwalk_entry allows cycling")

# 2. Patch navigation_planning.py: allow walk and run when Cycling via auto-dismount
with open("backend/black2/world/navigation_planning.py", "r", encoding="utf-8") as f:
    plan_text = f.read()

old_avail = """        on_foot = transport in (None, "", "OnFoot")
        run_item_ok = has_running_shoes is True or (has_running_shoes is None and allow_running is True)
        bike_item_ok = has_bicycle is True or (has_bicycle is None and transport == "Cycling")
        available = {
            "walk": on_foot,
            "run": on_foot and allow_running is True and run_item_ok,
            "bike": allow_cycling is True and not bike_blockers and (transport == "Cycling" or (on_foot and has_bicycle is True)),
            "surf": transport == "Surf",
        }"""

new_avail = """        on_foot = transport in (None, "", "OnFoot")
        can_dismount = (transport == "Cycling")
        foot_accessible = on_foot or can_dismount
        run_item_ok = has_running_shoes is True or (has_running_shoes is None and allow_running is True)
        bike_item_ok = has_bicycle is True or (has_bicycle is None and transport == "Cycling")
        available = {
            "walk": foot_accessible,
            "run": foot_accessible and allow_running is True and run_item_ok,
            "bike": allow_cycling is True and not bike_blockers and (transport == "Cycling" or (on_foot and has_bicycle is True)),
            "surf": transport == "Surf",
        }"""
plan_text = plan_text.replace(old_avail, new_avail)

old_startable = """        startable = {
            "walk": on_foot,
            "run": on_foot and allow_running is True and (has_running_shoes is True),
            "bike": allow_cycling is True and not bike_blockers and has_bicycle is True,
            "surf": transport == "Surf",
        }"""

new_startable = """        startable = {
            "walk": foot_accessible,
            "run": foot_accessible and allow_running is True and (has_running_shoes is True),
            "bike": allow_cycling is True and not bike_blockers and has_bicycle is True,
            "surf": transport == "Surf",
        }"""
plan_text = plan_text.replace(old_startable, new_startable)

old_walk_reason = """"walk": "PlayerRuntime is on foot" if on_foot else f"current transport is {transport!r}; navigation has no verified dismount primitive","""
new_walk_reason = """"walk": "PlayerRuntime is on foot" if on_foot else "Player is on bicycle and can automatically dismount to walk on catwalk/restricted terrain","""
plan_text = plan_text.replace(old_walk_reason, new_walk_reason)

with open("backend/black2/world/navigation_planning.py", "w", encoding="utf-8") as f:
    f.write(plan_text)
print("2. navigation_planning.py patched: walk and run enabled with auto-dismount")

# 3. Patch navigation_tasks.py: auto-dismount before walking/running onto restricted terrain
with open("backend/black2/world/navigation_tasks.py", "r", encoding="utf-8") as f:
    task_text = f.read()

old_bike_action = """        # 自动上车机制：若规划选定了自行车，且玩家当前处于步行中，尝试按快捷键 Y 上车
        current_transport = str((runtime_player.get("locomotion") or {}).get("transport_mode") or "")
        if selected_mode == "bike" and current_transport not in {"Cycling"}:
            try:
                await self.client.press_buttons(["Y"], frames=4)
                await asyncio.sleep(0.15)
                if self.live_player_sampler is not None:
                    res = self.live_player_sampler()
                    if inspect.isawaitable(res):
                        await res
            except Exception:
                pass"""

new_bike_action = """        # 自动换乘机制：
        # 1. 若规划选定自行车且当前在步行，按快捷键 Y 上车
        # 2. 若规划选定步行/奔跑 (如穿越独木桥) 且当前在骑车，按快捷键 Y 自动下车
        current_transport = str((runtime_player.get("locomotion") or {}).get("transport_mode") or "")
        if selected_mode == "bike" and current_transport not in {"Cycling"}:
            try:
                await self.client.press_buttons(["Y"], frames=4)
                await asyncio.sleep(0.15)
                if self.live_player_sampler is not None:
                    res = self.live_player_sampler()
                    if inspect.isawaitable(res):
                        await res
            except Exception:
                pass
        elif selected_mode in {"walk", "run"} and current_transport == "Cycling":
            try:
                await self.client.press_buttons(["Y"], frames=4)
                await asyncio.sleep(0.15)
                if self.live_player_sampler is not None:
                    res = self.live_player_sampler()
                    if inspect.isawaitable(res):
                        await res
            except Exception:
                pass"""
task_text = task_text.replace(old_bike_action, new_bike_action)

with open("backend/black2/world/navigation_tasks.py", "w", encoding="utf-8") as f:
    f.write(task_text)
print("3. navigation_tasks.py patched: auto-dismount implemented in task runner")

# 4. Patch frontend/v2.js: update catwalk_entry presentation
with open("frontend/v2.js", "r", encoding="utf-8") as f:
    v2_text = f.read()

old_ce_desc = """descElem.innerHTML = `<span style="color:var(--accent-amber); font-weight:700;">🌉 独木桥桥头入口 (Catwalk Entry · 0x00BF)：</span>高台与独木桥主体的过渡端点。踏入后角色将进入独木桥平衡状态。主轴向可通行，侧向为悬空边缘，禁止自行车骑行。`;"""
new_ce_desc = """descElem.innerHTML = `<span style="color:var(--accent-amber); font-weight:700;">🌉 独木桥桥头入口 (Catwalk Entry · 0x00BF)：</span>高台平坦路面与独木桥的平整接驳点。与普通路面一样允许骑车通行，直达独木桥前端。`;"""
v2_text = v2_text.replace(old_ce_desc, new_ce_desc)

old_ce_bike = """    } else if (isTrueCatwalk) {
      mBike.className = 'ds-badge ds-badge-amber';
      mBike.innerText = '🚲 自行车: [NO 独木桥狭窄阻断]';"""

new_ce_bike = """    } else if (isCatwalkEntry) {
      mBike.className = 'ds-badge ds-badge-yellow';
      mBike.innerText = '🚲 自行车: [OK 允许平地骑行·黄色]';
    } else if (isCatwalkBody) {
      mBike.className = 'ds-badge ds-badge-amber';
      mBike.innerText = '🚲 自行车: [NO 独木桥狭窄阻断 (自动下车)]';"""
v2_text = v2_text.replace(old_ce_bike, new_ce_bike)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(v2_text)
print("4. frontend/v2.js patched: catwalk entry allows bike, body auto-dismounts")
