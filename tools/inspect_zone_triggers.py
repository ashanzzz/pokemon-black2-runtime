import sys
sys.path.insert(0, '.')
from backend.black2.world.gen5_rom_map import Gen5RomMap
rom = Gen5RomMap()
for zid in [439, 427, 437, 446, 457, 436, 438]:
    zh = rom.zone(zid)
    ent = rom.entities(zh.entities_id)
    print('Zone', zid, 'Entities ID:', zh.entities_id, 'counts:', ent['counts'])
    for t in ent.get('triggers', []):
        print('  Trig', t['id'], 'entity_id:', t['entity_id'], 'x:', t['x'], 'z:', t['z'], 'y:', t['y'], 'hex:', t['raw_hex'])
