import * as THREE from 'three';
import { clone as cloneSkeleton } from 'three/addons/utils/SkeletonUtils.js';
import {Black2World3D as BaseWorld3D, esc} from '/frontend/world3d-runtime.js?base=17&navigation=2&static-preview=1&cell-grid=1&connected-map=1';

function marker(radius=6){
  const group=new THREE.Group();
  const ring=new THREE.Mesh(
    new THREE.RingGeometry(radius-1.4,radius,32),
    new THREE.MeshBasicMaterial({transparent:true,opacity:.9,side:THREE.DoubleSide,depthTest:false,depthWrite:false})
  );
  ring.rotation.x=-Math.PI/2;ring.position.y=.35;ring.renderOrder=1200;
  const dot=new THREE.Mesh(
    new THREE.CircleGeometry(1.6,20),
    new THREE.MeshBasicMaterial({transparent:true,opacity:.95,side:THREE.DoubleSide,depthTest:false,depthWrite:false})
  );
  dot.rotation.x=-Math.PI/2;dot.position.y=.37;dot.renderOrder=1201;
  group.add(ring,dot);group.userData={presentation_only:true,source:'exact FieldActor.WPos anchor'};
  return group;
}

export class Black2World3D extends BaseWorld3D{
  constructor(host,ui={}){
    super(host,ui);
    // Record the GLTF material's actual sidedness before the legacy loader
    // callback changes it. glTF doubleSided=true remains DoubleSide; ordinary
    // walls/furniture retain FrontSide after the static swap.
    const rawLoad=this.loader.load.bind(this.loader);
    this.loader.load=(url,onLoad,onProgress,onError)=>rawLoad(url,g=>{
      g.scene?.traverse?.(o=>{
        if(!o.isMesh)return;
        const ms=Array.isArray(o.material)?o.material:[o.material];
        for(const m of ms.filter(Boolean)){
          m.userData=m.userData||{};
          if(m.userData.black2OriginalSide===undefined)m.userData.black2OriginalSide=m.side;
        }
      });
      onLoad?.(g);
    },onProgress,onError);
    this.playerPositionMarker=marker(6.4);
    this.playerPositionMarker.name='player-runtime-wpos-marker';
    this.playerAnchor.add(this.playerPositionMarker);
    this.encounterRoot=new THREE.Group();
    this.encounterRoot.name='encounter-regions';
    this.encounterRoot.visible=false;
    this.encounterPayload=null;
    this.encounterSelectedId=null;
    this.worldRoot.add(this.encounterRoot);
  }


  _encounterColor(method,evidence,selected=false){
    if(selected)return 0xe8f6ff;
    if(method==='walk_regular')return 0x579562;
    if(method==='walk_double_grass')return 0x7b8447;
    if(method==='surf_candidate')return evidence==='verified'?0x4f8fb5:0x55758d;
    return 0x7b8794;
  }

  clearEncounterRegions(){
    this._disposeRoot(this.encounterRoot);
    this.encounterRoot.clear();
    this.encounterPayload=null;
    this.encounterSelectedId=null;
  }

  setEncounterRegionsVisible(value){this.encounterRoot.visible=!!value}

