import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.decoders.trainer_rom import decode_gen5_message_file

p = navigation_static_provider()
arc = p.rom.rom.archive("a/0/0/2")

for zid in range(428, 437):
    z = p.rom.zone(zid)
    tf_id = z.text_file_id
    msgs = decode_gen5_message_file(arc.files[tf_id])[0]
    print(f"\n================ ZONE {zid} (text_id={tf_id}) ================")
    for idx, m in enumerate(msgs):
        t = m.get('text', '')
        if any(kw in t for kw in ['妈妈', '劲敌', '修', '贝尔', '红豆杉', '妹妹', '家', '欢迎回家', '休息', '活力']):
            clean = t.encode('raw_unicode_escape').decode('utf-8', errors='ignore').replace('\n', ' ')
            print(f"  [{idx}]: {clean}")
