import requests, json, binascii, time

# 1. 读当前 Party Slot 1 的 136 字节 BoxPokemon
gd_res = requests.post('http://127.0.0.1:8765/api/dev/memory_batch_snapshot', json={
    'ranges': [{'id': 'p', 'domain': 'Main RAM', 'offset': 0x21E624, 'length': 144}]
}).json()
p_raw = binascii.unhexlify(gd_res['results']['p']['hex'])
mon1_box = list(p_raw[8:8 + 136])

# 2. 写入 Box 1 Slot 1 (0x02205C24)
w_res = requests.post('http://127.0.0.1:8765/api/dev/memory_write', json={
    'addr': 0x02205C24,
    'bytes': mon1_box,
    'domain': 'Main RAM'
}).json()
time.sleep(0.2)
print('Step 1: Placed mon into Box 1 Slot 1:', w_res)

# 3. 执行 Withdraw: Box 1 Slot 1 -> Party Slot 2
w_call = requests.post('http://127.0.0.1:8765/api/v1/pokemon/pc/withdraw', json={
    'box_id': 1,
    'box_slot': 1,
    'target_party_slot': 2
})
time.sleep(0.2)
print('Step 2: Withdraw status:', w_call.status_code, w_call.json())

# 4. 回读队伍
party_res = requests.get('http://127.0.0.1:8765/api/v1/game/party').json()
time.sleep(0.2)
print('Step 3: Party count after withdraw:', party_res.get('count'))
for s in party_res.get('slots', []):
    print(f'  Slot {s["slot"]}: {s["species_name_en"]} Lv.{s["level"]} HP={s["current_hp"]}/{s["max_hp"]}')

# 5. 执行 Deposit: Party Slot 2 -> Box 1 Slot 1
d_call = requests.post('http://127.0.0.1:8765/api/v1/pokemon/pc/deposit', json={
    'party_slot': 2,
    'target_box': 1,
    'target_slot': 1
})
time.sleep(0.2)
print('Step 4: Deposit status:', d_call.status_code, d_call.json())

# 6. 回读队伍
party_res2 = requests.get('http://127.0.0.1:8765/api/v1/game/party').json()
print('Step 5: Party count after deposit:', party_res2.get('count'))

# 7. 清理 Box 1 Slot 1
requests.post('http://127.0.0.1:8765/api/dev/memory_write', json={
    'addr': 0x02205C24,
    'bytes': [0] * 136,
    'domain': 'Main RAM'
})
print('SUCCESS! Full Withdraw & Deposit Cycle Complete!')
