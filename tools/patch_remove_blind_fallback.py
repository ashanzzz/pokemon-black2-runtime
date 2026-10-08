with open("backend/black2/api/battle_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

old_code = """    # Player moves
    identity = await _battle_identity(evidence)
    player_active = identity.get("player", {}).get("active") or {}
    moves = player_active.get("moves")
    if not moves:
        party = await _party_decoder.sample()
        slots = party.get("slots") if party.get("status") == "candidate" else []
        moves = slots[0].get("moves", []) if slots else []"""

new_code = """    # Player moves from real BattleMon only (no blind fallback to party.slots[0])
    identity = await _battle_identity(evidence)
    player_active = identity.get("player", {}).get("active") or {}
    moves = player_active.get("moves") or []
    if not moves:
        return {
            **_read_only_contract(),
            "format": "black2-battle-moves/v2",
            "status": "unresolved",
            "actor": actor,
            "side": "player",
            "party_slot": None,
            "moves": [],
            "has_usable_moves": False,
            "all_pp_exhausted": False,
            "contents_known": False,
            "source": "btl_pokeparam.c (+0x110)",
            "reason": "Active player BattleMon is not yet resolved in battle heap. No blind fallback to persistent party is performed.",
            "evidence": evidence,
        }"""

assert old_code in text
text = text.replace(old_code, new_code)
with open("backend/black2/api/battle_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Removed blind party.slots[0] fallback from /battle/moves in battle_routes.py!")