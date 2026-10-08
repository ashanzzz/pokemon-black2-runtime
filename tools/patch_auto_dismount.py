with open("backend/black2/world/navigation_tasks.py", "r", encoding="utf-8") as f:
    text = f.read()

old_code = """        current_transport = str((runtime_player.get("locomotion") or {}).get("transport_mode") or "")
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

new_code = """        current_transport = str((runtime_player.get("locomotion") or {}).get("transport_mode") or "")
        if selected_mode == "bike" and current_transport not in {"Cycling"}:
            try:
                await self.client.press_buttons(["Y"], frames=4)
                await asyncio.sleep(0.2)
                if self.live_player_sampler is not None:
                    res = self.live_player_sampler()
                    if inspect.isawaitable(res):
                        await res
            except Exception:
                pass
        elif selected_mode in {"walk", "run"} and current_transport == "Cycling":
            # 自动下车机制：当路径进入独木桥/窄道等禁止骑行的地形时，自动按 Y 键下车换乘为步行/奔跑
            try:
                await self.client.press_buttons(["Y"], frames=4)
                await asyncio.sleep(0.2)
                if self.live_player_sampler is not None:
                    res = self.live_player_sampler()
                    if inspect.isawaitable(res):
                        await res
            except Exception:
                pass"""

text = text.replace(old_code, new_code)

with open("backend/black2/world/navigation_tasks.py", "w", encoding="utf-8") as f:
    f.write(text)

print("navigation_tasks.py updated with auto-dismount when entering catwalk!")
