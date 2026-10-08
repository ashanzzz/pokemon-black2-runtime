with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

old_part = """    party_moves = set()
    try:
        from ..decoders.party_runtime import PlayerPartyDecoder
        party_dec = PlayerPartyDecoder()
        party_data = party_dec.decode()
        for mon in (party_data or {}).get("pokemon", []):
            for m in mon.get("moves", []):
                mid = m.get("id")
                if isinstance(mid, int):
                    party_moves.add(mid)
    except Exception:
        pass"""

new_part = """    party_moves = set()
    try:
        from .battle_routes import _party_decoder
        party_data = await _party_decoder.sample()
        for slot in (party_data or {}).get("slots", []):
            for m in slot.get("moves", []):
                mid = m.get("move_id")
                if isinstance(mid, int):
                    party_moves.add(mid)
    except Exception:
        pass"""

if old_part in text:
    text = text.replace(old_part, new_part)
    with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Fixed navigation_fast_travel_evaluate party decoder call!")
else:
    print("old_part not found in navigation_routes.py")