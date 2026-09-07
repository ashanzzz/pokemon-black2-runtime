const $=id=>document.getElementById(id);
let currentTab='moves';
let last={state:null,request:null,evidence:null,health:null};

function text(id,value,fallback='—'){const el=$(id);if(el)el.textContent=(value===null||value===undefined||value==='')?fallback:String(value)}
function yesNo(v){return v===true?'是':v===false?'否':'未解析'}
function badge(el,active,status){if(!el)return;el.className='badge';if(active===true){el.classList.add('good');el.textContent='ACTIVE'}else if(active===false){el.classList.add('neutral');el.textContent='inactive'}else{el.classList.add(status==='candidate'?'candidate':'neutral');el.textContent=status||'unresolved'}}
function activePanel(id,active){const el=$(id);if(!el)return;el.classList.toggle('is-active',active===true);el.classList.toggle('is-inactive',active===false)}
async function json(url){const r=await fetch(url,{cache:'no-store'});if(!r.ok)throw new Error(`${r.status} ${url}`);return r.json()}

function renderState(s){
  last.state=s;
  const labels={exploration:'探索',battle:'战斗',menu:'菜单',dialogue:'对话',transition:'过渡',unknown:'未解析'};
  text('primaryContext',labels[s.primary_context]||s.primary_context||'未解析');
  text('frameText',s.frame!=null?`Frame ${s.frame}`:'Frame —');
  text('inputOwner',labels[s.input?.owner]||s.input?.owner);
  text('inputSummary',s.input?.owner?`当前输入由「${labels[s.input.owner]||s.input.owner}」层处理 · ${s.input.kind||'未解析'} · 是否需要输入 ${yesNo(s.input.required)}`:'当前输入归属未解析');
  const chips=$('layerChips'); chips.innerHTML='';
  for(const layer of s.layers||[]){const c=document.createElement('span');c.className='chip '+(layer.active===true?'active ':'')+((s.overlays||[]).includes(layer.id)?'overlay ':'')+(layer.active==null?'unknown':'');c.textContent=`${labels[layer.id]||layer.id} ${layer.active===true?'ON':layer.active===false?'off':'?'}`;chips.appendChild(c)}
  const ex=s.exploration||{};badge($('explorationBadge'),ex.active,ex.active==null?'unresolved':'observed');activePanel('explorationPanel',ex.active);
  text('zoneText',ex.zone_id);const p=ex.position||{};text('posText',Number.isInteger(p.x)?`(${p.x}, ${p.y}, ${p.z})`:null);text('moveText',yesNo(ex.can_move));
  const d=s.dialogue||{};badge($('dialogueBadge'),d.active,d.active==null?'unresolved':'observed');activePanel('dialoguePanel',d.active);text('speakerText',d.speaker);text('dialogueText',d.text||d.full_text,'当前没有已解析对话。');text('dialogueHint',d.recommended_action);
  const cl=$('choiceList');cl.innerHTML='';for(const c of d.choices||[]){const row=document.createElement('div');row.className='choice';row.textContent=`${c.selected?'▶ ':''}${c.label??c.text??`选项 ${c.index}`}`;cl.appendChild(row)}
  const b=s.battle||{};badge($('battleBadge'),b.active,b.active_status);activePanel('battlePanel',b.active);text('battleTitle',b.active===true?'检测到战斗场景':b.active===false?'当前无战斗':'战斗状态未解析');text('battleFormat',`形式 ${b.format||'未解析'}`);text('battlePhase',`阶段 ${b.phase||'未解析'}`);text('partySummary',b.party_header?.count!=null?`队伍 ${b.party_header.count} / ${b.party_header.capacity} · 槽位内容仍未解密验证`:'队伍内容未解析');
  text('battleExecution',b.execution_available?'执行可用':'执行关闭');
  renderCommand();
}

function renderRequest(r){last.request=r;text('battleId',r.battle_id);text('requestId',r.request_id);text('waitingPlayer',yesNo(r.waiting_for_player));text('blockingOverlay',r.blocking_overlay);text('requestReason',r.reason?.message)}
function renderEvidence(e){last.evidence=e;text('gameDataPtr',e.pointers?.game_data);text('fieldStatusPtr',e.pointers?.field_status);text('partyPtr',e.pointers?.party);text('busyFlag',e.field_busy?.raw!=null?`${e.field_busy.raw} · ${e.field_busy.name}`:null);text('confidence',typeof e.confidence==='number'?`${Math.round(e.confidence*100)}% · ${e.verified?'verified':'candidate'}`:null);text('evidenceReason',e.reason);badge($('evidenceBadge'),e.active,e.status)}
function renderHealth(h){last.health=h;text('backendHealth','● online');text('bridgeHealth',h.bridge_connected?'● connected':'○ disconnected');text('ramStatus',h.semantic_status||h.runtime_status)}

async function renderCommand(){const el=$('commandContent');if(!el)return;const b=last.state?.battle||{};if(b.active!==true){el.innerHTML='<div class="empty-state"><strong>当前没有可确认的战斗</strong><span>战斗面板会在 RAM 候选检测到 FieldStatus.BusyFlag=1 时激活。</span></div>';return}
  const why=last.request?.reason?.message||'动作执行仍由证据门禁阻止。';
  if(currentTab==='moves'){el.innerHTML=`<div class="empty-state"><strong>招式槽位尚未验证</strong><span>API 已就绪：GET /api/v1/battle/moves。等 IREJ Move/PP/target RAM 解码验证后，这里直接显示四个技能卡片。</span><span>${why}</span></div>`}
  else if(currentTab==='items'){el.innerHTML='<div class="empty-state"><strong>战斗背包接口已拆分</strong><span>recovery · status_restore · pokeballs · battle_items</span><span>当前 live Bag 可用项与捕获权限仍保持 unknown，不会根据截图猜。</span></div>'}
  else if(currentTab==='party'){const h=b.party_header||{};el.innerHTML=`<div class="empty-state"><strong>队伍 Header ${h.count??'?'} / ${h.capacity??'?'}</strong><span>GET /api/v1/battle/party 已返回结构证据。Species、HP、招式等槽位内容等解密/校验通过后再开放。</span></div>`}
  else{el.innerHTML=`<div class="empty-state"><strong>逃跑是语义动作，不是盲按菜单</strong><span>POST /api/v1/battle/decisions 支持 run，但在逃跑合法性、菜单位置和结果确认完成前会返回 409。</span><span>${why}</span></div>`}
}

document.querySelectorAll('.cmd').forEach(btn=>btn.addEventListener('click',()=>{document.querySelectorAll('.cmd').forEach(x=>x.classList.remove('active'));btn.classList.add('active');currentTab=btn.dataset.tab;renderCommand()}));

async function refresh(){
  try{const [state,request,evidence,health]=await Promise.all([json('/api/v1/game/current'),json('/api/v1/battle/request'),json('/api/v1/battle/evidence'),json('/api/v1/runtime/health')]);renderState(state);renderRequest(request);renderEvidence(evidence);renderHealth(health)}
  catch(err){text('backendHealth','○ error');text('inputSummary',`读取失败：${err.message}`)}
}
refresh();setInterval(refresh,1000);
