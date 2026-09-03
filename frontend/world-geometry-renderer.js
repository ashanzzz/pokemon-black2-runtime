/* BMD0-only 3D schematic renderer. It never receives texture URLs. */
(function () {
  const UNITS_PER_TILE = 16;

  class MapGeometryRenderer {
    constructor(canvas) {
      this.canvas = canvas;
      this.loader = null;
      this.scene = null;
      this.camera = null;
      this.renderer = null;
      this.controls = null;
      this.group = null;
      this.marker = null;
      this.entries = [];
      this.models = new Map();
      this.viewMode = 'iso';
      this.showAxes = true;
      this.showNavMesh = true;
      this.axesGroup = null;
      this.navMeshGroup = null;
    }

    initialize() {
      if (this.renderer) return true;
      if (!this.canvas || !window.THREE?.GLTFLoader || !window.THREE?.OrbitControls) return false;
      this.loader = new THREE.GLTFLoader();
      this.scene = new THREE.Scene();
      this.scene.background = new THREE.Color(0x06111e);
      this.camera = new THREE.PerspectiveCamera(45, 1, .1, 10000);
      this.renderer = new THREE.WebGLRenderer({ canvas: this.canvas, antialias: true });
      this.renderer.setPixelRatio(devicePixelRatio || 1);
      this.renderer.outputEncoding = THREE.sRGBEncoding;
      this.controls = new THREE.OrbitControls(this.camera, this.canvas);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = .08;
      this.controls.enablePan = true;
      this.controls.screenSpacePanning = true;
      this.controls.enableZoom = true;
      this.controls.zoomSpeed = 1.2;
      this.controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
      this.controls.mouseButtons.MIDDLE = THREE.MOUSE.PAN;
      this.controls.mouseButtons.RIGHT = null;
      this.canvas.addEventListener('contextmenu', event => {
        event.preventDefault();
        event.stopPropagation();
      });
      this.scene.add(new THREE.HemisphereLight(0xffffff, 0x35516f, 1.8));
      const light = new THREE.DirectionalLight(0xeaf6ff, 1.45);
      light.position.set(120, 200, 80);
      this.scene.add(light);
      this.group = new THREE.Group();
      this.scene.add(this.group);
      this.marker = this.createMarker();
      this.scene.add(this.marker);
      this.selectionMarker = this.createSelectionMarker();
      this.scene.add(this.selectionMarker);
      this.axesGroup = this.createAxesAndRuler();
      this.axesGroup.visible = this.showAxes !== false;
      this.scene.add(this.axesGroup);
      this.navMeshGroup = new THREE.Group();
      this.navMeshGroup.name = 'geometryNavMeshGroup';
      this.navMeshGroup.visible = this.showNavMesh !== false;
      this.scene.add(this.navMeshGroup);
      this.raycaster = new THREE.Raycaster();
      this.bindPicking();
      this.resize();
      this.animate();
      return true;
    }

    createAxesAndRuler() {
      const group = new THREE.Group();
      group.name = 'geometryAxesAndRulerGroup';

      const axisLengthX = 1080;
      const axisLengthZ = 560;
      const tickSpacing = 16;

      const makeSprite = (text, color = '#38bdf8', bgColor = 'rgba(15, 23, 42, 0.88)', fontSize = 24) => {
        const canvas = document.createElement('canvas');
        canvas.width = 280;
        canvas.height = 64;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = bgColor;
        if (ctx.roundRect) ctx.roundRect(4, 4, 272, 56, 8);
        else ctx.fillRect(4, 4, 272, 56);
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.stroke();
        ctx.fillStyle = color;
        ctx.font = `bold ${fontSize}px ui-monospace, Consolas, sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(text, 140, 32);
        const texture = new THREE.CanvasTexture(canvas);
        const spriteMat = new THREE.SpriteMaterial({ map: texture, depthTest: false, depthWrite: false });
        const sprite = new THREE.Sprite(spriteMat);
        sprite.scale.set(42, 9.6, 1);
        return sprite;
      };

      // 1. X Axis (Red, East)
      const xMat = new THREE.LineBasicMaterial({ color: 0xef4444, linewidth: 3, depthTest: false });
      const xLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-48, 0, 0), new THREE.Vector3(axisLengthX, 0, 0)]), xMat);
      xLine.renderOrder = 900;
      const xCone = new THREE.Mesh(new THREE.ConeGeometry(3.5, 12, 12), new THREE.MeshBasicMaterial({ color: 0xef4444, depthTest: false }));
      xCone.rotation.z = -Math.PI / 2;
      xCone.position.set(axisLengthX, 0, 0);
      xCone.renderOrder = 900;
      const xLabel = makeSprite('+X (东/East 游戏X)', '#ef4444', 'rgba(30, 10, 10, 0.9)');
      xLabel.position.set(axisLengthX + 28, 6, 0);
      xLabel.renderOrder = 950;

      // 2. Z Axis (Blue, South)
      const zMat = new THREE.LineBasicMaterial({ color: 0x3b82f6, linewidth: 3, depthTest: false });
      const zLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, -48), new THREE.Vector3(0, 0, axisLengthZ)]), zMat);
      zLine.renderOrder = 900;
      const zCone = new THREE.Mesh(new THREE.ConeGeometry(3.5, 12, 12), new THREE.MeshBasicMaterial({ color: 0x3b82f6, depthTest: false }));
      zCone.rotation.x = Math.PI / 2;
      zCone.position.set(0, 0, axisLengthZ);
      zCone.renderOrder = 900;
      const zLabel = makeSprite('+Z (南/South 游戏Y)', '#3b82f6', 'rgba(10, 20, 40, 0.9)');
      zLabel.position.set(0, 6, axisLengthZ + 28);
      zLabel.renderOrder = 950;

      // 3. Y Axis (Green, Elevation)
      const yMat = new THREE.LineBasicMaterial({ color: 0x22c55e, linewidth: 3, depthTest: false });
      const yLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, -48, 0), new THREE.Vector3(0, 220, 0)]), yMat);
      yLine.renderOrder = 900;
      const yCone = new THREE.Mesh(new THREE.ConeGeometry(3.5, 12, 12), new THREE.MeshBasicMaterial({ color: 0x22c55e, depthTest: false }));
      yCone.position.set(0, 220, 0);
      yCone.renderOrder = 900;
      const yLabel = makeSprite('+Y (高度/Elevation)', '#22c55e', 'rgba(10, 35, 15, 0.9)');
      yLabel.position.set(0, 235, 0);
      yLabel.renderOrder = 950;

      // 4. Origin Marker
      const originLabel = makeSprite('大地图原点 (0, 0)', '#94a3b8', 'rgba(15, 23, 42, 0.92)', 22);
      originLabel.position.set(-22, 5, -22);
      originLabel.renderOrder = 950;

      // 5. Zone Labels & Dividing Line
      const m282Label = makeSprite('19号道路 (#282) 0~31格', '#f87171', 'rgba(35, 15, 15, 0.9)', 20);
      m282Label.position.set(256, 6, -22);
      m282Label.renderOrder = 950;

      const seamGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(512, 0, -32), new THREE.Vector3(512, 0, 528)]);
      const seamMat = new THREE.LineDashedMaterial({ color: 0x06b6d4, dashSize: 8, gapSize: 4, linewidth: 2, depthTest: false });
      const seamLine = new THREE.Line(seamGeo, seamMat);
      seamLine.computeLineDistances();
      seamLine.renderOrder = 900;

      const seamLabel = makeSprite('分界线 X=512 (19号道路 | 算木镇)', '#22d3ee', 'rgba(6, 40, 55, 0.92)', 19);
      seamLabel.position.set(512, 6, -22);
      seamLabel.renderOrder = 950;

      const m283Label = makeSprite('算木镇 (#283) 32~63格', '#60a5fa', 'rgba(10, 25, 45, 0.9)', 20);
      m283Label.position.set(768, 6, -22);
      m283Label.renderOrder = 950;

      const endLabel = makeSprite('全幅东端 X=1024 (64格)', '#94a3b8', 'rgba(15, 23, 42, 0.9)', 19);
      endLabel.position.set(1024, 6, -22);
      endLabel.renderOrder = 950;

      group.add(m282Label, seamLine, seamLabel, m283Label, endLabel);

      // 6. Ticks & Ruler Marks
      const tickPoints = [];
      const tickMat = new THREE.LineBasicMaterial({ color: 0x64748b, depthTest: false });

      for (let x = 0; x <= axisLengthX; x += tickSpacing) {
        const isChunk = x % 512 === 0;
        const isMedium = x % 64 === 0;
        const len = isChunk ? 14 : isMedium ? 6 : 3;
        tickPoints.push(new THREE.Vector3(x, 0, -len), new THREE.Vector3(x, 0, len));
      }

      for (let z = 0; z <= axisLengthZ; z += tickSpacing) {
        const isChunk = z % 512 === 0;
        const isMedium = z % 64 === 0;
        const len = isChunk ? 14 : isMedium ? 6 : 3;
        tickPoints.push(new THREE.Vector3(-len, 0, z), new THREE.Vector3(len, 0, z));
        if (isChunk && z > 0) {
          const chunkSprite = makeSprite(`Z=${z} (南界 ${z / 16}格)`, '#60a5fa', 'rgba(10, 20, 40, 0.88)', 20);
          chunkSprite.position.set(-25, 4, z);
          chunkSprite.renderOrder = 950;
          group.add(chunkSprite);
        }
      }

      const ticksLine = new THREE.LineSegments(new THREE.BufferGeometry().setFromPoints(tickPoints), tickMat);
      ticksLine.renderOrder = 900;

      // 7. Height / Elevation Scale Labels along Y axis
      const heightMarkers = [
        { y: -32, label: 'Y=-32 (水底倒影)', color: '#06b6d4' },
        { y: 0, label: 'Y=0 (基准平原)', color: '#94a3b8' },
        { y: 16, label: 'Y=16 (平地/算木镇主路)', color: '#10b981' },
        { y: 32, label: 'Y=32 (跃层/高台台阶)', color: '#f59e0b' },
        { y: 48, label: 'Y=48 (山石崖顶/屋顶)', color: '#f43f5e' },
      ];

      heightMarkers.forEach(hm => {
        const hLine = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-8, hm.y, 0), new THREE.Vector3(8, hm.y, 0)]), new THREE.LineBasicMaterial({ color: new THREE.Color(hm.color), depthTest: false }));
        hLine.renderOrder = 900;
        const hSprite = makeSprite(hm.label, hm.color, 'rgba(15, 23, 42, 0.92)', 20);
        hSprite.position.set(30, hm.y, 0);
        hSprite.renderOrder = 950;
        group.add(hLine, hSprite);
      });

      group.add(xLine, xCone, xLabel, zLine, zCone, zLabel, yLine, yCone, yLabel, originLabel, ticksLine);
      group.renderOrder = 900;
      return group;
    }

    createSelectionMarker() {
      const group = new THREE.Group();
      group.name = 'geometrySelectionPillar';
      const pillarHeight = 24;
      const baseTileSize = 16;

      const boxGeo = new THREE.BoxGeometry(baseTileSize, pillarHeight, baseTileSize);
      const boxMat = new THREE.MeshBasicMaterial({
        color: 0x06b6d4,
        transparent: true,
        opacity: 0.38,
        depthTest: true,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      const pillar = new THREE.Mesh(boxGeo, boxMat);
      pillar.position.y = pillarHeight / 2;

      const edgesGeo = new THREE.EdgesGeometry(boxGeo);
      const edgesMat = new THREE.LineBasicMaterial({
        color: 0x22d3ee,
        linewidth: 2,
        transparent: true,
        opacity: 0.9,
        depthTest: true,
        depthWrite: false,
      });
      const edges = new THREE.LineSegments(edgesGeo, edgesMat);
      edges.position.y = pillarHeight / 2;

      const groundFrameGeo = new THREE.EdgesGeometry(new THREE.PlaneGeometry(baseTileSize, baseTileSize));
      const groundFrameMat = new THREE.LineBasicMaterial({
        color: 0x38bdf8,
        linewidth: 3,
        depthTest: true,
      });
      const groundFrame = new THREE.LineSegments(groundFrameGeo, groundFrameMat);
      groundFrame.rotation.x = -Math.PI / 2;
      groundFrame.position.y = 0.15;

      const groundQuadGeo = new THREE.PlaneGeometry(baseTileSize, baseTileSize);
      const groundQuadMat = new THREE.MeshBasicMaterial({
        color: 0x0ea5e9,
        transparent: true,
        opacity: 0.3,
        depthTest: true,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      const groundQuad = new THREE.Mesh(groundQuadGeo, groundQuadMat);
      groundQuad.rotation.x = -Math.PI / 2;
      groundQuad.position.y = 0.1;

      group.add(pillar, edges, groundFrame, groundQuad);
      group.renderOrder = 500;
      group.visible = false;
      return group;
    }

    bindPicking() {
      let start = null;
      this.canvas.addEventListener('pointerdown', event => {
        if (event.button !== 0 && event.button !== 2) return;
        start = { x: event.clientX, y: event.clientY, button: event.button, id: event.pointerId, time: Date.now() };
      });
      this.canvas.addEventListener('pointerup', event => {
        if (!start || start.id !== event.pointerId || start.button !== event.button) return;
        const dist = Math.hypot(event.clientX - start.x, event.clientY - start.y);
        const duration = Date.now() - start.time;
        if (dist > 14 && duration > 350) return;
        this.pickSurface(event);
      });
      this.canvas.addEventListener('pointercancel', () => { start = null; });
    }

    pickSurface(event) {
      if (!this.raycaster || !this.camera || !this.entries.length) return;
      const rect = this.canvas.getBoundingClientRect();
      const pointer = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      );
      this.scene.updateMatrixWorld(true);
      this.camera.updateMatrixWorld(true);
      this.entries.forEach(e => e.copy.updateMatrixWorld(true));
      this.raycaster.setFromCamera(pointer, this.camera);
      const hit = this.raycaster.intersectObjects(this.entries.map(e => e.copy), true)[0];
      if (!hit) return;

      const entry = this.entries.find(e => {
        let n = hit.object;
        while (n) {
          if (n === e.copy) return true;
          n = n.parent;
        }
        return false;
      });
      if (!entry) return;

      const bounds = new THREE.Box3().setFromObject(entry.copy);
      const width = Math.max(1, bounds.max.x - bounds.min.x);
      const depth = Math.max(1, bounds.max.z - bounds.min.z);
      const tileW = width / 32;
      const tileD = depth / 32;
      const point = hit.point.clone();
      const localTileX = Math.min(31, Math.max(0, Math.floor((point.x - bounds.min.x) / tileW)));
      const localTileY = Math.min(31, Math.max(0, Math.floor((point.z - bounds.min.z) / tileD)));
      const tileCenterX = bounds.min.x + (localTileX + 0.5) * tileW;
      const tileCenterZ = bounds.min.z + (localTileY + 0.5) * tileD;

      const topY = bounds.max.y + 64;
      const downRay = new THREE.Raycaster(
        new THREE.Vector3(tileCenterX, topY, tileCenterZ),
        new THREE.Vector3(0, -1, 0),
        0,
        topY - bounds.min.y + 64,
      );
      const groundHits = downRay.intersectObject(entry.copy, true);
      const surfaceY = groundHits.length > 0 ? groundHits[0].point.y : point.y;

      this.selectionMarker.scale.set(tileW / 16, 1, tileD / 16);
      this.selectionMarker.position.set(tileCenterX, surfaceY, tileCenterZ);
      this.selectionMarker.visible = true;

      const code = hit.object.material?.name || `M${entry.item?.model_id}.T1`;
      if (this.onSelect) {
        this.onSelect({
          model_id: entry.item?.model_id,
          cell: entry.item?.cell,
          localTileX,
          localTileY,
          code,
          point: { x: Number(tileCenterX.toFixed(3)), y: Number(surfaceY.toFixed(3)), z: Number(tileCenterZ.toFixed(3)) },
        });
      }
    }

    async render(data) {
      if (!this.initialize()) throw new Error('3D 示意图引擎尚未就绪');
      const entries = await Promise.all((data.models || []).map(async item => {
        const source = await this.loadModel(item.asset_url);
        const copy = this.prepare(this.clone(source.scene));
        return { item, copy, materialCodes: this.applyCodeMaterials(copy, item) };
      }));
      this.clear();
      this.entries = entries;
      const tileSize = data.chunk_tile_size || { width: 32, height: 32 };
      const playerChunk = data.player_chunk || { x: 0, y: 0 };
      const isGallery = !data.player_alignment?.verified;

      if (!isGallery) {
        entries.forEach(entry => {
          const cell = entry.item.cell || { x: 0, y: 0 };
          entry.copy.position.set(
            (cell.x - playerChunk.x) * tileSize.width * UNITS_PER_TILE,
            0,
            (cell.y - playerChunk.y) * tileSize.height * UNITS_PER_TILE,
          );
          this.group.add(entry.copy);
        });
      } else {
        const activeCells = (data.active_cells || []).filter(c => c.x != null && c.y != null);
        if (activeCells.length > 1) {
          const minX = Math.min(...activeCells.map(c => c.x));
          const minY = Math.min(...activeCells.map(c => c.y));
          const unit = 32 * UNITS_PER_TILE;
          entries.forEach(({ item, copy }) => {
            const match = activeCells.find(c => c.model_id === item.model_id);
            if (match) {
              copy.position.set((match.x - minX) * unit, 0, (match.y - minY) * unit);
            }
            this.group.add(copy);
          });
        } else {
          entries.forEach(({ copy }) => this.group.add(copy));
        }
      }

      this.frame(this.viewMode === 'top');
      this.setPlayer(data, data.live_player);
      this.renderNavMesh(data);
      return uniqueCodes(entries.flatMap(entry => entry.materialCodes));
    }

    renderNavMesh(data) {
      if (!this.navMeshGroup) return;
      this.clearNavMesh();

      const tileSize = data.chunk_tile_size || { width: 32, height: 32 };
      const tileUnit = 16;
      const navGeo = new THREE.PlaneGeometry(tileUnit * 0.88, tileUnit * 0.88);
      const navMat = new THREE.MeshBasicMaterial({
        color: 0x10b981,
        transparent: true,
        opacity: 0.35,
        side: THREE.DoubleSide,
        depthTest: true,
        depthWrite: false,
      });

      const borderGeo = new THREE.EdgesGeometry(navGeo);
      const borderMat = new THREE.LineBasicMaterial({
        color: 0x34d399,
        linewidth: 2,
        transparent: true,
        opacity: 0.7,
        depthTest: true,
      });

      this.entries.forEach(entry => {
        entry.copy.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(entry.copy);
        const tileW = Math.max(1, bounds.max.x - bounds.min.x) / tileSize.width;
        const tileD = Math.max(1, bounds.max.z - bounds.min.z) / tileSize.height;

        entry.copy.traverse(child => {
          if (!child.isMesh || !child.geometry) return;
          const pos = child.geometry.attributes.position;
          const index = child.geometry.index;
          const rawName = child.userData?.rawName || child.material?.name || '';
          const matName = String(rawName).toLowerCase();

          const isWalkable = matName.includes('michi') || matName.includes('miti')
            || matName.includes('road') || matName.includes('yuka') || matName.includes('hibi')
            || matName.includes('kaidan') || matName.includes('stair') || matName.includes('hasi')
            || matName.includes('grass') || matName.includes('kusa') || matName.includes('ue_grass');

          const isBlocked = matName.includes('gake') || matName.includes('kabe')
            || matName.includes('wall') || matName.includes('mizu') || matName.includes('water')
            || matName.includes('ki0') || matName.includes('saku') || matName.includes('fence')
            || matName.includes('air') || matName.includes('sky');

          if (!isWalkable || isBlocked) return;

          if (index) {
            const addedTiles = new Set();
            for (let i = 0; i < index.count; i += 3) {
              const i0 = index.getX(i);
              const x0 = pos.getX(i0), y0 = pos.getY(i0), z0 = pos.getZ(i0);
              const localTileX = Math.min(31, Math.max(0, Math.floor((x0 + 256) / 16)));
              const localTileY = Math.min(31, Math.max(0, Math.floor((z0 + 256) / 16)));
              const key = `${localTileX},${localTileY}`;
              if (!addedTiles.has(key)) {
                addedTiles.add(key);
                const tileCenterX = bounds.min.x + (localTileX + 0.5) * tileW;
                const tileCenterZ = bounds.min.z + (localTileY + 0.5) * tileD;
                const surfaceY = Math.max(y0, 16.0) + 0.15;

                const tileMesh = new THREE.Mesh(navGeo, navMat);
                tileMesh.rotation.x = -Math.PI / 2;
                tileMesh.position.set(tileCenterX, surfaceY, tileCenterZ);

                const tileBorder = new THREE.LineSegments(borderGeo, borderMat);
                tileBorder.rotation.x = -Math.PI / 2;
                tileBorder.position.set(tileCenterX, surfaceY + 0.05, tileCenterZ);

                this.navMeshGroup.add(tileMesh, tileBorder);
              }
            }
          }
        });
      });

      this.navMeshGroup.renderOrder = 400;
      this.navMeshGroup.visible = this.showNavMesh !== false;
    }

    clearNavMesh() {
      while (this.navMeshGroup?.children.length) {
        const child = this.navMeshGroup.children[0];
        child.geometry?.dispose();
        this.navMeshGroup.remove(child);
      }
    }

    setPlayer(data, player) {
      if (!player?.verified || !Number.isFinite(Number(player.x)) || !Number.isFinite(Number(player.y))) {
        this.marker.visible = false;
        return;
      }
      const size = data.chunk_tile_size || { width: 32, height: 32 };
      const chunkX = Math.floor(Number(player.x) / size.width);
      const chunkY = Math.floor(Number(player.y) / size.height);
      let entry = this.entries.find(item => (
        item.item.cell?.x === chunkX && item.item.cell?.y === chunkY
      ));
      if (!entry && this.entries.length > 0) entry = this.entries[0];
      if (!entry) {
        this.marker.visible = false;
        return;
      }
      entry.copy.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(entry.copy);
      const localX = Number(player.x) % size.width;
      const localY = Number(player.y) % size.height;
      const x = THREE.MathUtils.lerp(bounds.min.x, bounds.max.x, (localX + .5) / size.width);
      const z = THREE.MathUtils.lerp(bounds.min.z, bounds.max.z, (localY + .5) / size.height);
      this.marker.position.set(x, this.surfaceY(entry.copy, x, z, bounds), z);

      const headingArrow = this.marker.getObjectByName('headingArrow');
      if (headingArrow) {
        const facing = String(player.facing || 'South').toLowerCase();
        if (facing === 'north') headingArrow.rotation.y = Math.PI;
        else if (facing === 'south') headingArrow.rotation.y = 0;
        else if (facing === 'west') headingArrow.rotation.y = Math.PI / 2;
        else if (facing === 'east') headingArrow.rotation.y = -Math.PI / 2;
      }
      this.marker.visible = true;
    }

    resize() {
      if (!this.renderer || !this.canvas) return;
      const box = this.canvas.getBoundingClientRect();
      const width = Math.max(1, box.width);
      const height = Math.max(1, box.height);
      this.renderer.setSize(width, height, false);
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
    }

    adjustZoom(factor) {
      if (!this.controls) return;
      const offset = this.camera.position.clone().sub(this.controls.target);
      const distance = THREE.MathUtils.clamp(
        offset.length() * factor, this.controls.minDistance, this.controls.maxDistance,
      );
      this.camera.position.copy(this.controls.target).add(offset.setLength(distance));
      this.controls.update();
    }

    rotate(angle) {
      if (!this.controls) return;
      const offset = this.camera.position.clone().sub(this.controls.target);
      const cosine = Math.cos(angle);
      const sine = Math.sin(angle);
      this.camera.position.set(
        this.controls.target.x + offset.x * cosine - offset.z * sine,
        this.camera.position.y,
        this.controls.target.z + offset.x * sine + offset.z * cosine,
      );
      this.controls.update();
    }

    setView(mode) {
      this.viewMode = mode;
      this.frame(mode === 'top');
    }

    createMarker() {
      const group = new THREE.Group();
      group.name = 'playerAvatarGroup';

      // 1. Base footprint ring
      const groundRingGeo = new THREE.RingGeometry(2.5, 4.5, 24);
      const groundRingMat = new THREE.MeshBasicMaterial({
        color: 0x0ea5e9,
        transparent: true,
        opacity: 0.8,
        side: THREE.DoubleSide,
        depthTest: true,
        depthWrite: false,
      });
      const groundRing = new THREE.Mesh(groundRingGeo, groundRingMat);
      groundRing.rotation.x = -Math.PI / 2;
      groundRing.position.y = 0.2;

      // 2. Character body
      const bodyGeo = new THREE.CylinderGeometry(2.2, 2.8, 8, 16);
      const bodyMat = new THREE.MeshBasicMaterial({
        color: 0x2563eb,
        depthTest: true,
      });
      const body = new THREE.Mesh(bodyGeo, bodyMat);
      body.position.y = 4.2;

      // 3. Head / Cap
      const headGeo = new THREE.SphereGeometry(2.2, 16, 16);
      const headMat = new THREE.MeshBasicMaterial({
        color: 0xffffff,
        depthTest: true,
      });
      const head = new THREE.Mesh(headGeo, headMat);
      head.position.y = 9.5;

      // 4. Directional heading pointer
      const arrowConeGeo = new THREE.ConeGeometry(2.0, 6, 12);
      const arrowConeMat = new THREE.MeshBasicMaterial({
        color: 0xef4444,
        depthTest: true,
      });
      const arrowCone = new THREE.Mesh(arrowConeGeo, arrowConeMat);
      arrowCone.rotation.x = Math.PI / 2;
      arrowCone.position.set(0, 4.0, 6.0);
      const arrowGroup = new THREE.Group();
      arrowGroup.name = 'headingArrow';
      arrowGroup.add(arrowCone);

      group.add(groundRing, body, head, arrowGroup);
      group.visible = false;
      group.renderOrder = 800;
      return group;
    }

    applyCodeMaterials(model, item) {
      const defined = new Map((item.material_codes || []).map(code => [code.raw_name, code.code]));
      const codes = [];
      const materials = new Map();
      model.traverse(node => {
        if (!node.isMesh) return;
        const replace = (material, index) => {
          const key = `${material?.uuid || 'none'}:${index}`;
          if (materials.has(key)) return materials.get(key);
          const code = defined.get(material?.name) || `M${item.model_id}.T${index + 1}`;
          const color = codeColor(code);
          const result = new THREE.MeshBasicMaterial({
            color,
            side: THREE.DoubleSide,
          });
          result.name = code;
          result.userData = { code, rawName: material?.name || 'unnamed' };
          materials.set(key, result);
          codes.push({ code, raw_name: material?.name || 'unnamed', model_id: item.model_id });
          return result;
        };
        node.material = Array.isArray(node.material)
          ? node.material.map(replace) : replace(node.material, 0);
      });
      return codes;
    }

    prepare(model) {
      model.traverse(node => {
        if (!node.isMesh) return;
        node.frustumCulled = false;
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(node.geometry, 25),
          new THREE.LineBasicMaterial({ color: 0x102a43, depthTest: false, depthWrite: false }),
        );
        edges.renderOrder = 10;
        node.add(edges);
      });
      return model;
    }

    clone(scene) {
      return THREE.SkeletonUtils?.clone ? THREE.SkeletonUtils.clone(scene) : scene.clone(true);
    }

    loadModel(url) {
      if (!this.models.has(url)) {
        this.models.set(url, new Promise((resolve, reject) => this.loader.load(url, resolve, undefined, reject)));
      }
      return this.models.get(url);
    }

    surfaceY(model, x, z, bounds) {
      const startY = bounds.max.y + 128;
      const ray = new THREE.Raycaster(
        new THREE.Vector3(x, startY, z), new THREE.Vector3(0, -1, 0), 0, startY - bounds.min.y + 128,
      );
      const hit = ray.intersectObject(model, true)[0];
      return hit ? hit.point.y + 2.5 : bounds.max.y + 2.5;
    }

    frame(topDown) {
      if (!this.group || !this.controls) return;
      this.group.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(this.group);
      if (bounds.isEmpty()) return;
      const center = bounds.getCenter(new THREE.Vector3());
      const span = Math.max(bounds.max.x - bounds.min.x, bounds.max.z - bounds.min.z, 100);
      const fov = THREE.MathUtils.degToRad(this.camera.fov);
      const distance = Math.max(span, span / Math.max(this.camera.aspect, .35)) / (2 * Math.tan(fov / 2)) * 1.18;
      const direction = topDown ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(-.75, .62, .75).normalize();
      this.camera.up.copy(topDown ? new THREE.Vector3(0, 0, -1) : new THREE.Vector3(0, 1, 0));
      this.camera.position.copy(center).add(direction.multiplyScalar(distance));
      this.controls.minDistance = Math.max(20, span * .05);
      this.controls.maxDistance = Math.max(700, span * 8);
      this.controls.target.copy(center);
      this.controls.update();
    }

    clear() {
      while (this.group?.children.length) {
        const child = this.group.children[0];
        child.traverse(node => {
          node.geometry?.dispose();
          if (Array.isArray(node.material)) node.material.forEach(material => material.dispose());
          else node.material?.dispose();
        });
        this.group.remove(child);
      }
    }

    animate() {
      requestAnimationFrame(() => this.animate());
      this.controls?.update();
      this.renderer?.render(this.scene, this.camera);
    }
  }

  function codeColor(code) {
    let hash = 0;
    for (let index = 0; index < code.length; index += 1) hash = (hash * 31 + code.charCodeAt(index)) | 0;
    return new THREE.Color(`hsl(${Math.abs(hash) % 360}, 62%, 58%)`);
  }

  function uniqueCodes(codes) {
    return [...new Map(codes.map(code => [code.code, code])).values()].sort((left, right) => (
      left.code.localeCompare(right.code)
    ));
  }

  window.MapGeometryRenderer = MapGeometryRenderer;
})();
