import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii
from backend.black2.decoders.pc_storage_runtime import decode_box_pokemon
from backend.black2.dex.store import DexStore

url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'box1_s1', 'domain': 'Main RAM', 'offset': 0x205C24, 'length': 136}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
chunk = binascii.unhexlify(data['results']['box1_s1']['hex'])
print(f'Read Box 1 Slot 1: {len(chunk)} bytes')

dex = DexStore()
mon = decode_box_pokemon(chunk, dex)
if mon:
    print('SUCCESS! Decoded Box 1 Slot 1:')
    print(f'  Species: {mon["species_name"]} ({mon["species_name_en"]}), Level: {mon["level"]}')
    print(f'  Ability: {mon["ability_name"]} (ID: {mon["ability_id"]}), Held Item: {mon["held_item_name"]}')
    print(f'  IVs: {mon["ivs"]}')
    print(f'  EVs: {mon["evs"]}')
    print(f'  Moves: {[m["name"] for m in mon["moves"]]}')
    print(f'  Nature: {mon["nature"]["name"]} ({mon["nature"]["name_en"]})')
    print(f'  Gender: {mon["gender"]}, Shiny: {mon["is_shiny"]}')
else:
    print('Failed to decode mon (returned None).')
