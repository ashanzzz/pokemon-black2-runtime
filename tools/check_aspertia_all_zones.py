import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.decoders.trainer_rom import decode_gen5_message_file

p = navigation_static_provider()
arc = p.rom.rom.archive("a/0/0/2")

for zid in range(427, 437):
    z = p.rom.zone(zid)
    tf_id = z.text_file_id
    ent = p.rom.entities(z.entities_id)
    text_sample = []
    try:
        msgs = decode_gen5_message_file(arc.files[tf_id])[0]
        for m in msgs[:6]:
            t = m.get('text', '')
            if t and len(t) > 1:
                text_sample.append(repr(t[:25]))
    except Exception as e:
        text_sample.append(str(e))
    npcs = ent.get('npcs', [])
    print(f"Zone {zid}: text_id={tf_id}, npcs={len(npcs)}, warps={len(ent.get('warps', []))}")
    print(f"  Sample text: {text_sample}")
