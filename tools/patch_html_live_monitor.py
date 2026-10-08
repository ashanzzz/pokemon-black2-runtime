with open("frontend/v2.html", "r", encoding="utf-8") as f:
    text = f.read()

live_card_html = """                <div id="navLiveMonitorCard" style="border-top:1px solid var(--border-subtle); padding-top:8px; margin-top:8px;">
                  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                    <div style="font-size:12px; color:var(--text-1); font-weight:700;">
                      <span>[02.4] 实时任务执行动态监视流 (Live Action & RAM Landing Feed)</span>
                    </div>
                    <span id="navLiveTaskStatusBadge" class="ds-badge">等待下发</span>
                  </div>
                  <div id="navLiveTaskLog" style="background:#000; border:1px solid var(--border-subtle); border-radius:4px; padding:10px 14px; font-family:var(--font-mono); font-size:12px; color:var(--text-2); line-height:1.7; max-height:160px; overflow-y:auto;">
                    [等待下发任务... 点击“下发移动任务”或“立即直达选定格”后，此处将逐秒打印按键时序、物理帧步进、跨层阶梯完成度与落地坐标真值]
                  </div>
                </div>
"""

old_anchor = """                  <div id="navActionList" style="font-family:var(--font-mono); font-size:12px; color:var(--text-3); line-height:1.6;">
                    暂无规划路径。请点击上方网格选点生成动作流。
                  </div>
                </div>"""

new_anchor = """                  <div id="navActionList" style="font-family:var(--font-mono); font-size:12px; color:var(--text-3); line-height:1.6;">
                    暂无规划路径。请点击上方网格选点生成动作流。
                  </div>
                </div>
""" + live_card_html

if "navLiveMonitorCard" not in text:
    text = text.replace(old_anchor, new_anchor)
    with open("frontend/v2.html", "w", encoding="utf-8") as f:
        f.write(text)
    print("v2.html updated with navLiveMonitorCard!")
else:
    print("navLiveMonitorCard already present in v2.html")
