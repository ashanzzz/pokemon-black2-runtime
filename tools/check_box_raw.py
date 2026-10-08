import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii
from backend.black2.decoders.pc_storage_runtime import decode_box_pokemon
from backend.black2.dex.store import DexStore

url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'full', 'domain': 'Main RAM', 'offset': 0x200000, 'length': 0x40000}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
chunk = binascii.unhexlify(data['results']['full']['hex'])

ram = bytearray(0x250000)
ram[0x200000:0x200000 + len(chunk)] = chunk

box1_off = 0x205C24
print('box1_off raw hex:', ram[box1_off:box1_off+32].hex())

dex = DexStore()
pkm = decode_box_pokemon(bytes(ram[box1_off:box1_off+136]), dex)
print('pkm:', pkm)
