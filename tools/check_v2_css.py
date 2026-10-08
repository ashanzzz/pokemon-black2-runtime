with open("frontend/v2.css", "r", encoding="utf-8") as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "z-index" in line or "position: fixed" in line or "position: absolute" in line:
        print(f"Line {i+1}: {line.strip()}")
