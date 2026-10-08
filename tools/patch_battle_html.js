const fs = require('fs');

// 1. Upgrade frontend/v2.html
let html = fs.readFileSync('frontend/v2.html', 'utf8').replace(/\r\n/g, '\n');
const startTag = '<div id="combatInBattle"';
const endTag = 'id="pane-roster"';

const startIdx = html.indexOf(startTag);
const endIdx = html.indexOf(endTag);

if (startIdx !== -1 && endIdx !== -1) {
  // Find the closing </div> of combatInBattle before pane-roster
  const sub = html.substring(startIdx, endIdx);
  const lastSection = sub.lastIndexOf('</section>');
  // Three closing divs before </section>
  const combatEndInSub = sub.lastIndexOf('</div>', sub.lastIndexOf('</div>', sub.lastIndexOf('</div>', lastSection) - 1) - 1);
  const actualEndIdx = startIdx + combatEndInSub + 6;

  const newCombatInBattle = `            <!-- 进入对战后展示的真实对抗区 (由 JS 动态切换) -->
            <div id="combatInBattle" style="display:none; flex-direction:column; gap:14px;">
              <!-- 顶部对战阶段与状态机 HUD 状态条 -->
              <div style="display:flex; justify-content:space-between; align-items:center; background:var(--bg-3); padding:8px 14px; border-radius:var(--radius-md); border:1px solid var(--border-subtle); flex-wrap:wrap; gap:8px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="ds-badge ds-badge-red" id="battleKindBadge">野生遭遇战 (Wild Battle)</span>
                  <span class="ds-badge ds-badge-cyan" id="battlePhaseBadge">Phase: command_menu (根命令)</span>
                  <span class="ds-badge" id="battleCursorBadge">光标: 未知</span>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="ds-badge ds-badge-green" id="battleCanActBadge">可行动: 就绪</span>
                  <button class="ds-btn ds-btn-sm" onclick="pollBattleState()">[刷新对战真值]</button>
                </div>
              </div>

              <!-- 训练家信息条 (仅在训练家战显示，野生战自动隐藏) -->
              <div id="trainerHeaderBar" class="ds-panel" style="background:var(--bg-3); padding:10px 14px; display:none; justify-content:space-between; align-items:center;">
                <div>
                  <span class="ds-badge ds-badge-red" id="trainerTitleBadge">训练家对战</span>
                  <strong style="font-size:15px; margin-left:8px;" id="trainerNameText">训练家</strong>
                  <span style="font-size:12px; color:var(--text-3); margin-left:8px;" id="trainerIdText">编号: #--</span>
                </div>
                <div style="display:flex; align-items:center; gap:6px;">
                  <span style="font-size:12px; color:var(--text-2);">对方携带宝可梦:</span>
                  <span id="trainerBallsRow" style="display:flex; gap:4px;"></span>
                </div>
              </div>

              <!-- 双方对抗看板 (实时物种、等级、血条、特性与数值) -->
              <div style="display:flex; justify-content:space-between; align-items:stretch; background:var(--bg-1); padding:16px; border-radius:var(--radius-md); border:1px solid var(--border-subtle); gap:16px; flex-wrap:wrap;">
                <!-- 敌方战备卡 -->
                <div style="flex:1; min-width:280px; display:flex; flex-direction:column; justify-content:space-between;">
                  <div>
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                      <span style="font-size:12px; color:var(--accent-red); font-weight:700;" id="oppKindLabel">敌方出战宝可梦</span>
                      <span class="ds-badge" id="oppTypeBadge" style="font-size:11px;">[系别]</span>
                    </div>
                    <div style="font-size:18px; font-weight:800; margin-top:4px;" id="oppMonName">
                      等待读取... <span style="font-size:13px; color:var(--text-3);">Lv.--</span>
                    </div>
                  </div>
                  <div style="margin-top:8px;">
                    <div class="ds-progress" style="width:100%;"><div class="ds-progress-fill" id="oppMonHpBar" style="width:100%;"></div></div>
                    <div style="display:flex; justify-content:space-between; font-size:12px; color:var(--text-2); margin-top:4px;">
                      <span id="oppMonHpText">生命值: -- / -- (100%)</span>
                      <span id="oppMonStatusText">正常</span>
                    </div>
                    <div style="font-size:11px; color:var(--text-3); margin-top:2px;" id="oppMonMetaText">
                      特性: -- · 性别: -- · 阶级: 0阶
                    </div>
                  </div>
                </div>

                <!-- 中间 VS 分隔器 -->
                <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; padding:0 12px; border-left:1px dashed var(--border-subtle); border-right:1px dashed var(--border-subtle);">
                  <div style="font-size:18px; font-weight:900; color:var(--accent-amber);">VS</div>
                  <div style="font-size:11px; color:var(--text-3); margin-top:4px;">回合对抗</div>
                </div>

                <!-- 我方出战卡 -->
                <div style="flex:1; min-width:280px; text-align:right; display:flex; flex-direction:column; justify-content:space-between;">
                  <div>
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-direction:row-reverse;">
                      <span style="font-size:12px; color:var(--accent-green); font-weight:700;" id="playerKindLabel">我方当前出战宝可梦</span>
                      <span class="ds-badge ds-badge-cyan" id="playerTypeBadge" style="font-size:11px;">[系别]</span>
                    </div>
                    <div style="font-size:18px; font-weight:800; margin-top:4px;" id="playerMonName">
                      等待读取... <span style="font-size:13px; color:var(--text-3);">Lv.--</span>
                    </div>
                  </div>
                  <div style="margin-top:8px;">
                    <div class="ds-progress" style="width:100%; margin-left:auto;"><div class="ds-progress-fill" id="playerMonHpBar" style="width:100%;"></div></div>
                    <div style="display:flex; justify-content:space-between; font-size:12px; color:var(--text-2); margin-top:4px; flex-direction:row-reverse;">
                      <span id="playerMonHpText">生命值: -- / -- (100%)</span>
                      <span id="playerMonStatusText">正常</span>
                    </div>
                    <div style="font-size:11px; color:var(--text-3); margin-top:2px;" id="playerMonMetaText">
                      特性: -- · 性别: -- · 状态: 正常
                    </div>
                    <div style="font-size:11px; color:var(--text-3); margin-top:2px;" id="playerMonStatsText">
                      攻 -- / 防 -- / 特攻 -- / 特防 -- / 速度 --
                    </div>
                  </div>
                </div>
              </div>

              <!-- AI 决策推荐战术栏 -->
              <div id="battleDecisionBanner" class="ds-panel" style="background:rgba(0, 229, 255, 0.05); border-color:var(--accent-cyan); padding:10px 14px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                <div style="display:flex; align-items:center; gap:8px;">
                  <span class="ds-badge ds-badge-cyan">[AI 决策智脑]</span>
                  <span id="battleDecisionText" style="font-size:13px; color:var(--text-1); font-weight:600;">正在实时评估最优招式与相克关系...</span>
                </div>
                <button class="ds-btn ds-btn-sm ds-btn-primary" id="btnExecuteDecision" onclick="handleExecuteDecisionAction()">[一键执行 AI 推荐决策]</button>
              </div>

              <!-- 野生捕获评估卡片 (野生战展示，训练家战隐藏) -->
              <div id="wildCaptureCard" class="ds-panel" style="background:var(--bg-1); padding:12px; display:none; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                <div>
                  <span class="ds-badge ds-badge-green">捕获率实时评估</span>
                  <span style="font-size:13px; margin-left:8px;" id="wildCaptureText">预估捕获率: <strong style="color:var(--accent-green); font-size:15px;" id="wildCaptureRateVal">--%</strong></span>
                </div>
                <div style="display:flex; gap:8px; align-items:center;">
                  <select id="selectBallType" class="ds-input" style="width:120px; height:30px; font-size:12px; padding:0 6px;">
                    <option value="4" selected>精灵球 (#4)</option>
                    <option value="3">超级球 (#3)</option>
                    <option value="2">高级球 (#2)</option>
                  </select>
                  <button class="ds-btn ds-btn-success ds-btn-sm" id="btnThrowBall" onclick="handleCatchAction(parseInt(document.getElementById(selectBallType).value, 10))">[投掷精灵球 POST /catch]</button>
                </div>
              </div>

              <!-- 4 招式评分四宫格 (实时 PP 与克制倍率) -->
              <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                  <div style="font-size:13px; font-weight:700; color:var(--text-2);">四招式实时状态与 AI 相克矩阵 (点击直接出招):</div>
                  <span style="font-size:11px; color:var(--text-3);">(基于 btl_pokeparam.c 实时 PP 验证)</span>
                </div>
                <div class="ds-grid-2" id="battleMovesGrid">
                  <!-- 由 JS 实时渲染真实 4 招式卡片 -->
                </div>
              </div>

              <!-- 战斗中全队换人面板 (In-Battle Party Switch Deck) -->
              <div>
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                  <div style="font-size:13px; font-weight:700; color:var(--text-2);">战斗中全队换人席位 (点击直切出战 · POST /battle/switch):</div>
                  <span style="font-size:11px; color:var(--text-3);">(自动归位并完成 Shift 验证)</span>
                </div>
                <div class="ds-grid-3" id="battlePartyRoster" style="gap:8px;">
                  <!-- 由 JS 实时填充 6 席位卡片 -->
                </div>
              </div>

              <!-- 对战底层原子 API 控制台 -->
              <div style="border-top:1px solid var(--border-subtle); padding-top:10px;">
                <div style="font-size:12px; font-weight:700; color:var(--text-3); margin-bottom:6px;">
                  【全量对战原子 API 控制台】(忽略顺序 · 自由下发 · 闭环回读):
                </div>
                <div style="display:flex; gap:8px; flex-wrap:wrap;">
                  <button class="ds-btn ds-btn-primary ds-btn-sm" onclick="handleMoveAction(1)">[招式1 POST /battle/move(1)]</button>
                  <button class="ds-btn ds-btn-primary ds-btn-sm" onclick="handleMoveAction(2)">[招式2 POST /battle/move(2)]</button>
                  <button class="ds-btn ds-btn-primary ds-btn-sm" onclick="handleMoveAction(3)">[招式3 POST /battle/move(3)]</button>
                  <button class="ds-btn ds-btn-primary ds-btn-sm" onclick="handleMoveAction(4)">[招式4 POST /battle/move(4)]</button>
                  <button class="ds-btn ds-btn-yellow ds-btn-sm" onclick="handleSwitchPokemon(1)">[换人: 1号位]</button>
                  <button class="ds-btn ds-btn-yellow ds-btn-sm" onclick="handleSwitchPokemon(2)">[换人: 2号位]</button>
                  <button class="ds-btn ds-btn-yellow ds-btn-sm" onclick="handleSwitchPokemon(3)">[换人: 3号位]</button>
                  <button class="ds-btn ds-btn-yellow ds-btn-sm" onclick="handleSwitchPokemon(4)">[换人: 4号位]</button>
                  <button class="ds-btn ds-btn-yellow ds-btn-sm" onclick="handleSwitchPokemon(5)">[换人: 5号位]</button>
                  <button class="ds-btn ds-btn-yellow ds-btn-sm" onclick="handleSwitchPokemon(6)">[换人: 6号位]</button>
                  <button class="ds-btn ds-btn-cyan ds-btn-sm" onclick="handleBattleItemAction(17, 1)">[用药: 伤药(17) POST /battle/item]</button>
                  <button class="ds-btn ds-btn-green ds-btn-sm" onclick="handleCatchAction(4)" id="btnActionCatch">[投球: 精灵球(4) POST /battle/catch]</button>
                  <button class="ds-btn ds-btn-danger ds-btn-sm" onclick="handleRunAction()" id="btnActionRun">[脱离战斗 (原子逃跑 POST /battle/flee)]</button>
                  <button class="ds-btn ds-btn-sm" style="border-color:#BA68C8; color:#CE93D8;" onclick="handleBattleSurveyAction(3)">[多轮对战自动化逆向测绘 POST /battle/survey]</button>
                </div>
              </div>

              <!-- 可折叠底层 ARM9 RAM 结构体回读看板 -->
              <details class="ds-panel" style="background:var(--bg-1); padding:10px 14px; margin-top:6px;">
                <summary style="cursor:pointer; font-size:12px; font-weight:700; color:var(--text-2);">
                  [03.3] 对战底层 ARM9 RAM (btl_pokeparam.c) 结构体实时回读看板 (点击展开/折叠)
                </summary>
                <div style="display:flex; gap:16px; margin-top:8px; font-family:var(--font-mono); font-size:11px; flex-wrap:wrap;">
                  <div style="flex:1; min-width:240px; background:#000; padding:10px; border-radius:4px;" id="ramOppDump">
                    敌方对象: 待读取...
                  </div>
                  <div style="flex:1; min-width:240px; background:#000; padding:10px; border-radius:4px;" id="ramPlayerDump">
                    我方对象: 待读取...
                  </div>
                </div>
              </details>
            </div>`;

  html = html.substring(0, startIdx) + newCombatInBattle + html.substring(actualEndIdx);
  fs.writeFileSync('frontend/v2.html', html, 'utf8');
  console.log('Successfully upgraded frontend/v2.html combat panel!');
} else {
  console.log('Could not find start/end indices in v2.html');
}