with open("frontend/v2.css", "r", encoding="utf-8") as f:
    text = f.read()

if ".cell-elevation-gap" not in text:
    gap_css = """
.cell-elevation-gap {
  background: rgba(255, 255, 255, 0.02) !important;
  color: #64748B !important;
  font-size: 13px !important;
  border-color: rgba(255, 255, 255, 0.05) !important;
}
"""
    text += gap_css
    with open("frontend/v2.css", "w", encoding="utf-8") as f:
        f.write(text)
    print("Added .cell-elevation-gap to v2.css")
else:
    print("Already in v2.css")
