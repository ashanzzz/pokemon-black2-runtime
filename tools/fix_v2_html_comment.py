with open("frontend/v2.html", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace("<!-- 选定瓦片全息物理感知档案卡<!-- 选定瓦片全息物理感知档案卡 (明细展示) -->", "<!-- 选定瓦片全息物理感知档案卡 (明细展示) -->")

with open("frontend/v2.html", "w", encoding="utf-8") as f:
    f.write(text)

print("Fixed v2.html comment typo")
