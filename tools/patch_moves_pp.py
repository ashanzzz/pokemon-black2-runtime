with open("backend/black2/api/battle_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

old_ret = """    return {
        **_read_only_contract(),
        "format": "black2-battle-moves/v2",
        "status": "partial" if moves else "unresolved",
        "actor": actor,
        "side": "player",
        "party_slot": 1 if moves else None,
        "moves": moves,
        "contents_known": bool(moves),
        "source": "btl_pokeparam.c (+0x110) & GameData.PokeParty",
        "dex_contract": "/api/v1/dex/moves/{id}",
        "evidence": evidence,
    }"""

new_ret = """    enriched_moves = []
    for m in (moves or []):
        row = dict(m)
        cpp = row.get("current_pp", 0)
        row["usable"] = bool(cpp is not None and cpp > 0)
        row["is_empty_pp"] = not row["usable"]
        enriched_moves.append(row)

    return {
        **_read_only_contract(),
        "format": "black2-battle-moves/v2",
        "status": "partial" if enriched_moves else "unresolved",
        "actor": actor,
        "side": "player",
        "party_slot": 1 if enriched_moves else None,
        "moves": enriched_moves,
        "has_usable_moves": any(m.get("usable") for m in enriched_moves),
        "all_pp_exhausted": bool(enriched_moves and not any(m.get("usable") for m in enriched_moves)),
        "contents_known": bool(enriched_moves),
        "source": "btl_pokeparam.c (+0x110) & GameData.PokeParty",
        "dex_contract": "/api/v1/dex/moves/{id}",
        "evidence": evidence,
    }"""

assert old_ret in text
text = text.replace(old_ret, new_ret)
with open("backend/black2/api/battle_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated battle_moves with PP usability and exhaustion detection in battle_routes.py!")