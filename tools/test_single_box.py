import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii
from backend.black2.decoders.pc_storage_runtime import decode_box_pokemon
from backend.black2.dex.store import DexStore

url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'box1', 'domain': 'Main RAM', 'offset': 0x205C24, 'length': 0x1000}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
chunk = binascii.unhexlify(data['results']['box1']['hex'])
print(f'Read Box 1: {len(chunk)} bytes')

dex = DexStore()
count = 0
for s in range(30):
    slot_raw = chunk[s*136:(s+1)*136]
    mon = decode_box_pokemon(slot_raw, dex)
    if mon:
        count += 1
        if count <= 5:
            print(f'Slot {s+1}: {mon["species_name"]} ({mon["species_name_en"]}) Lv.{mon["level"]}, 特性: {mon["ability_name"]}, 道具: {mon["held_item_name"]}')

print(f'Total valid decoded in Box 1: {count}')
