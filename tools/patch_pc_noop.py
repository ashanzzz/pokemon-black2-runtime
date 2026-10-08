with open("backend/black2/api/pc_routes.py", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

old_code = """@router.post("/party/swap")
async def post_party_swap(req: PartySwapOrderRequest):
    \"\"\"Atomically swap the order of two Pokemon within the active player party (1..6).\"\"\"
    try:
        save_base, party_ptr = await _get_save_base_and_party_ptr()
        party_raw = await _read_main_ram(party_ptr - 0x02000000, 8 + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE)
        party_count = struct.unpack("<I", party_raw[4:8])[0]

        if req.slot_a > party_count:
            raise HTTPException(status_code=400, detail=f"slot_a {req.slot_a} is empty (party has {party_count} members).")
        if req.slot_b > party_count:
            raise HTTPException(status_code=400, detail=f"slot_b {req.slot_b} is empty (party has {party_count} members).")

        if req.slot_a == req.slot_b:
            return {
                "ok": True,
                "status": "succeeded",
                "action": "swap_party_order",
                "message": "Slots are identical; no-op.",
                "slot_a": req.slot_a,
                "slot_b": req.slot_b,
            }"""

new_code = """@router.post("/party/swap")
async def post_party_swap(req: PartySwapOrderRequest):
    \"\"\"Atomically swap the order of two Pokemon within the active player party (1..6).\"\"\"
    try:
        if req.slot_a == req.slot_b:
            return {
                "ok": True,
                "status": "succeeded",
                "action": "swap_party_order",
                "message": "Slots are identical; no-op.",
                "slot_a": req.slot_a,
                "slot_b": req.slot_b,
            }

        save_base, party_ptr = await _get_save_base_and_party_ptr()
        party_raw = await _read_main_ram(party_ptr - 0x02000000, 8 + POKE_PARTY_CAPACITY * PARTY_POKEMON_SIZE)
        party_count = struct.unpack("<I", party_raw[4:8])[0]

        if req.slot_a > party_count:
            raise HTTPException(status_code=400, detail=f"slot_a {req.slot_a} is empty (party has {party_count} members).")
        if req.slot_b > party_count:
            raise HTTPException(status_code=400, detail=f"slot_b {req.slot_b} is empty (party has {party_count} members).")"""

if old_code in text:
    text = text.replace(old_code, new_code)
    with open("backend/black2/api/pc_routes.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Updated post_party_swap in pc_routes.py successfully!")
else:
    print("old_code not found")