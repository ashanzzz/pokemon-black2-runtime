import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
from backend.black2.decoders.trainer_rom import decode_gen5_message_file

p = navigation_static_provider()
arc = p.rom.rom.archive("a/0/0/2")

keywords = ["桧扇市", "贝尔", "红豆杉", "修", "妹妹", "最初", "家", "主人公", "恭平", "鸣依"]

matches = []
for idx in range(len(arc.files)):
    try:
        msgs = decode_gen5_message_file(arc.files[idx])[0]
        for m_idx, m in enumerate(msgs):
            t = m.get('text', '')
            for kw in ["桧扇市", "我的家", "主角家", "阿修家", "劲敌家"]:
                if kw in t:
                    matches.append((idx, m_idx, kw, t[:50]))
    except Exception:
        pass

print(f"Total keyword matches: {len(matches)}")
for idx, m_idx, kw, t in matches[:20]:
    print(f"File {idx} msg {m_idx} ({kw}): {t}")
