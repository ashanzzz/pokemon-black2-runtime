with open("frontend/v2.css", "r", encoding="utf-8") as f:
    text = f.read()

stair_css = """
.cell-stair {
  font-weight: 900 !important;
  cursor: pointer;
  z-index: 7;
}

.cell-stair-up {
  background: rgba(0, 229, 255, 0.22) !important;
  color: #00E5FF !important;
  border: 1.5px solid #00E5FF !important;
  box-shadow: 0 0 10px rgba(0, 229, 255, 0.45) !important;
}

.cell-stair-down {
  background: rgba(255, 171, 0, 0.22) !important;
  color: #FFD54F !important;
  border: 1.5px solid #FFAB00 !important;
  box-shadow: 0 0 10px rgba(255, 171, 0, 0.45) !important;
}
"""

if ".cell-stair-up" not in text:
    text += stair_css
    with open("frontend/v2.css", "w", encoding="utf-8") as f:
        f.write(text)
    print("Added .cell-stair-up and .cell-stair-down to v2.css")
else:
    print("Already in v2.css")
