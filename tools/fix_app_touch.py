with open("backend/black2/api/app.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace("return await client.touch_screen(req.x, req.y, frames=req.frames)", "return await client.touch(req.x, req.y, frames=req.frames)")
with open("backend/black2/api/app.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated post_v1_input_touch to call client.touch()!")