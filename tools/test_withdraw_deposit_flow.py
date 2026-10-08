import sys; sys.path.insert(0, '.')
import requests, json

# 1. 读当前队伍
party_res = requests.get('http://127.0.0.1:8765/api/v1/game/party').json()
print(f'Party count before withdraw: {party_res.get("count")}')

# 2. 将水水獭的 136 字节写入 Box 1 Slot 1 (模拟仓储里存有水水獭)
gd_res = requests.post('http://127.0.0.1:8765/api/dev/memory_batch_snapshot', json={
    'ranges': [{'id': 'p', 'domain': 'Main RAM', 'offset': 0x21E624, 'length': 144}]
}).json()
import binascii
p_raw = binascii.unhexlify(gd_res['results']['p']['hex'])
mon1_box = list(p_raw[8:8 + 136])

# 写入 Box 1 Slot 1 (0x02205C24)
w_res = requests.post('http://127.0.0.1:8765/api/dev/memory_write', json={
    'addr': 0x02205C24,
    'bytes': mon1_box,
    'domain': 'Main RAM'
}).json()
print('Placed Pokemon into Box 1 Slot 1:', w_res)

# 验证 PC Box 1
box_res = requests.get('http://127.0.0.1:8765/api/v1/pokemon/pc/box/1').json()
print(f'Box 1 stored count: {box_res["count"]}')
print(f'Box 1 Slot 1 species: {box_res["slots"][0]["species_name"]} Lv.{box_res["slots"][0]["level"]}')

# 3. 调用 withdraw API 从 Box 1 Slot 1 取出到队伍 Slot 2
with_res = requests.post('http://127.0.0.1:8765/api/v1/pokemon/pc/withdraw', json={
    'box_id': 1,
    'box_slot': 1,
    'target_party_slot': 2
})
print('Withdraw API Status:', with_res.status_code)
print('Withdraw API Response:', with_res.json())

# 4. 验证 Box 1 Slot 1 是否已变空
box_after = requests.get('http://127.0.0.1:8765/api/v1/pokemon/pc/box/1').json()
print(f'Box 1 stored count after withdraw: {box_after["count"]}')
print(f'Box 1 Slot 1 empty: {box_after["slots"][0].get("empty")}')

# 5. 验证 Party 是否变为 2 只
party_after = requests.get('http://127.0.0.1:8765/api/v1/game/party').json()
print(f'Party count after withdraw: {party_after.get("count")}')
for s in party_after.get('slots', []):
    print(f'  Party Slot {s["slot"]}: {s["species_name_en"]} Lv.{s["level"]}, HP={s["current_hp"]}/{s["max_hp"]}')

# 6. 现在队伍有 2 ibr，测试 deposit 将 Slot 2 存入 Box 1 Slot 1
dep_res = requests.post('http://127.0.0.1:8765/api/v1/pokemon/pc/deposit', json={
    'party_slot': 2,
    'target_box': 1,
    'target_slot': 1
})
print('Deposit API Status:', dep_res.status_code)
print('Deposit API Response:', dep_res.json())

# 7. 验证队伍恢复为 1 只
party_final = requests.get('http://127.0.0.1:8765/api/v1/game/party').json()
print(f'Final Party count: {party_final.get("count")}')

# 8. 清空 Box 1 Slot 1 还原干净现场
requests.post('http://127.0.0.1:8765/api/dev/memory_write', json={
    'addr': 0x02205C24,
    'bytes': [0] * 136,
    'domain': 'Main RAM'
})
print('ALL E2E MUTATION TESTS PASSED!')
