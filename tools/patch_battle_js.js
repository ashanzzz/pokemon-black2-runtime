const fs = require("fs");

let js = fs.readFileSync("frontend/v2.js", "utf8");

const pos_rbs = js.indexOf("function renderBattleState(");
const pos_tsb = js.indexOf("function toggleSimulatedBattle(");

if (pos_rbs !== -1 && pos_tsb !== -1) {
  const newJs = `let cachedCurrentDecision = null;

async function pollCaptureEval() {
  try {
    const res = await fetch("/api/v1/battle/capture-eval");
    if (!res.ok) return;
    const d = await res.json();
    const rateElem = document.getElementById("wildCaptureRateVal");
    const textElem = document.getElementById("wildCaptureText");
    if (rateElem && d.estimated_catch_rate_percent !== undefined) {
      rateElem.innerText = d.estimated_catch_rate_percent.toFixed(1) + "%";
    }
    if (textElem && d.reason) {
      textElem.innerHTML = "预估捕获率: <strong style=\\"color:var(--accent-green); font-size:15px;\\" id=\\"wildCaptureRateVal\\">" + (d.estimated_catch_rate_percent || 0).toFixed(1) + "%</strong> (" + d.reason + ")";
    }
  } catch (e) {}
}

async function handleExecuteDecisionAction() {
  if (!cachedCurrentDecision) {
    appendLog("[AI决策执行] 当前暂无推荐决策");
    return;
  }
  const rec = cachedCurrentDecision;
  appendLog("[AI决策执行] 正在自动执行推荐操作: " + rec.type + " ...");
  if (rec.type === "use_move") {
    await handleMoveAction(rec.move_slot || 1);
  } else if (rec.type === "switch") {
    await handleSwitchPokemon(rec.party_slot || 2);
  } else if (rec.type === "run") {
    await handleRunAction();
  } else {
    appendLog("[AI决策执行] 未知动作类型: " + rec.type);
  }
}

function renderBattleState(data) {
  const inBattle = data.active === true;
  const notInBattleBox = document.getElementById("combatNotInBattle");
  const inBattleBox = document.getElementById("combatInBattle");
  const badge = document.getElementById("combatStatusBadge");

  if (!inBattle) {
    if (notInBattleBox) notInBattleBox.style.display = "flex";
    if (inBattleBox) inBattleBox.style.display = "none";
    if (badge) {
      badge.className = "ds-badge ds-badge-green";
      badge.innerText = "大地图常态 (无对战)";
    }
    return;
  }

  // 处于实战状态
  if (notInBattleBox) notInBattleBox.style.display = "none";
  if (inBattleBox) inBattleBox.style.display = "flex";
  if (badge) {
    badge.className = "ds-badge ds-badge-red";
    badge.innerText = "实战对抗中 (BUSY_BATTLE)";
  }

  const kind = data.battle_kind || "wild";
  const trainerBar = document.getElementById("trainerHeaderBar");
  const wildCard = document.getElementById("wildCaptureCard");
  const btnRun = document.getElementById("btnActionRun");
  const oppLabel = document.getElementById("oppKindLabel");

  // 1. 顶部 HUD 状态更新
  const kindBadge = document.getElementById("battleKindBadge");
  if (kindBadge) {
    kindBadge.className = kind === "trainer" ? "ds-badge ds-badge-amber" : "ds-badge ds-badge-red";
    kindBadge.innerText = kind === "trainer" ? "训练家对战 (Trainer Battle)" : "野生遭遇战 (Wild Battle)";
  }

  const phaseBadge = document.getElementById("battlePhaseBadge");
  if (phaseBadge) {
    const phaseStr = data.phase || "command_menu";
    phaseBadge.innerText = "Phase: " + phaseStr + (phaseStr === "command_menu" ? " (根命令)" : phaseStr === "move_menu" ? " (招式菜单)" : "");
    phaseBadge.className = phaseStr === "command_menu" ? "ds-badge ds-badge-cyan" : "ds-badge ds-badge-amber";
  }

  const cursorBadge = document.getElementById("battleCursorBadge");
  if (cursorBadge) {
    const cur = data.cursor || {};
    cursorBadge.innerText = "光标: " + (cur.slot ? ("槽位 " + cur.slot + " · " + (cur.grid || "")) : (cur.raw_u32 !== undefined ? ("0x" + cur.raw_u32.toString(16)) : "就绪"));
  }

  const canActBadge = document.getElementById("battleCanActBadge");
  if (canActBadge) {
    canActBadge.className = data.can_act ? "ds-badge ds-badge-green" : "ds-badge ds-badge-yellow";
    canActBadge.innerText = data.can_act ? "可行动: 就绪" : "等待推进 / 结算中";
  }

  if (kind === "trainer") {
    if (trainerBar) trainerBar.style.display = "flex";
    if (wildCard) wildCard.style.display = "none";
    if (oppLabel) oppLabel.innerText = "训练家出战宝可梦";
    if (btnRun) {
      btnRun.disabled = true;
      btnRun.innerText = "[面对训练家无法逃跑]";
      btnRun.className = "ds-btn ds-btn-ghost ds-btn-sm";
    }
  } else {
    if (trainerBar) trainerBar.style.display = "none";
    if (wildCard) wildCard.style.display = "flex";
    if (oppLabel) oppLabel.innerText = "野生遭遇宝可梦";
    if (btnRun) {
      btnRun.disabled = false;
      btnRun.innerText = "[脱离战斗 (原子逃跑 POST /battle/flee)]";
      btnRun.className = "ds-btn ds-btn-danger ds-btn-sm";
    }
    pollCaptureEval();
  }

  // 2. 渲染敌方宝可梦数据
  const opp = data.opponent?.active || data.opponent || {};
  const oppNameElem = document.getElementById("oppMonName");
  if (oppNameElem) {
    const oppName = opp.species?.names?.["zh-Hans"] || opp.species?.name || opp.species_name || "野生宝可梦";
    const oppEn = opp.species?.name || opp.name_en || "";
    const oppId = opp.species_id || opp.species?.id || "";
    oppNameElem.innerHTML = oppName + (oppEn ? " (" + oppEn + ")" : "") + (oppId ? " <span style=\\"font-size:12px;color:var(--text-3);\\">#" + oppId + "</span>" : "") + " <span style=\\"font-size:13px;color:var(--text-3);margin-left:6px;\\">Lv." + (opp.level || "?") + "</span>";
  }

  const oppHp = opp.current_hp ?? 0;
  const oppMaxHp = opp.max_hp || 1;
  const oppHpPct = Math.max(0, Math.min(100, Math.round((oppHp / oppMaxHp) * 100)));
  const oppHpBar = document.getElementById("oppMonHpBar");
  if (oppHpBar) {
    oppHpBar.style.width = oppHpPct + "%";
    oppHpBar.className = "ds-progress-fill " + (oppHpPct <= 20 ? "fill-red" : oppHpPct <= 50 ? "fill-amber" : "");
  }
  const oppHpText = document.getElementById("oppMonHpText");
  if (oppHpText) oppHpText.innerText = "生命值: " + oppHp + " / " + oppMaxHp + " (" + oppHpPct + "%)";

  const oppMeta = document.getElementById("oppMonMetaText");
  if (oppMeta) {
    const abilityName = opp.ability?.name || opp.ability?.name_en || opp.ability || "未揭示";
    const genderStr = opp.gender === "male" ? "♂ 雄性" : opp.gender === "female" ? "♀ 雌性" : "无性别";
    oppMeta.innerText = "特性: " + abilityName + " · 性别: " + genderStr;
  }

  // 3. 渲染我方出战宝可梦数据
  const pl = data.player?.active || data.player || {};
  const plNameElem = document.getElementById("playerMonName");
  if (plNameElem) {
    const plName = pl.species?.names?.["zh-Hans"] || pl.species?.name || pl.species_name || "我方首发";
    const plEn = pl.species?.name || pl.name_en || "";
    const plId = pl.species_id || pl.species?.id || "";
    plNameElem.innerHTML = plName + (plEn ? " (" + plEn + ")" : "") + (plId ? " <span style=\\"font-size:12px;color:var(--text-3);\\">#" + plId + "</span>" : "") + " <span style=\\"font-size:13px;color:var(--text-3);margin-left:6px;\\">Lv." + (pl.level || "?") + "</span>";
  }

  const plHp = pl.current_hp ?? 0;
  const plMaxHp = pl.max_hp || 1;
  const plHpPct = Math.max(0, Math.min(100, Math.round((plHp / plMaxHp) * 100)));
  const plHpBar = document.getElementById("playerMonHpBar");
  if (plHpBar) {
    plHpBar.style.width = plHpPct + "%";
    plHpBar.className = "ds-progress-fill " + (plHpPct <= 20 ? "fill-red" : plHpPct <= 50 ? "fill-amber" : "");
  }
  const plHpText = document.getElementById("playerMonHpText");
  if (plHpText) plHpText.innerText = "生命值: " + plHp + " / " + plMaxHp + " (" + plHpPct + "%)";

  const plMeta = document.getElementById("playerMonMetaText");
  if (plMeta) {
    const plAbility = pl.ability?.name || pl.ability?.name_en || pl.ability || "特性";
    const plGender = pl.gender === "male" ? "♂ 雄性" : pl.gender === "female" ? "♀ 雌性" : "无性别";
    plMeta.innerText = "特性: " + plAbility + " · 性别: " + plGender;
  }

  const plStats = document.getElementById("playerMonStatsText");
  if (plStats && pl.stats) {
    plStats.innerText = "攻 " + (pl.stats.attack || "-") + " / 防 " + (pl.stats.defense || "-") + " / 特攻 " + (pl.stats.special_attack || "-") + " / 特防 " + (pl.stats.special_defense || "-") + " / 速度 " + (pl.stats.speed || "-");
  }

  // 4. 渲染 AI 推荐战术 Banner
  const dec = data.decision || {};
  const rec = dec.recommended_action || data.recommended_action || {};
  const decText = document.getElementById("battleDecisionText");
  if (decText) {
    if (rec.type === "use_move") {
      decText.innerHTML = "<strong style=\\"color:var(--accent-green);\\">推荐出招:</strong> 使用「" + (rec.move_name || ("招式" + rec.move_slot)) + "」 · " + (rec.reason || "");
    } else if (rec.type === "switch") {
      decText.innerHTML = "<strong style=\\"color:var(--accent-amber);\\">推荐换人:</strong> 切换席位 #" + rec.party_slot + " · " + (rec.reason || "");
    } else if (rec.type === "run") {
      decText.innerHTML = "<strong style=\\"color:var(--accent-cyan);\\">推荐脱战:</strong> " + (rec.reason || "脱离战斗");
    } else {
      decText.innerText = rec.reason || "正在实时评估最优招式与相克关系...";
    }
  }
  cachedCurrentDecision = rec;

  // 5. 渲染 4 招式卡片
  const moves = pl.moves || [];
  const evalMoves = dec.moves_evaluated || [];
  const bestMove = dec.best_move || {};
  renderBattleMoves(moves, evalMoves, bestMove);

  // 6. 渲染全队在战换人面板
  renderBattlePartySwitchDeck(data.player?.party || cachedParty || [], pl.species_id);

  // 7. 渲染底层 ARM9 RAM 结构体回读
  renderBattleRamDump(data);
}

function renderBattleMoves(moves, evalMoves, bestMove) {
  const container = document.getElementById("battleMovesGrid");
  if (!container) return;
  if (!moves || moves.length === 0) {
    container.innerHTML = "<div style=\\"padding:12px; color:var(--text-3); grid-column:span 2; text-align:center;\\">等待读取出战宝可梦招式...</div>";
    return;
  }

  const evalMap = {};
  if (Array.isArray(evalMoves)) {
    evalMoves.forEach(em => { evalMap[em.slot] = em; });
  }

  container.innerHTML = moves.map(m => {
    const slot = m.slot;
    const em = evalMap[slot] || {};
    const moveName = m.name || m.name_zh || em.name_zh || ("招式 " + slot);
    const moveEn = m.name_en || em.name_en || "";
    const moveType = m.type || em.move_type_name || "一般";
    const curPp = m.current_pp ?? em.current_pp ?? 0;
    const maxPp = m.max_pp ?? em.max_pp ?? 0;
    const isOut = (curPp === 0);
    const isBest = (bestMove && bestMove.slot === slot) || (em.verdict === "best");
    const mult = em.type_multiplier !== undefined ? em.type_multiplier : 1.0;
    const power = m.power ?? em.base_power ?? "-";
    const acc = m.accuracy ?? em.accuracy ?? 100;
    const score = em.expected_score !== undefined ? em.expected_score.toFixed(1) : "-";

    let multBadge = "";
    if (mult > 1.0) multBadge = "<span class=\\"ds-badge ds-badge-green\\" style=\\"font-size:11px;\\">克制 " + mult + "x</span>";
    else if (mult === 0.0) multBadge = "<span class=\\"ds-badge ds-badge-red\\" style=\\"font-size:11px;\\">无效 0x</span>";
    else if (mult < 1.0) multBadge = "<span class=\\"ds-badge ds-badge-amber\\" style=\\"font-size:11px;\\">微弱 " + mult + "x</span>";

    return (
      "<div class=\\"ds-move-card " + (isBest ? "recommended" : "") + " " + (isOut ? "disabled" : "") + "\\" onclick=\\"" + (isOut ? "" : "handleMoveAction(" + slot + ")") + "\\">" +
        "<div style=\\"display:flex; justify-content:space-between; align-items:center;\\">" +
          "<div style=\\"display:flex; align-items:center; gap:6px;\\">" +
            "<span class=\\"ds-badge ds-badge-cyan\\" style=\\"font-weight:800; font-size:11px;\\">#" + slot + "</span>" +
            "<strong style=\\"font-size:14px; color:" + (isBest ? "var(--accent-green)" : "var(--text-1)") + ";\\">" + moveName + "</strong>" +
            (moveEn ? "<span style=\\"font-size:11px; color:var(--text-3); font-weight:normal;\\">" + moveEn + "</span>" : "") +
          "</div>" +
          "<div style=\\"display:flex; gap:4px; align-items:center;\\">" +
            "<span class=\\"ds-badge\\" style=\\"font-size:11px; font-weight:700;\\">[" + moveType + "]</span>" +
            multBadge +
          "</div>" +
        "</div>" +
        "<div style=\\"display:flex; justify-content:space-between; font-size:12px; color:var(--text-2); margin-top:2px;\\">" +
          "<span>威力: <strong>" + power + "</strong> · 命中: <strong>" + acc + "</strong></span>" +
          "<span>PP: <strong style=\\"color:" + (curPp > 3 ? "var(--accent-green)" : curPp > 0 ? "var(--accent-amber)" : "var(--accent-red)") + "; font-size:13px;\\">" + curPp + " / " + maxPp + "</strong></span>" +
        "</div>" +
        "<div style=\\"display:flex; justify-content:space-between; align-items:center; font-size:11px; margin-top:2px;\\">" +
          "<span style=\\"color:var(--text-3);\\">AI 评分: <strong style=\\"color:var(--accent-cyan);\\">" + score + "</strong></span>" +
          (isBest ? "<span class=\\"ds-badge ds-badge-green\\" style=\\"font-size:11px; font-weight:800;\\">★ AI 最优推荐出招</span>" : (isOut ? "<span style=\\"color:var(--accent-red); font-weight:700;\\">[PP已耗尽]</span>" : "<span style=\\"color:var(--text-3);\\">[点击直接出招]</span>")) +
        "</div>" +
      "</div>"
    );
  }).join("");
}

function renderBattlePartySwitchDeck(party, activeSpeciesId) {
  const container = document.getElementById("battlePartyRoster");
  if (!container) return;
  if (!party || party.length === 0) {
    container.innerHTML = "<div style=\\"padding:8px; color:var(--text-3); grid-column:span 3; text-align:center;\\">暂无全队数据</div>";
    return;
  }

  container.innerHTML = party.map(s => {
    const slot = s.slot;
    const name = s.species_name_zh || s.species_name || s.species || ("席位 " + slot);
    const hp = s.current_hp ?? 0;
    const maxHp = s.max_hp || 1;
    const hpPct = Math.max(0, Math.min(100, Math.round((hp / maxHp) * 100)));
    const isActive = (s.species === activeSpeciesId || s.species_id === activeSpeciesId || slot === 1 && !activeSpeciesId);
    const isFainted = (hp === 0);

    return (
      "<div class=\\"ds-party-slot " + (isActive ? "is-lead" : "") + " " + (isFainted ? "is-crit" : "") + "\\" style=\\"padding:8px 10px; gap:4px; font-size:12px;\\">" +
        "<div style=\\"display:flex; justify-content:space-between; align-items:center;\\">" +
          "<strong style=\\"color:" + (isActive ? "var(--accent-cyan)" : "var(--text-1)") + ";\\">" + slot + ". " + name + "</strong>" +
          "<span class=\\"ds-badge " + (isActive ? "ds-badge-cyan" : isFainted ? "ds-badge-red" : "") + "\\" style=\\"font-size:10px;\\">" +
            (isActive ? "★ 战斗中" : isFainted ? "濒死" : "Lv." + (s.level || "?")) +
          "</span>" +
        "</div>" +
        "<div class=\\"ds-progress\\" style=\\"height:4px; margin-top:2px;\\">" +
          "<div class=\\"ds-progress-fill " + (hpPct <= 20 ? "fill-red" : hpPct <= 50 ? "fill-amber" : "") + "\\" style=\\"width:" + hpPct + "%;\\"></div>" +
        "</div>" +
        "<div style=\\"display:flex; justify-content:space-between; align-items:center; margin-top:2px; font-size:11px;\\">" +
          "<span style=\\"color:var(--text-3);\\">" + hp + " / " + maxHp + "</span>" +
          (isActive ? "<span style=\\"color:var(--accent-cyan); font-weight:700;\\">出战中</span>" : isFainted ? "<span style=\\"color:var(--accent-red);\\">无法换入</span>" : ("<button class=\\"ds-btn ds-btn-sm ds-btn-yellow\\" style=\\"padding:2px 8px; font-size:11px;\\" onclick=\\"handleSwitchPokemon(" + slot + ")\\">[换人出战]</button>")) +
        "</div>" +
      "</div>"
    );
  }).join("");
}

function renderBattleRamDump(data) {
  const oppDump = document.getElementById("ramOppDump");
  const plDump = document.getElementById("ramPlayerDump");
  const opp = data.opponent?.active || data.opponent || {};
  const pl = data.player?.active || data.player || {};

  if (oppDump) {
    oppDump.innerHTML = (
      "<div style=\\"color:var(--accent-red); font-weight:800; border-bottom:1px solid #333; padding-bottom:4px; margin-bottom:6px;\\">[敌方 BattlePokeParam · 0x0225BCA8]</div>" +
      "<div>物种 ID: 0x" + ((opp.species_id || 0).toString(16).toUpperCase().padStart(4, "0")) + " (" + (opp.species_name_zh || opp.species || "未知") + ")</div>" +
      "<div>等级: " + (opp.level || "?") + " · 性别: " + (opp.gender || "无") + " · PID: 0x" + (opp.pid || "--------") + "</div>" +
      "<div>当前 HP: " + (opp.current_hp || 0) + " / 最大 HP: " + (opp.max_hp || 0) + "</div>" +
      "<div>特性 ID: " + (opp.ability?.id || "未揭示") + " (" + (opp.ability?.name || "") + ")</div>" +
      "<div>招式列表: " + ((opp.moves || []).map(m => m.name + "(" + m.current_pp + "/" + m.max_pp + ")").join(" · ") || "未探针") + "</div>"
    );
  }

  if (plDump) {
    plDump.innerHTML = (
      "<div style=\\"color:var(--accent-green); font-weight:800; border-bottom:1px solid #333; padding-bottom:4px; margin-bottom:6px;\\">[我方 BattlePokeParam · 0x0225B418]</div>" +
      "<div>物种 ID: 0x" + ((pl.species_id || 0).toString(16).toUpperCase().padStart(4, "0")) + " (" + (pl.species_name_zh || pl.species || "我方首发") + ")</div>" +
      "<div>等级: " + (pl.level || "?") + " · 性别: " + (pl.gender || "无") + " · PID: 0x" + (pl.pid || "--------") + "</div>" +
      "<div>当前 HP: " + (pl.current_hp || 0) + " / 最大 HP: " + (pl.max_hp || 0) + "</div>" +
      "<div>特性 ID: " + (pl.ability?.id || "39") + " (" + (pl.ability?.name || "精神力") + ")</div>" +
      "<div>五维能力: 攻" + (pl.stats?.attack || "-") + " 防" + (pl.stats?.defense || "-") + " 特攻" + (pl.stats?.special_attack || "-") + " 特防" + (pl.stats?.special_defense || "-") + " 速" + (pl.stats?.speed || "-") + "</div>" +
      "<div>招式列表: " + ((pl.moves || []).map(m => m.name + "(" + m.current_pp + "/" + m.max_pp + ")").join(" · ")) + "</div>"
    );
  }
}

`;

  js = js.substring(0, pos_rbs) + newJs + js.substring(pos_tsb);
  fs.writeFileSync("frontend/v2.js", js, "utf8");
  console.log("Upgraded frontend/v2.js battle rendering!");
} else {
  console.log("Positions not found in v2.js");
}