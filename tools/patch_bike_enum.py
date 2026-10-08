with open("backend/black2/api/player_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

# Replace slot_to_write with 0
old_block = """    # 自动内存快捷登记 (Auto Memory Shortcut Registration):
    # 动态定位自行车在重要道具口袋中的槽位索引，并写入 Block 32 + 0x5C 快捷登记槽！
    if _client is not None and state.get("bike_slot_index") is not None:
        sh_addr = await _resolve_shortcut_address(reader)
        if sh_addr is not None:
            try:
                # 第0槽绑定自行车，后续槽清空为 0xFF，确保按 Y 键直接走 EventShortcutCallDirect 直通上车/下车，绝不弹轮盘菜单
                slot_to_write = int(state["bike_slot_index"])
                bytes_payload = [slot_to_write] + [0xFF] * 15
                await _client.write_bytes(sh_addr, bytes_payload, domain="Main RAM")
            except Exception:
                pass"""

new_block = """    # 自动内存快捷登记 (Auto Memory Shortcut Registration):
    # 黑2官方 SaveBlock 32 + 0x5C (ShortcutSave) 中，0 固定为自行车全局枚举 (0=自行车, 1=地图, 2=对战记录器, 3=朋友手册, 4=超级钓竿, 5=寻宝机器)
    if _client is not None:
        sh_addr = await _resolve_shortcut_address(reader)
        if sh_addr is not None:
            try:
                # 第0槽绑定 0 (自行车)，后续清空为 0xFF，直通上车/下车，绝不弹轮盘
                bytes_payload = [0] + [0xFF] * 15
                await _client.write_bytes(sh_addr, bytes_payload, domain="Main RAM")
            except Exception:
                pass"""

text = text.replace(old_block, new_block)

old_dismount_block = """    # 确保快捷槽直通绑定自行车 (避免下车时误弹轮盘)
    if _client is not None and state.get("bike_slot_index") is not None:
        sh_addr = await _resolve_shortcut_address(reader)
        if sh_addr is not None:
            try:
                slot_to_write = int(state["bike_slot_index"])
                bytes_payload = [slot_to_write] + [0xFF] * 15
                await _client.write_bytes(sh_addr, bytes_payload, domain="Main RAM")
            except Exception:
                pass"""

new_dismount_block = """    # 确保快捷槽直通绑定自行车 (避免下车时误弹轮盘)
    if _client is not None:
        sh_addr = await _resolve_shortcut_address(reader)
        if sh_addr is not None:
            try:
                bytes_payload = [0] + [0xFF] * 15
                await _client.write_bytes(sh_addr, bytes_payload, domain="Main RAM")
            except Exception:
                pass"""

text = text.replace(old_dismount_block, new_dismount_block)

with open("backend/black2/api/player_routes.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Updated player_routes.py with canonical shortcut enum 0 for bicycle")
