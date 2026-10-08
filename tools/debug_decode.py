import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii
from backend.black2.decoders.party_runtime import _decrypt_words, _u16, _u32, BOX_ENCRYPTED_OFFSET, BOX_ENCRYPTED_SIZE

url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'box1', 'domain': 'Main RAM', 'offset': 0x205C24, 'length': 0x1000}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
chunk = binascii.unhexlify(data['results']['box1']['hex'])

slot_raw = chunk[0:136]
pid = _u32(slot_raw, 0)
checksum = _u16(slot_raw, 6)
print(f'Slot 1: PID=0x{pid:08X}, checksum=0x{checksum:04X}')
dec = _decrypt_words(slot_raw[BOX_ENCRYPTED_OFFSET:BOX_ENCRYPTED_OFFSET + BOX_ENCRYPTED_SIZE], checksum)
c_actual = sum(_u16(dec, off) for off in range(0, BOX_ENCRYPTED_SIZE, 2)) & 0xFFFF
print(f'Calc checksum: 0x{c_actual:04X}')
