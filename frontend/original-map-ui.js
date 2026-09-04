import {Black2World3D, esc} from '/frontend/world3d-runtime.js';

const UI_VERSION='6.2.1';
const $=selector=>document.querySelector(selector);
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const zh=(navigator.languages||[navigator.language||'en']).some(value=>/^zh(?:-|$)/i.test(String(value)));
const copy=zh?{
  pageTitle:'宝可梦 黑 2 · 3D 世界视口',viewportTab:'世界视口',monitorTab:'服务监控',outliner:'运行时大纲',pipeline:'世界加载管线',backendStep:'后端 API',bridgeStep:'BizHawk 桥接',romStep:'ROM 静态世界',playerStep:'玩家运行时',sceneStep:'3D 场景',sceneFacts:'场景事实',playerFacts:'玩家事实',status:'状态',notResolved:'尚未解析',properties:'视口属性',display:'显示',follow:'跟随玩家',framePlayer:'定位玩家',followToggle:'跟随玩家',actorsToggle:'运行时 NPC',gridToggle:'证据网格',runtimeActions:'运行时操作',discover:'发现玩家',refreshScene:'刷新场景',restart:'重启后端',logs:'日志与版本',discoveryNote:'玩家发现是一次显式只读 RAM 探测，不会写入游戏内存。',renderer:'玩家渲染器',mode:'模式',waiting:'等待中',shortcuts:'视口导航',orbit:'旋转',pan:'平移',zoom:'缩放',discoverShortcut:'发现玩家运行时',evidence:'证据包',capture:'截屏并验证 ZIP',evidenceNote:'包含截图、验证输出与 JSON manifest。',checkAgain:'重新检查',openMonitor:'打开监控',pathPreview:'路径预览',previewPath:'预览路径',clearPath:'清除',pathNote:'只显示导航 API 返回的候选路径，不代表已经执行。',nodes:'节点',
  checking:'正在检查',online:'在线',connected:'已连接',bridgeWaiting:'等待 BizHawk Lua Bridge 连接',romReady:'ROM 可用',playerReady:'玩家已解析',sceneReady:'场景已就绪',sceneWaiting:'等待玩家位置',runtimePipeline:'运行时加载管线',checkingTitle:'正在读取真实状态',checkingMessage:'依次确认后端、Bridge、ROM、玩家运行时和场景资源。',backendTitle:'后端不可用',backendMessage:'浏览器无法连接运行时 API。请查看后台监控。',bridgeTitle:'等待 BizHawk Bridge',bridgeMessage:'后端在线，但 BizHawk Lua 尚未连接到 8766。',romTitle:'ROM 素材不可用',romMessage:'玩家 RAM 状态和 ROM 静态素材是两条独立管线；当前静态世界不能加载。',playerTitle:'需要发现玩家运行时',playerMessage:'点击“发现玩家”，执行一次显式只读探测。完成后页面才会请求当前场景。',discoveringTitle:'正在发现玩家',discoveringMessage:'正在读取当前 RAM 并验证 FieldActor 结构，请保持游戏处于可移动场景。',elapsed:'已用时',sceneTitle:'正在构建当前场景',sceneMessage:'正在按当前 Zone 加载 ROM 地形、建筑和玩家素材。',ready:'就绪',unresolved:'未解析',blocked:'阻塞',error:'错误',loading:'加载中',frame:'帧',zone:'区域',environment:'环境',matrix:'矩阵',terrain:'地形块',buildings:'建筑',confidence:'置信度',gpos:'网格坐标',wpos:'世界坐标',facing:'朝向',motion:'移动',transport:'方式',release:'运行时',assets:'素材',actorUnresolved:'素材未解析',pathReady:'候选路径已显示',pathUnavailable:'没有可显示的候选路径',pathNeedPlayer:'请先发现玩家位置',goalRequired:'请输入目标 X 和 Z',captureWorking:'正在生成证据包…',captureReady:'证据包已生成',restartWorking:'正在重启，页面会自动重连…',requestFailed:'请求失败',progressReady:'世界视口已就绪'
}:{
  pageTitle:'Pokémon Black 2 · 3D World Viewport',viewportTab:'World Viewport',monitorTab:'Service Monitor',outliner:'Runtime Outliner',pipeline:'World pipeline',backendStep:'Backend API',bridgeStep:'BizHawk Bridge',romStep:'ROM Static World',playerStep:'Player Runtime',sceneStep:'3D Scene',sceneFacts:'Scene facts',playerFacts:'Player facts',status:'Status',notResolved:'Not resolved',properties:'Viewport Properties',display:'Display',follow:'Follow Player',framePlayer:'Frame Player',followToggle:'Follow player',actorsToggle:'Runtime NPCs',gridToggle:'Evidence grid',runtimeActions:'Runtime actions',discover:'Discover player',refreshScene:'Refresh scene',restart:'Restart backend',logs:'Logs & versions',discoveryNote:'Player discovery is an explicit read-only RAM probe. It never writes game memory.',renderer:'Player renderer',mode:'Mode',waiting:'Waiting',shortcuts:'Viewport navigation',orbit:'Orbit',pan:'Pan',zoom:'Zoom',discoverShortcut:'Discover player runtime',evidence:'Evidence package',capture:'Capture & verify ZIP',evidenceNote:'Screenshot, validation output and JSON manifest.',checkAgain:'Check again',openMonitor:'Open monitor',pathPreview:'Path preview',previewPath:'Preview path',clearPath:'Clear',pathNote:'Displays only the candidate path returned by the navigation API; it is not executed.',nodes:'nodes',
  checking:'checking',online:'online',connected:'connected',bridgeWaiting:'waiting for BizHawk Lua Bridge',romReady:'ROM available',playerReady:'player resolved',sceneReady:'scene ready',sceneWaiting:'waiting for player position',runtimePipeline:'Runtime pipeline',checkingTitle:'Reading real runtime state',checkingMessage:'Checking Backend, Bridge, ROM, PlayerRuntime and scene assets in order.',backendTitle:'Backend unavailable',backendMessage:'The browser cannot reach the runtime API. Open Service Monitor for details.',bridgeTitle:'Waiting for BizHawk Bridge',bridgeMessage:'The backend is online, but the BizHawk Lua bridge has not connected to port 8766.',romTitle:'ROM assets unavailable',romMessage:'Player RAM state and ROM static assets are separate pipelines; the static world cannot currently load.',playerTitle:'Player discovery required',playerMessage:'Click Discover player to run one explicit read-only probe. The current scene is requested only after that succeeds.',discoveringTitle:'Discovering player runtime',discoveringMessage:'Reading current RAM and validating the FieldActor structure. Keep the game in a controllable field scene.',elapsed:'elapsed',sceneTitle:'Building current scene',sceneMessage:'Loading ROM terrain, buildings and player assets for the current Zone.',ready:'ready',unresolved:'unresolved',blocked:'blocked',error:'error',loading:'loading',frame:'frame',zone:'Zone',environment:'Environment',matrix:'Matrix',terrain:'Terrain',buildings:'Buildings',confidence:'Confidence',gpos:'GPos',wpos:'WPos',facing:'Facing',motion:'Motion',transport:'Transport',release:'Runtime',assets:'assets',actorUnresolved:'asset unresolved',pathReady:'Candidate path displayed',pathUnavailable:'No candidate path is available',pathNeedPlayer:'Discover the player position first',goalRequired:'Enter target X and Z',captureWorking:'Creating evidence package…',captureReady:'Evidence package created',restartWorking:'Restarting; this page will reconnect…',requestFailed:'Request failed',progressReady:'World viewport ready'
};

