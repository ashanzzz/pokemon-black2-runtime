import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.decoders.trainer_rom import decode_gen5_message_file

p = navigation_static_provider()
arc = p.rom.rom.archive("a/0/0/2")

with open("tools/decoded_texts_169.txt", "w", encoding="utf-8") as f:
    for tf_id in [169, 170, 172, 173, 174, 175, 176, 177, 178, 179]:
        msgs = decode_gen5_message_file(arc.files[tf_id])[0]
        f.write(f"\n================ TEXT FILE {tf_id} (total {len(msgs)}) ================\n")
        for idx, m in enumerate(msgs):
            t = m.get('text', '').replace('\n', ' ')
            if len(t) > 0:
                f.write(f"[{idx}]: {t}\n")
print("Done writing tools/decoded_texts_169.txt")
