import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii, struct

url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'gd', 'domain': 'Main RAM', 'offset': 0x23B570, 'length': 0x200}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
raw = binascii.unhexlify(data['results']['gd']['hex'])
party_ptr = struct.unpack('<I', raw[0x194:0x198])[0]
bag_ptr = struct.unpack('<I', raw[0x190:0x194])[0]
print(f'Current Party ptr: 0x{party_ptr:08X}, base = 0x{party_ptr - 0x18E00:08X}')
print(f'Current Bag ptr:   0x{bag_ptr:08X}, base = 0x{bag_ptr - 0x18400:08X}')
