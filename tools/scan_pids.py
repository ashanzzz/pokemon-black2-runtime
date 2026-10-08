import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii

# 搜索 卡蒂狗 (92 2C F4 40) 和 叉字蝠 (D4 2E 32 10)
crobat_pid = bytes.fromhex('d42e3210')
growlithe_pid = bytes.fromhex('922cf440')

print('Scanning Main RAM in 64KB blocks...')
found_crobat = []
found_growlithe = []

# 只需扫 0x02200000 到 0x02300000 (1MB 范围)
for chunk_start in range(0x200000, 0x300000, 0x8000): # 32KB per request
    url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
    body = {'ranges': [{'id': 'c', 'domain': 'Main RAM', 'offset': chunk_start, 'length': 0x8000}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        raw = binascii.unhexlify(data['results']['c']['hex'])
        idx = 0
        while True:
            idx = raw.find(crobat_pid, idx)
            if idx == -1: break
            addr = 0x02000000 + chunk_start + idx
            found_crobat.append(hex(addr))
            idx += 4
        idx2 = 0
        while True:
            idx2 = raw.find(growlithe_pid, idx2)
            if idx2 == -1: break
            addr = 0x02000000 + chunk_start + idx2
            found_growlithe.append(hex(addr))
            idx2 += 4
    except Exception as e:
        print(f'Error at {hex(chunk_start)}: {e}')
        break

print('Crobat (Party) found at:', found_crobat)
print('Growlithe (Box) found at:', found_growlithe)