  setEncounterRegions(payload,{selectedRegionId=null,visible=true}={}){
    this._disposeRoot(this.encounterRoot);this.encounterRoot.clear();
    this.encounterPayload=payload||null;this.encounterSelectedId=selectedRegionId||null;
    const regions=Array.isArray(payload?.regions)?payload.regions:[];
    const fallbackY=(Number(this.player?.world?.y)||0)-this.origin.y;
    for(const region of regions){
      const tiles=Array.isArray(region?.tiles)?region.tiles:[];if(!tiles.length)continue;
      const selected=String(region.region_id)===String(selectedRegionId||'');
      const material=new THREE.MeshBasicMaterial({color:this._encounterColor(region.encounter_method,region.evidence,selected),transparent:true,opacity:selected?.48:(region.evidence==='verified'?.25:.16),side:THREE.DoubleSide,depthWrite:false,depthTest:true,polygonOffset:true,polygonOffsetFactor:-2,polygonOffsetUnits:-2});
      const geometry=new THREE.PlaneGeometry(13.4,13.4);geometry.rotateX(-Math.PI/2);
      const mesh=new THREE.InstancedMesh(geometry,material,tiles.length),matrix=new THREE.Matrix4();
      tiles.forEach((tile,index)=>{const x=Number(tile.x)*16+8-this.origin.x,z=Number(tile.z)*16+8-this.origin.z,surface=this._terrainSurfaceY?this._terrainSurfaceY(x,z,fallbackY):fallbackY,y=(Number.isFinite(surface)?surface:fallbackY)+.42;matrix.makeTranslation(x,y,z);mesh.setMatrixAt(index,matrix)});
      mesh.instanceMatrix.needsUpdate=true;mesh.renderOrder=1180;mesh.name=`encounter-region-${region.region_id}`;mesh.userData={kind:'encounter_region',region_id:region.region_id,presentation_only:true};this.encounterRoot.add(mesh);
      const rawSegments=Array.isArray(region.outline_segments)?region.outline_segments:[],positions=[];
      for(const seg of rawSegments){const x1=Number(seg.x1)*16-this.origin.x,z1=Number(seg.z1)*16-this.origin.z,x2=Number(seg.x2)*16-this.origin.x,z2=Number(seg.z2)*16-this.origin.z,mx=(x1+x2)/2,mz=(z1+z2)/2,surface=this._terrainSurfaceY?this._terrainSurfaceY(mx,mz,fallbackY):fallbackY,y=(Number.isFinite(surface)?surface:fallbackY)+.7;positions.push(x1,y,z1,x2,y,z2)}
      if(positions.length){const lineGeometry=new THREE.BufferGeometry();lineGeometry.setAttribute('position',new THREE.Float32BufferAttribute(positions,3));const lineMaterial=new THREE.LineBasicMaterial({color:this._encounterColor(region.encounter_method,region.evidence,selected),transparent:true,opacity:selected?1:.75,depthWrite:false,depthTest:true});const lines=new THREE.LineSegments(lineGeometry,lineMaterial);lines.renderOrder=1182;lines.userData={kind:'encounter_outline',region_id:region.region_id,presentation_only:true};this.encounterRoot.add(lines)}
    }
    this.encounterRoot.visible=!!visible;
  }

  _restoreOriginalMaterialSides(){
    this.staticRoot?.traverse?.(o=>{
      if(!o.isMesh)return;
      const ms=Array.isArray(o.material)?o.material:[o.material];
      for(const m of ms.filter(Boolean)){
        const side=m.userData?.black2OriginalSide;
        if(side!==undefined&&m.side!==side){m.side=side;m.needsUpdate=true;}
      }
    });
  }

  async _rebuildStaticAtomic(data,key){
    const ok=await super._rebuildStaticAtomic(data,key);
    if(ok){this._restoreOriginalMaterialSides();this.worldRoot.updateMatrixWorld(true);}
    return ok;
  }

  async applyActors(actors){
    if(!this.showActors)return;
    const sampledAt=performance.now();
    const seen=new Set();
    const same=actors.filter(a=>!a.is_player&&(a.same_current_scene===true||a.zone_id===this.player?.zone_id));
    this.diag.npc_total=same.length;this.diag.npc_original_count=0;this.diag.npc_fallback_count=0;
    for(const a of same){
      const id=String(a.slot??a.actor_uid);seen.add(id);let anchor=this.actorMarkers.get(id);
      if(!anchor){
        anchor=new THREE.Group();const visual=cloneSkeleton(await this._actorVisual(a));
        // Cached actor sprites may share a SpriteMaterial after cloning.  Each
        // live actor needs its own material because its facing frame can
        // change independently of another NPC using the same model code.
        if(visual.isSprite&&visual.material)visual.material=visual.material.clone();
        anchor.add(visual);
        const positionMarker=marker(5.2);positionMarker.name=`npc-${id}-runtime-wpos-marker`;anchor.add(positionMarker);
        const initial=this._display(a.world);
        anchor.position.copy(initial);
        anchor.userData={kind:'npc',actor:a,mode:visual.userData?.asset_mode||'fallback_marker',sprite:visual.isSprite?visual:null,motionFrom:initial.clone(),motionTarget:initial.clone(),motionStartedAt:sampledAt,motionDuration:1,lastSampleAt:sampledAt,actorMoving:false,motionFacing:Number(a.face_dir_raw)||0};
        this.actorMarkers.set(id,anchor);this.actorRoot.add(anchor);
      }
      const next=this._display(a.world),u=anchor.userData,priorTarget=u.motionTarget?.clone?.()||anchor.position.clone();
      const sampleInterval=Math.max(300,Math.min(1400,sampledAt-(Number(u.lastSampleAt)||sampledAt-900)));
      const sampleDx=next.x-priorTarget.x,sampleDz=next.z-priorTarget.z,sampleDistance=Math.hypot(sampleDx,sampleDz);
      // A 900 ms sample can land on two different animation poses while the
      // actor is standing still.  Only treat a real tile displacement as a
      // walk; otherwise FaceDir remains the stable idle direction.
      const moving=sampleDistance>=4,teleport=sampleDistance>96;
      let motionFacing=Number(a.face_dir_raw)||0;
      if(moving)motionFacing=Math.abs(sampleDx)>Math.abs(sampleDz)?(sampleDx<0?2:3):(sampleDz<0?0:1);
      anchor.userData.actor=a;
      u.motionFrom.copy(anchor.position);u.motionTarget.copy(next);u.motionStartedAt=sampledAt;u.motionDuration=moving?sampleInterval*.85:160;u.lastSampleAt=sampledAt;u.actorMoving=moving;u.motionFacing=motionFacing;
      if(teleport){anchor.position.copy(next);u.motionFrom.copy(next);u.actorMoving=false;u.motionDuration=1;}
      // Seed the new actor pose immediately.  The animation loop will advance
      // walkPhase while moving, but it must not retain a stale mirrored frame
      // from the previous direction after an idle direction change.
      await this._updateActorFrame(anchor,a,u.actorMoving,0,u.actorMoving?u.motionFacing:Number(a.face_dir_raw));
      if(anchor.userData.mode?.startsWith('candidate_original'))this.diag.npc_original_count++;else this.diag.npc_fallback_count++;
    }
    for(const [id,o] of this.actorMarkers)if(!seen.has(id)){this.actorRoot.remove(o);o.clear();this.actorMarkers.delete(id)}
    this._emitDiag();this.ui.onActors?.(same);
  }

