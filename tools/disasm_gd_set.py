import json, urllib.request, binascii
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB

url = "http://localhost:8765/api/dev/memory_batch_snapshot"
body = {"ranges": [{"id": "func", "domain": "Main RAM", "offset": 0x017650, "length": 48}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))

hex_code = data['results']['func']['hex']
code_bytes = binascii.unhexlify(hex_code)

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
for i in md.disasm(code_bytes, 0x02017650):
    print(f"0x{i.address:08X}:\t{i.mnemonic}\t{i.op_str}")
