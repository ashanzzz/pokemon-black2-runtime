import * as THREE from 'three';
import {Black2World3D as BaseWorld3D, esc} from '/frontend/world3d-runtime.js?base=1';

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
    const seen=new Set();
    const same=actors.filter(a=>!a.is_player&&(a.same_current_scene===true||a.zone_id===this.player?.zone_id));
    this.diag.npc_total=same.length;this.diag.npc_original_count=0;this.diag.npc_fallback_count=0;
    for(const a of same){
      const id=String(a.slot??a.actor_uid);seen.add(id);let anchor=this.actorMarkers.get(id);
      if(!anchor){
        anchor=new THREE.Group();const visual=(await this._actorVisual(a)).clone(true);anchor.add(visual);
        const positionMarker=marker(5.2);positionMarker.name=`npc-${id}-runtime-wpos-marker`;anchor.add(positionMarker);
        anchor.userData={kind:'npc',actor:a,mode:visual.userData?.asset_mode||'fallback_marker'};
        this.actorMarkers.set(id,anchor);this.actorRoot.add(anchor);
      }
      anchor.userData.actor=a;
      anchor.position.copy(this._display(a.world));
      if(anchor.userData.mode?.startsWith('candidate_original'))this.diag.npc_original_count++;else this.diag.npc_fallback_count++;
    }
    for(const [id,o] of this.actorMarkers)if(!seen.has(id)){this.actorRoot.remove(o);o.clear();this.actorMarkers.delete(id)}
    this._emitDiag();this.ui.onActors?.(same);
  }
}

export {esc};