document.documentElement.lang=zh?'zh-CN':'en';
document.title=copy.pageTitle;
document.querySelectorAll('[data-i18n]').forEach(node=>{if(copy[node.dataset.i18n])node.textContent=copy[node.dataset.i18n];});
$('#uiVersion').textContent=`UI ${UI_VERSION}`;

async function jsonFetch(url,options={},timeout=5000){
  const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeout);
  try{
    const response=await fetch(url,{...options,cache:'no-store',signal:controller.signal});
    const text=await response.text();let body=null;try{body=text?JSON.parse(text):null;}catch{body={detail:text};}
    if(!response.ok)throw new Error(body?.detail||`${response.status} ${response.statusText}`);
    return body;
  }finally{clearTimeout(timer);}
}
function errorText(error){return String(error?.message||error||copy.error).replace(/^\d+\s*/, '').slice(0,420);}

const stages={
  backend:{state:'active',detail:copy.checking},bridge:{state:'active',detail:copy.checking},rom:{state:'active',detail:copy.checking},player:{state:'active',detail:copy.checking},scene:{state:'waiting',detail:copy.sceneWaiting,fraction:0}
};
const order=['backend','bridge','rom','player','scene'];
const weights={backend:15,bridge:15,rom:20,player:20,scene:30};
let currentPlayer=null,currentScene=null,overlayAction=null,probing=false,systemProbeBusy=false;

