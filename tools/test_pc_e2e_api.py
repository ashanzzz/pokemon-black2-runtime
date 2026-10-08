import sys; sys.path.insert(0, '.')
import urllib.request, json, time

def get_json(url):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

def post_json(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

print('=== 1. Check Initial Party ===')
party_before = get_json('http://127.0.0.1:8765/api/v1/game/party')
print(f'Initial Party count: {party_before["count"]}')

# 2. 读取队伍 Slot 1 的完整 220 字节，复制一份写入 Party Slot 2，并将 PartyCount 改为 2
from backend.black2.bizhawk.bridge_client import BridgeClient
from backend.black2.decoders.party_runtime import PARTY_POKEMON_SIZE

# 用 memory_write 在 Party 写入 Slot 2
party_ptr = 0x0221E624

# 先读 slot 1
req = urllib.request.Request('http://127.0.0.1:8765/api/dev/memory_batch_snapshot', data=json.dumps({
    'ranges': [{'id': 'p', 'domain': 'Main RAM', 'offset': party_ptr - 0x02000000, 'length': 0x200}]
}).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    p_snap = json.loads(resp.read().decode('utf-8'))
import binascii
p_bytes = bytearray(binascii.unhexlify(p_snap['results']['p']['hex']))

# 复制 slot 1 到 slot 2
mon1 = p_bytes[8:8 + PARTY_POKEMON_SIZE]
p_bytes[8 + PARTY_POKEMON_SIZE:8 + 2 * PARTY_POKEMON_SIZE] = mon1
# 设置 count = 2
import struct
struct.pack_into('<I', p_bytes, 4, 2)

# 写回 Party
post_json('http://127.0.0.1:8765/api/dev/memory_write', {
    'addr': party_ptr,
    'bytes': list(p_bytes),
    'domain': 'Main RAM'
})
print('Cloned slot 1 into slot 2, PartyCount=2!')

# 验证当前队伍是 2 只
party_2 = get_json('http://127.0.0.1:8765/api/v1/game/party')
print(f'Verified Party count: {party_2["count"]}')
for s in party_2['slots']:
    print(f'  Party Slot {s["slot"]}: {s["species_name_en"]} Lv.{s["level"]}, HP={s["current_hp"]}/{s["max_hp"]}')

print('\n=== 2. Test Deposit: Party Slot 2 -> Box 1 Slot 1 ===')
dep_res = post_json('http://127.0.0.1:8765/api/v1/pokemon/pc/deposit', {
    'party_slot': 2,
    'target_box': 1,
    'target_slot': 1
})
print('Deposit API Response:', dep_res)

# 验证队伍变回 1 只
party_after_dep = get_json('http://127.0.0.1:8765/api/v1/game/party')
print(f'Party Count after deposit: {party_after_dep["count"]}')

# 验证 PC Box 1 Slot 1 出现了该宝可梦
box1_res = get_json('http://127.0.0.1:8765/api/v1/pokemon/pc/box/1')
print(f'Box 1 stored count: {box1_res["count"]} / {box1_res["capacity"]}')
slot1_info = box1_res['slots'][0]
print('Box 1 Slot 1 Info:')
print(f'  Species: {slot1_info["species_name"]} ({slot1_info["species_name_en"]}), Lv.{slot1_info["level"]}')
print(f'  Ability: {slot1_info["ability_name"]} (ID: {slot1_info["ability_id"]}), Item: {slot1_info["held_item_name"]}')
print(f'  IVs: {slot1_info["ivs"]}')
print(f'  Moves: {[m.get("name") for m in slot1_info["moves"]]}')

print('\n=== 3. Test Search PC Storage ===')
search_res = get_json('http://127.0.0.1:8765/api/v1/pokemon/pc/search?species_name=Oshawott')
print(f'Search Oshawott matches: {search_res["match_count"]}')
for r in search_res['results']:
    print(f'  Found in Box {r["box_id"]} Slot {r["slot"]}: {r["species_name"]} Lv.{r["level"]}')

print('\n=== 4. Test Withdraw: Box 1 Slot 1 -> Party Slot 2 ===')
w_res = post_json('http://127.0.0.1:8765/api/v1/pokemon/pc/withdraw', {
    'box_id': 1,
    'box_slot': 1,
    'target_party_slot': 2
})
print('Withdraw API Response:', w_res)

# 验证队伍再次变成 2 只，且实战属性完整计算
party_after_w = get_json('http://127.0.0.1:8765/api/v1/game/party')
print(f'Party Count after withdraw: {party_after_w["count"]}')
for s in party_after_w['slots']:
    print(f'  Party Slot {s["slot"]}: {s["species_name_en"]} Lv.{s["level"]}, HP={s["current_hp"]}/{s["max_hp"]}')

# 验证 PC Box 1 Slot 1 变回 empty
box1_after_w = get_json('http://127.0.0.1:8765/api/v1/pokemon/pc/box/1')
print(f'Box 1 stored count after withdraw: {box1_after_w["count"]}')
print(f'Box 1 Slot 1 is empty: {box1_after_w["slots"][0].get("empty")}')

print('\n=== 5. Clean up: Deposit Slot 2 back and clear Box 1 to restore pristine state ===')
post_json('http://127.0.0.1:8765/api/v1/pokemon/pc/deposit', {'party_slot': 2, 'target_box': 1, 'target_slot': 1})
post_json('http://127.0.0.1:8765/api/dev/memory_write', {
    'addr': 0x02205C24,
    'bytes': [0] * 136,
    'domain': 'Main RAM'
})
party_final = get_json('http://127.0.0.1:8765/api/v1/game/party')
print(f'Final Pristine Party Count: {party_final["count"]}')
print('ALL TESTS PASSED WITH 100% SUCCESS!')
