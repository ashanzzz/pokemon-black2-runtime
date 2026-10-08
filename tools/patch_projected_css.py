# 1. Update v2.css with cell-path-projected classes
with open("frontend/v2.css", "r", encoding="utf-8") as f:
    css = f.read()

proj_css = """
/* 跨层空间垂直投影节点 (边缘发光，代表同坐标在另一标高层) */
.cell-path-projected {
  background: rgba(255, 214, 0, 0.08) !important;
  border: 1.5px dashed #FFD600 !important;
  color: #FFE082 !important;
  font-weight: 700 !important;
  box-shadow: 0 0 10px rgba(255, 214, 0, 0.35) !important;
  z-index: 8;
  opacity: 0.88;
}

.cell-path-projected.proj-run {
  background: rgba(0, 230, 118, 0.08) !important;
  border: 1.5px dashed #00E676 !important;
  color: #B9F6CA !important;
  box-shadow: 0 0 10px rgba(0, 230, 118, 0.35) !important;
}

.cell-path-projected.proj-bike {
  background: rgba(255, 214, 0, 0.08) !important;
  border: 1.5px dashed #FFD600 !important;
  color: #FFE082 !important;
  box-shadow: 0 0 10px rgba(255, 214, 0, 0.35) !important;
}

.cell-path-projected.proj-walk {
  background: rgba(0, 230, 118, 0.08) !important;
  border: 1.5px dashed #00E676 !important;
  color: #69F0AE !important;
  box-shadow: 0 0 10px rgba(0, 230, 118, 0.35) !important;
}

.cell-path-projected.proj-surf {
  background: rgba(13, 71, 161, 0.12) !important;
  border: 1.5px dashed #40C4FF !important;
  color: #80D8FF !important;
  box-shadow: 0 0 10px rgba(64, 196, 255, 0.35) !important;
}

.cell-path-projected.proj-goal {
  border: 2px dashed #FF1744 !important;
  color: #FF5252 !important;
  box-shadow: 0 0 14px rgba(255, 23, 68, 0.6) !important;
  animation: ds-pulse 1.4s infinite alternate;
}
"""

if ".cell-path-projected" not in css:
    css += proj_css
    with open("frontend/v2.css", "w", encoding="utf-8") as f:
        f.write(css)
    print("Added .cell-path-projected to v2.css")
else:
    print(".cell-path-projected already present in v2.css")
