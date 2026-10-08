with open("backend/black2/api/pc_routes.py", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

target_model = """class MoveRequest(BaseModel):
    src_box: int = Field(..., ge=1, le=24)
    src_slot: int = Field(..., ge=1, le=30)
    dst_box: int = Field(..., ge=1, le=24)
    dst_slot: int = Field(..., ge=1, le=30)"""

new_model = """class MoveRequest(BaseModel):
    src_box: int = Field(..., ge=1, le=24)
    src_slot: int = Field(..., ge=1, le=30)
    dst_box: int = Field(..., ge=1, le=24)
    dst_slot: int = Field(..., ge=1, le=30)


class PartySwapOrderRequest(BaseModel):
    slot_a: int = Field(..., ge=1, le=6, description="Party slot A (1..6)")
    slot_b: int = Field(..., ge=1, le=6, description="Party slot B (1..6)")"""

text = text.replace(target_model, new_model)

target_endpoint = """@router.post("/swap")
async def post_swap(req: SwapRequest):"""

new_endpoint = """@router.post("/party/swap")
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
            }

        off_a = 8 + (req.slot_a - 1) * PARTY_POKEMON_SIZE
        off_b = 8 + (req.slot_b - 1) * PARTY_POKEMON_SIZE

        data_a = party_raw[off_a:off_a + PARTY_POKEMON_SIZE]
        data_b = party_raw[off_b:off_b + PARTY_POKEMON_SIZE]

        await _write_main_ram(party_ptr + off_a, data_b)
        await _write_main_ram(party_ptr + off_b, data_a)

        from .battle_routes import _party_decoder
        updated_party = await _party_decoder.sample()

        return {
            "ok": True,
            "status": "succeeded",
            "action": "swap_party_order",
            "slot_a": req.slot_a,
            "slot_b": req.slot_b,
            "party": updated_party,
        }
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"post_party_swap error: {type(e).__name__}: {e}")


@router.post("/swap")
async def post_swap(req: SwapRequest):"""

text = text.replace(target_endpoint, new_endpoint)

with open("backend/black2/api/pc_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated pc_routes.py with /party/swap successfully!")