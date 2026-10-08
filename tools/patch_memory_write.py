with open('backend/black2/api/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

req_code = """
class MemoryWriteRequest(BaseModel):
    addr: int
    bytes: list[int]
    domain: str = "Main RAM"
"""

endpoint_code = """
@app.post("/api/dev/memory_write")
async def post_dev_memory_write(req: MemoryWriteRequest):
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    return await client.write_bytes(req.addr, req.bytes, domain=req.domain)
"""

if "MemoryWriteRequest" not in content:
    content = content.replace("class MemoryBatchSnapshotRequest(BaseModel):", req_code + "\nclass MemoryBatchSnapshotRequest(BaseModel):")
    content = content.replace('@app.post("/api/dev/memory_batch_snapshot")', endpoint_code + '\n@app.post("/api/dev/memory_batch_snapshot")')
    with open("backend/black2/api/app.py", "w", encoding="utf-8") as f:
        f.write(content)
    print("Patched app.py with memory_write")
else:
    print("Already present")
