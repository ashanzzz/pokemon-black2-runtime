with open("frontend/v2.html", "r", encoding="utf-8") as f:
    html_text = f.read().replace("\r\n", "\n")

# Topbar brand update
topbar_brand = """    <div class="ds-brand">
      <span>黑2自主控制台</span>
      <span class="ds-badge ds-badge-cyan">测试版本 V2</span>
    </div>"""

new_topbar_brand = """    <div class="ds-brand">
      <span>黑2自主控制台</span>
      <span class="ds-badge ds-badge-cyan">测试版本 V2</span>
    </div>

    <div class="ds-badge ds-badge-yellow" title="项目唯一目标：全面暴露底层真值 API 与确定性执行管道，赋能外部 AI 自由感知与操纵游戏，绝不对打游戏流程做主观业务优化">
      【核心定位】全量底层 API 暴露与交互大屏 · 绝不干预或优化 AI 打游戏流程
    </div>"""

if topbar_brand in html_text and "【核心定位】" not in html_text:
    html_text = html_text.replace(topbar_brand, new_topbar_brand)

# Combat panel action buttons
old_combat_actions = """              <!-- 动作测试控制栏 -->
              <div style="display:flex; gap:10px;">
                <button class="ds-btn ds-btn-primary" onclick="handleMoveAction(1)">[一键执行推荐招式]</button>
                <button class="ds-btn" onclick="handleCatchAction()" id="btnActionCatch">[投掷精灵球]</button>
                <button class="ds-btn ds-btn-danger" onclick="handleRunAction()" id="btnActionRun">[脱离战斗 (逃跑)]</button>
                <button class="ds-btn" onclick="handleSwitchPokemon(3)">[切换出战宝可梦]</button>
              </div>"""

new_combat_actions = """              <!-- 动作测试控制栏 (全量底层对战原子 API 交互区) -->
              <div style="display:flex; flex-direction:column; gap:8px;">
                <div style="font-size:12px; font-weight:700; color:var(--text-3);">
                  【对战原子 API 暴露与闭环回读控制台】(支持忽略顺序 · 自由下发 · 自动归位 · 内存真实值校验):
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                  <button class="ds-btn ds-btn-primary" onclick="handleMoveAction(1)">[招式1: POST /battle/move(1)]</button>
                  <button class="ds-btn ds-btn-primary" onclick="handleMoveAction(2)">[招式2: POST /battle/move(2)]</button>
                  <button class="ds-btn ds-btn-primary" onclick="handleMoveAction(3)">[招式3: POST /battle/move(3)]</button>
                  <button class="ds-btn ds-btn-primary" onclick="handleMoveAction(4)">[招式4: POST /battle/move(4)]</button>
                  <button class="ds-btn ds-btn-yellow" onclick="handleSwitchPokemon(1)">[换人: 槽位1 /battle/switch]</button>
                  <button class="ds-btn ds-btn-yellow" onclick="handleSwitchPokemon(2)">[换人: 槽位2 /battle/switch]</button>
                  <button class="ds-btn ds-btn-yellow" onclick="handleSwitchPokemon(3)">[换人: 槽位3 /battle/switch]</button>
                  <button class="ds-btn ds-btn-yellow" onclick="handleSwitchPokemon(4)">[换人: 槽位4 /battle/switch]</button>
                  <button class="ds-btn ds-btn-cyan" onclick="handleBattleItemAction(17, 1)">[用药: 伤药(17) POST /battle/item]</button>
                  <button class="ds-btn ds-btn-green" onclick="handleCatchAction(4)" id="btnActionCatch">[投球: 精灵球(4) POST /battle/catch]</button>
                  <button class="ds-btn ds-btn-danger" onclick="handleRunAction()" id="btnActionRun">[脱离战斗 (原子逃跑 POST /battle/flee)]</button>
                  <button class="ds-btn" style="border-color:#BA68C8; color:#CE93D8;" onclick="handleBattleSurveyAction(3)">[🔬 多轮对战自动化逆向测绘 POST /battle/survey]</button>
                </div>
              </div>"""

if old_combat_actions in html_text:
    html_text = html_text.replace(old_combat_actions, new_combat_actions)
    print("Replaced combat actions in v2.html successfully!")
else:
    print("old_combat_actions not found in v2.html")

with open("frontend/v2.html", "w", encoding="utf-8") as f:
    f.write(html_text)
print("Updated frontend/v2.html successfully!")