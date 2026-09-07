import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js';

const API='/api/v1/map/v6';
const LAB='/api/v1/lab';
const TILE=16;
const PLAYER_POLL_MS=180;
// Actor RAM remains authoritative, but NPC motion is rendered locally between
// snapshots.  This keeps scripted/random movement correct while halving the
// ActorSystem heap read rate compared with the previous 450 ms polling.
const ACTOR_POLL_MS=900;
const TARGET_RENDER_FPS=30;
const DIR_STEP=[0,3,2,3,0,1,3,0,0,0,4,0,4,2,2,2,3,3,3,3,3,3,0,0,0,0,0,3,3,3,3,3,2,2,0];
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

async function getJSON(url,timeout=5000){const c=new AbortController(),t=setTimeout(()=>c.abort(),timeout);try{const r=await fetch(url,{cache:'no-store',signal:c.signal});if(!r.ok)throw new Error(`${r.status} ${await r.text()}`);return await r.json()}finally{clearTimeout(t)}}
function finite(v){return Number.isFinite(Number(v))?Number(v):null}
export function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}

export class Black2World3D{
  constructor(host,ui={}){
    this.host=host;this.ui=ui;this.disposed=false;
    this.scene=new THREE.Scene();this.scene.background=new THREE.Color(0x08111c);this.scene.fog=new THREE.FogExp2(0x08111c,.00055);
    this.camera=new THREE.PerspectiveCamera(42,1,.5,25000);
    this.renderer=new THREE.WebGLRenderer({antialias:false,alpha:false,powerPreference:'high-performance'});this.renderer.setPixelRatio(Math.min(devicePixelRatio||1,1.25));this.renderer.outputColorSpace=THREE.SRGBColorSpace;this.renderer.shadowMap.enabled=false;this.renderer.localClippingEnabled=true;host.appendChild(this.renderer.domElement);
    this.controls=new OrbitControls(this.camera,this.renderer.domElement);this.controls.enableDamping=true;this.controls.dampingFactor=.08;this.controls.enablePan=true;this.controls.enableZoom=true;this.controls.screenSpacePanning=true;this.controls.minDistance=12;this.controls.maxDistance=12000;this.controls.mouseButtons.MIDDLE=THREE.MOUSE.PAN;this.controls.mouseButtons.RIGHT=THREE.MOUSE.PAN;this.controls.enabled=false;
    this.loader=new GLTFLoader();
    this.worldRoot=new THREE.Group();this.referenceRoot=new THREE.Group();this.staticRoot=new THREE.Group();this.entranceRoot=new THREE.Group();this.actorRoot=new THREE.Group();this.pathRoot=new THREE.Group();this.pathRoot.name='navigation-route';this.debugRoot=new THREE.Group();this.worldRoot.add(this.referenceRoot,this.staticRoot,this.entranceRoot,this.pathRoot,this.actorRoot,this.debugRoot);this.scene.add(this.worldRoot);
    this.scene.add(new THREE.HemisphereLight(0xe9f2ff,0x4a4438,2.1));const sun=new THREE.DirectionalLight(0xffffff,2.1);sun.position.set(-400,900,450);this.scene.add(sun);
    this.playerAnchor=new THREE.Group();this.playerAnchor.userData={kind:'player'};this.actorRoot.add(this.playerAnchor);this.playerVisual=null;this.playerVisualMode='none';this.gender=ui.gender||'male';this.playerMeta=null;this.billboardStep=0;this.lastBillboardFrame=null;this.spriteCache=new Map();
    this.player=null;this.sceneData=null;this.playerTarget=new THREE.Vector3();this.playerDisplay=new THREE.Vector3();this.origin=new THREE.Vector3();this.lastPlayerFrame=null;this.playerMotion={from:new THREE.Vector3(),to:new THREE.Vector3(),startedAt:0,duration:1,moving:false,face:1,sampleFrame:null};this.playerDiscoveryRequest=null;this.lastPlayerDiscoveryAt=0;this.unresolvedPlayerSamples=0;this.staticPreview=false;this.staticPreviewZone=null;this.staticPreviewRequest=null;this.connectedWorld=false;
    this.mountedSceneKey=null;this.loadingGeneration=0;this.sceneRequest=null;this.sceneRefreshPending=false;this.sceneRefreshForce=false;this.sceneRefreshDelay=0;this.sceneRefreshTimer=null;this.followPlayer=true;this.showActors=false;this.showDebug=false;this.navigationPath=[];this.navigationTarget=null;this.actorMarkers=new Map();this.actorAssetCache=new Map();this.lastRenderAt=0;this.fpsFrames=0;this.fpsWindow=performance.now();this.fps=0;
    this.diag=this._newDiagnostics();this.selectionRoot=new THREE.Group();this.cellHoverRoot=new THREE.Group();this.cellLockedRoot=new THREE.Group();this.cellHoverRoot.name='terrain-cell-hover';this.cellLockedRoot.name='terrain-cell-locked';this.worldRoot.add(this.selectionRoot,this.cellHoverRoot,this.cellLockedRoot);this.hoverCell=null;this.lockedCell=null;this.raycaster=new THREE.Raycaster();this.pointer=new THREE.Vector2();this._onPick=e=>this._pick(e);this._onPointerMove=e=>this._hover(e);this._onPointerLeave=()=>{this._clearCellHover();this.ui.onPointer?.(null,false)};this.renderer.domElement.addEventListener('click',this._onPick);this.renderer.domElement.addEventListener('pointermove',this._onPointerMove);this.renderer.domElement.addEventListener('pointerleave',this._onPointerLeave);this._makeGrid();this.resize();addEventListener('resize',()=>this.resize());this._animate(performance.now());
  }
  _newDiagnostics(){return{scene_key:null,scene_load_ms:null,terrain_total:0,terrain_loaded:0,terrain_failed:0,terrain_fallback:0,buildings_total:0,buildings_loaded:0,buildings_failed:0,doors_total:0,doors_rendered:0,door_positions:[],failed_assets:[],player_mode:this.playerVisualMode,npc_total:0,npc_original_count:0,npc_fallback_count:0,fps:this.fps}}
  diagnostics(){return{...this.diag,failed_assets:[...this.diag.failed_assets].slice(-100),door_positions:[...(this.diag.door_positions||[])],fps:this.fps,player_mode:this.playerVisualMode,npc_total:this.actorMarkers.size}}
  _emitDiag(){this.ui.onDiagnostics?.(this.diagnostics())}
  resize(){const w=Math.max(1,this.host.clientWidth),h=Math.max(1,this.host.clientHeight);this.camera.aspect=w/h;this.camera.updateProjectionMatrix();this.renderer.setSize(w,h,false)}
  _makeGrid(){const g=new THREE.GridHelper(2048,128,0x435064,0x202833);g.position.y=.05;g.material.transparent=true;g.material.opacity=.28;g.visible=false;g.name='evidence-grid';this.debugRoot.add(g)}
  setDebug(v){this.showDebug=!!v;for(const o of this.debugRoot.children)o.visible=this.showDebug;this.referenceRoot.visible=this.showDebug&&this.staticRoot.children.some(o=>o.userData?.kind==='terrain'&&o.visible)}
  setRuntimeActors(v){this.showActors=!!v;if(!v)this._clearActors();}
  async setConnectedWorld(v){this.connectedWorld=!!v;this.mountedSceneKey=null;this.ui.onStatus?.('scene','candidate',this.connectedWorld?'loading connected outdoor cluster':'loading single Zone');return this.refreshScene(true)}
  setFollow(v){this.followPlayer=!!v;this.controls.enabled=!this.followPlayer;if(!v)this.controls.target.copy(this.playerDisplay);this.ui.onFollowChange?.(this.followPlayer)}
  framePlayer(){if(!this.player)return;this._frameCamera(this.sceneData?.environment)}
  async start(){this._discoverPlayer(true);this._playerLoop();this._actorLoop();this.ui.onStatus?.('scene','unresolved','waiting player')}
  _staticPreviewEnvelope(raw,zoneId){
    const source=raw?.static&&Array.isArray(raw.static.terrains)?raw.static:(raw||{}),staticData=JSON.parse(JSON.stringify(source));
    const entities=staticData.entities&&typeof staticData.entities==='object'?staticData.entities:(staticData.entities={});
    for(const warp of Array.isArray(entities.warps)?entities.warps:[]){
      if(!warp||warp.world)continue;
      const x=finite(warp.x_world),z=finite(warp.y_world),y=finite(warp.z)??0;
      if(x!=null&&z!=null)warp.world={x,y,z};
    }
    const positions=[];
    const collect=item=>{const w=item?.world;if(w&&finite(w.x)!=null&&finite(w.z)!=null)positions.push({x:finite(w.x),y:finite(w.y)??0,z:finite(w.z)})};
    (staticData.terrains||[]).forEach(collect);(staticData.buildings||[]).forEach(collect);(entities.warps||[]).forEach(collect);
    const bounds=positions.length?{minX:Math.min(...positions.map(p=>p.x)),maxX:Math.max(...positions.map(p=>p.x)),minZ:Math.min(...positions.map(p=>p.z)),maxZ:Math.max(...positions.map(p=>p.z))}:{minX:0,maxX:0,minZ:0,maxZ:0};
    const supplied=raw?.scene_origin&&finite(raw.scene_origin.x)!=null&&finite(raw.scene_origin.z)!=null?raw.scene_origin:null;
    const origin=supplied?{x:finite(supplied.x),y:finite(supplied.y)??0,z:finite(supplied.z),source:supplied.source||'static_preview_bounds'}:{x:(bounds.minX+bounds.maxX)/2,y:0,z:(bounds.minZ+bounds.maxZ)/2,source:'static_preview_bounds'};
    const suppliedPlayer=raw?.player&&typeof raw.player==='object'?raw.player:null;
    const player=suppliedPlayer||{status:'candidate',confidence:'candidate',source:'static_rom_preview',zone_id:zoneId,frame:null,coordinate_space:'gen5-field-world-v1',grid:{x:Math.floor(origin.x/TILE),y:0,z:Math.floor(origin.z/TILE)},world:{x:origin.x,y:origin.y,z:origin.z},orientation:{face_dir_raw:null,facing:'Unresolved',verified:false},locomotion:{phase:'StaticPreview',semantic_state:'PreviewOnly'}};
    player.zone_id=Number(player.zone_id??zoneId);player.status=player.status||'candidate';player.confidence=player.confidence||'candidate';player.source=player.source||'static_rom_preview';
    if(!player.world||finite(player.world.x)==null||finite(player.world.z)==null)player.world={x:origin.x,y:origin.y,z:origin.z};
    if(!player.grid||!Number.isInteger(Number(player.grid.x))||!Number.isInteger(Number(player.grid.z)))player.grid={x:Math.floor(player.world.x/TILE),y:0,z:Math.floor(player.world.z/TILE)};
    const matrixId=staticData.matrix?.matrix_id??staticData.matrix?.id??'unknown',key=raw?.render_key||`zone:${zoneId}:matrix:${matrixId}:static-preview`;
    return {...raw,format:'black2-world3d-scene/v6',status:'candidate',confidence:'candidate',preview_only:true,zone_id:Number(zoneId),scene_key:raw?.scene_key||key,render_key:key,scene_origin:origin,player,static:staticData,environment:raw?.environment||staticData.environment||'exterior',coordinate_space:'gen5-field-world-v1'};
  }
  async loadStaticZone(zoneId){
    const id=Number(zoneId);if(!Number.isInteger(id)||id<0)throw new Error('Zone must be a non-negative integer');
    if(this.staticPreviewRequest)return this.staticPreviewRequest;
    const req=(async()=>{this.staticPreview=true;this.staticPreviewZone=id;this.ui.onStatus?.('player','candidate',`static preview Zone ${id}`);this.ui.onStatus?.('scene','candidate',`loading static Zone ${id}`);try{
      const raw=await getJSON(this.connectedWorld?`${API}/scene/connected/zone/${encodeURIComponent(id)}`:`${API}/scene/zone/${encodeURIComponent(id)}`,10000),data=this._staticPreviewEnvelope(raw,id),key=data.render_key||data.scene_key;
      this.player=data.player;this.lastPlayerFrame=null;this.unresolvedPlayerSamples=0;this.playerMotion.moving=false;
      const mounted=await this._rebuildStaticAtomic(data,key);if(!mounted)throw new Error('static Zone scene was superseded');
      this.sceneData=data;this.ui.onPlayer?.(this.player);this.ui.onScene?.(data);this.ui.onStatus?.('scene','candidate',`static preview Zone ${id}`);return true;
    }catch(error){this.staticPreview=false;this.staticPreviewZone=null;this.ui.onStatus?.('scene','degraded',`static preview: ${error.message||error}`);throw error}
    })();this.staticPreviewRequest=req;try{return await req}finally{if(this.staticPreviewRequest===req)this.staticPreviewRequest=null}
  }
  async exitStaticPreview(){
    if(!this.staticPreview)return this.refreshScene(true);
    this.staticPreview=false;this.staticPreviewZone=null;this.mountedSceneKey=null;this.player=null;this.sceneData=null;this._clearActors();this._disposeRoot(this.staticRoot);this._disposeRoot(this.referenceRoot);this._disposeRoot(this.entranceRoot);this._disposeNavigationVisuals();this.ui.onStatus?.('scene','unresolved','returning to live PlayerRuntime');
    await this._discoverPlayer(true);return this.refreshScene(true);
  }
  async _discoverPlayer(force=false){if(this.playerDiscoveryRequest)return this.playerDiscoveryRequest;const now=performance.now();if(!force&&now-this.lastPlayerDiscoveryAt<750)return null;this.lastPlayerDiscoveryAt=now;const req=(async()=>{try{this.ui.onStatus?.('player','candidate','discovering runtime');const p=await getJSON('/api/v1/player/runtime',14000);this.applyPlayer(p,'runtime');this.ui.onStatus?.('player',p.status||'unresolved',p.frame!=null?`f${p.frame}`:'discovery complete');return p}catch(e){this.ui.onStatus?.('player','degraded',`discovery: ${e.message}`);return null}})();this.playerDiscoveryRequest=req;try{return await req}finally{if(this.playerDiscoveryRequest===req)this.playerDiscoveryRequest=null}}
  async _playerLoop(){while(!this.disposed){try{const p=await getJSON(`${API}/player/live`,2200);if(!this.staticPreview){this.applyPlayer(p,'live');this.ui.onStatus?.('player',p.status||'unresolved',`f${p.frame??'—'}`);if(p.status==='unresolved')this._discoverPlayer(false)}else this.ui.onStatus?.('player','candidate',`static preview Zone ${this.staticPreviewZone}`)}catch(e){if(!this.staticPreview){this.ui.onStatus?.('player','degraded',e.message);this._discoverPlayer(false)}}await sleep(PLAYER_POLL_MS)}}
  async _actorLoop(){while(!this.disposed){await sleep(ACTOR_POLL_MS);if(!this.showActors)continue;try{const r=await getJSON(`${LAB}/actors/live`,3000);await this.applyActors(r.actors||[])}catch(e){this.ui.onStatus?.('actors','degraded',e.message)}}}
  _markSceneRefreshPending(delay=0,force=false){this.sceneRefreshPending=true;this.sceneRefreshForce=this.sceneRefreshForce||force;this.sceneRefreshDelay=Math.max(this.sceneRefreshDelay||0,delay||0)}
  async refreshScene(force=false){if(this.staticPreview){if(force&&this.staticPreviewZone!=null)return this.loadStaticZone(this.staticPreviewZone);return true}if(this.sceneRequest){this._markSceneRefreshPending(0,force);return this.sceneRequest}if(this.sceneRefreshTimer){clearTimeout(this.sceneRefreshTimer);this.sceneRefreshTimer=null;}const req=this._refreshScene(force);this.sceneRequest=req;try{return await req}finally{if(this.sceneRequest===req){this.sceneRequest=null;if(this.sceneRefreshPending&&!this.disposed){const nextForce=this.sceneRefreshForce,delay=this.sceneRefreshDelay;this.sceneRefreshPending=false;this.sceneRefreshForce=false;this.sceneRefreshDelay=0;this.sceneRefreshTimer=setTimeout(()=>{this.sceneRefreshTimer=null;this.refreshScene(nextForce).catch(e=>this.ui.onStatus?.('scene','degraded',e.message))},delay)}}}}
  async _refreshScene(force){if(this.staticPreview)return true;if(!this.player||this.player.status==='unresolved'){if(force)await this._discoverPlayer(true);if(!this.player||this.player.status==='unresolved'){this._markSceneRefreshPending(600,force);return false}}const requestedZone=this.player.zone_id,sceneUrl=this.connectedWorld?`${API}/scene/connected/current`:`${API}/scene/current`,data=await getJSON(sceneUrl,8000);if(data.status==='unresolved'){this.ui.onStatus?.('scene','unresolved',`waiting for Zone ${requestedZone??'—'}`);this._markSceneRefreshPending(600,force);this._discoverPlayer(false);return false}const currentZone=this.player?.zone_id;if((requestedZone!=null&&currentZone!==requestedZone)||(currentZone!=null&&data.zone_id!=null&&data.zone_id!==currentZone)){this._markSceneRefreshPending(250,force);this.ui.onStatus?.('scene','candidate',`discarded stale Zone ${data.zone_id??'—'} response`);return false}const key=data.render_key||data.scene_key||`zone:${data.zone_id}`;if(force||key!==this.mountedSceneKey){const mounted=await this._rebuildStaticAtomic(data,key);if(!mounted)return false}this.sceneData=data;this.ui.onStatus?.('scene',data.status||'candidate',key);this.ui.onScene?.(data);return true}
  _display(p,origin=this.origin){return new THREE.Vector3(finite(p?.x)??0,finite(p?.y)??0,finite(p?.z)??0).sub(origin)}
  _disposeRoot(root){const geometries=new Set(),materials=new Set(),textures=new Set();root?.traverse?.(o=>{if(o.geometry)geometries.add(o.geometry);const ms=o.material?(Array.isArray(o.material)?o.material:[o.material]):[];for(const m of ms.filter(Boolean)){materials.add(m);if(m.map)textures.add(m.map)}});for(const t of textures)t.dispose?.();for(const m of materials)m.dispose?.();for(const g of geometries)g.dispose?.();root?.clear?.()}
  _semanticLabel(text,color='#8be9ff'){
    if(typeof document==='undefined')return null;
    const canvas=document.createElement('canvas'),ctx=canvas.getContext('2d');if(!ctx)return null;
    const value=String(text||'').slice(0,48),font='bold 22px ui-monospace,Consolas,monospace';ctx.font=font;const width=Math.max(96,Math.ceil(ctx.measureText(value).width+24));canvas.width=width;canvas.height=34;ctx.font=font;ctx.fillStyle='rgba(5,14,23,.88)';ctx.fillRect(0,0,width,34);ctx.strokeStyle=color;ctx.lineWidth=2;ctx.strokeRect(1,1,width-2,32);ctx.fillStyle=color;ctx.textBaseline='middle';ctx.fillText(value,12,17);
    const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;texture.minFilter=THREE.LinearFilter;const sprite=new THREE.Sprite(new THREE.SpriteMaterial({map:texture,transparent:true,depthTest:false,depthWrite:false}));sprite.scale.set(width/3.8,9,1);sprite.renderOrder=1308;sprite.userData={kind:'semantic_label',presentation_only:true,text:value};return sprite;
  }
  _renderEntrances(entities,origin,buildings=[]){
    this._disposeRoot(this.entranceRoot);
    const warps=Array.isArray(entities?.warps)?entities.warps:[],warpWorlds=[];
    for(const warp of warps){
      const raw=warp?.world||{};
      const widthTiles=Math.max(1,Number(warp.width)||1);
      const heightTiles=Math.max(1,Number(warp.height)||1);
      const rawX=finite(raw.x)??finite(warp?.x_world);
      const rawY=finite(raw.y)??finite(warp?.z)??0;
      const rawZ=finite(raw.z)??finite(warp?.y_world);
      if(rawX==null||rawZ==null)continue;
      // Prefer the backend's explicit semantic center.  The fallback keeps
      // old scene payloads compatible: ROM coords are the first tile of the
      // footprint, while the orange marker is centered on its middle tile.
      const semanticWorld={x:rawX,y:rawY,z:rawZ};
      const exportedCenter=warp?.display_world_center||warp?.semantic_entry_world;
      const displayWorld=exportedCenter&&finite(exportedCenter.x)!=null&&finite(exportedCenter.z)!=null
        ? {x:finite(exportedCenter.x),y:finite(exportedCenter.y)??rawY,z:finite(exportedCenter.z)}
        : {x:rawX+((widthTiles-1)*TILE*.5),y:rawY,z:rawZ+((heightTiles-1)*TILE*.5)};
      // Keep the ROM anchor intact.  The marker is presentation-centered,
      // while navigation receives the same center as an explicit semantic
      // entry point for a multi-tile warp.
      warpWorlds.push({warp,world:displayWorld,semanticWorld:displayWorld});
      const group=new THREE.Group();group.position.copy(this._display(displayWorld,origin));group.name=`warp-${warp.id}`;group.userData={kind:'warp',warp,semantic_world:displayWorld,display_world_center:displayWorld,rom_anchor_world:semanticWorld};
      const width=widthTiles*TILE;
      const ring=new THREE.Mesh(new THREE.RingGeometry(Math.max(4,width*.32),Math.max(5,width*.42),24),new THREE.MeshBasicMaterial({color:0xffd166,transparent:true,opacity:.9,side:THREE.DoubleSide,depthTest:false,depthWrite:false}));
      // If raw warp Y is 0 or unstated, use the player elevation or building elevation as reasonable fallback
      const fallbackY=group.position.y>0?group.position.y:(finite(this.player?.world?.y)??16);
      const surfaceY=this._terrainSurfaceY?this._terrainSurfaceY(group.position.x,group.position.z,fallbackY):fallbackY;
      group.position.y=surfaceY;ring.rotation.x=-Math.PI/2;ring.position.y=1.2;ring.renderOrder=1100;group.add(ring);
      const label=this._semanticLabel(`W${warp.id} → ${warp.target_zone_or_map_raw??'?'}`,'#ffd166');if(label){label.position.set(0,10,0);group.add(label)}
      this.entranceRoot.add(group);
    }
    const doorBuildings=(buildings||[]).filter(building=>building?.door_uid!=null&&building.world);
    this.diag.doors_total=doorBuildings.length;this.diag.doors_rendered=0;this.diag.door_positions=[];
    for(const building of doorBuildings){
      if(building?.door_uid==null||!building.world)continue;
      const group=new THREE.Group(),doorWorld=this._doorWorld(building);group.position.copy(this._display(doorWorld,origin));group.rotation.y=(finite(building.rotation_degrees)??0)*Math.PI/180;group.name=`door-${building.id||building.uid}`;
      const nearest=warpWorlds.map(item=>({...item,distance:Math.hypot(item.world.x-doorWorld.x,item.world.z-doorWorld.z)})).sort((a,b)=>a.distance-b.distance)[0],warpCandidate=nearest&&nearest.distance<=128?nearest:null;
      group.userData={kind:'door',building,warp_candidate:warpCandidate?{
        ...(warpCandidate.warp||{}),
        display_world_center:warpCandidate.world,
        semantic_entry_world:warpCandidate.semanticWorld,
      }:null,semantic_overlay:true};
      const width=18,height=32,frameMat=new THREE.LineBasicMaterial({color:0x66d9ef,transparent:true,opacity:.98,depthTest:false,depthWrite:false});
      const frame=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(width,height,2)),frameMat);frame.position.y=Math.max(8,height*.5);frame.renderOrder=1101;
      const face=new THREE.Mesh(new THREE.PlaneGeometry(width*.82,height*.82),new THREE.MeshBasicMaterial({color:0x22b8cf,transparent:true,opacity:.27,side:THREE.DoubleSide,depthTest:false,depthWrite:false}));face.position.set(0,height*.5,0);face.renderOrder=1100;
      const surfaceY=this._terrainSurfaceY?this._terrainSurfaceY(group.position.x,group.position.z,group.position.y):group.position.y,groundY=surfaceY-group.position.y;
      const threshold=new THREE.Mesh(new THREE.BoxGeometry(width*.92,.7,4),new THREE.MeshBasicMaterial({color:0x37e6ff,transparent:true,opacity:.85,depthTest:false,depthWrite:false}));threshold.position.set(0,groundY+.55,0);threshold.renderOrder=1103;
      const marker=new THREE.Mesh(new THREE.TorusGeometry(10,.85,8,32),new THREE.MeshBasicMaterial({color:0x22d3ee,transparent:true,opacity:.95,side:THREE.DoubleSide,depthTest:false,depthWrite:false}));marker.rotation.x=-Math.PI/2;marker.position.y=groundY+.9;marker.renderOrder=1102;
      const arrow=new THREE.Mesh(new THREE.ConeGeometry(2.8,8,4),new THREE.MeshBasicMaterial({color:0xfff3a3,transparent:true,opacity:.96,depthTest:false,depthWrite:false}));arrow.rotation.x=Math.PI/2;arrow.position.set(0,groundY+1.7,8);arrow.renderOrder=1104;
      const labelText=`D${building.door_uid}${warpCandidate?` · W${warpCandidate.warp.id}→${warpCandidate.warp.target_zone_or_map_raw??'?'}`:''}`,label=this._semanticLabel(labelText,'#66d9ef');if(label){label.position.set(0,height+13,0);group.add(label)}
      group.add(face,frame,threshold,marker,arrow);this.entranceRoot.add(group);this.diag.doors_rendered++;
      if(warpCandidate){
        const a=new THREE.Vector3(group.position.x,groundY+1.1,group.position.z),b=this._display(warpCandidate.world,origin);b.y=(this._terrainSurfaceY?this._terrainSurfaceY(b.x,b.z,b.y):b.y)+1.1;
        const line=new THREE.Line(new THREE.BufferGeometry().setFromPoints([a,b]),new THREE.LineBasicMaterial({color:0x5ee7ff,transparent:true,opacity:.55,depthTest:false,depthWrite:false}));line.renderOrder=1099;line.userData={kind:'door_warp_link',presentation_only:true,door_uid:building.door_uid,warp_id:warpCandidate.warp.id};this.entranceRoot.add(line);
      }
      this.diag.door_positions.push({id:building.id||building.uid,door_uid:building.door_uid,world:{x:doorWorld.x,y:doorWorld.y,z:doorWorld.z},warp_candidate:warpCandidate?{id:warpCandidate.warp.id,target_zone:warpCandidate.warp.target_zone_or_map_raw,distance_world:Math.round(warpCandidate.distance*100)/100}:null,source:'building.world + rotated door_offset',render:'semantic_overlay_no_independent_rom_mesh'});
    }
    this.entranceRoot.visible=true;
  }
  _doorWorld(building){
    const base=building?.world||{},off=building?.door_offset||{};
    const rot=(Number(building?.rotation_degrees)||0)*Math.PI/180;
    // Gen-5 door_offset.x anchors to the tile edge (+16).
    // The door geometry is center-anchored with width 16 (1 tile).
    // Compensate by half a tile (-8 units) in local space so the door frame
    // centers exactly on the interaction tile [X, X+16].
    const ox=(Number(off.x)||0)-8,oz=Number(off.z)||0;
    return{x:(Number(base.x)||0)+ox*Math.cos(rot)-oz*Math.sin(rot),y:(Number(base.y)||0)+(Number(off.y)||0),z:(Number(base.z)||0)+ox*Math.sin(rot)+oz*Math.cos(rot)};
  }
  _clipTerrainToCell(root,item,origin,span){
    const center=this._display(item.world,origin),half=span*.5;
    const planes=[
      new THREE.Plane(new THREE.Vector3(1,0,0),-(center.x-half)),
      new THREE.Plane(new THREE.Vector3(-1,0,0),center.x+half),
      new THREE.Plane(new THREE.Vector3(0,0,1),-(center.z-half)),
      new THREE.Plane(new THREE.Vector3(0,0,-1),center.z+half),
    ];
    root.traverse(o=>{
      if(!o.isMesh||!o.material)return;
      const source=Array.isArray(o.material)?o.material:[o.material];
      const clipped=source.map(m=>{const copy=m.clone();copy.fog=false;copy.clippingPlanes=planes;copy.clipIntersection=false;copy.needsUpdate=true;return copy});
      o.material=Array.isArray(o.material)?clipped:clipped[0];
    });
  }
  async _rebuildStaticAtomic(data,key){const started=performance.now(),generation=++this.loadingGeneration,nextRoot=new THREE.Group(),nextOrigin=new THREE.Vector3(finite(data.scene_origin?.x)??0,0,finite(data.scene_origin?.z)??0);const st=data.static||{},terrain=st.terrains||[],buildings=st.buildings||[],cache=new Map();this.diag=this._newDiagnostics();this.diag.scene_key=key;this.diag.terrain_total=terrain.length;this.diag.buildings_total=buildings.length;this.ui.onProgress?.(0,terrain.length+buildings.length,'loading');let done=0;
    const load=async(url,kind,id)=>{if(cache.has(url))return cache.get(url);const p=new Promise((resolve,reject)=>this.loader.load(url,g=>{g.scene.traverse(o=>{if(o.isMesh){o.frustumCulled=true;const ms=Array.isArray(o.material)?o.material:[o.material];for(const m of ms.filter(Boolean)){m.side=THREE.DoubleSide;m.fog=false;if(m.map){m.map.colorSpace=THREE.SRGBColorSpace;m.map.magFilter=THREE.NearestFilter;}m.needsUpdate=true}}});resolve(g.scene)},undefined,reject));cache.set(url,p);try{return await p}catch(e){this.diag.failed_assets.push({kind,id,url,error:String(e.message||e)});throw e}};
    const pool=async(items,n,fn)=>{let i=0;await Promise.all(Array.from({length:Math.min(n,items.length)},async()=>{while(i<items.length){const idx=i++;await fn(items[idx])}}))};
    await pool(terrain,3,async item=>{if(generation!==this.loadingGeneration)return;try{if(!item.asset_url){nextRoot.add(this._terrainCoordinateFallback(item,nextOrigin,data));this.diag.terrain_fallback++;return}const base=await load(item.asset_url,'terrain',item.id);if(generation!==this.loadingGeneration)return;const o=cloneSkeleton(base);o.position.copy(this._display(item.world,nextOrigin));this._clipTerrainToCell(o,item,nextOrigin,finite(st.chunk_span_world)??512);o.name=item.id;o.userData={kind:'terrain',item};nextRoot.add(o);o.updateMatrixWorld(true);this.diag.terrain_loaded++}catch{this.diag.terrain_failed++;nextRoot.add(this._terrainCoordinateFallback(item,nextOrigin,data));this.diag.terrain_fallback++}finally{this.ui.onProgress?.(++done,terrain.length+buildings.length,'terrain')}});
    await pool(buildings,2,async item=>{if(generation!==this.loadingGeneration)return;try{const base=await load(item.asset_url,'building',item.id||item.uid);if(generation!==this.loadingGeneration)return;const o=cloneSkeleton(base);o.position.copy(this._display(item.world,nextOrigin));o.rotation.y=(finite(item.rotation_degrees)??0)*Math.PI/180;o.name=item.id||`building-${item.uid}`;o.userData={kind:'building',uid:item.uid,door_uid:item.door_uid,item};nextRoot.add(o);o.updateMatrixWorld(true);this.diag.buildings_loaded++}catch{this.diag.buildings_failed++}finally{this.ui.onProgress?.(++done,terrain.length+buildings.length,'building')}});
    if(generation!==this.loadingGeneration)return false;if(this.player&&this.player.status!=='unresolved'&&this.player.zone_id!=null&&data.zone_id!=null&&this.player.zone_id!==data.zone_id){this._markSceneRefreshPending(250,false);return false}const old=this.staticRoot;this.worldRoot.remove(old);this.staticRoot=nextRoot;this.worldRoot.add(this.staticRoot);this._disposeRoot(old);this.clearCellSelection();this.origin.copy(nextOrigin);this.mountedSceneKey=key;this.sceneData=data;this.worldRoot.updateMatrixWorld(true);this._updateReferenceGrid(terrain,nextOrigin);this._renderEntrances(data.static?.entities,nextOrigin,buildings);this.diag.scene_load_ms=Math.round(performance.now()-started);this.scene.fog.density=this.connectedWorld?.00008:(data.environment==='interior'?.0012:.00052);if(this.player){const d=this._display(this.player.world),m=this.playerMotion,now=performance.now();this.playerDisplay.copy(d);this.playerTarget.copy(d);this.playerAnchor.position.copy(d);m.from.copy(d);m.to.copy(d);m.startedAt=now;m.duration=1;m.moving=false;m.sampleAt=now}await this._ensurePlayerVisual();this._clearActors();this._frameCamera(data.environment);this.ui.onProgress?.(done,terrain.length+buildings.length,'ready');this._emitDiag();return true}
  _updateReferenceGrid(terrain,origin){this._disposeRoot(this.referenceRoot);const span=Math.max(TILE,finite(this.sceneData?.static?.chunk_span_world)??512),divisions=Math.max(1,Math.round(span/TILE));for(const item of terrain){const pos=this._display(item.world,origin),grid=new THREE.GridHelper(span,divisions,0x7fc9e8,0x365f78);grid.position.set(pos.x,finite(item.world?.y)??0,pos.z);for(const material of (Array.isArray(grid.material)?grid.material:[grid.material])){material.transparent=true;material.opacity=.24;material.depthWrite=false}grid.renderOrder=-10;grid.name=`${item.id}-tile-grid`;grid.userData={kind:'reference_grid',tile_size:TILE,span_world:span};this.referenceRoot.add(grid)}// A whole-chunk GridHelper is flat by definition and cannot follow indoor stairs or raised floors. Keep it as an opt-in evidence aid; per-cell hover/locked frames are the surface-conforming selection overlay.
    this.referenceRoot.visible=!!this.showDebug}
  _terrainCoordinateFallback(item,origin,data){
    // A missing GLB must never look like real map geometry.  The old blue
    // plane made failed conversions appear as phantom platforms that do not
    // exist in the NDS renderer.  Keep an empty diagnostic node so counters
    // and picking can still identify the failed cell; visual fallback is
    // intentionally opt-in through the asset diagnostics instead.
    const group=new THREE.Group();group.name=`${item.id}-coordinate-fallback`;group.position.copy(this._display(item.world,origin));group.userData={kind:'terrain',item,asset_mode:'coordinate_fallback',rendered:false};
    group.visible=false;
    return group;
  }
  async _ensurePlayerVisual(){if(this.playerVisual)return;let meta=null;try{meta=await getJSON(`${API}/player/asset/meta?gender=${encodeURIComponent(this.gender)}`,3000)}catch{}this.playerMeta=meta;
    if(meta?.resource_kind==='nsbtx_billboard'){this.billboardStep=DIR_STEP[meta.registry?.sprite_controller_type]||0;const frame=(this.player?.orientation?.face_dir_raw??1)*this.billboardStep;try{const tex=await this._spriteTexture(frame);const s=new THREE.Sprite(new THREE.SpriteMaterial({map:tex,transparent:true,alphaTest:.1}));s.scale.set(32,32,1);s.center.set(.5,0);this.playerVisual=s;this.playerVisualMode='original_billboard';this.lastBillboardFrame=frame;this.playerAnchor.add(s);this.ui.onActorMode?.(this.playerVisualMode,meta);this.diag.player_mode=this.playerVisualMode;this._emitDiag();return}catch(e){this.diag.failed_assets.push({kind:'player_sprite',url:`${API}/player/asset/sprite/${frame}.png`,error:String(e.message||e)})}}
    this.playerVisual=this._pixelHero(0x4e8bd9);this.playerVisualMode='diagnostic_fallback';this.playerAnchor.add(this.playerVisual);this.diag.player_mode=this.playerVisualMode;this.ui.onActorMode?.(this.playerVisualMode,meta);this._emitDiag()}
  async _spriteTexture(frame){const k=`p:${this.gender}:${frame}`;if(this.spriteCache.has(k))return this.spriteCache.get(k);const p=new THREE.TextureLoader().loadAsync(`${API}/player/asset/sprite/${frame}.png?gender=${encodeURIComponent(this.gender)}`).then(t=>{t.colorSpace=THREE.SRGBColorSpace;t.magFilter=THREE.NearestFilter;t.minFilter=THREE.NearestFilter;return t});this.spriteCache.set(k,p);return p}
  async _updatePlayerFrame(p,movingOverride=null,faceOverride=null,walkPhase=0){if(this.playerVisualMode!=='original_billboard'||!this.playerVisual?.material)return;const step=this.billboardStep||0,face=faceOverride??p.orientation?.face_dir_raw??1,moving=movingOverride??p.locomotion?.phase==='Moving',anim=step>1&&moving?(step===2?(walkPhase%2===0?1:0):1+(walkPhase%(step-1))):0,frame=face*step+anim;if(frame===this.lastBillboardFrame)return;try{this.playerVisual.material.map=await this._spriteTexture(frame);this.playerVisual.material.needsUpdate=true;this.lastBillboardFrame=frame}catch{}}
  _pixelHero(color){const g=new THREE.Group(),body=new THREE.Mesh(new THREE.BoxGeometry(9,14,6),new THREE.MeshLambertMaterial({color})),head=new THREE.Mesh(new THREE.BoxGeometry(9,9,8),new THREE.MeshLambertMaterial({color:0xe6c39c}));body.position.y=9;head.position.y=20;g.add(body,head);return g}
  applyPlayer(p,source='live'){
    if(!p)return;
    if(this.staticPreview&&source!=='static-preview')return;
    if(p.status==='unresolved'){
      // Keep the last resolved sample while the bridge is re-discovering the
      // actor after a warp.  Dropping the sample is intentional, but the
      // unresolved edge must schedule discovery so the following resolved
      // Zone can still drive a scene refresh.
      this.unresolvedPlayerSamples++;
      if(source==='live'&&(this.unresolvedPlayerSamples===1||this.unresolvedPlayerSamples%4===0))this._discoverPlayer(false);
      return;
    }
    this.unresolvedPlayerSamples=0;
    const previous=this.player,first=!previous,zoneChanged=!!(previous&&p.zone_id!==previous.zone_id),now=performance.now(),d=this._display(p.world),m=this.playerMotion;
    const prevWorld=previous?.world||{},pwX=finite(prevWorld.x),pwY=finite(prevWorld.y),pwZ=finite(prevWorld.z),nwX=finite(p.world?.x),nwY=finite(p.world?.y),nwZ=finite(p.world?.z);
    const worldJump=!!(previous&&pwX!=null&&pwY!=null&&pwZ!=null&&nwX!=null&&nwY!=null&&nwZ!=null&&Math.hypot(nwX-pwX,nwY-pwY,nwZ-pwZ)>96);
    const prevChunk=previous?.chunk||{},nextChunk=p.chunk||{},chunkChanged=!!(previous&&('index' in prevChunk||'index' in nextChunk)&&(`${prevChunk.index??''}:${prevChunk.x??''}:${prevChunk.z??''}`!==`${nextChunk.index??''}:${nextChunk.x??''}:${nextChunk.z??''}`));
    const hadPlayerFrame=this.lastPlayerFrame!=null;
    this.player=p;this.playerTarget.copy(d);
    if(!hadPlayerFrame||first||zoneChanged||worldJump){this.playerDisplay.copy(d);this.playerAnchor.position.copy(d);m.from.copy(d);m.to.copy(d);m.startedAt=now;m.duration=1;m.moving=false;m.sampleAt=now;m.sampleFrame=p.frame}
    else {const dx=d.x-m.to.x,dz=d.z-m.to.z,dy=d.y-m.to.y,dist=Math.hypot(dx,dy,dz),elapsed=Math.max(16,now-(Number(m.sampleAt)||now-180)),rawFace=finite(p.orientation?.face_dir_raw);m.from.copy(this.playerDisplay);m.to.copy(d);m.startedAt=now;m.duration=Math.max(90,Math.min(320,elapsed*.92));m.moving=dist>.15;m.face=Math.max(0,Math.min(3,rawFace??1));m.sampleAt=now;m.sampleFrame=p.frame;if(m.moving)m.face=Math.abs(dx)>Math.abs(dz)?(dx<0?2:3):(dz<0?0:1);else{this.playerDisplay.copy(d);m.from.copy(d)}if(dist>96){m.from.copy(d);this.playerDisplay.copy(d);this.playerAnchor.position.copy(d);m.moving=false;m.duration=1}}
    this.lastPlayerFrame=p.frame??this.lastPlayerFrame;
    const yaw=finite(p.orientation?.yaw_degrees_if_model_forward_is_south);if(yaw!=null&&this.playerVisualMode!=='original_billboard')this.playerAnchor.rotation.y=yaw*Math.PI/180;this._updatePlayerFrame(p,m.moving,m.moving?m.face:null,0);this.ui.onPlayer?.(p);
    if(first||zoneChanged||worldJump||chunkChanged)this.refreshScene(zoneChanged||worldJump).catch(e=>this.ui.onStatus?.('scene','degraded',e.message));
  }
  _actorFrameSpec(actor,controller,moving=false,walkPhase=0,faceOverride=null){const raw=faceOverride??actor?.face_dir_raw,face=Math.max(0,Math.min(3,Number(raw)||0)),step=DIR_STEP[controller]||1;
    // Controller 2 stores one side-facing strip: frame 4 is idle and frames
    // 5/6 are the two walking poses.  East is the complete strip mirrored;
    // mirroring only one walk frame and showing the other unmirrored makes an
    // eastbound NPC appear to turn its head on every animation tick.
    if(step===2&&face>=2){
      const frame=!moving?4:(walkPhase%2===0?5:6);
      return{frame,flip:face===3?-1:1};
    }
    const base=face*step;if(!moving||step<=1)return{frame:base,flip:1};const animated=step===2?(walkPhase%2===0?1:0):1+(walkPhase%(step-1));return{frame:base+animated,flip:1}}
  async _actorSpriteTexture(code,frame){const k=`npc:${code}:${frame}`;if(this.spriteCache.has(k))return this.spriteCache.get(k);const p=new THREE.TextureLoader().loadAsync(`${LAB}/actors/${code}/sprite/${frame}.png`).then(t=>{t.colorSpace=THREE.SRGBColorSpace;t.magFilter=THREE.NearestFilter;t.minFilter=THREE.NearestFilter;return t});this.spriteCache.set(k,p);return p}
  async _updateActorFrame(anchor,actor,moving=false,walkPhase=0,faceOverride=null){const sprite=anchor?.userData?.sprite;if(!sprite?.material)return;const spec=this._actorFrameSpec(actor,Number(sprite.userData?.sprite_controller_type)||0,moving,walkPhase,faceOverride),code=sprite.userData?.obj_code;sprite.userData.actor_base_width??=Math.abs(sprite.scale.x||32);sprite.scale.x=sprite.userData.actor_base_width*spec.flip;if(sprite.userData.actor_frame===spec.frame&&sprite.userData.actor_flip===spec.flip)return;const token=`${code}:${spec.frame}:${spec.flip}`;sprite.userData.actor_frame_token=token;try{const tex=await this._actorSpriteTexture(code,spec.frame);if(sprite.userData.actor_frame_token!==token)return;sprite.material.map=tex;sprite.material.needsUpdate=true;sprite.userData.actor_frame=spec.frame;sprite.userData.actor_flip=spec.flip}catch{}}
  async _actorVisual(actor){const code=actor.obj_code_candidate??actor.model_id;const key=String(code);if(this.actorAssetCache.has(key))return this.actorAssetCache.get(key);const promise=(async()=>{try{const meta=await getJSON(`${LAB}/actors/${code}/asset`,2500);if(meta.resource_kind==='nsbtx_billboard'){const controller=Number(meta.registry?.sprite_controller_type)||0,spec=this._actorFrameSpec(actor,controller),tex=await this._actorSpriteTexture(code,spec.frame);const s=new THREE.Sprite(new THREE.SpriteMaterial({map:tex,transparent:true,alphaTest:.1}));s.scale.set(32*spec.flip,32,1);s.center.set(.5,0);s.userData={asset_mode:'candidate_original_billboard',obj_code:code,sprite_controller_type:controller,actor_frame:spec.frame};return s}if(meta.resource_kind==='nsbmd_3d'){const g=await new Promise((res,rej)=>this.loader.load(`${LAB}/actors/${code}/model.glb`,x=>res(x.scene),undefined,rej));g.userData={asset_mode:'candidate_original_glb',obj_code:code};return g}}catch{}const f=this._pixelHero(0x6db3e6);f.userData={asset_mode:'fallback_marker',obj_code:code};return f})();this.actorAssetCache.set(key,promise);return promise}
  async applyActors(actors){if(!this.showActors)return;const currentZone=finite(this.player?.zone_id),seen=new Set(),same=(Array.isArray(actors)?actors:[]).filter(a=>{if(!a||a.is_player||a.same_current_scene===false)return false;const rawZone=finite(a.zone_id),effectiveZone=finite(a.effective_zone_id_candidate);return (currentZone==null)||(rawZone===currentZone)||(effectiveZone===currentZone)||(rawZone===0&&a.same_current_scene===true)});this.diag.npc_total=same.length;this.diag.npc_original_count=0;this.diag.npc_fallback_count=0;
    // Publish the RAM snapshot before waiting for optional NPC meshes/sprites.
    // Asset decoding can take several seconds on the first poll; navigation
    // must still receive the actor tiles immediately for occupancy filtering.
    this.ui.onActors?.(same);
    for(const a of same){const id=String(a.slot??a.actor_uid);seen.add(id);let anchor=this.actorMarkers.get(id);if(!anchor){anchor=new THREE.Group();const visual=cloneSkeleton(await this._actorVisual(a));anchor.add(visual);anchor.userData={kind:'npc',actor:a,mode:visual.userData?.asset_mode||'fallback_marker'};this.actorMarkers.set(id,anchor);this.actorRoot.add(anchor)}else anchor.userData.actor=a;anchor.position.copy(this._display(a.world));if(anchor.userData.mode?.startsWith('candidate_original'))this.diag.npc_original_count++;else this.diag.npc_fallback_count++}for(const [id,o] of this.actorMarkers)if(!seen.has(id)){this.actorRoot.remove(o);o.clear();this.actorMarkers.delete(id)}this._emitDiag();this.ui.onActors?.(same)}
  _animateActors(now){for(const anchor of this.actorMarkers.values()){const u=anchor.userData,from=u.motionFrom,to=u.motionTarget;if(from&&to){const duration=Math.max(1,Number(u.motionDuration)||1),t=Math.min(1,Math.max(0,(now-(Number(u.motionStartedAt)||now))/duration));anchor.position.lerpVectors(from,to,t);const moving=!!u.actorMoving&&t<1,phase=Math.floor(now/140);this._updateActorFrame(anchor,u.actor,moving,phase,moving?u.motionFacing:null).catch(()=>{});if(t>=1)u.actorMoving=false}}}
  _terrainSurfaceY(x,z,fallback){
    if(!this.staticRoot?.children?.length)return fallback;
    // Cast ray downward from high above.
    const startY=Math.max(512,(Number(fallback)||0)+256);
    const ray=new THREE.Raycaster(new THREE.Vector3(x,startY,z),new THREE.Vector3(0,-1,0),0,4096);
    const terrain=this.staticRoot.children.filter(o=>o.userData?.kind==='terrain'&&o.visible);
    const hits=ray.intersectObjects(terrain,true).filter(h=>Number.isFinite(h.point?.y));
    if(hits.length){
      // The ray can cross several indoor slabs (floor, ceiling, upper floor).
      // The renderer already knows the elevation of the surface that was
      // picked, so choose the hit nearest to that fallback.  Selecting the
      // highest hit makes a ground cell float to the ceiling or sink one tile
      // below the floor when the fallback is zero/negative.
      const anchor=finite(fallback);
      if(anchor!=null){
        return hits.reduce((best,hit)=>{
          if(!best)return hit;
          return Math.abs(hit.point.y-anchor)<Math.abs(best.point.y-anchor)?hit:best;
        },null)?.point.y??anchor;
      }
      return hits[0].point.y;
    }
    return fallback;
  }
  _clearActors(){for(const o of this.actorMarkers.values()){this.actorRoot.remove(o);o.clear()}this.actorMarkers.clear();this.diag.npc_total=0;this.diag.npc_original_count=0;this.diag.npc_fallback_count=0;this._emitDiag()}
  _disposeNavigationVisuals(){this._disposeRoot(this.pathRoot);this.pathRoot.clear()}
  clearNavigationPath({keepTarget=false}={}){this.navigationPath=[];if(!keepTarget)this.navigationTarget=null;this._disposeNavigationVisuals();if(keepTarget&&this.navigationTarget)this._renderNavigationTarget(this.navigationTarget)}
  _navigationGridWorld(point){
    const position=point?.position&&typeof point.position==='object'?point.position:point||{};
    const samples=Array.isArray(point?.world_samples)?point.world_samples:[];
    const sample=samples.length?samples[samples.length-1]:null;
    const explicit=point?.world_position||point?.world||sample;
    if(explicit&&finite(explicit.x)!=null&&finite(explicit.z)!=null)return {x:finite(explicit.x),y:finite(explicit.y)??finite(this.player?.world?.y)??0,z:finite(explicit.z)};
    const x=finite(position.x),z=finite(position.z);if(x==null||z==null)return null;
    const gridY=finite(position.y),playerGridY=finite(this.player?.grid?.y),playerWorldY=finite(this.player?.world?.y);
    const layerOffset=playerGridY!=null&&playerWorldY!=null?playerWorldY-playerGridY*TILE:0;
    let y=gridY!=null?gridY*TILE+layerOffset:(playerWorldY??0);
    const wx=x*TILE+TILE/2,wz=z*TILE+TILE/2,surface=this._terrainSurfaceY(wx-this.origin.x,wz-this.origin.z,y-this.origin.y);
    if(Number.isFinite(surface)&&Math.abs((surface+this.origin.y)-y)<=TILE*1.5)y=surface+this.origin.y;
    return {x:wx,y,z:wz};
  }
  _routeMaterial(color,opacity=1){const material=new THREE.MeshBasicMaterial({color,transparent:opacity<1,opacity,depthTest:false,depthWrite:false});material.toneMapped=false;return material}
  _routeNode(world,color,radius=2.15){const mesh=new THREE.Mesh(new THREE.SphereGeometry(radius,12,8),this._routeMaterial(color,.96));mesh.position.copy(this._display(world));mesh.renderOrder=1302;mesh.userData={kind:'navigation_marker',presentation_only:true};return mesh}
  _routeSegment(a,b,color=0x36d9ff){const delta=new THREE.Vector3().subVectors(b,a),length=delta.length();if(length<.01)return null;const mesh=new THREE.Mesh(new THREE.CylinderGeometry(1.15,1.15,length,8,1,false),this._routeMaterial(color,.84));mesh.position.copy(a).add(b).multiplyScalar(.5);mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),delta.normalize());mesh.renderOrder=1300;mesh.userData={kind:'navigation_route',presentation_only:true};return mesh}
  _routeArrow(a,b){const delta=new THREE.Vector3().subVectors(b,a),distance=delta.length();if(distance<4)return null;const direction=delta.normalize(),length=Math.min(9,Math.max(5,distance*.48)),arrow=new THREE.ArrowHelper(direction,a.clone().lerp(b,.38),length,0xffffff,Math.min(4.8,length*.55),3.4);arrow.traverse(o=>{if(o.material){o.material.depthTest=false;o.material.depthWrite=false;o.material.transparent=true;o.material.opacity=.96}o.renderOrder=1304;o.userData={kind:'navigation_arrow',presentation_only:true}});return arrow}
  _renderNavigationTarget(target){if(target?.zone_id!=null&&this.player?.zone_id!=null&&Number(target.zone_id)!==Number(this.player.zone_id))return;const world=this._navigationGridWorld(target);if(!world)return;const shown=this._display({...world,y:world.y+2.2}),group=new THREE.Group();group.name='navigation-goal';group.userData={kind:'navigation_target',presentation_only:true};const ring=new THREE.Mesh(new THREE.TorusGeometry(5.1,.7,8,28),this._routeMaterial(0xffc857,.98));ring.rotation.x=Math.PI/2;ring.renderOrder=1305;const pin=new THREE.Mesh(new THREE.ConeGeometry(2.4,6,16),this._routeMaterial(0xffc857,.98));pin.position.y=5;pin.rotation.x=Math.PI;pin.renderOrder=1305;group.position.copy(shown);group.add(ring,pin);this.pathRoot.add(group)}
  setNavigationTarget(target){this.navigationTarget=target||null;this.clearNavigationPath({keepTarget:true});return !!this.navigationTarget}
  setNavigationPath(points,options={}){
    const zoneId=options.zoneId??this.player?.zone_id;if(Object.prototype.hasOwnProperty.call(options,'target'))this.navigationTarget=options.target||null;this._disposeNavigationVisuals();
    const source=Array.isArray(points)?points:[],visible=source.filter(p=>p?.zone_id==null||zoneId==null||Number(p.zone_id)===Number(zoneId));
    const resolved=visible.map(p=>({source:p,world:this._navigationGridWorld(p)})).filter(p=>p.world);
    this.navigationPath=visible;if(resolved.length<2){if(this.navigationTarget)this._renderNavigationTarget(this.navigationTarget);return resolved.length}
    const pts=resolved.map(p=>this._display({...p.world,y:p.world.y+3.2})),arrowStride=Math.max(1,Math.ceil((pts.length-1)/64)),nodeStride=Math.max(1,Math.ceil(pts.length/120));
    for(let i=1;i<pts.length;i++){const segment=this._routeSegment(pts[i-1],pts[i]);if(segment)this.pathRoot.add(segment);if((i-1)%arrowStride===0){const arrow=this._routeArrow(pts[i-1],pts[i]);if(arrow)this.pathRoot.add(arrow)}}
    for(let i=1;i<pts.length-1;i+=nodeStride)this.pathRoot.add(this._routeNode(resolved[i].world,0x8be9ff,1.65));
    this.pathRoot.add(this._routeNode(resolved[0].world,0x62d98b,3),this._routeNode(resolved[resolved.length-1].world,0xffc857,3.4));
    return resolved.length
  }

  _nodeForHit(hit){let node=hit?.object;while(node&&node!==this.worldRoot&&!node.userData?.kind)node=node.parent;return node&&node.userData?.kind?node:null}
  _semanticPickId(node){
    if(!node)return 'player';
    if(node.userData?.kind==='npc'){
      const actor=node.userData.actor||{};
      // slot/actor_uid zero are valid IDs.  Nullish fallback is required;
      // truthiness would misidentify slot 0 as the player.
      return actor.slot ?? actor.actor_uid ?? actor.uid ?? 'player';
    }
    const name=node.name||null;
    return name ?? node.userData?.uid ?? 'player';
  }
  _sceneBounds(){const terrain=this.sceneData?.static?.terrains||[],span=Math.max(TILE,finite(this.sceneData?.static?.chunk_span_world)??512);if(!terrain.length)return null;const xs=terrain.map(x=>finite(x.world?.x)).filter(x=>x!=null),zs=terrain.map(x=>finite(x.world?.z)).filter(x=>x!=null);if(!xs.length||!zs.length)return null;return {minX:Math.min(...xs)-span/2,maxX:Math.max(...xs)+span/2,minZ:Math.min(...zs)-span/2,maxZ:Math.max(...zs)+span/2}}
  _pointerContext(event){
    const rect=this.renderer.domElement.getBoundingClientRect();if(!rect.width||!rect.height)return null;
    const cssX=event.clientX-rect.left,cssY=event.clientY-rect.top;
    this.pointer.x=(cssX/rect.width)*2-1;this.pointer.y=-(cssY/rect.height)*2+1;
    this.raycaster.setFromCamera(this.pointer,this.camera);
    const hits=this.raycaster.intersectObjects([this.playerAnchor,this.actorRoot,this.staticRoot,this.entranceRoot],true);
    // Presentation-only actor/entrance overlays are deliberately rendered on
    // top of the ROM meshes.  A normal depth-sorted raycast can nevertheless
    // return a wall or roof first when the overlay is visually visible
    // through/above that mesh (for example an NPC standing behind a beam).
    // Prefer the semantic overlay hit whenever the pointer is inside its
    // rendered footprint, then fall back to the nearest static mesh.
    const semanticKinds=['player','npc','door','warp','trigger'];
    const pickHit=hits.find(hit=>semanticKinds.includes(this._nodeForHit(hit)?.userData?.kind))||hits.find(hit=>this._nodeForHit(hit));
    const pickNode=this._nodeForHit(pickHit),pointer={css:{x:cssX,y:cssY},device_px:{x:cssX*this.renderer.getPixelRatio(),y:cssY*this.renderer.getPixelRatio()},ndc:{x:this.pointer.x,y:this.pointer.y}};
    if(!this.player||this.player.status==='unresolved')return {hits,pickHit,pickNode,pointer,coordinate:null};
    const pickedKind=pickNode?.userData?.kind;
    const linkedWarp=pickedKind==='door'?pickNode.userData?.warp_candidate:null;
    const authoritativeWorld=pickedKind==='npc'?pickNode.userData?.actor?.world
      :pickedKind==='warp'?pickNode.userData?.semantic_world
      :linkedWarp?(linkedWarp.display_world_center||linkedWarp.semantic_entry_world||linkedWarp.world)
      :pickedKind==='player'?this.player?.world:null;
    const semanticPoint=authoritativeWorld&&finite(authoritativeWorld.x)!=null&&finite(authoritativeWorld.y)!=null&&finite(authoritativeWorld.z)!=null?this._display(authoritativeWorld):null;
    // THREE.Raycaster Intersection.point is already expressed in Three.js world space.
    // Never apply object.matrixWorld again here, including for Apicula SkinnedMesh terrain.
    const meshPoint=semanticPoint||pickHit?.point?.clone?.();
    // Empty canvas regions have no render mesh to intersect. Projecting onto
    // the player's elevation is useful only inside the loaded terrain bounds.
    const planePoint=new THREE.Vector3(),plane=new THREE.Plane(new THREE.Vector3(0,1,0),-this.playerDisplay.y),point=meshPoint||this.raycaster.ray.intersectPlane(plane,planePoint);
    if(!point)return {hits,pickHit,pickNode,pointer,coordinate:null};
    const worldX=point.x+this.origin.x,worldY=point.y+this.origin.y,worldZ=point.z+this.origin.z;
    if(!meshPoint){const bounds=this._sceneBounds();if(bounds&&(worldX<bounds.minX||worldX>bounds.maxX||worldZ<bounds.minZ||worldZ>bounds.maxZ))return {hits,pickHit,pickNode,pointer,coordinate:{status:'outside_scene',confidence:'unresolved',source:'outside_scene_bounds',world:{x:worldX,y:worldY,z:worldZ},grid:null,tile_centre:null,chunk:null,scene_origin:{x:this.origin.x,y:this.origin.y,z:this.origin.z},pointer,coordinate_contract:'GPos.x=floor(WPos.x/16); GPos.z=floor(WPos.z/16); stationary tile centre is WPos=(GPos*16)+8',note:'点击位置在场景半透明格网范围外，请在格网内点击。'}}}
    const gridX=Math.floor(worldX/TILE),gridZ=Math.floor(worldZ/TILE),gridY=finite(this.player?.grid?.y)??finite(this.player?.position?.grid?.y)??0,liveSize=finite(this.player?.chunk?.tile_size),sceneSpan=finite(this.sceneData?.static?.chunk_span_world),chunkTileSize=liveSize&&liveSize>0?liveSize:(sceneSpan&&sceneSpan/TILE>0&&Number.isInteger(sceneSpan/TILE)?sceneSpan/TILE:null),mod=(value,size)=>((value%size)+size)%size;
    const terrainHit=pickNode?.userData?.kind==='terrain',objectKind=pickNode?.userData?.kind||null,surfaceKind=terrainHit?'terrain':objectKind?'object':'terrain_projection',isGround=terrainHit;
    const tileCentreX=gridX*TILE+TILE/2,tileCentreZ=gridZ*TILE+TILE/2,centreSurface=this._terrainSurfaceY?this._terrainSurfaceY(tileCentreX-this.origin.x,tileCentreZ-this.origin.z,point.y):point.y,tileCentreY=finite(centreSurface)!=null?centreSurface+this.origin.y:worldY;
    return {hits,pickHit,pickNode,pointer,coordinate:{
      status:'resolved',confidence:meshPoint?'candidate':'probable',source:meshPoint?'rendered_mesh_surface':'player_elevation_reference_plane',surface_kind:surfaceKind,object_kind:objectKind,is_ground:isGround,
      world:{x:worldX,y:worldY,z:worldZ},grid:{x:gridX,y:gridY,z:gridZ},tile_centre:{x:tileCentreX,y:tileCentreY,z:tileCentreZ},planning_coordinate:{zone_id:this.sceneData?.zone_id??this.player?.zone_id,x:gridX,y:gridY,z:gridZ},
      chunk:chunkTileSize?{x:Math.floor(gridX/chunkTileSize),z:Math.floor(gridZ/chunkTileSize),tile_size:chunkTileSize,local_tile:{x:mod(gridX,chunkTileSize),z:mod(gridZ,chunkTileSize)}}:null,
      scene_origin:{x:this.origin.x,y:this.origin.y,z:this.origin.z},pointer,
      coordinate_contract:'GPos.x=floor(WPos.x/16); GPos.z=floor(WPos.z/16); stationary tile centre is WPos=(GPos*16)+8',
      note:terrainHit?'Terrain mesh hit; this cell is a ground planning candidate.':meshPoint?'Rendered object hit; its cell is not assumed to be standable.':'No terrain mesh was hit; the projected coordinate is informational only and cannot lock a planning cell.'
    }};
  }
  _cellKey(point){const grid=point?.grid,zone=point?.planning_coordinate?.zone_id??this.sceneData?.zone_id??this.player?.zone_id;return point?.is_ground&&grid&&Number.isInteger(Number(grid.x))&&Number.isInteger(Number(grid.y))&&Number.isInteger(Number(grid.z))?`${zone}:${Number(grid.x)}:${Number(grid.y)}:${Number(grid.z)}`:null}
  _cellSurfaceLocalY(point){const world=point?.world||{},fallback=(finite(world.y)??finite(this.player?.world?.y)??0)-this.origin.y,grid=point?.grid||{};const x=(finite(grid.x)??0)*TILE+TILE/2-this.origin.x,z=(finite(grid.z)??0)*TILE+TILE/2-this.origin.z,surface=this._terrainSurfaceY?this._terrainSurfaceY(x,z,fallback):fallback;return finite(surface)??fallback}
  _renderCellFrame(root,point,{color=0x62d0ff,opacity=.95,renderOrder=2600,locked=false}={}){if(!root||!point?.is_ground)return;const grid=point.grid||{},zone=point.planning_coordinate?.zone_id??this.sceneData?.zone_id??this.player?.zone_id,key=this._cellKey(point),group=new THREE.Group(),surfaceY=this._cellSurfaceLocalY(point),overlayOffset=.75;group.name=`${locked?'locked':'hover'}-terrain-cell-${zone}-${grid.x}-${grid.y}-${grid.z}`;group.userData={kind:locked?'locked_terrain_cell':'hover_terrain_cell',presentation_only:true,cell:{zone_id:zone,x:Number(grid.x),y:Number(grid.y),z:Number(grid.z)},source:point.surface_kind,surface_overlay:{surface_local_y:surfaceY,offset_world:overlayOffset,overlay_local_y:surfaceY+overlayOffset,depth_test:false,render_order:renderOrder},surface_point:{world:{...(point.world||{})},grid:{...grid},planning_coordinate:{...(point.planning_coordinate||{})}}};group.position.set(Number(grid.x)*TILE+TILE/2-this.origin.x,surfaceY+overlayOffset,Number(grid.z)*TILE+TILE/2-this.origin.z);const half=TILE/2-.65,points=[new THREE.Vector3(-half,0,-half),new THREE.Vector3(half,0,-half),new THREE.Vector3(half,0,half),new THREE.Vector3(-half,0,half)];const line=new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(points),new THREE.LineBasicMaterial({color,transparent:true,opacity,depthTest:false,depthWrite:false}));line.renderOrder=renderOrder;const fill=new THREE.Mesh(new THREE.PlaneGeometry(TILE-1.3,TILE-1.3),new THREE.MeshBasicMaterial({color,transparent:true,opacity:locked?.12:.055,side:THREE.DoubleSide,depthTest:false,depthWrite:false}));fill.rotation.x=-Math.PI/2;fill.renderOrder=renderOrder-1;group.add(fill,line);root.add(group);return key}
  _refreshCellOverlayHeights(){for(const root of [this.cellHoverRoot,this.cellLockedRoot])root?.children?.forEach(group=>{const point=group.userData?.surface_point;if(!point)return;const surfaceY=this._cellSurfaceLocalY(point),offset=Number(group.userData?.surface_overlay?.offset_world??.75),y=surfaceY+offset;if(Number.isFinite(y)&&Math.abs(group.position.y-y)>.01)group.position.y=y;if(group.userData?.surface_overlay){group.userData.surface_overlay.surface_local_y=surfaceY;group.userData.surface_overlay.overlay_local_y=y}})}
  _setHoverCell(point){const key=this._cellKey(point);if(!key){this._clearCellHover();return}if(key===this.hoverCell)return;this._disposeRoot(this.cellHoverRoot);this._renderCellFrame(this.cellHoverRoot,point,{color:0x62d0ff,opacity:.95,renderOrder:2600});this.hoverCell=key}
  _clearCellHover(){this._disposeRoot(this.cellHoverRoot);this.hoverCell=null}
  _setLockedCell(point){const key=this._cellKey(point);this._disposeRoot(this.cellLockedRoot);this.lockedCell=null;if(!key)return;this._renderCellFrame(this.cellLockedRoot,point,{color:0xffd166,opacity:1,renderOrder:2600,locked:true});this.lockedCell=key}
  setLockedCell(point){this._setLockedCell(point);return !!this.lockedCell}
  clearCellSelection(){this._clearCellHover();this._disposeRoot(this.cellLockedRoot);this.lockedCell=null}
  _hover(event){const result=this._pointerContext(event);if(result?.coordinate){this._setHoverCell(result.coordinate);this.ui.onPointer?.(result.coordinate,false)}else{this._clearCellHover();this.ui.onPointer?.(null,false)}}
  _pick(event){
    const result=this._pointerContext(event);if(!result)return;const point=result.coordinate;
    const node=result.pickNode;
    if(node?.userData?.kind==='terrain'&&point){
      const item=node.userData.item||{},hit=result.pickHit,matrix=hit?.object?.matrixWorld?.elements||[];
      console.info('[Terrain Click Debug]',{
        'item.cell':item?.cell,
        'item.world':item?.world,
        'point':hit?.point?{x:hit.point.x,y:hit.point.y,z:hit.point.z}:null,
        'scene_origin':{x:this.origin.x,y:this.origin.y,z:this.origin.z},
        'worldX':point.world?.x,
        'worldZ':point.world?.z,
        'grid':point.grid,
        'isSkinnedMesh':!!hit?.object?.isSkinnedMesh,
        'matrixWorldTranslation':hit?.object?{x:finite(matrix[12]),y:finite(matrix[13]),z:finite(matrix[14])}:null,
        'raycastPointSpace':'three_world'
      });
    }
    if(point){
      // A terrain mesh is only a visual surface. It becomes a locked map cell
      // only after the backend has proved a collision-valid standing tile.
      if(!point.is_ground){this._disposeRoot(this.cellLockedRoot);this.lockedCell=null}
      const kind=node?.userData?.kind||'map_point';
      const id=node?this._semanticPickId(node):`${point.grid?.x},${point.grid?.z}`;
      // Pass the hit identity alongside the geometric point.  The workbench
      // uses this to distinguish a floor click from a wall/furniture/NPC hit
      // before asking the backend to snap to a standing tile.
      const linkedWarp=node?.userData?.kind==='door'?node.userData?.warp_candidate:null;
      const navigationPick=linkedWarp?{kind:'warp',id:`warp-${linkedWarp.id}`,source_kind:'door'}:{kind,id};
      this.ui.onPointer?.(point,true,navigationPick);
    }
    if(node&&point?.status==='resolved'){// A floor click selects a logical cell, not the full terrain asset bounding box.
      if(point.is_ground){this._highlight(null);this.ui.onSelect?.({kind:'map_point',id:`${point.grid.x},${point.grid.z}`,payload:point});return;}
      this._highlight(node);const kind=node.userData.kind;const item=kind==='building'||kind==='terrain'?node.userData.item:kind==='door'?node.userData.building:kind==='npc'?node.userData.actor:kind==='warp'?{...(node.userData.warp||{}),semantic_entry_world:node.userData.semantic_world,display_world_center:node.userData.display_world_center,rom_anchor_world:node.userData.rom_anchor_world}:this.player;const payload={...(item||{}),picked_coordinate:point};this.ui.onSelect?.({kind,id:this._semanticPickId(node),payload,node});return;}
    if(point?.status==='resolved'){this._highlight(null);this.ui.onSelect?.({kind:'map_point',id:`${point.grid.x},${point.grid.z}`,payload:point})}
  }
  _highlight(node){this._disposeRoot(this.selectionRoot);if(!node)return;try{const box=new THREE.BoxHelper(node,0x62d0ff);box.material.depthTest=false;box.renderOrder=999;this.selectionRoot.add(box)}catch{}}
  setLayerVisibility(kind,visible){for(const child of this.staticRoot.children){if(child.userData?.kind===kind)child.visible=!!visible}if(kind==='terrain')this.referenceRoot.visible=!!visible&&this.showDebug;if(kind==='npc')this.setRuntimeActors(visible);if(kind==='player')this.playerAnchor.visible=!!visible;}

  _frameCamera(env){const indoor=env==='interior',d=indoor?120:240,h=indoor?145:260;this.camera.position.set(this.playerDisplay.x+90,this.playerDisplay.y+h,this.playerDisplay.z+d);this.camera.lookAt(this.playerDisplay.x,this.playerDisplay.y+12,this.playerDisplay.z);this.controls.target.copy(this.playerDisplay)}
  _animate(now){if(this.disposed)return;requestAnimationFrame(t=>this._animate(t));const m=this.playerMotion;if(m.to&&m.from&&m.moving){const t=Math.min(1,Math.max(0,(now-m.startedAt)/Math.max(1,m.duration)));this.playerDisplay.lerpVectors(m.from,m.to,t);this.playerAnchor.position.copy(this.playerDisplay);this._updatePlayerFrame(this.player, t<1,m.face,Math.floor(now/140));if(t>=1)m.moving=false}else{this.playerAnchor.position.copy(this.playerDisplay);this._updatePlayerFrame(this.player,false,null,0)}this._animateActors(now);this._refreshCellOverlayHeights();if(this.followPlayer){const indoor=this.sceneData?.environment==='interior',desired=new THREE.Vector3(this.playerDisplay.x+85,this.playerDisplay.y+(indoor?145:245),this.playerDisplay.z+(indoor?120:220));this.camera.position.lerp(desired,.08);this.camera.lookAt(this.playerDisplay.x,this.playerDisplay.y+12,this.playerDisplay.z)}else this.controls.update();if(now-this.lastRenderAt>=1000/TARGET_RENDER_FPS){this.renderer.render(this.scene,this.camera);this.lastRenderAt=now;this.fpsFrames++;if(now-this.fpsWindow>=1000){this.fps=Math.round(this.fpsFrames*1000/(now-this.fpsWindow));this.fpsFrames=0;this.fpsWindow=now;this.diag.fps=this.fps;this._emitDiag()}}}
  dispose(){this.disposed=true;this.loadingGeneration++;if(this.sceneRefreshTimer){clearTimeout(this.sceneRefreshTimer);this.sceneRefreshTimer=null}this.sceneRefreshPending=false;this.sceneRefreshForce=false;this.renderer.domElement.removeEventListener('click',this._onPick);this.renderer.domElement.removeEventListener('pointermove',this._onPointerMove);this.renderer.domElement.removeEventListener('pointerleave',this._onPointerLeave);this._disposeRoot(this.referenceRoot);this._disposeRoot(this.selectionRoot);this._disposeRoot(this.cellHoverRoot);this._disposeRoot(this.cellLockedRoot);this.renderer.dispose();this.controls.dispose()}
}
