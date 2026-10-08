with open("frontend/v2.css", "r", encoding="utf-8") as f:
    css = f.read()

log_drawer_css = """
/* ==========================================================================
   全局底部常驻实时日志控制台抽屉 (Global Bottom Drawer)
   ========================================================================== */
.ds-global-log-drawer {
  height: 220px;
  background: #000;
  border-top: 2px solid var(--accent-cyan);
  display: flex;
  flex-direction: column;
  user-select: text;
  flex-shrink: 0;
}

.ds-log-drawer-header {
  height: 38px;
  background: var(--bg-1);
  border-bottom: 1px solid var(--border-subtle);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 16px;
  user-select: none;
}

.ds-global-log-drawer .ds-log {
  flex: 1;
  height: auto;
  border: none;
  border-radius: 0;
  padding: 10px 16px;
  overflow-y: auto;
  font-size: 13px;
  line-height: 1.7;
}
"""

if ".ds-global-log-drawer" not in css:
    css += "\n" + log_drawer_css
    with open("frontend/v2.css", "w", encoding="utf-8") as f:
        f.write(css)
    print("Added global log drawer styles to v2.css!")
else:
    print("Drawer styles already exist.")
