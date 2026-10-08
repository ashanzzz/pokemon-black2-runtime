with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

old_decl = """  const isCatwalkBody = (symbol === '╫' || tileClass === 190 || matKind === 'catwalk');"""

new_decl = """  const isCatwalkBody = (symbol === '╫' || tileClass === 190 || matKind === 'catwalk');
  const isStair = (symbol === '▲' || symbol === '▼' || matKind.includes('stair') || String(cell.kind || '').includes('slope') || String(cell.kind || '').includes('阶梯'));"""

text = text.replace(old_decl, new_decl)

# Also remove duplicate `const isStair` lower down
text = text.replace("const isStair = (['▲', '▼'].includes(symbol) || matKind.includes('stair'));\n", "")

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("Moved isStair declaration to the top of renderTileInspection")
