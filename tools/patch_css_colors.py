with open("frontend/v2.css", "r", encoding="utf-8") as f:
    css = f.read()

old_colors = '''/* ==========================================================================
   路径高亮色彩规范 (步行=绿, 奔跑=深绿, 自行车=蓝, 冲浪=青蓝)
   ========================================================================== */
.cell-path-walk {
  background: rgba(0, 240, 144, 0.35) !important;
  border-color: #00F090 !important;
  color: #00F090 !important;
  font-weight: 900 !important;
  box-shadow: 0 0 10px rgba(0, 240, 144, 0.6) !important;
  z-index: 5;
}

.cell-path-run {
  background: rgba(0, 168, 107, 0.45) !important;
  border-color: #00C853 !important;
  color: #00FF7F !important;
  font-weight: 900 !important;
  box-shadow: 0 0 12px rgba(0, 200, 83, 0.7) !important;
  z-index: 5;
}

.cell-path-bike {
  background: rgba(0, 114, 255, 0.40) !important;
  border-color: #2979FF !important;
  color: #64B5F6 !important;
  font-weight: 900 !important;
  box-shadow: 0 0 12px rgba(41, 121, 255, 0.7) !important;
  z-index: 5;
}

.cell-path-surf {
  background: rgba(0, 229, 255, 0.40) !important;
  border-color: #00E5FF !important;
  color: #E0F7FA !important;
  font-weight: 900 !important;
  box-shadow: 0 0 12px rgba(0, 229, 255, 0.7) !important;
  z-index: 5;
}'''

new_colors = '''/* ==========================================================================
   路径高亮色彩规范 (步行=明亮绿, 奔跑=深墨绿, 自行车=金黄色, 冲浪水路=纯蓝色)
   实心高对比度底色 + 高光轮廓 + 纯白粗体数字，确保任何屏幕一览无余
   ========================================================================== */
.cell-path-walk {
  background: #009624 !important;
  border: 2px solid #00E676 !important;
  color: #FFFFFF !important;
  font-weight: 900 !important;
  box-shadow: 0 0 12px rgba(0, 230, 118, 0.8) !important;
  z-index: 8;
}

.cell-path-run {
  background: #004D20 !important;
  border: 2px solid #00C853 !important;
  color: #69F0AE !important;
  font-weight: 900 !important;
  box-shadow: 0 0 14px rgba(0, 200, 83, 0.85) !important;
  z-index: 8;
}

.cell-path-bike {
  background: #FF8F00 !important;
  border: 2px solid #FFD54F !important;
  color: #FFFFFF !important;
  font-weight: 900 !important;
  box-shadow: 0 0 14px rgba(255, 213, 79, 0.9) !important;
  z-index: 8;
}

.cell-path-surf {
  background: #0052CC !important;
  border: 2px solid #448AFF !important;
  color: #FFFFFF !important;
  font-weight: 900 !important;
  box-shadow: 0 0 14px rgba(68, 138, 255, 0.85) !important;
  z-index: 8;
}

.cell-path-goal {
  border: 2px solid #FFD700 !important;
  box-shadow: 0 0 16px #FFD700 !important;
}

.ds-badge-yellow {
  border-color: rgba(255, 214, 0, 0.6);
  color: #FFD600;
  background: rgba(255, 214, 0, 0.12);
}

.ds-badge-blue {
  border-color: rgba(41, 121, 255, 0.6);
  color: #2979FF;
  background: rgba(41, 121, 255, 0.12);
}'''

assert old_colors in css, "old_colors anchor not found"
css = css.replace(old_colors, new_colors, 1)

with open("frontend/v2.css", "w", encoding="utf-8") as f:
    f.write(css)

print("Updated path colors in frontend/v2.css successfully!")
