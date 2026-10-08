with open("frontend/v2.html", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

target = """          <div class="ds-panel-footer" style="display:flex; justify-content:space-between; align-items:center;">
            <span>全员健康状态监控通过 GameData (+0x194) 物理主内存直读</span>
            <button class="ds-btn ds-btn-success" onclick="handleNurseRecovery()">[宝可梦中心护士全员满血恢复 (2100号脚本)]</button>
          </div>"""

replacement = """          <div class="ds-panel-footer" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
            <div style="display:flex; align-items:center; gap:8px;">
              <span style="font-size:12px; color:var(--text-2); font-weight:700;">队伍调序/更换首发 (POST /game/party/swap):</span>
              <select id="partySwapSlotA" class="ds-input" style="width:120px; height:28px; padding:0 6px; font-size:12px;">
                <option value="1">1号席位 (首发)</option>
                <option value="2">2号席位</option>
                <option value="3">3号席位</option>
                <option value="4">4号席位</option>
                <option value="5">5号席位</option>
                <option value="6">6号席位</option>
              </select>
              <span style="color:var(--text-3); font-weight:700;">⇄</span>
              <select id="partySwapSlotB" class="ds-input" style="width:120px; height:28px; padding:0 6px; font-size:12px;">
                <option value="1">1号席位 (首发)</option>
                <option value="2">2号席位</option>
                <option value="3" selected>3号席位</option>
                <option value="4">4号席位</option>
                <option value="5">5号席位</option>
                <option value="6">6号席位</option>
              </select>
              <button class="ds-btn ds-btn-sm ds-btn-yellow" onclick="handlePartySwapOrder()">[立即交换队伍顺序]</button>
            </div>
            <button class="ds-btn ds-btn-success" onclick="handleNurseRecovery()">[宝可梦中心护士全员满血恢复 (2100号脚本)]</button>
          </div>"""

if target in text:
    text = text.replace(target, replacement)
    with open("frontend/v2.html", "w", encoding="utf-8") as f:
        f.write(text)
    print("Updated frontend/v2.html with party swap UI controls!")
else:
    print("target not found in v2.html")

with open("frontend/v2.js", "r", encoding="utf-8") as f:
    js_text = f.read().replace("\r\n", "\n")

fn_code = """async function handlePartySwapOrder() {
  const slotA = parseInt(document.getElementById( partySwapSlotA)?.value || 1, 10);
  const slotB = parseInt(document.getElementById(partySwapSlotB)?.value || 2, 10);
  appendLog(\"[队伍调序] 正在交换队伍席位 \" + slotA + \" 与 \" + slotB + \" (POST /api/v1/game/party/swap)...\");
  try {
    const res = await fetch(/api/v1/game/party/swap, {
      method: POST,
      headers: { Content-Type: application/json },
      body: JSON.stringify({ slot_a: slotA, slot_b: slotB })
    });
    const d = await res.json();
    if (res.ok) {
      appendLog(\"[调序成功] 席位 \" + slotA + \" 与 \" + slotB + \" 顺序已调换，首发已更新！\");
      pollParty();
    } else {
      appendLog(\"[调序失败] \" + (d.detail || JSON.stringify(d)));
    }
  } catch (e) {
    appendLog(\"[调序异常] \" + e);
  }
}
"""

if "async function handlePartySwapOrder" not in js_text:
    js_text = js_text.replace("async function handleStepForward() {", fn_code + "\nasync function handleStepForward() {")
    with open("frontend/v2.js", "w", encoding="utf-8") as f:
        f.write(js_text)
    print("Added handlePartySwapOrder to frontend/v2.js successfully!")