function setStage(id,state,detail,fraction){
  const stage=stages[id];if(!stage)return;
  stage.state=state;stage.detail=detail||state;if(Number.isFinite(fraction))stage.fraction=Math.max(0,Math.min(1,fraction));
  renderPipeline();
}
function renderPipeline(){
  let percent=0,readyCount=0,blocked=false;
  for(const id of order){
    const stage=stages[id],node=document.querySelector(`[data-step="${id}"]`);
    node.dataset.state=stage.state;node.querySelector('.step-detail').textContent=stage.detail;
    let fraction=0;if(stage.state==='ready'){fraction=1;readyCount++;}else if(stage.state==='active'&&id==='scene'){fraction=stage.fraction||0;}if(stage.state==='blocked'||stage.state==='error')blocked=true;
    percent+=weights[id]*fraction;
  }
  $('#pipelineCount').textContent=`${readyCount} / ${order.length}`;
  const rounded=Math.round(percent);$('#progressFill').style.width=`${rounded}%`;$('#progressFill').classList.toggle('blocked',blocked);$('#progressPercent').textContent=`${rounded}%`;
  const first=order.map(id=>({id,...stages[id]})).find(stage=>stage.state!=='ready');
  $('#progressLabel').textContent=first?first.detail:copy.progressReady;
  renderChips();renderBlocker();
}
function setChip(id,state,label){const node=$(id);node.dataset.state=state;node.querySelector('span').textContent=label;}
function renderChips(){
  setChip('#httpChip',stages.backend.state,`HTTP · ${stages.backend.state==='ready'?copy.online:stages.backend.state}`);
  setChip('#bridgeChip',stages.bridge.state,`Bridge · ${stages.bridge.state==='ready'?copy.connected:stages.bridge.state}`);
  setChip('#romChip',stages.rom.state,`ROM · ${stages.rom.state==='ready'?copy.ready:stages.rom.state}`);
  setChip('#playerChip',stages.player.state,`Player · ${stages.player.state==='ready'?copy.ready:stages.player.state}`);
}
function blockerModel(){
  if(stages.backend.state!=='ready')return {state:stages.backend.state,title:stages.backend.state==='active'?copy.checkingTitle:copy.backendTitle,message:stages.backend.state==='active'?copy.checkingMessage:copy.backendMessage,detail:stages.backend.detail,action:probeSystem,label:copy.checkAgain};
  if(stages.bridge.state!=='ready')return {state:stages.bridge.state,title:copy.bridgeTitle,message:copy.bridgeMessage,detail:stages.bridge.detail,action:probeSystem,label:copy.checkAgain};
  if(stages.rom.state!=='ready')return {state:stages.rom.state,title:copy.romTitle,message:copy.romMessage,detail:stages.rom.detail,action:probeSystem,label:copy.checkAgain};
  if(stages.player.state!=='ready')return {state:stages.player.state,title:stages.player.state==='active'?copy.discoveringTitle:copy.playerTitle,message:stages.player.state==='active'?copy.discoveringMessage:copy.playerMessage,detail:stages.player.detail,action:discoverPlayer,label:copy.discover};
  if(stages.scene.state!=='ready')return {state:stages.scene.state==='waiting'?'active':stages.scene.state,title:copy.sceneTitle,message:copy.sceneMessage,detail:stages.scene.detail,action:refreshScene,label:copy.refreshScene};
  return null;
}
function renderBlocker(){
  const model=blockerModel();if(!model){$('#blocker').hidden=true;return;}
  $('#blocker').hidden=false;$('#blockerCard').dataset.state=model.state;$('#blockerKicker').textContent=copy.runtimePipeline;$('#blockerTitle').textContent=model.title;$('#blockerMessage').textContent=model.message;$('#blockerDetail').textContent=model.detail;$('#primaryAction').textContent=model.label;$('#primaryAction').disabled=model.state==='active'&&probing;overlayAction=model.action;
}
function facts(target,items){target.innerHTML=items.map(([key,value,state])=>`<dt>${esc(key)}</dt><dd class="${state||''}">${esc(value??copy.unresolved)}</dd>`).join('');}
function fixed(value){const number=Number(value);return Number.isFinite(number)?number.toFixed(2):'—';}

