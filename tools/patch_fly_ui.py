with open("frontend/v2.html", "r", encoding="utf-8") as f:
    html = f.read()

target_html = """              <div style="font-size:13px; color:var(--text-2); margin-top:6px; line-height:1.7;">
                已启用同向直线批量合并输入算法。脱离现实时间休眠，基于底层游戏内存帧推进与坐标实时制动，模拟器加速模式下依然平滑刹车。
              </div>"""

new_fly_card = """              <div style="font-size:13px; color:var(--text-2); margin-top:6px; line-height:1.7;">
                已启用同向直线批量合并输入算法。脱离现实时间休眠，基于底层游戏内存帧推进与坐标实时制动，模拟器加速模式下依然平滑刹车。
              </div>
              <div class="ds-panel-divider" style="margin: 12px 0; border-top: 1px dashed rgba(255,255,255,0.15);"></div>
              <div style="background: rgba(0, 229, 255, 0.05); border: 1px solid rgba(0, 229, 255, 0.25); border-radius: 6px; padding: 12px;">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                  <div style="display:flex; align-items:center; gap:8px;">
                    <span class="ds-badge ds-badge-cyan" style="font-weight:700;">[02.4] 🦅 飞翔穿梭 API</span>
                    <strong style="color:var(--text-1); font-size:14px;">跨区直达航线 (Fast Travel / Fly)</strong>
                  </div>
                  <span class="ds-badge ds-badge-cyan" id="flyStatusBadge">🦅 叉字蝠 · 飞翔 #19 就绪</span>
                </div>
                <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap;">
                  <label style="font-size:13px; color:var(--text-1); font-weight:600;">飞翔目标城镇：</label>
                  <select id="flyDestinationSelect" style="padding:6px 12px; background:#0f172a; color:#f8fafc; border:1px solid #38bdf8; border-radius:4px; font-size:13px; min-width:260px;">
                    <option value="120">双龙市 (Zone 120 · 第7道馆夏卡)</option>
                    <option value="406" selected>涟漪镇 (Zone 406 · 东部海岸)</option>
                    <option value="389">笼目镇 (Zone 389 · 东北重镇)</option>
                    <option value="458">山路镇 (Zone 458 · 反转山)</option>
                    <option value="107">吹寄市 (Zone 107 · 第6道馆)</option>
                    <option value="96">帆巴市 (Zone 96 · 第5道馆)</option>
                    <option value="62">雷文市 (Zone 62 · 第4道馆)</option>
                    <option value="28">飞云市 (Zone 28 · 第3道馆)</option>
                    <option value="448">立涌市 (Zone 448 · 第2道馆)</option>
                    <option value="439">算木镇 (Zone 439 · 牧场)</option>
                    <option value="427">桧扇市 (Zone 427 · 起始城镇)</option>
                  </select>
                  <button class="ds-btn ds-btn-cyan" onclick="handleExecuteFly()">[🚀 立即启航飞翔 (POST /fast-travel/fly)]</button>
                  <button class="ds-btn ds-btn-sm" onclick="handleEvaluateFly()">[🔍 评估飞翔许可 (/evaluate)]</button>
                </div>
                <div id="flyFeedbackBox" style="font-size:12px; color:var(--text-2); margin-top:6px;">
                  💡 授权依据：持有吹寄道馆喷射徽章，队伍 6 号位叉字蝠已习得招式 19「飞翔」，降落点严格对齐官方 ROM 宝可梦中心门外门垫。
                </div>
              </div>"""

if "[02.4] 🦅 飞翔穿梭 API" not in html:
    assert target_html in html
    html = html.replace(target_html, new_fly_card)
    html = html.replace("v2.js?v=20261008_05", "v2.js?v=20261008_06")
    with open("frontend/v2.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("Updated frontend/v2.html with Fly console card!")
else:
    print("Fly console card already in frontend/v2.html")

# Update frontend/v2.js with Fly handlers
with open("frontend/v2.js", "r", encoding="utf-8") as f:
    js = f.read()

fly_js = """
async function handleExecuteFly() {
  const destVal = document.getElementById('flyDestinationSelect')?.value || '120';
  const destName = document.getElementById('flyDestinationSelect')?.selectedOptions[0]?.text || destVal;
  appendLog(`[飞翔指令] 准备启航飞翔前往 ${destName} (POST /api/v1/navigation/fast-travel/fly)...`);
  appendNavTaskLog(`[飞翔启航] 正在通过 6号位叉字蝠 (Fly #19) 跨区飞翔前往 ${destName}...`, 'var(--accent-cyan)');
  try {
    const res = await fetch('/api/v1/navigation/fast-travel/fly', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ destination: parseInt(destVal, 10), method: 'auto' })
    });
    const d = await res.json();
    if (res.ok) {
      const depName = d.departure?.zone_id ? `Zone ${d.departure.zone_id}` : '当前区域';
      const arrName = d.destination?.name || `Zone ${d.destination?.zone_id}`;
      const grid = d.destination?.landing_grid ? `(${d.destination.landing_grid.x}, ${d.destination.landing_grid.z})` : '';
      appendLog(`[飞翔成功] 航程完成！从 ${depName} ➔ 成功抵达【${arrName}】门垫 ${grid}`);
      appendLog(`[飞翔凭证] 执飞机体: 席位${d.fly_pokemon?.slot}【${d.fly_pokemon?.species_name} Lv.${d.fly_pokemon?.level}】· 喷射徽章许可已核验`);
      appendNavTaskLog(`[飞翔着陆] 成功降落【${arrName}】宝可梦中心门外门垫 ${grid}！`, 'var(--accent-green)');
      pollPlayerRuntime();
      pollRadar();
    } else {
      appendLog(`[飞翔拒绝] ${d.detail?.message || JSON.stringify(d)}`);
      appendNavTaskLog(`[飞翔拒绝] ${d.detail?.message || '未满足起飞条件'}`, 'var(--accent-red)');
    }
  } catch (e) {
    appendLog(`[飞翔异常] ${e}`);
    appendNavTaskLog(`[飞翔异常] ${e}`, 'var(--accent-red)');
  }
}

async function handleEvaluateFly() {
  appendLog('[飞翔评估] 正在校验当前环境与队伍飞翔资质 (GET /api/v1/navigation/fast-travel/evaluate)...');
  try {
    const res = await fetch('/api/v1/navigation/fast-travel/evaluate');
    const d = await res.json();
    appendLog(`[评估结果] 合法起飞: ${d.legal} | 持有飞翔技能: ${d.has_move_fly} | 区域允许: ${d.current_zone_allows_fly}`);
    appendLog(`[评估依据] ${d.reason} (合众可用降落城镇数: ${d.available_destinations_count})`);
    const badge = document.getElementById('flyStatusBadge');
    if (badge) {
      if (d.legal) {
        badge.innerText = '🦅 飞翔就绪 · 58城镇全域通达';
        badge.className = 'ds-badge ds-badge-cyan';
      } else {
        badge.innerText = '🚫 当前禁止飞翔';
        badge.className = 'ds-badge ds-badge-rose';
      }
    }
  } catch (e) {
    appendLog(`[评估异常] ${e}`);
  }
}
"""

if "function handleExecuteFly" not in js:
    js += fly_js
    with open("frontend/v2.js", "w", encoding="utf-8") as f:
        f.write(js)
    print("Added Fly handlers to frontend/v2.js!")
else:
    print("Fly handlers already in frontend/v2.js")