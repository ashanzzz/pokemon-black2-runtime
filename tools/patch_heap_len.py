with open("backend/black2/decoders/battle_identity.py", "r", encoding="utf-8") as f:
    text = f.read()

assert "BATTLE_POKE_HEAP_LENGTH = 0x1000" in text
text = text.replace("BATTLE_POKE_HEAP_LENGTH = 0x1000", "BATTLE_POKE_HEAP_LENGTH = 0x2800")
with open("backend/black2/decoders/battle_identity.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated BATTLE_POKE_HEAP_LENGTH to 0x2800!")