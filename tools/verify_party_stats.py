import sys; sys.path.insert(0, '.')
import urllib.request, json, binascii, struct
from backend.black2.decoders.party_runtime import (
    IREJ_REV1_GAME_DATA,
    _decrypt_words,
    PARTY_POKEMON_SIZE,
    BOX_POKEMON_SIZE,
    _u16,
    _u32,
)

# 读当前队伍里的水水獭实战数据
url = 'http://localhost:8765/api/dev/memory_batch_snapshot'
body = {'ranges': [{'id': 'party', 'domain': 'Main RAM', 'offset': 0x21E624, 'length': 0x100}]}
req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'), headers={'Content-Type': 'application/json'})
with urllib.request.urlopen(req) as resp:
    data = json.loads(resp.read().decode('utf-8'))
p_raw = binascii.unhexlify(data['results']['party']['hex'])
mon1_full = p_raw[8:8 + PARTY_POKEMON_SIZE]

pid = _u32(mon1_full, 0)
party_encrypted = mon1_full[BOX_POKEMON_SIZE:] # 84 bytes
party_decrypted = _decrypt_words(party_encrypted, pid)

print(f'Party Decrypted Length: {len(party_decrypted)}')
status = _u32(party_decrypted, 0)
level = party_decrypted[4]
cur_hp = _u16(party_decrypted, 6)
max_hp = _u16(party_decrypted, 8)
atk = _u16(party_decrypted, 10)
defense = _u16(party_decrypted, 12)
spe = _u16(party_decrypted, 14)
spa = _u16(party_decrypted, 16)
spd = _u16(party_decrypted, 18)

print(f'Live Mon1: Level={level}, HP={cur_hp}/{max_hp}, Atk={atk}, Def={defense}, Spe={spe}, SpA={spa}, SpD={spd}')
