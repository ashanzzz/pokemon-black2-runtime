import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const API = '/api/v1/map/v6';
const TILE = 16;
const DIR_STEP = [0,3,2,3,0,1,3,0,0,0,4,0,4,2,2,2,3,3,3,3,3,3,0,0,0,0,0,3,3,3,3,3,2,2,0];

const sleep = ms => new Promise(r => setTimeout(r, ms));
async function getJSON(url) {
  const r = await fetch(url, {cache:'no-store'});
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function finite(v){ return Number.isFinite(Number(v)) ? Number(v) : null; }
function esc(v){return String(v??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

export class Black2World3D {
  constructor(host, ui={}) {
    this.host = host;
    this.ui = ui;
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0a0d12);
    this.scene.fog = new THREE.FogExp2(0x0a0d12, 0.00065);
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.5, 20000);
    this.renderer = new THREE.WebGLRenderer({antialias:true, alpha:false, powerPreference:'high-performance'});
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.shadowMap.enabled = false;
    this.host.appendChild(this.renderer.domElement);

    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.enabled = false;

    this.loader = new GLTFLoader();
    this.worldRoot = new THREE.Group();
    this.staticRoot = new THREE.Group();
    this.actorRoot = new THREE.Group();
    this.debugRoot = new THREE.Group();
    this.worldRoot.add(this.staticRoot, this.actorRoot, this.debugRoot);
    this.scene.add(this.worldRoot);

    const hemi = new THREE.HemisphereLight(0xe9f2ff, 0x4a4438, 2.1);
    this.scene.add(hemi);
    const sun = new THREE.DirectionalLight(0xffffff, 2.3);
    sun.position.set(-400, 900, 450);
    this.scene.add(sun);

    this.playerAnchor = new THREE.Group();
    this.actorRoot.add(this.playerAnchor);
    this.playerVisual = null;
    this.playerVisualMode = 'none';
    this.gender = ui.gender || 'male';
    this.actorMeta = null;
    this.billboardStep = 0;
    this.lastBillboardFrame = null;
    this.playerTarget = new THREE.Vector3();
    this.playerDisplay = new THREE.Vector3();
    this.origin = new THREE.Vector3();
    this.sceneKey = null;
    this.sceneData = null;
    this.player = null;
    this.actorMarkers = new Map();
    this.modelCache = new Map();
    this.followPlayer = true;
    this.showRuntimeActors = true;
    this.showDebug = false;
    this.disposed = false;
    this.lastPlayerFrame = null;
    this.lastSceneError = null;
    this.loadingGeneration = 0;

    this._makeGrid();
    this.resize();
    addEventListener('resize', () => this.resize());
    this._animate();
  }

  resize(){
    const w=Math.max(1,this.host.clientWidth), h=Math.max(1,this.host.clientHeight);
    this.camera.aspect=w/h; this.camera.updateProjectionMatrix(); this.renderer.setSize(w,h,false);
  }

  _makeGrid(){
    const grid = new THREE.GridHelper(2048, 128, 0x435064, 0x202833);
    grid.rotation.x = 0;
    grid.position.y = 0.05;
    grid.material.transparent = true;
    grid.material.opacity = 0.32;
    grid.visible = false;
    grid.name = 'debug-grid';
    this.debugRoot.add(grid);
  }

  setDebug(v){ this.showDebug=!!v; for(const o of this.debugRoot.children)o.visible=this.showDebug; }
  setRuntimeActors(v){ this.showRuntimeActors=!!v; for(const [_,o] of this.actorMarkers)o.visible=this.showRuntimeActors; }
  setFollow(v){
    this.followPlayer=!!v;
    this.controls.enabled=!this.followPlayer;
    if(!this.followPlayer){ this.controls.target.copy(this.playerDisplay); }
  }

  async start(){
    await this.refreshScene(true);
    this._playerLoop();
    this._sceneLoop();
    this._actorsLoop();
  }

  async _sceneLoop(){
    while(!this.disposed){
      await sleep(1000);
      try{ await this.refreshScene(false); }catch(e){ this._status('scene', 'degraded', e.message); }
    }
  }
  async _playerLoop(){
    while(!this.disposed){
      try{
        const p=await getJSON(`${API}/player/live`);
        this.applyPlayer(p);
        this._status('player', p.status||'unresolved', `f${p.frame??'—'}`);
      }catch(e){ this._status('player','degraded',e.message); }
      await sleep(90);
    }
  }
  async _actorsLoop(){
    while(!this.disposed){
      await sleep(900);
      try{
        const r=await getJSON(`${API}/actors/live`);
        this.applyActors(r.actors||[]);
      }catch(e){ /* actor overlay is optional */ }
    }
  }

  _status(kind,state,detail=''){
    if(this.ui.onStatus) this.ui.onStatus(kind,state,detail);
  }

  async refreshScene(force=false){
    const q=force?'?force_identity=true':'';
    const data=await getJSON(`${API}/scene/current${q}`);
    if(data.status==='unresolved'){
      this._status('scene','unresolved',data.reason||'runtime unresolved');
      return;
    }
    this.sceneData=data;
    const key=data.scene_key;
    if(key!==this.sceneKey){
      this.sceneKey=key;
      await this._rebuildStatic(data);
      await this._ensurePlayerVisual();
    }
    this._updateInfo(data);
    this._status('scene', data.status||'resolved', `${data.environment||'—'} · Zone ${data.zone_id}`);
  }

  _canonicalToDisplay(p){
    return new THREE.Vector3(
      finite(p?.x)??0,
      finite(p?.y)??0,
      finite(p?.z)??0
    ).sub(this.origin);
  }

  async _rebuildStatic(data){
    const generation=++this.loadingGeneration;
    this._disposeGroup(this.staticRoot);
    this.staticRoot.clear();
    this._disposeActorMarkers();
    const o=data.scene_origin||{};
    this.origin.set(finite(o.x)??0, 0, finite(o.z)??0);
    const st=data.static||{};
    this.scene.fog.density = data.environment==='interior' ? 0.0013 : 0.00055;
    const terrain=st.terrains||[], buildings=st.buildings||[];
    this._progress(0, terrain.length+buildings.length, 'loading original 3D world');
    let done=0;
    await this._pool(terrain, 4, async item=>{
      if(generation!==this.loadingGeneration)return;
      try{
        const base=await this._loadModel(item.asset_url);
        const obj=base.clone(true);
        obj.name=item.id;
        obj.position.copy(this._canonicalToDisplay(item.world));
        this.staticRoot.add(obj);
      }catch(e){ console.debug('terrain source-only', item, e); }
      this._progress(++done, terrain.length+buildings.length, 'terrain/buildings');
    });
    await this._pool(buildings, 3, async item=>{
      if(generation!==this.loadingGeneration)return;
      try{
        const base=await this._loadModel(item.asset_url);
        const obj=base.clone(true);
        obj.name=item.id||`building-${item.uid}`;
        obj.position.copy(this._canonicalToDisplay(item.world));
        obj.rotation.y=(finite(item.rotation_degrees)??0)*Math.PI/180;
        this.staticRoot.add(obj);
        if(item.has_door_metadata) this._addDoorDebug(item);
      }catch(e){ console.debug('building source-only', item, e); }
      this._progress(++done, terrain.length+buildings.length, 'terrain/buildings');
    });
    this._progress(done, terrain.length+buildings.length, 'ready');
    this._frameCamera(data.environment);
  }

  async _pool(items, concurrency, fn){
    let i=0; const workers=[];
    for(let w=0;w<Math.min(concurrency,items.length);w++) workers.push((async()=>{
      while(i<items.length){const idx=i++; await fn(items[idx],idx);}
    })());
    await Promise.all(workers);
  }

  _progress(done,total,label){ if(this.ui.onProgress)this.ui.onProgress(done,total,label); }

  async _loadModel(url){
    if(this.modelCache.has(url)) return this.modelCache.get(url);
    const p=new Promise((resolve,reject)=>this.loader.load(url,g=>{
      g.scene.traverse(o=>{if(o.isMesh){o.frustumCulled=true; if(o.material){o.material.side=THREE.DoubleSide;}}});
      resolve(g.scene);
    },undefined,reject));
    this.modelCache.set(url,p);
    return p;
  }

  _addDoorDebug(item){
    const geo=new THREE.CylinderGeometry(2.2,2.2,10,10);
    const mat=new THREE.MeshBasicMaterial({color:0xff8e6e,transparent:true,opacity:.72});
    const m=new THREE.Mesh(geo,mat);m.position.copy(this._canonicalToDisplay(item.world));m.position.y+=5;m.visible=this.showDebug;
    m.userData={kind:'door',door_uid:item.door_uid};this.debugRoot.add(m);
  }

  async _ensurePlayerVisual(){
    if(this.playerVisual){this.playerAnchor.remove(this.playerVisual);this._disposeObject(this.playerVisual);this.playerVisual=null;}
    let meta=null;
    try{meta=await getJSON(`${API}/player/asset/meta?gender=${encodeURIComponent(this.gender)}`);}catch(e){/* fallback */}
    this.actorMeta=meta;
    if(meta?.resource_kind==='nsbmd_3d'){
      try{
        const base=await this._loadModel(`${API}/player/asset/model.glb?gender=${encodeURIComponent(this.gender)}`);
        this.playerVisual=base.clone(true);this.playerVisualMode='original_glb';this.playerAnchor.add(this.playerVisual);
        this._actorMode(this.playerVisualMode,meta);return;
      }catch(e){console.debug('original player model unavailable',e);}
    }
    if(meta?.resource_kind==='nsbtx_billboard'){
      const step=DIR_STEP[meta.registry?.sprite_controller_type]||0; this.billboardStep=step;
      const face=this.player?.orientation?.face_dir_raw??1;
      const frame=face*step;
      try{
        const tex=await new THREE.TextureLoader().loadAsync(`${API}/player/asset/sprite/${frame}.png?gender=${encodeURIComponent(this.gender)}`);
        tex.colorSpace=THREE.SRGBColorSpace;tex.magFilter=THREE.NearestFilter;tex.minFilter=THREE.NearestFilter;
        const mat=new THREE.SpriteMaterial({map:tex,transparent:true,alphaTest:.1});
        const s=new THREE.Sprite(mat);s.scale.set(32,48,1);s.center.set(.5,0);s.position.y=0.2;
        this.playerVisual=s;this.playerVisualMode='original_billboard';this.lastBillboardFrame=frame;this.playerAnchor.add(s);this._actorMode(this.playerVisualMode,meta);return;
      }catch(e){console.debug('original player sprite extraction unavailable',e);}
    }
    this.playerVisual=this._pixelHero();this.playerVisualMode='pixel_marker';this.playerAnchor.add(this.playerVisual);this._actorMode(this.playerVisualMode,meta);
  }

  _actorMode(mode,meta){ if(this.ui.onActorMode)this.ui.onActorMode(mode,meta); }

  _pixelHero(){
    const g=new THREE.Group();
    const body=new THREE.Mesh(new THREE.BoxGeometry(9,14,6),new THREE.MeshLambertMaterial({color:0x4e8bd9}));body.position.y=9;
    const head=new THREE.Mesh(new THREE.BoxGeometry(9,9,8),new THREE.MeshLambertMaterial({color:0xe8c6a1}));head.position.y=20;
    const cap=new THREE.Mesh(new THREE.BoxGeometry(10,3,9),new THREE.MeshLambertMaterial({color:0xb74049}));cap.position.y=25;
    const arrow=new THREE.Mesh(new THREE.ConeGeometry(2.6,7,4),new THREE.MeshBasicMaterial({color:0xffe26a}));arrow.rotation.x=Math.PI/2;arrow.position.set(0,5,8);
    g.add(body,head,cap,arrow);return g;
  }

  applyPlayer(p){
    if(!p||p.status==='unresolved')return;
    const zoneChanged=this.player&&p.zone_id!==this.player.zone_id;
    this.player=p;
    const display=this._canonicalToDisplay(p.world||{});
    this.playerTarget.copy(display);
    if(this.lastPlayerFrame==null||zoneChanged){this.playerDisplay.copy(display);this.playerAnchor.position.copy(display);}
    this.lastPlayerFrame=p.frame;
    const yaw=finite(p.orientation?.yaw_degrees_if_model_forward_is_south);
    if(yaw!=null && this.playerVisualMode!=='original_billboard') this.playerAnchor.rotation.y=yaw*Math.PI/180;
    if(this.playerVisualMode==='original_billboard') this._updateBillboardFrame(p).catch(()=>{});
    this._updatePlayerHUD(p);
    if(zoneChanged) this.refreshScene(true).catch(()=>{});
  }


  async _updateBillboardFrame(p){
    if(!this.playerVisual?.material?.map || !this.actorMeta)return;
    const face=p.orientation?.face_dir_raw??1, step=this.billboardStep||0;
    const moving=p.locomotion?.phase==='Moving';
    const anim=step>1&&moving ? (Math.floor((p.frame||0)/5)%step) : 0;
    const frame=face*step+anim;
    if(frame===this.lastBillboardFrame)return;
    const tex=await new THREE.TextureLoader().loadAsync(`${API}/player/asset/sprite/${frame}.png?gender=${encodeURIComponent(this.gender)}`);
    tex.colorSpace=THREE.SRGBColorSpace;tex.magFilter=THREE.NearestFilter;tex.minFilter=THREE.NearestFilter;
    const old=this.playerVisual.material.map;this.playerVisual.material.map=tex;this.playerVisual.material.needsUpdate=true;old?.dispose?.();this.lastBillboardFrame=frame;
  }

  applyActors(actors){
    const seen=new Set();
    for(const a of actors){
      if(a.is_player)continue;
      const id=String(a.slot??a.actor_uid??Math.random());seen.add(id);
      let m=this.actorMarkers.get(id);
      if(!m){m=this._runtimeActorMarker();this.actorMarkers.set(id,m);this.actorRoot.add(m);}
      m.position.copy(this._canonicalToDisplay(a.world));m.visible=this.showRuntimeActors;
      m.userData=a;
    }
    for(const [id,m] of this.actorMarkers)if(!seen.has(id)){this.actorRoot.remove(m);this._disposeObject(m);this.actorMarkers.delete(id);}
  }

  _runtimeActorMarker(){
    const geo=new THREE.CapsuleGeometry(4,10,3,7);const mat=new THREE.MeshLambertMaterial({color:0x7ec8ff,transparent:true,opacity:.82});
    const m=new THREE.Mesh(geo,mat);m.position.y+=9;return m;
  }

  _frameCamera(env){
    const indoor=env==='interior';
    const d=indoor?120:240,h=indoor?145:270;
    this.camera.position.set(this.playerDisplay.x+90,this.playerDisplay.y+h,this.playerDisplay.z+d);
    this.camera.lookAt(this.playerDisplay.x,this.playerDisplay.y+12,this.playerDisplay.z);
    this.controls.target.copy(this.playerDisplay);
  }

  _animate(){
    if(this.disposed)return;
    requestAnimationFrame(()=>this._animate());
    this.playerDisplay.lerp(this.playerTarget,0.34);
    this.playerAnchor.position.copy(this.playerDisplay);
    if(this.followPlayer){
      const indoor=this.sceneData?.environment==='interior';
      const desired=new THREE.Vector3(this.playerDisplay.x+85,this.playerDisplay.y+(indoor?145:250),this.playerDisplay.z+(indoor?120:225));
      this.camera.position.lerp(desired,0.075);
      const look=new THREE.Vector3(this.playerDisplay.x,this.playerDisplay.y+12,this.playerDisplay.z);
      const dir=look.clone().sub(this.camera.position).normalize();
      const current=this.camera.getWorldDirection(new THREE.Vector3());
      current.lerp(dir,0.12);this.camera.lookAt(this.camera.position.clone().add(current));
    }else this.controls.update();
    this.renderer.render(this.scene,this.camera);
  }

  _updateInfo(data){ if(this.ui.onScene)this.ui.onScene(data); }
  _updatePlayerHUD(p){ if(this.ui.onPlayer)this.ui.onPlayer(p); }

  _disposeActorMarkers(){for(const [_,m] of this.actorMarkers){this.actorRoot.remove(m);this._disposeObject(m);}this.actorMarkers.clear();}
  _disposeGroup(g){for(const o of [...g.children])this._disposeObject(o);}
  _disposeObject(o){o.traverse?.(x=>{if(x.geometry)x.geometry.dispose?.();if(x.material){const ms=Array.isArray(x.material)?x.material:[x.material];for(const m of ms){if(m.map)m.map.dispose?.();m.dispose?.();}}});}
  dispose(){this.disposed=true;this.renderer.dispose();this.controls.dispose();}
}

export { esc };