const viewer=new Black2World3D($('#world'),{
  onStatus(kind,state,detail){
    if(kind==='player'){
      if(state==='resolved'||state==='candidate')setStage('player','ready',detail||copy.playerReady);
      else if(state==='degraded')setStage('player','error',detail||copy.error);
      else setStage('player',probing?'active':'waiting',detail||copy.unresolved);
    }
    if(kind==='scene'){
      if(state==='resolved'||state==='candidate')setStage('scene','ready',detail||copy.sceneReady,1);
      else if(state==='degraded')setStage('scene','error',detail||copy.error);
      else setStage('scene','waiting',detail||copy.sceneWaiting,0);
    }
  },
  onProgress(done,total,label){
    if(total>0&&done<total)setStage('scene','active',`${copy.assets} ${done} / ${total}`,done/total);
    else if(total>0&&done>=total)setStage('scene','ready',`${copy.assets} ${done} / ${total}`,1);
  },
  onFollowChange(following){$('#followToggle').checked=following;$('#followButton').classList.toggle('active',following);},
  onActorMode(mode,meta){facts($('#rendererFacts'),[[copy.mode,mode||copy.actorUnresolved],[copy.assets,meta?.resource_kind||copy.actorUnresolved]]);},
  onScene(scene){
    currentScene=scene;const stat=scene.static||{},matrix=stat.matrix||{};
    $('#sceneConfidence').textContent=scene.confidence||copy.unresolved;$('#viewportScene').textContent=`Zone ${scene.zone_id??copy.unresolved} · ${scene.environment||copy.unresolved}`;
    facts($('#sceneFacts'),[[copy.zone,scene.zone_id],[copy.environment,scene.environment],[copy.matrix,matrix.matrix_id],[copy.terrain,(stat.terrains||[]).length],[copy.buildings,(stat.buildings||[]).length],[copy.confidence,scene.confidence]]);
  },
  onPlayer(player){
    currentPlayer=player;const grid=player.grid||{},world=player.world||{},orientation=player.orientation||{},motion=player.locomotion||{};
    $('#playerFrame').textContent=`${copy.frame} ${player.frame??'—'}`;$('#frameInfo').textContent=`${copy.frame} ${player.frame??'—'} · GPos ${grid.x??'—'}, ${grid.y??'—'}, ${grid.z??'—'}`;
    facts($('#playerFacts'),[[copy.gpos,`${grid.x??'—'}, ${grid.y??'—'}, ${grid.z??'—'}`],[copy.wpos,`${fixed(world.x)}, ${fixed(world.y)}, ${fixed(world.z)}`],[copy.facing,`${orientation.facing||'—'} / ${orientation.facing_zh||'—'}`],[copy.motion,motion.semantic_state],[copy.transport,motion.transport_mode]]);
    if(!$('#goalX').value&&Number.isFinite(Number(grid.x)))$('#goalX').value=grid.x;
    if(!$('#goalZ').value&&Number.isFinite(Number(grid.z)))$('#goalZ').value=grid.z;
  }
});