  _doorWorld(building){
    const base=building?.world||{}, off=building?.door_offset||{};
    const rot=(Number(building?.rotation_degrees)||0)*Math.PI/180;
    // Gen-5 door_offset.x anchors to the tile edge (+16).
    // The door geometry is center-anchored with width 16 (1 tile).
    // Compensate by half a tile (-8 units) in local space so the door frame
    // centers exactly on the interaction tile [X, X+16].
    const ox=(Number(off.x)||0)-8, oz=Number(off.z)||0;
    return {x:(Number(base.x)||0)+ox*Math.cos(rot)-oz*Math.sin(rot),y:(Number(base.y)||0)+(Number(off.y)||0),z:(Number(base.z)||0)+ox*Math.sin(rot)+oz*Math.cos(rot)};
  }

  _playerRenderPosition(source){
    const p=source.clone();
    // FieldActor.WPos stays authoritative in the inspector.  At stair seams
    // the converted mesh may differ slightly, so adjust only the visible foot
    // anchor to the nearest plausible surface at this X/Z.
    const surface=this._terrainSurfaceY(p.x,p.z,p.y);
    if(Number.isFinite(surface)&&Math.abs(surface-p.y)<=4)p.y=surface;
    return p;
  }

  _animate(now){
    if(this.disposed)return;
    requestAnimationFrame(t=>this._animate(t));
    const m=this.playerMotion;
    if(m.to&&m.from&&m.moving){
      const t=Math.min(1,Math.max(0,(now-m.startedAt)/Math.max(1,m.duration)));
      this.playerDisplay.lerpVectors(m.from,m.to,t);
      this.playerAnchor.position.copy(this._playerRenderPosition(this.playerDisplay));
      this._updatePlayerFrame(this.player,t<1,m.face,Math.floor(now/140));
      if(t>=1)m.moving=false;
    }else{
      this.playerAnchor.position.copy(this._playerRenderPosition(this.playerDisplay));
      this._updatePlayerFrame(this.player,false,null,0);
    }
    this._animateActors(now);
    // This subclass owns the render loop; refresh base hover/locked frames
    // so the cell follows ramps and raised/lowered floor surfaces every frame.
    this._refreshCellOverlayHeights();
    if(this.followPlayer){const indoor=this.sceneData?.environment==='interior',shown=this.playerAnchor.position,desired=new THREE.Vector3(shown.x+85,shown.y+(indoor?145:245),shown.z+(indoor?120:220));this.camera.position.lerp(desired,.08);this.camera.lookAt(shown.x,shown.y+12,shown.z)}else this.controls.update();
    if(now-this.lastRenderAt>=1000/30){this.renderer.render(this.scene,this.camera);this.lastRenderAt=now;this.fpsFrames++;if(now-this.fpsWindow>=1000){this.fps=Math.round(this.fpsFrames*1000/(now-this.fpsWindow));this.fpsFrames=0;this.fpsWindow=now;this.diag.fps=this.fps;this._emitDiag()}}
  }
}

export {esc};
