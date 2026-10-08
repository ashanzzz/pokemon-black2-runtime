import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.decoders.trainer_rom import decode_gen5_message_file

p = navigation_static_provider()
arc = p.rom.rom.archive("a/0/0/2")

for zid in [428, 429, 431, 433, 434, 435, 436]:
    z = p.rom.zone(zid)
    tf_id = z.text_file_id
    msgs = decode_gen5_message_file(arc.files[tf_id])[0]
    print(f"=== ZONE {zid} (text file {tf_id}, msgs={len(msgs)}) ===")
    for idx in range(min(15, len(msgs))):
        t = msgs[idx].get('text', '')
        # print unicode escape
        clean_t = t.encode('raw_unicode_escape').decode('utf-8', errors='ignore').replace('\n', ' ')
        print(f"  [{idx}]: {clean_t[:40]}")