async function probeSystem(){
  if(systemProbeBusy)return;systemProbeBusy=true;
  try{
    const [controlResult,mapResult,versionResult]=await Promise.allSettled([
      jsonFetch('/api/v1/runtime/control/status',{},2200),jsonFetch('/api/v1/map/v6/status',{},5000),jsonFetch('/api/v1/runtime/versions',{},2200)
    ]);
    if(controlResult.status==='rejected'){
      setStage('backend','error',errorText(controlResult.reason));return;
    }
    const control=controlResult.value,health=control.health||{};setStage('backend','ready',`PID ${control.pid} · v${control.version}`);
    setStage('bridge',health.bridge_connected?'ready':'waiting',health.bridge_connected?`${copy.connected} · ${copy.frame} ${health.frame??0}`:copy.bridgeWaiting);
    if(versionResult.status==='fulfilled')$('#releaseVersion').textContent=`${copy.release} ${versionResult.value.release}`;
    if(mapResult.status==='fulfilled'&&mapResult.value.status==='ready')setStage('rom','ready',control.rom?.file_name||mapResult.value.rom?.file_name||copy.romReady);
    else setStage('rom','blocked',mapResult.status==='fulfilled'?(mapResult.value.detail||mapResult.value.status):errorText(mapResult.reason));
  }finally{systemProbeBusy=false;}
}
async function discoverPlayer(){
  if(probing)return;probing=true;$('#discoverButton').disabled=true;setStage('player','active',copy.discoveringTitle);
  const started=performance.now();const elapsedTimer=setInterval(()=>setStage('player','active',`${copy.discoveringTitle} · ${copy.elapsed} ${Math.floor((performance.now()-started)/1000)}s`),1000);
  try{
    const player=await jsonFetch('/api/v1/player/runtime',{},60000);viewer.applyPlayer(player);
    if(player.status==='resolved'||player.status==='candidate'){setStage('player','ready',`${copy.frame} ${player.frame??'—'}`);await viewer.refreshScene(true);}
    else setStage('player','waiting',player.reason||copy.unresolved);
  }catch(error){setStage('player','error',errorText(error));}
  finally{clearInterval(elapsedTimer);probing=false;$('#discoverButton').disabled=false;renderPipeline();}
}
async function refreshScene(){setStage('scene','active',copy.loading,0.05);try{await viewer.refreshScene(true);}catch(error){setStage('scene','error',errorText(error));}}
async function restartBackend(){
  const button=$('#restartButton');button.disabled=true;$('#actionNote').textContent=copy.restartWorking;
  try{
    const [control,versions]=await Promise.all([jsonFetch('/api/v1/runtime/control'),jsonFetch('/api/v1/runtime/versions')]);
    const version=versions.components?.find(item=>item.id==='runtime_control');
    if(version?.status!=='compatible'||control.version!==version.observed_version||!control.restart_token)throw new Error('runtime-control capability mismatch');
    await jsonFetch('/api/v1/runtime/restart',{method:'POST',headers:{'X-Runtime-Restart-Token':control.restart_token}});
    setStage('backend','active',copy.restartWorking);setStage('bridge','waiting',copy.bridgeWaiting);setStage('player','waiting',copy.unresolved);setStage('scene','waiting',copy.sceneWaiting);
  }catch(error){$('#actionNote').textContent=`${copy.requestFailed}: ${errorText(error)}`;button.disabled=false;}
}
async function previewPath(){
  if(!currentPlayer){$('#pathStatus').textContent=copy.pathNeedPlayer;return;}
  const goalX=Number($('#goalX').value),goalZ=Number($('#goalZ').value),grid=currentPlayer.grid||{};
  if(!Number.isInteger(goalX)||!Number.isInteger(goalZ)){$('#pathStatus').textContent=copy.goalRequired;return;}
  const button=$('#previewPathButton');button.disabled=true;
  try{
    const matrixId=currentScene?.static?.matrix?.matrix_id??0;
    const result=await jsonFetch('/api/v1/nav/find_path',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({start_x:grid.x,start_y:grid.z,goal_x:goalX,goal_y:goalZ,matrix_id:matrixId,allow_water:false})},12000);
    const count=viewer.setNavigationPath(result.path||[]);if(count>1)viewer.frameNavigationPath();$('#pathCount').textContent=`${count} ${copy.nodes}`;$('#pathStatus').textContent=count>1?`${copy.pathReady} · ${count} ${copy.nodes}`:copy.pathUnavailable;
  }catch(error){$('#pathStatus').textContent=`${copy.requestFailed}: ${errorText(error)}`;}
  finally{button.disabled=false;}
}
async function captureEvidence(){
  const button=$('#evidenceButton'),label=$('#evidenceLabel').value.trim()||'evidence';button.disabled=true;$('#evidenceStatus').textContent=copy.captureWorking;
  try{const result=await jsonFetch(`/api/v1/map/v6/evidence/capture?label=${encodeURIComponent(label)}`,{method:'POST'},60000);$('#evidenceStatus').innerHTML=result.ok?`${esc(copy.captureReady)} · <a href="${esc(result.download_url)}" download>${esc(result.zip_name)}</a>`:`${esc(copy.requestFailed)}: ${esc(result.detail||copy.error)}`;}
  catch(error){$('#evidenceStatus').textContent=`${copy.requestFailed}: ${errorText(error)}`;}finally{button.disabled=false;}
}

