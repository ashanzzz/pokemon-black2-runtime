import sys; sys.path.insert(0, '.')
import urllib.request, json, struct, binascii

party_ptr = 0x0221E624

# 读 party_ptr 前 8 字节
url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'p', 'domain': 'Main RAM', 'offset': party_ptr - 0x02000000, 'length': 8}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
raw = binascii.unhexlify(res['results']['p']['hex'])
cap, cnt = struct.unpack('<II', raw)
print(f'Before: capacity={cap}, count={cnt}')

# 写入 count = 2 到 party_ptr + 4
write_url = 'http://localhost:8765/api/dev/memory_write'
w_body = {'addr': party_ptr + 4, 'bytes': [2, 0, 0, 0], 'domain': 'Main RAM'}
req = urllib.request.Request(write_url, data=json.dumps(w_body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    w_res = json.loads(resp.read().decode('utf-8'))
print('Write response:', w_res)

# 回读
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    res2 = json.loads(resp.read().decode('utf-8'))
raw2 = binascii.unhexlify(res2['results']['p']['hex'])
cap2, cnt2 = struct.unpack('<II', raw2)
print(f'After: capacity={cap2}, count={cnt2}')

# 恢复 count = 1
w_body['bytes'] = [1, 0, 0, 0]
req = urllib.request.Request(write_url, data=json.dumps(w_body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    pass
print('Restored count=1')
