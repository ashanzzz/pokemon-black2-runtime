with open("backend/black2/decoders/party_runtime.py", "r", encoding="utf-8") as f:
    text = f.read()

target = """    # Block A (0x00) holds species/item/experience; block B (0x20) holds the
    # four move IDs and their current PP. Party-only values begin at 0x88.
    moves = [
        {"slot": move_slot + 1, "move_id": _u16(data, 0x20 + move_slot * 2), "current_pp": data[0x28 + move_slot]}
        for move_slot in range(4)
    ]"""

replacement = """    # Block A (0x00) holds species/item/experience; block B (0x20) holds the
    # four move IDs and their current PP. Block B offset 0x2C holds PP Up counts.
    # Party-only values begin at 0x88.
    dex = _get_dex()
    moves = []
    for move_slot in range(4):
        move_id = _u16(data, 0x20 + move_slot * 2)
        cur_pp = int(data[0x28 + move_slot])
        pp_ups = int(data[0x2C + move_slot] & 0x03) if move_id > 0 else 0
        base_pp = 0
        if dex and move_id > 0:
            m_entity = dex.get("moves", move_id) or {}
            base_pp = int(m_entity.get("pp") or 0)
        max_pp = base_pp + (base_pp * pp_ups // 5) if base_pp > 0 else cur_pp
        moves.append({
            "slot": move_slot + 1,
            "move_id": move_id,
            "current_pp": cur_pp,
            "max_pp": max_pp,
            "base_pp": base_pp,
            "pp_ups": pp_ups,
            "usable": bool(cur_pp > 0 and move_id > 0),
        })"""

helper = """_shared_dex = None

def _get_dex():
    global _shared_dex
    if _shared_dex is None:
        try:
            from ..dex.store import DexStore
            _shared_dex = DexStore()
        except Exception:
            _shared_dex = None
    return _shared_dex

"""

if "_get_dex()" not in text:
    assert target in text
    text = text.replace(target, replacement)
    text = helper + text
    with open("backend/black2/decoders/party_runtime.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Updated party_runtime.py with full PP fields (max_pp, base_pp, pp_ups, usable)!")
else:
    print("Already updated.")