$('#primaryAction').addEventListener('click',()=>overlayAction?.());
$('#followButton').addEventListener('click',()=>viewer.setFollow(!viewer.followPlayer));
$('#followToggle').addEventListener('change',event=>viewer.setFollow(event.target.checked));
$('#actorsToggle').addEventListener('change',event=>viewer.setRuntimeActors(event.target.checked));
$('#gridToggle').addEventListener('change',event=>viewer.setDebug(event.target.checked));
$('#frameButton').addEventListener('click',()=>viewer.framePlayer());
$('#discoverButton').addEventListener('click',discoverPlayer);$('#refreshButton').addEventListener('click',refreshScene);$('#restartButton').addEventListener('click',restartBackend);
$('#previewPathButton').addEventListener('click',previewPath);$('#clearPathButton').addEventListener('click',()=>{viewer.clearNavigationPath();$('#pathCount').textContent=`0 ${copy.nodes}`;$('#pathStatus').textContent=copy.pathNote;});
$('#evidenceButton').addEventListener('click',captureEvidence);
addEventListener('keydown',event=>{if(event.key.toLowerCase()==='r'&&!event.ctrlKey&&!event.metaKey&&!/INPUT|TEXTAREA/.test(event.target.tagName))discoverPlayer();});

renderPipeline();viewer.start();probeSystem();setInterval(probeSystem,1800);
console.info(JSON.stringify({component:'original-map-ui',version:UI_VERSION,operation:'initialize',language:zh?'zh-CN':'en',result:'started'}));
