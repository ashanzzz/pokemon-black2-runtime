with open("backend/black2/decoders/battle_identity.py", "r", encoding="utf-8") as f:
    text = f.read()

dup = """        else:
            player_active = None
            opponent_party = []
            opponent_active = None
            side_status = "unresolved"
        else:
            player_active = None
            opponent_party = []
            opponent_active = None
            side_status = "unresolved\""""

single = """        else:
            player_active = None
            opponent_party = []
            opponent_active = None
            side_status = "unresolved\""""

assert dup in text
text = text.replace(dup, single)
with open("backend/black2/decoders/battle_identity.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Removed duplicate else block!")