import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii

url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'full', 'domain': 'Main RAM', 'offset': 0x200000, 'length': 0x40000}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
chunk = binascii.unhexlify(data['results']['full']['hex'])

target = bytes.fromhex('922cf440')
idx = chunk.find(target)
print('Target index in chunk:', hex(idx), 'Absolute address:', hex(0x02200000 + idx))
