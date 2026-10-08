import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii, struct
from backend.black2.decoders.party_runtime import (
    PARTY_POKEMON_SIZE,
    BOX_POKEMON_SIZE,
    BOX_ENCRYPTED_OFFSET,
    BOX_ENCRYPTED_SIZE,
    _decrypt_words,
    _unshuffle_blocks,
    _u16,
    _u32,
)
from backend.black2.decoders.pc_storage_runtime import (
    decode_box_pokemon,
    calculate_level_from_exp,
    NATURE_NAMES_ZH,
    SAVE_BLOCK_PARTY_OFFSET,
    SAVE_BLOCK_BOXES_OFFSET,
    BOX_STRIDE,
)
from backend.black2.dex.store import DexStore

dex = DexStore()

# 1. 读当前 Party
url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [
    {'id': 'gd', 'domain': 'Main RAM', 'offset': 0x23B570, 'length': 0x200},
]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
gd_raw = binascii.unhexlify(data['results']['gd']['hex'])
party_ptr = struct.unpack('<I', gd_raw[0x194:0x198])[0]
save_base = party_ptr - SAVE_BLOCK_PARTY_OFFSET
print(f'SaveBase: 0x{save_base:08X}, PartyPtr: 0x{party_ptr:08X}')

# 读 Party Slot 1
body = {'ranges': [
    {'id': 'party', 'domain': 'Main RAM', 'offset': party_ptr - 0x02000000, 'length': 0x200},
]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
p_raw = binascii.unhexlify(data['results']['party']['hex'])
p_count = struct.unpack('<I', p_raw[4:8])[0]
print(f'Original Party Count: {p_count}')

mon1_full = p_raw[8:8 + PARTY_POKEMON_SIZE]
mon1_box = mon1_full[:BOX_POKEMON_SIZE] # 136 bytes BoxPokemon

# 解码 mon1
mon_info = decode_box_pokemon(mon1_box, dex)
print(f'Slot 1: {mon_info["species_name"]} (Lv.{mon_info["level"]}), PID={mon_info["pid"]}')

# 2. 模拟存入: 将 mon1_box 写入 Box 1 Slot 1
box1_slot1_addr = save_base + SAVE_BLOCK_BOXES_OFFSET # 0x02205C24
print(f'Writing test Pokemon to Box 1 Slot 1 at 0x{box1_slot1_addr:08X}...')

write_url = 'http://localhost:8765/api/dev/memory_write'
write_body = {
    'addr': box1_slot1_addr,
    'bytes': list(mon1_box),
    'domain': 'Main RAM'
}
req = urllib.request.Request(write_url, data=json.dumps(write_body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    w_res = json.loads(resp.read().decode('utf-8'))
print('Box 1 Slot 1 write result:', w_res)

# 3. 重新回读 Box 1 Slot 1 验证
body = {'ranges': [
    {'id': 'box1_s1', 'domain': 'Main RAM', 'offset': box1_slot1_addr - 0x02000000, 'length': BOX_POKEMON_SIZE},
]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
read_back = binascii.unhexlify(data['results']['box1_s1']['hex'])
box_mon = decode_box_pokemon(read_back, dex)
print('Read back Box 1 Slot 1 successfully!')
print(f'  Species: {box_mon["species_name"]} ({box_mon["species_name_en"]}), Level: {box_mon["level"]}')
print(f'  Ability: {box_mon["ability_name"]} (ID: {box_mon["ability_id"]}), Held Item: {box_mon["held_item_name"]}')
print(f'  IVs: {box_mon["ivs"]}')
print(f'  Moves: {[m["name"] for m in box_mon["moves"]]}')
