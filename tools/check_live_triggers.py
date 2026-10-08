import sys; sys.path.insert(0, '.')

import requests, binascii, struct
from backend.black2.world.gen5_rom_map import Gen5RomMap

rom = Gen5RomMap()
ew_data = requests.post('http://127.0.0.1:8765/api/dev/memory_batch_snapshot', json={
    'ranges': [{'id': 'ew', 'domain': 'Main RAM', 'offset': 0x225724, 'length': 431 * 2}]
}).json()
raw_works = binascii.unhexlify(ew_data['results']['ew']['hex'])
works = struct.unpack(f'<{len(raw_works)//2}H', raw_works)

for zid in [427, 437, 439, 446]:
    zh = rom.zone(zid)
    ent = rom.entities(zh.entities_id)
    print('=== Zone', zid, '(Entities', zh.entities_id, ') ===')
    for t in ent.get('triggers', []):
        rec = bytes.fromhex(t['raw_hex'])
        scrid = struct.unpack('<H', rec[0:2])[0]
        expected_val = struct.unpack('<H', rec[2:4])[0]
        var_id = struct.unpack('<H', rec[4:6])[0]
        x = struct.unpack('<h', rec[10:12])[0]
        z = struct.unpack('<h', rec[12:14])[0]
        w = struct.unpack('<H', rec[14:16])[0]
        h = struct.unpack('<H', rec[16:18])[0]
        raw_y = struct.unpack('<h', rec[18:20])[0]
        y = raw_y // 16 if raw_y % 16 == 0 else raw_y

        cur_val = None
        is_active = False
        if 0x4000 <= var_id < 0x4000 + len(works):
            cur_val = works[var_id - 0x4000]
            is_active = (cur_val == expected_val)
        
        tiles = [(x+dx, z+dz, y) for dx in range(w) for dz in range(h)]
        status_str = f'*** ACTIVE STORY GATE *** (Blocks {tiles})' if is_active else 'inactive/passed'
        tid = t['id']
        print(f'  Trig #{tid}: SCRID={scrid}, Var=0x{var_id:04X} (Live={cur_val}, Expected={expected_val}) -> {status_str}')
