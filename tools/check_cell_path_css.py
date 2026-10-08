with open("frontend/v2.css", "r", encoding="utf-8") as f:
    text = f.read()

for line in text.splitlines():
    if "cell-path" in line:
        print(line)
