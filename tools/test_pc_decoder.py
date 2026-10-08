import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii
from backend.black2.decoders.pc_storage_runtime import decode_all_boxes, search_pc_storage
from backend.black2.dex.store import DexStore

url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'full', 'domain': 'Main RAM', 'offset': 0x200000, 'length': 0x40000}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
chunk = binascii.unhexlify(data['results']['full']['hex'])

# 构建虚拟 ram 缓冲区，使得 0x02200000 偏移与绝对地址对齐
ram = bytearray(0x240000)
ram[0x200000:0x200000 + len(chunk)] = chunk
print(f'Buffered RAM: {len(ram)} bytes')

dex = DexStore()
res = decode_all_boxes(bytes(ram), dex)
print(f'SaveBlock Base: {res["save_block_base"]}')
print(f'Total Stored: {res["total_stored"]} / {res["total_capacity"]}')
for b in res['boxes'][:3]:
    print(f'Box {b["box_id"]} ({b["name"]}): {b["count"]} pokemons')
    for mon in b['slots'][:3]:
        print(f'  Slot {mon["slot"]}: {mon["species_name"]} ({mon["species_name_en"]}) Lv.{mon["level"]}, 特性: {mon["ability_name"]}, 道具: {mon["held_item_name"]}')

results = search_pc_storage(res, ability_id=22)
print(f'Search Intimidate (Ability 22): found {len(results)} pokemons')
for r in results:
    print(f'  Box {r["box_id"]} Slot {r["slot"]}: {r["species_name"]} Lv.{r["level"]}')
