with open("backend/black2/api/pc_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

old_p1 = '?? {req.slot_a}?{name_a} Lv.{lvl_a}?? ?? {req.slot_b}?{name_b} Lv.{lvl_b}?'
new_p1 = '席位 {req.slot_a}【{name_a} Lv.{lvl_a}】与 席位 {req.slot_b}【{name_b} Lv.{lvl_b}】'

old_p2 = "?? RAM ???????????????: ?? 1?{lead_mon.get('species_name_zh') or lead_mon.get('species_name')} Lv.{lead_mon.get('level')}?"
new_p2 = "物理 RAM 原子调换成功！最新首发已变更为: 席位 1【{lead_mon.get('species_name_zh') or lead_mon.get('species_name')} Lv.{lead_mon.get('level')}】"

old_p3 = '?? {req.party_slot}?{pokemon_name}????? {req.move_slot} ????{move_name}?'
new_p3 = '席位 {req.party_slot}【{pokemon_name}】招式槽位 {req.move_slot} 已学会「{move_name}」'

old_p4 = '???????{old_move_name}????? RAM ???: 0x{new_checksum:04X}'
new_p4 = '（替换原招式「{old_move_name}」），物理 RAM 校验码: 0x{new_checksum:04X}'

text = text.replace(old_p1, new_p1).replace(old_p2, new_p2).replace(old_p3, new_p3).replace(old_p4, new_p4)

with open("backend/black2/api/pc_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Unicode strings restored perfectly!")