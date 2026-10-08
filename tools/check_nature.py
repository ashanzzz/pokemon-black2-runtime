import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii
from backend.black2.decoders.party_runtime import (
    PARTY_POKEMON_SIZE,
    BOX_POKEMON_SIZE,
    BOX_ENCRYPTED_OFFSET,
    BOX_ENCRYPTED_SIZE,
    _decrypt_words,
    _unshuffle_blocks,
    _u16,
    _u32,
)

url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'party', 'domain': 'Main RAM', 'offset': 0x21E624, 'length': 0x100}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
p_raw = binascii.unhexlify(data['results']['party']['hex'])
mon1_full = p_raw[8:8 + PARTY_POKEMON_SIZE]
pid = _u32(mon1_full, 0)
csum = _u16(mon1_full, 6)
dec = _decrypt_words(mon1_full[BOX_ENCRYPTED_OFFSET:BOX_ENCRYPTED_OFFSET + BOX_ENCRYPTED_SIZE], csum)
unshuf = _unshuffle_blocks(dec, pid)
nature_id = unshuf[0x39]
print(f'Nature byte in Block B (0x39): {nature_id}, PID%25 = {pid % 25}')
