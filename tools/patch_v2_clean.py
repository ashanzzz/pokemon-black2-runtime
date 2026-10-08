with open("frontend/v2.js", "r", encoding="utf-8") as f:
    lines = f.read().splitlines()

clean_actions = [
    "async function handleMoveAction(slot) {",
    "  appendLog(\"[招式指令] 正在执行招式槽位 \" + slot + \" (POST /api/v1/battle/move)...\");",
    "  try {",
    "    const res = await fetch(\"/api/v1/battle/move\", {",
    "      method: \"POST\",",
    "      headers: { \"Content-Type\": \"application/json\" },",
    "      body: JSON.stringify({ move_slot: slot })",
    "    });",
    "    const d = await res.json();",
    "    appendLog(\"[招式结果] 状态: \" + d.status + \", 是否执行: \" + d.executed + \", 校验: \" + JSON.stringify(d.verification || d.reason || {}));",
    "    pollBattleState();",
    "  } catch (e) {",
    "    appendLog(\"[招式异常] \" + e);",
    "  }",
    "}",
    "",
    "async function handleCatchAction(itemId) {",
    "  itemId = itemId || 4;",
    "  appendLog(\"[投球指令] 投掷精灵球 (POST /api/v1/battle/catch, item_id=\" + itemId + \")...\");",
    "  try {",
    "    const res = await fetch(\"/api/v1/battle/catch\", {",
    "      method: \"POST\",",
    "      headers: { \"Content-Type\": \"application/json\" },",
    "      body: JSON.stringify({ item_id: itemId })",
    "    });",
    "    const d = await res.json();",
    "    appendLog(\"[投球结果] 状态: \" + d.status + \", 是否执行: \" + d.executed + \", 校验: \" + JSON.stringify(d.verification || d.reason || {}));",
    "    pollBattleState();",
    "    pollInventory();",
    "  } catch (e) {",
    "    appendLog(\"[投球异常] \" + e);",
    "  }",
    "}",
    "",
    "async function handleRunAction() {",
    "  appendLog(\"[脱战指令] 发送原子逃跑指令 (POST /api/v1/battle/flee)...\");",
    "  try {",
    "    const res = await fetch(\"/api/v1/battle/flee\", {",
    "      method: \"POST\",",
    "      headers: { \"Content-Type\": \"application/json\" }",
    "    });",
    "    const d = await res.json();",
    "    appendLog(\"[脱战结果] 状态: \" + d.status + \", 是否成功: \" + d.ok + \", 消息: \" + (d.message || \"\"));",
    "    pollBattleState();",
    "    pollPlayerRuntime();",
    "  } catch (e) {",
    "    appendLog(\"[脱战异常] \" + e);",
    "  }",
    "}",
    "",
    "async function handleBattleItemAction(itemId, partySlot) {",
    "  itemId = itemId || 17;",
    "  partySlot = partySlot || 1;",
    "  appendLog(\"[战斗用药] 使用道具 #\" + itemId + \" 至队伍槽位 \" + partySlot + \" (POST /api/v1/battle/item)...\");",
    "  try {",
    "    const res = await fetch(\"/api/v1/battle/item\", {",
    "      method: \"POST\",",
    "      headers: { \"Content-Type\": \"application/json\" },",
    "      body: JSON.stringify({ item_id: itemId, target_party_slot: partySlot })",
    "    });",
    "    const d = await res.json();",
    "    appendLog(\"[道具结果] 状态: \" + d.status + \", 是否执行: \" + d.executed + \", 校验: \" + JSON.stringify(d.verification || d.reason || {}));",
    "    pollBattleState();",
    "    pollInventory();",
    "    pollParty();",
    "  } catch (e) {",
    "    appendLog(\"[道具异常] \" + e);",
    "  }",
    "}",
    "",
    "async function handleBattleSurveyAction(count) {",
    "  count = count || 3;",
    "  appendLog(\"[对战逆向测绘] 启动多轮自动化巡逻与对战逆向测绘 (POST /api/v1/battle/survey, 轮数=\" + count + \")...\");",
    "  try {",
    "    const res = await fetch(\"/api/v1/battle/survey\", {",
    "      method: \"POST\",",
    "      headers: { \"Content-Type\": \"application/json\" },",
    "      body: JSON.stringify({ battles_to_run: count })",
    "    });",
    "    const d = await res.json();",
    "    appendLog(\"[测绘报告] 完成轮数: \" + (d.rounds_completed || 0) + \", 结果: \" + JSON.stringify(d.summary || d));",
    "    pollBattleState();",
    "    pollPlayerRuntime();",
    "  } catch (e) {",
    "    appendLog(\"[测绘异常] \" + e);",
    "  }",
    "}"
]

start_i = None
end_i = None
for i, l in enumerate(lines):
    if "async function handleMoveAction(" in l:
        start_i = i
    if start_i is not None and "async function handleBicycleMount(" in l:
        end_i = i
        break

if start_i is not None and end_i is not None:
    lines = lines[:start_i] + clean_actions + [""] + lines[end_i:]

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("Clean actions written successfully!")