with open("backend/black2/api/app.py", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

target = """app.include_router(pc_router)"""
replacement = """app.include_router(pc_router)
from .pc_routes import post_party_swap, PartySwapOrderRequest

@app.post("/api/v1/game/party/swap")
@app.post("/api/v1/player/party/swap")
async def global_party_swap(req: PartySwapOrderRequest):
    \"\"\"Atomically swap the order of two Pokemon within the active party (1..6).\"\"\"
    return await post_party_swap(req)"""

if target in text and "global_party_swap" not in text:
    text = text.replace(target, replacement)
    with open("backend/black2/api/app.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Updated app.py with global_party_swap successfully!")
else:
    print("app.py already has global_party_swap or target not found")