import re

# 1. Update v2.css
with open("frontend/v2.css", "r", encoding="utf-8") as f:
    css = f.read()

path_css_replacement = """
/* 高对比度全息寻路路径步态显色体系 */
.cell-path-walk {
  background: #00E676 !important;
  border: 2px solid #FFFFFF !important;
  color: #000000 !important;
  font-weight: 900 !important;
  box-shadow: 0 0 14px #00E676 !important;
  z-index: 10;
}

.cell-path-run {
  background: #1B5E20 !important;
  border: 2px solid #00E676 !important;
  color: #E8F5E9 !important;
  font-weight: 900 !important;
  text-shadow: 0 0 4px #00E676 !important;
  box-shadow: 0 0 14px rgba(0, 230, 118, 0.7) !important;
  z-index: 10;
}

.cell-path-bike {
  background: #FFD600 !important;
  border: 2px solid #FFAB00 !important;
  color: #000000 !important;
  font-weight: 900 !important;
  box-shadow: 0 0 16px #FFD600 !important;
  z-index: 10;
}

.cell-path-surf {
  background: #0D47A1 !important;
  border: 2px solid #40C4FF !important;
  color: #FFFFFF !important;
  font-weight: 900 !important;
  box-shadow: 0 0 16px #2979FF !important;
  z-index: 10;
}

.cell-path-goal {
  border: 2px solid #FF1744 !important;
  box-shadow: 0 0 18px #FF1744 !important;
  background: #D50000 !important;
  color: #FFFFFF !important;
  font-weight: 900 !important;
  z-index: 11;
  animation: ds-pulse 1.2s infinite alternate;
}

/* 21x21 与 31x31 动态高密度视口网格收缩支持 */
.radius-10 .ds-map-cell {
  width: 22px;
  height: 22px;
  font-size: 11px;
}
.radius-10 .ds-ruler-col, .radius-10 .ds-ruler-row {
  font-size: 10px;
  width: 22px;
  height: 22px;
}

.radius-15 .ds-map-cell {
  width: 15px;
  height: 15px;
  font-size: 9px;
  border-width: 0.5px;
}
.radius-15 .ds-ruler-col, .radius-15 .ds-ruler-row {
  font-size: 8px;
  width: 15px;
  height: 15px;
}
"""

if ".radius-10" not in css:
    # replace cell-path definitions
    css = re.sub(r'/\* 寻路路径节点高亮 \*/[\s\S]*?\.cell-path-goal\s*\{[\s\S]*?\}', path_css_replacement.strip(), css)
    with open("frontend/v2.css", "w", encoding="utf-8") as f:
        f.write(css)
    print("v2.css updated with high-contrast colors and dense grid classes.")
else:
    print("v2.css already updated.")
