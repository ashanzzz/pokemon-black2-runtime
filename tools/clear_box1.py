import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii

# 将 Box 1 Slot 1 清零 (136 字节 0)
write_url = 'http://localhost:8765/api/dev/memory_write'
write_body = {
    'addr': 0x02205C24,
    'bytes': [0] * 136,
    'domain': 'Main RAM'
}
req = urllib.request.Request(write_url, data=json.dumps(write_body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    res = json.loads(resp.read().decode('utf-8'))
print('Cleared Box 1 Slot 1:', res)
