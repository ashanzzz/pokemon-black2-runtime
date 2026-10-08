import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii, struct
from backend.black2.decoders.party_runtime import (
    IREJ_REV1_GAME_DATA,
    GAME_DATA_PARTY_PTR,
    _u32,
    _u16,
    PARTY_POKEMON_SIZE,
    BOX_POKEMON_SIZE,
)

# 读 GameData
url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'gd', 'domain': 'Main RAM', 'offset': 0x23B570, 'length': 0x200}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
raw = binascii.unhexlify(data['results']['gd']['hex'])
party_ptr = struct.unpack('<I', raw[0x194:0x198])[0]
print(f'Live Party ptr: 0x{party_ptr:08X}')

# 读 Party 结构体 (0x534 字节)
body = {'ranges': [{'id': 'party', 'domain': 'Main RAM', 'offset': party_ptr - 0x02000000, 'length': 0x200}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
party_raw = binascii.unhexlify(data['results']['party']['hex'])
capacity = struct.unpack('<I', party_raw[0:4])[0]
count = struct.unpack('<I', party_raw[4:8])[0]
print(f'Party Capacity: {capacity}, Count: {count}')

mon1 = party_raw[8:8 + PARTY_POKEMON_SIZE]
pid1 = struct.unpack('<I', mon1[0:4])[0]
csum1 = struct.unpack('<H', mon1[6:8])[0]
print(f'Party Slot 1: PID=0x{pid1:08X}, Checksum=0x{csum1:04X}')
