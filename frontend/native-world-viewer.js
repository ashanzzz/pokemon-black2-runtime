/* ROM-native world viewer shared by the main dashboard and the standalone map page. */
(function () {
  const NONE = 0xFFFFFFFF;
  // Keep the ROM-native BMD0 grid at the proven 16 units per map tile.
  // The original renderer used this scale; 24 introduces a false Y seam.
  const WORLD_UNITS_PER_TILE = 16;

  // ROM Model ID to Location Name dictionary for easy map verification
  const KNOWN_MODELS = {
    280: "桧扇市 (Aspertia City)",
    281: "19号道路 (Route 19)",
    282: "19号道路 (Route 19)",
    283: "算木镇 (Floccesy Town)",
    284: "算木牧场 (Floccesy Ranch)",
    285: "20号道路 (Route 20)",
    286: "立涌市 (Virbank City)",
    287: "立涌联合工业区 (Virbank Complex)",
  };

  function resolveModelLocationName(modelId) {
    if (modelId == null) return "未知区域";
    return KNOWN_MODELS[modelId] || `模型 #${modelId}`;
  }

  function interpretHeightLayer(surfaceY) {
    if (surfaceY == null) return { tag: '未知', desc: '未知高度', tier: 0 };
    const y = Number(surfaceY);
    if (y <= -20) {
      return { tag: '🌊 水面/倒影底面', desc: `底部水面反射层 (Y=${y.toFixed(1)})`, tier: -1, isWaterOrBottom: true };
    }
    if (y < -1) {
      return { tag: '💧 浅水/洼地', desc: `下沉水洼层 (Y=${y.toFixed(1)})`, tier: -0.5, isWaterOrBottom: true };
    }
    if (y <= 6) {
      return { tag: '🌱 标准平地', desc: `地面 0 级平地 (Y=${y.toFixed(1)})`, tier: 0, isGround: true };
    }
    if (y <= 22) {
      return { tag: '🪜 1级高台/台阶', desc: `高程 +1 层跃台 (Y=${y.toFixed(1)})`, tier: 1, isLedge: true };
    }
    if (y <= 38) {
      return { tag: '⛰️ 2级平台/山岩', desc: `高程 +2 层平台 (Y=${y.toFixed(1)})`, tier: 2, isHigh: true };
    }
    if (y <= 54) {
      return { tag: '🏠 3级屋顶/高崖', desc: `高程 +3 层景观 (Y=${y.toFixed(1)})`, tier: 3, isHigh: true };
    }
    return { tag: '🏢 高层建筑顶', desc: `高空建筑层 (Y=${y.toFixed(1)})`, tier: 4, isHigh: true };
  }

  class NativeWorldViewer {
    constructor(root) {
      this.root = root;
      this.loader = null;
      this.models = new Map();
      this.scene = null;
      this.camera = null;
      this.renderer = null;
      this.controls = null;
      this.group = null;
      this.interactionGroup = null;
      this.schematicOverlayGroup = null;
      this.marker = null;
      this.selectionMarker = null;
      this.raycaster = null;
      this.pointerStart = null;
      this.selectedSurface = null;
      this.playerRenderAnchor = null;
      this.areaSize = 1; // 1 = 1x1 tile, 3 = 3x3 tiles, 5 = 5x5 tiles
      this.showNavMesh = true; // Walkable path green indicator
      this.showAxes = true; // Coordinate axes and ruler
      this.axesGroup = null;
      this.navMeshGroup = null;
      this.info = null;
      this.knowledge = null;
      this.observations = [];
      this.galleryEntries = [];
      this.renderEntries = [];
      this.mode = 'schematic';
      this.schematic = null;
      this.geometry = null;
      this.schematicData = null;
      this.machineData = null;
      this.machineBusy = false;
      this.collisionPlane = 'all';
      this.knowledgeBusy = false;
      this.probeBusy = false;
      this.loadBusy = false;
      this.pendingLoad = false;
      this.retryTimer = null;
      this.retryDelay = 600;
      this.refreshTimer = null;
      this.lastLivePosition = null;
      this.lastMapSection = null;
      this.lastSceneCacheKey = null;
      this.sceneCheckBusy = false;
      this.playerRefreshBusy = false;
      this.lastSceneCheckAt = 0;
    }

    el(name) {
      return this.root.querySelector(`[data-world="${name}"]`);
    }

    setText(name, text) {
      const element = this.el(name);
      if (element) element.textContent = text;
    }

    init() {
      const schematicCanvas = this.el('schematic-canvas');
      this.schematic = window.MapSchematicCanvas
        ? new window.MapSchematicCanvas(schematicCanvas) : null;
      this.geometry = window.MapGeometryRenderer
        ? new window.MapGeometryRenderer(this.el('geometry-canvas')) : null;
      if (this.geometry) {
        this.geometry.onSelect = (sel) => {
          const locName = resolveModelLocationName(sel.model_id);
          const hLayer = interpretHeightLayer(sel.point?.y);
          const checkCode = `#${sel.model_id ?? '?'}(${locName.split(' ')[0]})[${sel.localTileX},${sel.localTileY}]@Y${(sel.point?.y ?? 0).toFixed(1)}`;
          const text = [
            `【核对代号】${checkCode}`,
            `【地名/模型】${locName} (模型 #${sel.model_id ?? '—'} · 矩阵格 (${sel.cell?.x ?? '?'}, ${sel.cell?.y ?? '?'}))`,
            `【地块网格】局部瓦片 [${sel.localTileX}, ${sel.localTileY}] (0~31)`,
            `【高度层级】${hLayer.tag} · ${hLayer.desc} (3D Y=${sel.point.y})`,
            `【材质槽位】${sel.code || 'M?.T0'}`,
            `【3D 坐标】X=${sel.point.x}  Y=${sel.point.y}  Z=${sel.point.z}`,
            '─────────────────────────────────────',
            '💡 提示：色块代表 BMD0 原始材质槽位，可直接复制核对代号告知我：',
            `  "${checkCode} = [草坪/道路/屋顶/障碍]"`,
          ].join('\n');
          this.setText('geometry-codes', text);
          this.setStatus(`已选: ${checkCode} · ${hLayer.tag} · 材质编码 ${sel.code}`);
        };
      }
      window.addEventListener('resize', () => this.resize());
      this.bindControls();
      this.updateAxesButton();
      this.updateNavMeshButton();
      this.resize();
      this.renderMode('schematic');
      this.refreshTimer = setInterval(() => this.refreshPlayer(), 400);
    }

    ensureRenderer() {
      if (this.renderer) return true;
      if (!window.THREE?.GLTFLoader || !window.THREE?.OrbitControls) {
        this.setStatus('实际图的 3D 引擎尚未就绪，请刷新主页面后重试。', true);
        return false;
      }
      const canvas = this.el('canvas');
      this.loader = new THREE.GLTFLoader();
      this.scene = new THREE.Scene();
      this.scene.background = new THREE.Color(0x02070d);
      this.camera = new THREE.PerspectiveCamera(45, 1, .1, 10000);
      this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
      this.renderer.setPixelRatio(devicePixelRatio || 1);
      this.renderer.outputEncoding = THREE.sRGBEncoding;
      this.controls = new THREE.OrbitControls(this.camera, canvas);
      this.controls.enableDamping = true;
      this.controls.dampingFactor = .08;
      this.controls.enablePan = true;
      this.controls.screenSpacePanning = true;
      this.controls.enableZoom = true;
      this.controls.zoomSpeed = 1.2;
      this.controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
      this.controls.mouseButtons.MIDDLE = THREE.MOUSE.PAN;
      this.controls.mouseButtons.RIGHT = null; // Completely disable right-click rotation to prevent browser menu/gesture interference
      canvas.addEventListener('contextmenu', event => {
        event.preventDefault();
        event.stopPropagation();
      });
      this.scene.add(new THREE.HemisphereLight(0xffffff, 0x26364e, 1.1));
      const sun = new THREE.DirectionalLight(0xfff4dc, 1.2);
      sun.position.set(100, 180, 100);
      this.scene.add(sun);
      this.group = new THREE.Group();
      this.scene.add(this.group);
      this.interactionGroup = new THREE.Group();
      this.scene.add(this.interactionGroup);
      this.schematicOverlayGroup = new THREE.Group();
      this.scene.add(this.schematicOverlayGroup);
      this.navMeshGroup = new THREE.Group();
      this.navMeshGroup.name = 'navMeshGroup';
      this.navMeshGroup.visible = this.showNavMesh;
      this.scene.add(this.navMeshGroup);
      this.marker = new THREE.Group();
      this.marker.name = 'playerAvatarGroup';

      // 1. Base shadow / footprint ring on ground
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

      // 2. Character body figure (stylized trainer figure)
      const bodyGeo = new THREE.CylinderGeometry(2.2, 2.8, 8, 16);
      const bodyMat = new THREE.MeshBasicMaterial({
        color: 0x2563eb, // Trainer blue jacket
        depthTest: true,
      });
      const body = new THREE.Mesh(bodyGeo, bodyMat);
      body.position.y = 4.2;

      // 3. Head / Cap
      const headGeo = new THREE.SphereGeometry(2.2, 16, 16);
      const headMat = new THREE.MeshBasicMaterial({
        color: 0xffffff, // White cap
        depthTest: true,
      });
      const head = new THREE.Mesh(headGeo, headMat);
      head.position.y = 9.5;

      // 4. Directional heading pointer
      const arrowConeGeo = new THREE.ConeGeometry(2.0, 6, 12);
      const arrowConeMat = new THREE.MeshBasicMaterial({
        color: 0xef4444, // Bright red facing pointer
        depthTest: true,
      });
      const arrowCone = new THREE.Mesh(arrowConeGeo, arrowConeMat);
      arrowCone.rotation.x = Math.PI / 2;
      arrowCone.position.set(0, 4.0, 6.0);
      const arrowGroup = new THREE.Group();
      arrowGroup.name = "headingArrow";
      arrowGroup.add(arrowCone);

      this.marker.add(groundRing, body, head, arrowGroup);
      this.marker.renderOrder = 800;
      this.marker.visible = false;
      this.scene.add(this.marker);
      this.raycaster = new THREE.Raycaster();
      this.selectionMarker = this.createSelectionMarker();
      this.scene.add(this.selectionMarker);
      this.axesGroup = this.createAxesAndRuler();
      this.axesGroup.visible = this.showAxes;
      this.scene.add(this.axesGroup);
      this.bindMapPicking(canvas);
      this.resize();
      this.animate();
      return true;
    }

    bindControls() {
      this.el('zoom-out')?.addEventListener('click', () => this.adjustZoom(.8));
      this.el('zoom-in')?.addEventListener('click', () => this.adjustZoom(1.25));
      this.el('rotate-left')?.addEventListener('click', () => this.rotateView(-Math.PI / 4));
      this.el('rotate-right')?.addEventListener('click', () => this.rotateView(Math.PI / 4));
      this.el('top')?.addEventListener('click', () => this.setViewMode('top'));
      this.el('iso')?.addEventListener('click', () => this.setViewMode('iso'));
      this.el('fit')?.addEventListener('click', () => this.frameGroup(this.viewMode === 'top'));
      this.el('toggle-axes')?.addEventListener('click', () => this.toggleAxes());
      this.el('toggle-nav')?.addEventListener('click', () => this.toggleNavMesh());
      this.el('refresh')?.addEventListener('click', () => this.refreshView());
      this.el('refresh-knowledge')?.addEventListener('click', () => this.refreshKnowledge());
      this.el('raw')?.addEventListener('click', () => this.refreshView());
      this.el('collision-plane')?.addEventListener('change', event => {
        this.collisionPlane = event.target.value;
        this.renderInteractionLayer();
      });
      this.el('compact')?.addEventListener('click', () => {
        this.root.classList.toggle('world-compact');
        this.el('compact').textContent = this.root.classList.contains('world-compact')
          ? '信息面板：紧凑' : '信息面板：标准';
      });
      this.root.querySelectorAll('[data-world-mode]').forEach(button => {
        button.addEventListener('click', () => this.renderMode(button.dataset.worldMode));
      });
      this.root.querySelectorAll('[data-world-probe]').forEach(button => {
        button.addEventListener('click', () => this.probe(button.dataset.worldProbe));
      });
      this.el('variant')?.addEventListener('change', event => this.showCandidate(Number(event.target.value)));
      this.el('copy-selection-code')?.addEventListener('click', () => this.copySelectionCode());
      this.el('copy-selection')?.addEventListener('click', () => this.copySelection());
      this.el('clear-selection')?.addEventListener('click', () => this.clearSelection());
      this.el('set-player-anchor')?.addEventListener('click', () => this.setPlayerRenderAnchor());
      this.el('copy-all-materials')?.addEventListener('click', () => this.copyAllMaterials());

      // Area size buttons (1x1, 3x3, 5x5)
      this.root.querySelectorAll('[data-area-size]').forEach(btn => {
        btn.addEventListener('click', () => {
          const size = Number(btn.dataset.areaSize || 1);
          this.setAreaSize(size);
        });
      });

      // Quick terrain tag buttons
      this.root.querySelectorAll('[data-tag-terrain]').forEach(btn => {
        btn.addEventListener('click', () => this.quickTagTerrain(btn.dataset.tagTerrain));
      });
    }

    toggleAxes() {
      this.showAxes = !this.showAxes;
      if (this.axesGroup) {
        this.axesGroup.visible = this.showAxes;
      }
      if (this.geometry) {
        this.geometry.showAxes = this.showAxes;
        if (this.geometry.axesGroup) {
          this.geometry.axesGroup.visible = this.showAxes;
        }
      }
      this.updateAxesButton();
    }

    updateAxesButton() {
      const btn = this.el('toggle-axes');
      if (btn) {
        btn.textContent = this.showAxes ? '坐标尺: 开' : '坐标尺: 关';
        btn.className = this.showAxes
          ? 'px-2 py-1 text-[11px] rounded bg-cyan-950/80 text-cyan-300 border border-cyan-700 hover:bg-cyan-900 font-medium'
          : 'px-2 py-1 text-[11px] rounded bg-slate-800 text-slate-400 border border-slate-700 hover:bg-slate-700';
        btn.setAttribute('aria-pressed', String(this.showAxes));
      }
    }

    toggleNavMesh() {
      this.showNavMesh = !this.showNavMesh;
      if (this.navMeshGroup) {
        this.navMeshGroup.visible = this.showNavMesh;
      }
      if (this.geometry) {
        this.geometry.showNavMesh = this.showNavMesh;
        if (this.geometry.navMeshGroup) {
          this.geometry.navMeshGroup.visible = this.showNavMesh;
        }
      }
      this.updateNavMeshButton();
    }

    updateNavMeshButton() {
      const btn = this.el('toggle-nav');
      if (btn) {
        btn.textContent = this.showNavMesh ? '可通行路径: 开' : '可通行路径: 关';
        btn.className = this.showNavMesh
          ? 'px-2 py-1 text-[11px] rounded bg-emerald-950/80 text-emerald-300 border border-emerald-700 hover:bg-emerald-900 font-medium'
          : 'px-2 py-1 text-[11px] rounded bg-slate-800 text-slate-400 border border-slate-700 hover:bg-slate-700';
        btn.setAttribute('aria-pressed', String(this.showNavMesh));
      }
    }

    renderNavMesh(data) {
      if (!this.navMeshGroup) return;
      this.clearNavMesh();

      const tileSize = data.chunk_tile_size || { width: 32, height: 32 };
      const tileUnit = 16;
      const navGeo = new THREE.PlaneGeometry(tileUnit * 0.88, tileUnit * 0.88);
      const navMat = new THREE.MeshBasicMaterial({
        color: 0x10b981, // Vibrant emerald green for reachable path
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

      this.renderEntries.forEach(entry => {
        entry.copy.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(entry.copy);
        const tileW = Math.max(1, bounds.max.x - bounds.min.x) / tileSize.width;
        const tileD = Math.max(1, bounds.max.z - bounds.min.z) / tileSize.height;

        // Traverse mesh to identify reachable road / ground polygons
        entry.copy.traverse(child => {
          if (!child.isMesh || !child.geometry) return;
          const pos = child.geometry.attributes.position;
          const index = child.geometry.index;
          const mat = child.material;
          const matName = String(Array.isArray(mat) ? mat[0]?.name : mat?.name || '').toLowerCase();

          // Check if material is walkable road / path / ground (exclude water, cliffs, trees, walls)
          const isWalkable = matName.includes('michi') || matName.includes('miti')
            || matName.includes('road') || matName.includes('yuka') || matName.includes('hibi')
            || matName.includes('kaidan') || matName.includes('stair') || matName.includes('hasi')
            || matName.includes('grass') || matName.includes('kusa') || matName.includes('ue_grass');

          const isBlocked = matName.includes('gake') || matName.includes('kabe')
            || matName.includes('wall') || matName.includes('mizu') || matName.includes('water')
            || matName.includes('ki0') || matName.includes('saku') || matName.includes('fence')
            || matName.includes('air') || matName.includes('sky');

          if (!isWalkable || isBlocked) return;

          // For each walkable area, place glowing green indicator tiles
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
      this.navMeshGroup.visible = this.showNavMesh;
    }

    clearNavMesh() {
      while (this.navMeshGroup?.children.length) {
        const child = this.navMeshGroup.children[0];
        child.geometry?.dispose();
        this.navMeshGroup.remove(child);
      }
    }

    createAxesAndRuler() {
      const group = new THREE.Group();
      group.name = 'axesAndRulerGroup';

      const axisLengthX = 1080; // 64+ tiles (covers both #282 and #283 stitched)
      const axisLengthZ = 560;  // 32+ tiles depth
      const tickSpacing = 16;   // 1 tile = 16 units

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

      // 1. X Axis (Red, East / 游戏水平X轴向东)
      const xMat = new THREE.LineBasicMaterial({ color: 0xef4444, linewidth: 3, depthTest: false });
      const xGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(-48, 0, 0),
        new THREE.Vector3(axisLengthX, 0, 0),
      ]);
      const xLine = new THREE.Line(xGeo, xMat);
      xLine.renderOrder = 900;

      const xCone = new THREE.Mesh(
        new THREE.ConeGeometry(3.5, 12, 12),
        new THREE.MeshBasicMaterial({ color: 0xef4444, depthTest: false }),
      );
      xCone.rotation.z = -Math.PI / 2;
      xCone.position.set(axisLengthX, 0, 0);
      xCone.renderOrder = 900;

      const xLabel = makeSprite('+X (东/East 游戏X)', '#ef4444', 'rgba(30, 10, 10, 0.9)');
      xLabel.position.set(axisLengthX + 28, 6, 0);
      xLabel.renderOrder = 950;

      // 2. Z Axis (Blue, South / 游戏垂直Y轴向南)
      const zMat = new THREE.LineBasicMaterial({ color: 0x3b82f6, linewidth: 3, depthTest: false });
      const zGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0, -48),
        new THREE.Vector3(0, 0, axisLengthZ),
      ]);
      const zLine = new THREE.Line(zGeo, zMat);
      zLine.renderOrder = 900;

      const zCone = new THREE.Mesh(
        new THREE.ConeGeometry(3.5, 12, 12),
        new THREE.MeshBasicMaterial({ color: 0x3b82f6, depthTest: false }),
      );
      zCone.rotation.x = Math.PI / 2;
      zCone.position.set(0, 0, axisLengthZ);
      zCone.renderOrder = 900;

      const zLabel = makeSprite('+Z (南/South 游戏Y)', '#3b82f6', 'rgba(10, 20, 40, 0.9)');
      zLabel.position.set(0, 6, axisLengthZ + 28);
      zLabel.renderOrder = 950;

      // 3. Y Axis (Green, Elevation / 垂直高度Y轴)
      const yMat = new THREE.LineBasicMaterial({ color: 0x22c55e, linewidth: 3, depthTest: false });
      const yGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, -48, 0),
        new THREE.Vector3(0, 220, 0),
      ]);
      const yLine = new THREE.Line(yGeo, yMat);
      yLine.renderOrder = 900;

      const yCone = new THREE.Mesh(
        new THREE.ConeGeometry(3.5, 12, 12),
        new THREE.MeshBasicMaterial({ color: 0x22c55e, depthTest: false }),
      );
      yCone.position.set(0, 220, 0);
      yCone.renderOrder = 900;

      const yLabel = makeSprite('+Y (高度/Elevation)', '#22c55e', 'rgba(10, 35, 15, 0.9)');
      yLabel.position.set(0, 235, 0);
      yLabel.renderOrder = 950;

      // 4. Origin Marker (原点)
      const originLabel = makeSprite('大地图原点 (0, 0)', '#94a3b8', 'rgba(15, 23, 42, 0.92)', 22);
      originLabel.position.set(-22, 5, -22);
      originLabel.renderOrder = 950;

      // 5. Stitched Map Zone Milestone Labels & Dividing Seam Line
      // Model 282 Zone Label
      const m282Label = makeSprite('19号道路 (#282) 0~31格', '#f87171', 'rgba(35, 15, 15, 0.9)', 20);
      m282Label.position.set(256, 6, -22);
      m282Label.renderOrder = 950;

      // Chunk dividing boundary line at X=512 along Z
      const seamGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(512, 0, -32),
        new THREE.Vector3(512, 0, 528),
      ]);
      const seamMat = new THREE.LineDashedMaterial({
        color: 0x06b6d4,
        dashSize: 8,
        gapSize: 4,
        linewidth: 2,
        depthTest: false,
      });
      const seamLine = new THREE.Line(seamGeo, seamMat);
      seamLine.computeLineDistances();
      seamLine.renderOrder = 900;

      const seamLabel = makeSprite('分界线 X=512 (19号道路 | 算木镇)', '#22d3ee', 'rgba(6, 40, 55, 0.92)', 19);
      seamLabel.position.set(512, 6, -22);
      seamLabel.renderOrder = 950;

      // Model 283 Zone Label
      const m283Label = makeSprite('算木镇 (#283) 32~63格', '#60a5fa', 'rgba(10, 25, 45, 0.9)', 20);
      m283Label.position.set(768, 6, -22);
      m283Label.renderOrder = 950;

      // End of Stitched Map Label
      const endLabel = makeSprite('全幅东端 X=1024 (64格)', '#94a3b8', 'rgba(15, 23, 42, 0.9)', 19);
      endLabel.position.set(1024, 6, -22);
      endLabel.renderOrder = 950;

      group.add(m282Label, seamLine, seamLabel, m283Label, endLabel);

      // 6. Ticks & Ruler Marks along X and Z
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

      const ticksLine = new THREE.LineSegments(
        new THREE.BufferGeometry().setFromPoints(tickPoints),
        tickMat,
      );
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
        const hLine = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(-8, hm.y, 0), new THREE.Vector3(8, hm.y, 0)]),
          new THREE.LineBasicMaterial({ color: new THREE.Color(hm.color), depthTest: false }),
        );
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

    setAreaSize(size) {
      this.areaSize = size;
      this.root.querySelectorAll('[data-area-size]').forEach(btn => {
        const active = Number(btn.dataset.areaSize) === size;
        btn.className = active
          ? 'px-1.5 py-0.5 text-[10px] rounded bg-cyan-800 text-white border border-cyan-600 font-bold area-size-btn'
          : 'px-1.5 py-0.5 text-[10px] rounded bg-slate-800 text-slate-300 border border-slate-700 hover:bg-slate-700 area-size-btn';
      });
      if (this.lastSelectedTarget) {
        const { entry, point, local, coordinateStatus, hit } = this.lastSelectedTarget;
        this.selectMapPoint(entry, point, local, coordinateStatus, hit);
      }
    }

    renderMode(mode) {
      this.mode = mode;
      this.root.dataset.worldMode = mode;
      this.root.querySelectorAll('[data-world-mode]').forEach(button => {
        const active = button.dataset.worldMode === mode;
        button.classList.toggle('world-mode-active', active);
        if (active) {
          button.className = 'px-2 py-1 text-[11px] rounded bg-cyan-700 text-white font-medium';
        } else {
          button.className = 'px-2 py-1 text-[11px] rounded text-slate-300 hover:bg-slate-800';
        }
        button.setAttribute('aria-pressed', String(active));
      });
      const actual = mode === 'actual' || mode === 'overlay';
      const machine = mode === 'machine';
      const canvas = this.el('canvas');
      const geometryCanvas = this.el('geometry-canvas');
      const schematicCanvas = this.el('schematic-canvas');
      const machineView = this.el('machine');
      if (canvas) canvas.hidden = !actual;
      if (geometryCanvas) geometryCanvas.hidden = mode !== 'schematic';
      if (schematicCanvas) schematicCanvas.hidden = true;
      if (machineView) machineView.hidden = !machine;
      this.updateSelectionVisibility();
      if (mode === 'schematic') {
        clearTimeout(this.retryTimer);
        this.marker && (this.marker.visible = false);
        this.setText('hint', '3D 示意图：滑轮按下平移 · 左/右键拖拽翻转 · 滚轮缩放 · 原始材质槽位编码');
        if (this.ensureGeometryRenderer()) this.loadStructure();
        else {
          if (schematicCanvas) schematicCanvas.hidden = false;
          this.loadSchematic(true);
        }
      } else if (machine) {
        clearTimeout(this.retryTimer);
        this.marker && (this.marker.visible = false);
        this.setText('hint', 'AI 数据：固定字段 JSON · tile_records 可直接供程序读取 · 未验证通行性为 unknown');
        this.loadMachine();
      } else {
        this.setText('hint', '实际图：滑轮按下平移 · 左/右键拖拽翻转 · 滚轮缩放 · 单击模型选点');
        if (this.ensureRenderer()) this.load();
      }
      this.resize();
      requestAnimationFrame(() => this.resize());
      this.renderEvidence();
    }

    resize() {
      const box = this.el('stage');
      const width = Math.max(1, box.clientWidth);
      const height = Math.max(1, box.clientHeight);
      this.schematic?.resize();
      this.geometry?.resize();
      if (!this.renderer) return;
      this.renderer.setSize(width, height, false);
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
    }

    animate() {
      requestAnimationFrame(() => this.animate());
      this.controls?.update();
      this.renderer?.render(this.scene, this.camera);
    }

    bindMapPicking(canvas) {
      canvas.addEventListener('pointerdown', event => {
        if (event.button !== 0 && event.button !== 2) return;
        this.pointerStart = {
          button: event.button,
          pointerId: event.pointerId,
          x: event.clientX,
          y: event.clientY,
          time: Date.now(),
        };
      });
      canvas.addEventListener('pointerup', event => {
        const start = this.pointerStart;
        this.pointerStart = null;
        if (!start || start.pointerId !== event.pointerId || start.button !== event.button) return;
        const dist = Math.hypot(event.clientX - start.x, event.clientY - start.y);
        const duration = Date.now() - start.time;
        // Allow up to 14px movement or fast click (< 350ms) to ensure smooth picking
        if (dist > 14 && duration > 350) return;
        this.pickMapSurface(event);
      });
      canvas.addEventListener('pointercancel', () => { this.pointerStart = null; });
    }

    pickMapSurface(event) {
      if (!this.raycaster || !this.camera || !this.group?.children.length) return;
      if (this.mode !== 'actual' && this.mode !== 'overlay') return;
      const canvas = this.el('canvas');
      const rect = canvas.getBoundingClientRect();
      const pointer = new THREE.Vector2(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1,
      );
      this.scene.updateMatrixWorld(true);
      this.camera.updateMatrixWorld(true);
      this.group.updateMatrixWorld(true);
      this.renderEntries.forEach(entry => entry.copy.updateMatrixWorld(true));
      this.raycaster.setFromCamera(pointer, this.camera);

      // Raycast directly against this.group (the actually displayed model nodes)
      const hits = this.raycaster.intersectObjects(this.group.children, true);
      const hit = hits.find(h => {
        let p = h.object;
        while (p) {
          if (p === this.selectionMarker || p === this.marker || p.name === 'selectionPillarGroup' || p.name === 'headingArrow') return false;
          p = p.parent;
        }
        return true;
      });

      if (hit) {
        const entry = this.entryForObject(hit.object);
        if (!entry) {
          this.setStatus('选中的网格没有对应的 ROM 模型记录。', true);
          return;
        }
        const point = hit.point.clone();
        const local = entry.copy.worldToLocal(point.clone());
        this.selectMapPoint(entry, point, local, 'BMD0 mesh surface', hit);
        return;
      }

      const planePoint = this.projectToMapPlane();
      if (!planePoint) {
        this.setStatus('没有选到模型表面；请直接点击地图模型。', true);
        return;
      }
      const entry = this.entryAtScenePoint(planePoint);
      if (!entry) {
        this.setStatus('所点位置不在当前地图的已加载模型边界内。', true);
        return;
      }
      const local = entry.copy.worldToLocal(planePoint.clone());
      this.selectMapPoint(entry, planePoint, local, 'map-plane projection', null);
    }

    projectToMapPlane() {
      const bounds = new THREE.Box3().setFromObject(this.group);
      if (bounds.isEmpty()) return null;
      // Default to ground level 0 rather than negative extreme values like -8160
      const planeY = (bounds.min.y >= -64 && bounds.min.y <= 64) ? bounds.min.y : 0;
      const point = this.raycaster.ray.intersectPlane(
        new THREE.Plane(new THREE.Vector3(0, 1, 0), -planeY), new THREE.Vector3(),
      );
      if (!point || point.x < bounds.min.x - 32 || point.x > bounds.max.x + 32
        || point.z < bounds.min.z - 32 || point.z > bounds.max.z + 32) return null;
      return point;
    }

    entryAtScenePoint(point) {
      return this.renderEntries.find(entry => {
        const bounds = new THREE.Box3().setFromObject(entry.copy);
        return point.x >= bounds.min.x && point.x <= bounds.max.x
          && point.z >= bounds.min.z && point.z <= bounds.max.z;
      }) || null;
    }

    selectMapPoint(entry, point, local, coordinateStatus, hit = null) {
      this.lastSelectedTarget = { entry, point, local, coordinateStatus, hit };
      entry.copy.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(entry.copy);
      const width = Math.max(1, bounds.max.x - bounds.min.x);
      const depth = Math.max(1, bounds.max.z - bounds.min.z);
      const tileW = width / 32;
      const tileD = depth / 32;
      const localTileX = Math.min(31, Math.max(0, Math.floor((point.x - bounds.min.x) / tileW)));
      const localTileY = Math.min(31, Math.max(0, Math.floor((point.z - bounds.min.z) / tileD)));

      // Calculate area bounding box [minLocalX..maxLocalX, minLocalY..maxLocalY] based on areaSize (1, 3, 5)
      const halfArea = Math.floor((this.areaSize || 1) / 2);
      const minLocalX = Math.max(0, localTileX - halfArea);
      const maxLocalX = Math.min(31, localTileX + halfArea);
      const minLocalY = Math.max(0, localTileY - halfArea);
      const maxLocalY = Math.min(31, localTileY + halfArea);

      const areaSpanX = maxLocalX - minLocalX + 1;
      const areaSpanY = maxLocalY - minLocalY + 1;
      const areaCenterX = bounds.min.x + (minLocalX + areaSpanX / 2) * tileW;
      const areaCenterZ = bounds.min.z + (minLocalY + areaSpanY / 2) * tileD;

      // Scan all materials and height profiles across this area
      const areaScan = this.scanAreaMaterials(entry, minLocalX, maxLocalX, minLocalY, maxLocalY, tileW, tileD, bounds);
      const surfaceY = (areaScan.maxY !== -Infinity && areaScan.maxY !== undefined && !isNaN(areaScan.maxY))
        ? areaScan.maxY : (point.y ?? 16.0);

      const cell = entry.item?.cell || {};
      const globalTileX = Number.isFinite(Number(cell.x)) ? Number(cell.x) * 32 + localTileX : null;
      const globalTileY = Number.isFinite(Number(cell.y)) ? Number(cell.y) * 32 + localTileY : null;
      const modelId = entry.item?.model_id ?? null;
      const locName = resolveModelLocationName(modelId);

      // Extract mesh & material info from hit or from top scanned material
      let meshName = 'Mesh';
      let materialIndex = 0;
      let materialName = areaScan.materials[0]?.name || 'default';
      if (hit?.object) {
        meshName = hit.object.name || 'Mesh';
        if (hit.face?.materialIndex != null) {
          materialIndex = hit.face.materialIndex;
          if (Array.isArray(hit.object.material)) {
            materialName = hit.object.material[materialIndex]?.name || `mat_${materialIndex}`;
          } else if (hit.object.material) {
            materialName = hit.object.material.name || 'mat_0';
          }
        } else if (hit.object.material) {
          materialName = Array.isArray(hit.object.material)
            ? hit.object.material[0]?.name || 'mat_0'
            : hit.object.material.name || 'mat_0';
        }
      }
      if ((!materialName || materialName === 'default') && areaScan.materials.length > 0) {
        materialName = areaScan.materials[0].name;
      }

      const heightLayer = interpretHeightLayer(surfaceY);
      const rangeTag = this.areaSize > 1
        ? `[${minLocalX}..${maxLocalX},${minLocalY}..${maxLocalY}](${this.areaSize}x${this.areaSize})`
        : `[${localTileX},${localTileY}]`;
      const checkCode = `#${modelId ?? '?'}(${locName.split(' ')[0]})${rangeTag}@Y${surfaceY.toFixed(1)}`;

      this.selectedSurface = this.selectionRecord(
        entry, point, local, coordinateStatus,
        {
          localTileX, localTileY, globalTileX, globalTileY,
          minLocalX, maxLocalX, minLocalY, maxLocalY,
          areaSpanX, areaSpanY, areaMaterials: areaScan,
          meshName, materialIndex, materialName,
          surfaceY, heightLayer,
          checkCode, locationName: locName
        },
      );

      // Scale & position the 3D box pillar highlight to encompass the chosen XZ area
      this.selectionMarker.scale.set((tileW * areaSpanX) / 16, 1, (tileD * areaSpanY) / 16);
      this.selectionMarker.position.set(areaCenterX, surfaceY, areaCenterZ);
      this.updateSelectionVisibility();
      this.setText('selection', this.selectionText(this.selectedSurface));
      this.renderMaterialsListUI(this.selectedSurface);
      this.setStatus(`已选: ${checkCode} · ${heightLayer.tag} · 共 ${areaScan.materials.length} 种材质`);
    }

    scanAreaMaterials(entry, minLocalX, maxLocalX, minLocalY, maxLocalY, tileW, tileD, bounds) {
      const materialsMap = new Map();
      const allHeights = [];

      const minBoxX = -256 + minLocalX * 16;
      const maxBoxX = -256 + (maxLocalX + 1) * 16;
      const minBoxZ = -256 + minLocalY * 16;
      const maxBoxZ = -256 + (maxLocalY + 1) * 16;

      // 1. Precise triangle-level scan on entry.copy mesh primitives
      entry.copy.traverse(child => {
        if (!child.isMesh || !child.geometry) return;
        const pos = child.geometry.attributes.position;
        if (!pos) return;
        const index = child.geometry.index;
        const mat = child.material;
        const matName = Array.isArray(mat) ? mat[0]?.name : mat?.name || child.name || 'default';

        if (index) {
          for (let i = 0; i < index.count; i += 3) {
            const i0 = index.getX(i), i1 = index.getX(i + 1), i2 = index.getX(i + 2);
            const x0 = pos.getX(i0), y0 = pos.getY(i0), z0 = pos.getZ(i0);
            const x1 = pos.getX(i1), y1 = pos.getY(i1), z1 = pos.getZ(i1);
            const x2 = pos.getX(i2), y2 = pos.getY(i2), z2 = pos.getZ(i2);
            const triMinX = Math.min(x0, x1, x2), triMaxX = Math.max(x0, x1, x2);
            const triMinZ = Math.min(z0, z1, z2), triMaxZ = Math.max(z0, z1, z2);
            if (!(triMaxX < minBoxX || triMinX > maxBoxX || triMaxZ < minBoxZ || triMinZ > maxBoxZ)) {
              const avgY = Number(((y0 + y1 + y2) / 3).toFixed(1));
              allHeights.push(avgY);
              if (!materialsMap.has(matName)) {
                materialsMap.set(matName, {
                  name: matName,
                  interpretation: this.interpretMaterialName(matName) || '常规地表',
                  heights: new Set(),
                  hitCount: 0,
                });
              }
              const item = materialsMap.get(matName);
              item.heights.add(avgY);
              item.hitCount++;
            }
          }
        } else {
          for (let i = 0; i < pos.count; i += 3) {
            const x0 = pos.getX(i), y0 = pos.getY(i), z0 = pos.getZ(i);
            const x1 = pos.getX(i + 1), y1 = pos.getY(i + 1), z1 = pos.getZ(i + 1);
            const x2 = pos.getX(i + 2), y2 = pos.getY(i + 2), z2 = pos.getZ(i + 2);
            const triMinX = Math.min(x0, x1, x2), triMaxX = Math.max(x0, x1, x2);
            const triMinZ = Math.min(z0, z1, z2), triMaxZ = Math.max(z0, z1, z2);
            if (!(triMaxX < minBoxX || triMinX > maxBoxX || triMaxZ < minBoxZ || triMinZ > maxBoxZ)) {
              const avgY = Number(((y0 + y1 + y2) / 3).toFixed(1));
              allHeights.push(avgY);
              if (!materialsMap.has(matName)) {
                materialsMap.set(matName, {
                  name: matName,
                  interpretation: this.interpretMaterialName(matName) || '常规地表',
                  heights: new Set(),
                  hitCount: 0,
                });
              }
              const item = materialsMap.get(matName);
              item.heights.add(avgY);
              item.hitCount++;
            }
          }
        }
      });

      // 2. If triangle-level scan didn't find materials (e.g. schematic custom materials), fallback to raycasting
      if (!materialsMap.size) {
        for (let tx = minLocalX; tx <= maxLocalX; tx++) {
          for (let ty = minLocalY; ty <= maxLocalY; ty++) {
            const centerX = bounds.min.x + (tx + 0.5) * tileW;
            const centerZ = bounds.min.z + (ty + 0.5) * tileD;
            const topY = bounds.max.y + 128;
            const downRay = new THREE.Raycaster(
              new THREE.Vector3(centerX, topY, centerZ),
              new THREE.Vector3(0, -1, 0),
              0,
              topY - bounds.min.y + 128,
            );
            const hits = downRay.intersectObject(entry.copy, true);
            hits.forEach(h => {
              const yVal = Number(h.point.y.toFixed(1));
              allHeights.push(yVal);
              let matName = h.object.material?.name || 'default';
              if (!materialsMap.has(matName)) {
                materialsMap.set(matName, {
                  name: matName,
                  interpretation: this.interpretMaterialName(matName) || '常规地表',
                  heights: new Set(),
                  hitCount: 0,
                });
              }
              const item = materialsMap.get(matName);
              item.heights.add(yVal);
              item.hitCount++;
            });
          }
        }
      }

      const matList = Array.from(materialsMap.values()).map(m => ({
        name: m.name,
        interpretation: m.interpretation,
        heights: Array.from(m.heights).sort((a, b) => b - a),
        hitCount: m.hitCount,
      }));

      return {
        materials: matList,
        minY: allHeights.length ? Math.min(...allHeights) : 0,
        maxY: allHeights.length ? Math.max(...allHeights) : 0,
      };
    }

    renderMaterialsListUI(selection) {
      const container = this.el('materials-list');
      if (!container) return;
      container.replaceChildren();

      const materials = selection.area_materials?.materials || [];
      if (!materials.length) {
        const empty = document.createElement('div');
        empty.className = 'text-slate-500 italic text-[11px] p-1';
        empty.textContent = '该区域未检测到独立材质面片。';
        container.appendChild(empty);
        return;
      }

      materials.forEach(mat => {
        const item = document.createElement('button');
        item.className = 'w-full text-left p-1.5 rounded bg-slate-900/90 hover:bg-slate-800 border border-slate-700/60 flex items-center justify-between gap-2 transition text-[11px] group';

        const left = document.createElement('div');
        left.className = 'flex items-center gap-1.5 min-w-0';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'font-mono text-cyan-300 font-semibold truncate group-hover:text-cyan-200';
        nameSpan.textContent = mat.name;
        const typeSpan = document.createElement('span');
        typeSpan.className = 'text-slate-400 text-[10px] truncate';
        typeSpan.textContent = mat.interpretation;
        left.appendChild(nameSpan);
        left.appendChild(typeSpan);

        const right = document.createElement('div');
        right.className = 'flex items-center gap-1.5 flex-shrink-0';
        const heightBadge = document.createElement('span');
        heightBadge.className = 'px-1 py-0.5 rounded bg-slate-800 text-amber-300 text-[10px] font-mono border border-slate-700';
        heightBadge.textContent = `Y=${mat.heights.join('/')}`;
        const copyHint = document.createElement('span');
        copyHint.className = 'text-cyan-400 text-[10px] font-semibold opacity-0 group-hover:opacity-100 transition';
        copyHint.textContent = '复制';
        right.appendChild(heightBadge);
        right.appendChild(copyHint);

        item.appendChild(left);
        item.appendChild(right);

        item.addEventListener('click', () => {
          const text = `[素材核对] ${selection.check_code} 材质: ${mat.name} · 高度: Y=${mat.heights.join('/')} · 地貌: ${mat.interpretation}`;
          try { navigator.clipboard.writeText(text); } catch (_) {}
          this.setStatus(`已复制素材信息: ${mat.name} (Y=${mat.heights.join('/')})`);
        });

        container.appendChild(item);
      });
    }

    copyAllMaterials() {
      if (!this.selectedSurface) {
        this.setStatus('请先在地图上选择一个区域。', true);
        return;
      }
      const sel = this.selectedSurface;
      const materials = sel.area_materials?.materials || [];
      const matLines = materials.map(m => `  * 材质: ${m.name} | 高度: Y=${m.heights.join('/')} | 地貌: ${m.interpretation} (${m.hitCount}面片)`).join('\n');
      const text = [
        `【区域素材清单】${sel.check_code}`,
        `【地图位置】${sel.location_name} · 瓦片范围 [X:${sel.tile_range?.minX ?? sel.tile?.local_x}..${sel.tile_range?.maxX ?? sel.tile?.local_x}, Y:${sel.tile_range?.minY ?? sel.tile?.local_y}..${sel.tile_range?.maxY ?? sel.tile?.local_y}]`,
        `【高度跨度】Y=${sel.area_materials?.minY ?? 0} ~ Y=${sel.area_materials?.maxY ?? 0}`,
        `【包含素材 (${materials.length} 种)】:`,
        matLines || '  (无材质记录)',
      ].join('\n');
      try { navigator.clipboard.writeText(text); } catch (_) {}
      this.setStatus(`已复制 ${materials.length} 种区域素材清单到剪贴板`);
    }

    entryForObject(object) {
      let node = object;
      while (node) {
        if (node.userData?.mapEntry) return node.userData.mapEntry;
        node = node.parent;
      }
      return null;
    }

    interpretMaterialName(name) {
      if (!name || name === 'default') return null;
      const n = name.toLowerCase();
      if (n.includes('dansa') || n.includes('step')) return '【高低差/台阶】单向跳跃坎台 (Ledge / Step)';
      if (n.includes('gake') || n.includes('yama')) return '【悬崖/山岩】固体阻挡障碍 (Cliff / Mountain)';
      if (n.includes('kabe') || n.includes('wall')) return '【墙体/建筑】固体阻挡障碍 (Wall / Building)';
      if (n.includes('kusa') || n.includes('grass')) return '【草地/草丛】地面/可能遇敌 (Grass)';
      if (n.includes('mizu') || n.includes('water') || n.includes('umi') || n.includes('ike') || n.includes('shore')) return '【水面/水域】需冲浪技能 (Water)';
      if (n.includes('michi') || n.includes('miti') || n.includes('douro') || n.includes('road') || n.includes('tile') || n.includes('isi') || n.includes('yuka') || n.includes('hibi') || n.includes('haizai') || n.includes('ochiba')) return '【道路/石砖地】可通行硬化地面 (Road / Floor)';
      if (n.includes('ki') || n.includes('tree') || n.includes('mori')) return '【树木/森林】不可通行植被障碍 (Tree)';
      if (n.includes('saku') || n.includes('fence') || n.includes('tesuri') || n.includes('isu')) return '【栅栏/长椅】阻挡障碍物 (Fence / Obstacle)';
      if (n.includes('kadan') || n.includes('flower') || n.includes('hana') || n.includes('saien') || n.includes('rabender')) return '【花坛/花圃】景观装饰 (Flowerbed / Garden)';
      if (n.includes('hasi') || n.includes('bridge')) return '【桥梁】可通过通道 (Bridge)';
      if (n.includes('saka') || n.includes('slope')) return '【斜坡】高度过渡坡道 (Slope)';
      if (n.includes('kaidan') || n.includes('stair')) return '【楼梯】高程连接台阶 (Stairs)';
      if (n.includes('tuta')) return '【藤蔓/墙面细节】装饰物 (Vine)';
      if (n.includes('air') || n.includes('sky')) return '【空气/虚空】不可停留 (Air / Sky)';
      return null;
    }

    decodePermissionByte(byte) {
      if (byte == null) return '未知 / 未检测到碰撞字节';
      const hex = `0x${Number(byte).toString(16).padStart(2, '0').toUpperCase()}`;
      const MAP = {
        0x00: '【可通行】平地 / 道路 / 普通地面 (无阻挡)',
        0x01: '【不可通行】固体阻挡 / 墙体 / 建筑物 / 树木',
        0x02: '【不可通行】不可穿越边界障碍',
        0x08: '【水岸】水面与陆地边缘',
        0x0C: '【水域】深水区 / 需要冲浪技能',
        0x10: '【传送门】建筑入口 / 室内换区 Warp 门',
        0x11: '【传送门】出入口 Warp 点',
        0x38: '【高低差】单向向南跳跃坎台 (Ledge ↓)',
        0x39: '【高低差】单向向北跳跃坎台 (Ledge ↑)',
        0x3A: '【高低差】单向向西跳跃坎台 (Ledge ←)',
        0x3B: '【高低差】单向向东跳跃坎台 (Ledge →)',
        0x3C: '【草丛】野生宝可梦高草丛 (可遇敌)',
        0x3D: '【深草丛】深色野生草丛 (双打遇敌)',
        0x80: '【楼梯】高程楼梯 / 坡道',
      };
      return `${hex} - ${MAP[byte] || '特殊事件/未知行为'}`;
    }

    selectionRecord(entry, scenePoint, localPoint, coordinateStatus, extra = {}) {
      const cell = entry.item?.cell || {};
      const modelId = entry.item?.model_id ?? null;
      const textureId = entry.item?.texture_id ?? null;

      let passabilityHint = null;
      // 1. Check ROM material name interpretation
      const matInterpretation = this.interpretMaterialName(extra.materialName);
      if (matInterpretation) {
        passabilityHint = `材质推断: ${matInterpretation}`;
      }

      // 2. Check collision/permission table if available
      if (extra.globalTileX != null && extra.globalTileY != null && this.schematicData?.tile_records) {
        const rec = this.schematicData.tile_records.find(
          r => r[0] === extra.globalTileX && r[1] === extra.globalTileY,
        );
        if (rec && rec.length > 3) {
          const p00 = rec[3];
          const permStr = this.decodePermissionByte(p00);
          passabilityHint = passabilityHint ? `${permStr} | ${passabilityHint}` : permStr;
        }
      }

      if (!passabilityHint) {
        passabilityHint = '未绑定实时碰撞字节（可通过地貌人工识别）';
      }

      const hitScenePoint = extra.surfaceY != null
        ? { x: scenePoint.x, y: Number(extra.surfaceY.toFixed(3)), z: scenePoint.z }
        : scenePoint;

      return {
        format: 'black2-render-surface-anchor/v2',
        check_code: extra.checkCode || `#${modelId}[${extra.localTileX},${extra.localTileY}]@Y${(extra.surfaceY ?? 0).toFixed(1)}`,
        location_name: extra.locationName || resolveModelLocationName(modelId),
        source: {
          map_header_id: this.info?.map_header_id ?? this.info?.map_definition_id ?? null,
          matrix_id: this.info?.matrix_id ?? null,
          model_id: modelId,
          texture_id: textureId,
          matrix_cell: Number.isFinite(Number(cell.x)) && Number.isFinite(Number(cell.y))
            ? { x: Number(cell.x), y: Number(cell.y) } : null,
          scene_cache_key: this.info?.cache?.key ?? null,
        },
        tile: {
          local_x: extra.localTileX,
          local_y: extra.localTileY,
          global_x: extra.globalTileX,
          global_y: extra.globalTileY,
        },
        tile_range: {
          minX: extra.minLocalX ?? extra.localTileX,
          maxX: extra.maxLocalX ?? extra.localTileX,
          minY: extra.minLocalY ?? extra.localTileY,
          maxY: extra.maxLocalY ?? extra.localTileY,
          spanX: extra.areaSpanX ?? 1,
          spanY: extra.areaSpanY ?? 1,
        },
        area_materials: extra.areaMaterials || null,
        height_layer: extra.heightLayer || interpretHeightLayer(extra.surfaceY),
        detected_layers: extra.detectedLayers || [],
        mesh_info: {
          name: extra.meshName,
          material_index: extra.materialIndex,
          material_name: extra.materialName,
          material_code: `M${modelId ?? '?'}.T${extra.materialIndex ?? 0}`,
          material_guess: matInterpretation || '未自动推断',
        },
        render_scene_point: this.roundPoint(hitScenePoint),
        render_model_point: this.roundPoint(localPoint),
        coordinate_status: `${coordinateStatus}; tile [${extra.localTileX}, ${extra.localTileY}]`,
        passability_hint: passabilityHint,
      };
    }

    roundPoint(point) {
      return {
        x: Number(point.x.toFixed(3)),
        y: Number(point.y.toFixed(3)),
        z: Number(point.z.toFixed(3)),
      };
    }

    selectionText(selection) {
      const source = selection.source;
      const cell = source.matrix_cell ? `(${source.matrix_cell.x}, ${source.matrix_cell.y})` : '候选未定';
      const scene = selection.render_scene_point;
      const tile = selection.tile || {};
      const range = selection.tile_range || {};
      const mesh = selection.mesh_info || {};
      const hLayer = selection.height_layer || interpretHeightLayer(scene.y);
      const locName = selection.location_name || resolveModelLocationName(source.model_id);
      const materials = selection.area_materials?.materials || [];

      const globalStr = (tile.global_x != null && tile.global_y != null)
        ? `全局瓦片: [X=${tile.global_x}, Y=${tile.global_y}]`
        : '全局坐标: 等待矩阵原点对齐';

      const rangeStr = (range.spanX > 1 || range.spanY > 1)
        ? `局部范围: [X:${range.minX}..${range.maxX}, Y:${range.minY}..${range.maxY}] (${range.spanX}×${range.spanY} 区域)`
        : `局部瓦片: [${tile.local_x ?? '?'}, ${tile.local_y ?? '?'}] (0~31)`;

      const matSummary = materials.length > 0
        ? `包含素材 (${materials.length} 种): ${materials.map(m => `${m.name}(Y=${m.heights.join('/')})`).join(', ')}`
        : `材质: ${mesh.material_name || 'default'} (槽位 #${mesh.material_index ?? 0})`;

      const userTagStr = selection.user_tag ? `\n【人工标注】${selection.user_tag}` : '';

      return [
        `【核对代号】${selection.check_code || `#${source.model_id}[${tile.local_x},${tile.local_y}]`}`,
        `【地名/模型】${locName} (模型 #${source.model_id ?? '—'} · Chunk ${cell})`,
        `【地块网格】${rangeStr}`,
        `【游戏坐标】${globalStr}`,
        `【高度层级】${hLayer.tag} · ${hLayer.desc} (3D Y=${scene.y})`,
        `【材质信息】${matSummary}`,
        `【通行/地貌】${selection.passability_hint || '待实测标记'}${userTagStr}`,
      ].join('\n');
    }

    async copySelectionCode() {
      if (!this.selectedSurface) {
        this.setStatus('请先点击地图选择一个瓦片。', true);
        return;
      }
      const code = this.selectedSurface.check_code || `#${this.selectedSurface.source.model_id}[${this.selectedSurface.tile.local_x},${this.selectedSurface.tile.local_y}]`;
      const text = `${code} (${this.selectedSurface.location_name || '地图'} 瓦片[${this.selectedSurface.tile.local_x}, ${this.selectedSurface.tile.local_y}] Y=${this.selectedSurface.render_scene_point?.y})`;
      try {
        await navigator.clipboard.writeText(text);
      } catch (_) {
        const area = document.createElement('textarea');
        area.value = text;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        area.remove();
      }
      this.setStatus(`已复制核对代号: ${text}（直接粘贴发送给 AI）`);
    }

    quickTagTerrain(tag) {
      if (!this.selectedSurface) {
        this.setStatus('请先点击地图选择一个瓦片。', true);
        return;
      }
      this.selectedSurface.user_tag = tag;
      const code = this.selectedSurface.check_code;
      const copyText = `[地貌标注] ${code} = 【${tag}】`;
      this.setText('selection', this.selectionText(this.selectedSurface));
      try {
        navigator.clipboard.writeText(copyText);
      } catch (_) {}
      this.setStatus(`已标记为【${tag}】并复制: ${copyText}`);
    }

    async copySelection() {
      if (!this.selectedSurface) return;
      const text = JSON.stringify(this.selectedSurface, null, 2);
      try {
        await navigator.clipboard.writeText(text);
      } catch (_) {
        const area = document.createElement('textarea');
        area.value = text;
        area.style.position = 'fixed';
        area.style.opacity = '0';
        document.body.appendChild(area);
        area.select();
        document.execCommand('copy');
        area.remove();
      }
      this.setStatus('地图锚点已复制。请粘贴给我，并补充角色的游戏坐标 X,Y。');
    }

    clearSelection() {
      this.selectedSurface = null;
      if (this.selectionMarker) this.selectionMarker.visible = false;
      const panel = this.el('selection-panel');
      if (panel) panel.hidden = true;
      this.setText('selection', '尚未选择地图表面。');
    }

    setPlayerRenderAnchor() {
      if (!this.selectedSurface) {
        this.setStatus('请先点击实际图中主角脚下的位置。', true);
        return;
      }
      const player = this.info?.live_player || null;
      this.playerRenderAnchor = {
        render_scene_point: { ...this.selectedSurface.render_scene_point },
        render_model_point: { ...this.selectedSurface.render_model_point },
        source: { ...this.selectedSurface.source },
        game_position: this.copyLivePlayer(player),
      };
      this.updatePlayerXYZ(player);
      this.setStatus('主角实际图 XYZ 锚点已记录；主角移动后会明确标记为需重新确认。');
    }

    clearPlayerRenderAnchor() {
      this.playerRenderAnchor = null;
      this.updatePlayerXYZ(this.info?.live_player || null);
    }

    copyLivePlayer(player) {
      if (!player?.verified || !Number.isFinite(Number(player.x)) || !Number.isFinite(Number(player.y))) {
        return null;
      }
      return {
        x: Number(player.x),
        y: Number(player.y),
        elevation: Number.isFinite(Number(player.elevation)) ? Number(player.elevation) : null,
      };
    }

    updatePlayerXYZ(player) {
      const game = this.copyLivePlayer(player);
      if (!game) {
        this.setText('player-xyz', '游戏 XYZ：等待 ARM9 玩家坐标。\n实际图 XYZ：未设置。');
        return;
      }
      const mapName = player?.map_name || (player?.map_section_id ? `Map #${player.map_section_id}` : '未定');
      const facing = player?.facing || 'South';
      const facingCn = { 'North': '北 (North/↑)', 'South': '南 (South/↓)', 'West': '西 (West/←)', 'East': '东 (East/→)' }[facing] || facing;
      const chunkX = Math.floor(game.x / 32);
      const chunkY = Math.floor(game.y / 32);
      const localX = game.x % 32;
      const localY = game.y % 32;

      const gameLine = [
        `【当前地图】${mapName}`,
        `【游戏全局】X=${game.x}  Y=${game.y}  E=${game.elevation ?? 0}（E 为高程层级）`,
        `【分块索引】Chunk=(${chunkX}, ${chunkY}) · 局部 Local=(${localX}, ${localY})`,
        `【主角朝向】${facingCn} · 状态: ${player?.movement_state || 'Unresolved'}`
      ].join('\n');

      const anchor = this.playerRenderAnchor;
      if (!anchor) {
        this.setText('player-xyz', `${gameLine}\n\n【实际图 3D】自动吸附 BMD0 网格表面；点击脚下可“设为主角”校准。`);
        return;
      }
      const current = anchor.game_position;
      const sameTile = current && current.x === game.x && current.y === game.y;
      const point = anchor.render_scene_point;
      const renderLine = `【实际图 3D】X=${point.x}  Y=${point.y}  Z=${point.z}`;
      const state = sameTile
        ? '状态：已验证当前主角锚点。'
        : `状态：上次确认于 X=${current?.x ?? '?'},Y=${current?.y ?? '?'}；主角已移动。`;
      this.setText('player-xyz', `${gameLine}\n\n${renderLine}\n${state}`);
    }

    updateSelectionVisibility() {
      const visible = Boolean(this.selectedSurface && (this.mode === 'actual' || this.mode === 'overlay'));
      if (this.selectionMarker) this.selectionMarker.visible = visible;
      const panel = this.el('selection-panel');
      if (panel) panel.hidden = !visible;
    }

    createSelectionMarker() {
      const group = new THREE.Group();
      group.name = 'selectionPillarGroup';

      const pillarHeight = 24;
      const baseTileSize = 16;

      // 1. Semi-transparent 3D box column (centered on tile, base sits at y=0)
      const boxGeo = new THREE.BoxGeometry(baseTileSize, pillarHeight, baseTileSize);
      const boxMat = new THREE.MeshBasicMaterial({
        color: 0x06b6d4, // Cyan
        transparent: true,
        opacity: 0.38,
        depthTest: true,
        depthWrite: false,
        side: THREE.DoubleSide,
      });
      const pillar = new THREE.Mesh(boxGeo, boxMat);
      pillar.name = 'pillarMesh';
      pillar.position.y = pillarHeight / 2; // base at y=0

      // 2. Crisp wireframe edge lines around the pillar
      const edgesGeo = new THREE.EdgesGeometry(boxGeo);
      const edgesMat = new THREE.LineBasicMaterial({
        color: 0x22d3ee, // Bright cyan edge
        linewidth: 2,
        transparent: true,
        opacity: 0.9,
        depthTest: true,
        depthWrite: false,
      });
      const edges = new THREE.LineSegments(edgesGeo, edgesMat);
      edges.name = 'pillarEdges';
      edges.position.y = pillarHeight / 2;

      // 3. Ground 1x1 tile footprint border (on the surface floor)
      const groundFrameGeo = new THREE.EdgesGeometry(new THREE.PlaneGeometry(baseTileSize, baseTileSize));
      const groundFrameMat = new THREE.LineBasicMaterial({
        color: 0x38bdf8,
        linewidth: 3,
        depthTest: true,
      });
      const groundFrame = new THREE.LineSegments(groundFrameGeo, groundFrameMat);
      groundFrame.name = 'groundFrame';
      groundFrame.rotation.x = -Math.PI / 2;
      groundFrame.position.y = 0.15; // slightly above floor to prevent z-fighting

      // 4. Ground tile highlight quad
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
      groundQuad.name = 'groundQuad';
      groundQuad.rotation.x = -Math.PI / 2;
      groundQuad.position.y = 0.1;

      // 5. Central vertical pin line from surface to pillar top
      const pinGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0, 0),
        new THREE.Vector3(0, pillarHeight, 0),
      ]);
      const pinMat = new THREE.LineBasicMaterial({
        color: 0xffffff,
        linewidth: 2,
        transparent: true,
        opacity: 0.8,
        depthTest: true,
      });
      const pin = new THREE.Line(pinGeo, pinMat);
      pin.name = 'centerPin';

      group.add(pillar, edges, groundFrame, groundQuad, pin);
      group.renderOrder = 500;
      group.visible = false;
      return group;
    }

    setStatus(text, bad = false) {
      const status = this.el('status');
      if (!status) return;
      status.textContent = text;
      status.className = bad ? 'text-xs text-rose-300' : 'text-xs text-slate-300';
    }

    refreshView() {
      if (this.mode === 'schematic') this.loadStructure();
      else if (this.mode === 'machine') this.loadMachine();
      else this.load();
    }

    async loadSchematic(includeRaw = true) {
      try {
        this.setStatus(includeRaw
          ? '正在读取当前驻留块的 Pxx 原始字节…'
          : '正在读取当前 ROM 编号示意图…');
        const response = await fetch(`/api/v1/map/schematic?include_raw=${includeRaw}`, { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        this.schematicData = data;
        this.schematic?.setData(data);
        this.showSchematicInfo(data);
        this.el('knowledge').textContent = data.ai_text || 'AI 地图代码未生成。';
        this.showSchematicEvents(data);
        if (data.status === 'aligned') {
          const cells = data.matrix?.cells?.length || 0;
          const rawLabel = data.raw_permissions_included ? 'Pxx 原始格已读取' : '未读取 Pxx';
          this.setStatus(`示意图已读取：${cells} 个矩阵格；${rawLabel}；未请求 GLB、官方贴图或截图。`);
        } else if (data.status === 'unanchored') {
          this.setStatus('已读取 ROM 候选，但玩家矩阵原点未验证；不绘制假地图。');
        } else {
          this.setStatus(`示意图暂不可用：${data.message}`, true);
        }
      } catch (error) {
        this.setStatus(`无法读取示意图：${error.message}`, true);
      }
    }

    ensureGeometryRenderer() {
      const ok = Boolean(this.geometry?.initialize());
      if (ok && this.geometry) {
        this.geometry.showAxes = this.showAxes;
        if (this.geometry.axesGroup) {
          this.geometry.axesGroup.visible = this.showAxes;
        }
        this.geometry.showNavMesh = this.showNavMesh;
        if (this.geometry.navMeshGroup) {
          this.geometry.navMeshGroup.visible = this.showNavMesh;
        }
      }
      return ok;
    }

    async loadStructure() {
      if (!this.ensureGeometryRenderer()) {
        this.loadSchematic(true);
        return;
      }
      if (this.loadBusy) {
        this.pendingLoad = true;
        return;
      }
      this.loadBusy = true;
      this.setStatus('正在读取 BMD0 无贴图几何与原始 Pxx 编码…');
      try {
        const geometryResponse = await fetch('/api/v1/map/geometry', { cache: 'no-store' });
        const geometry = await geometryResponse.json();
        if (!geometryResponse.ok) throw new Error(geometry.detail || `HTTP ${geometryResponse.status}`);
        if (!geometry.renderable || !(geometry.models || []).length) {
          const isCandidate = geometry.display_mode === 'candidate-gallery'
            || geometry.display_mode === 'texture-candidate-gallery';
          if (!this.hasVerifiedStructure()) {
            this.geometry.clear();
            if (isCandidate && geometry.live_player) {
              this.showCandidateBackendInfo(geometry);
            } else {
              this.setText('geometry-codes', '等待 BMD0、矩阵和玩家落点同时获得验证；不绘制候选假地图。');
            }
          }
          this.scheduleAlignmentRetry(
            this.hasVerifiedStructure()
              ? '暂未取得新地图坐标，保留上一份已验证结构图'
              : isCandidate
                ? '候选矩阵已读取，等待玩家-矩阵原点对齐验证'
                : '3D 示意图等待已验证场景',
          );
          return;
        }
        this.info = geometry;
        if (geometry.scene?.id || geometry.cache?.key) {
          this.lastSceneCacheKey = geometry.scene?.id || geometry.cache.key;
        }
        const codes = await this.geometry.render(geometry);
        this.showStructureInfo(geometry, codes);
        const scope = geometry.player_alignment?.verified ? '当前地图块' : '独立候选模型';
        this.setStatus(`3D 示意图已加载：${geometry.models.length} 个 BMD0 几何块 · ${scope} · 未请求 BTX0、PNG 或官方材质。`);
        this.loadStructureEvidence(geometry);
      } catch (error) {
        this.setStatus(`无法读取 3D 示意图：${error.message}`, true);
      } finally {
        this.loadBusy = false;
        if (this.pendingLoad) {
          this.pendingLoad = false;
          this.refreshView();
        }
      }
    }

    async loadStructureEvidence(geometry) {
      try {
        const response = await fetch('/api/v1/map/schematic?include_raw=true', { cache: 'no-store' });
        const schematic = await response.json();
        if (!response.ok || schematic.status !== 'aligned' || !this.sameStructureScene(geometry, schematic)) {
          this.scheduleAlignmentRetry('BMD0 结构已显示，正在补充同一帧的原始 Pxx 和事件记录');
          return;
        }
        this.schematicData = schematic;
        this.showSchematicInfo(schematic);
        this.showSchematicEvents(schematic);
        this.el('knowledge').textContent = schematic.ai_text || 'AI 地图代码未生成。';
      } catch (_) {
        this.scheduleAlignmentRetry('BMD0 结构已显示，正在补充同一帧的原始 Pxx 和事件记录');
      }
    }

    sameStructureScene(geometry, schematic) {
      if (geometry.matrix_id !== schematic.matrix?.id) return false;
      const geometryPlayer = geometry.live_player || {};
      const schematicPlayer = schematic.live_player || {};
      return geometryPlayer.x === schematicPlayer.x && geometryPlayer.y === schematicPlayer.y;
    }

    hasVerifiedStructure() {
      return Boolean(this.info?.player_alignment?.verified && this.geometry?.entries?.length);
    }

    showCandidateBackendInfo(geometry) {
      // Show rich backend state info in the geometry-codes panel while waiting for alignment
      const player = geometry.live_player || {};
      const pa = geometry.player_alignment || {};
      const scene = geometry.scene || {};
      const candidateScenes = geometry.candidate_scenes || [];
      // loaded_model_ids is top-level in the geometry response (not nested under scene)
      const modelIds = (geometry.candidate_model_ids || geometry.loaded_model_ids || scene.active_model_ids || scene.loaded_model_ids || []);
      const mapSection = player.map_section_id != null ? `地图区段 #${player.map_section_id}` : '';

      const verification = geometry.verification || {};
      const bmd0Offsets = verification.loaded_bmd0_offsets || {};
      const bmd0Info = Object.entries(bmd0Offsets).map(([id, addrs]) => `BMD0 #${id}@${addrs[0]?.toString(16).toUpperCase()}`).join(', ');

      const candidateMatrixIds = candidateScenes.map(s => `#${s.matrix_id}`).join(', ');
      const candidateDetail = candidateScenes.map(s => {
        const cells = (s.resident_cells || []).map(c => `(${c.x},${c.y})→#${c.model_id}`).join(' ');
        return `  矩阵 #${s.matrix_id} [${s.matrix_size?.width}×${s.matrix_size?.height}]: ${cells}`;
      });

      const lines = [
        `后端状态：${geometry.display_mode || 'candidate-gallery'}`,
        `玩家全局坐标：X=${player.x ?? '?'}, Y=${player.y ?? '?'}`,
        mapSection,
        player.facing ? `玩家朝向：${player.facing}  运动状态：${player.movement_state || '?'}` : '',
        `矩阵原点对齐：${pa.verified ? '已验证' : pa.reason || '候选阶段，尚未验证'}`,
        candidateScenes.length > 0
          ? `候选矩阵：${candidateMatrixIds}（共 ${candidateScenes.length} 个候选场景）`
          : '',
        ...candidateDetail,
        modelIds.length > 0
          ? `已加载模型 ID：${modelIds.map(id => `#${id}`).join(', ')}`
          : '',
        '',
        '等待玩家-矩阵原点对齐后即可渲染 BMD0 几何。',
        '对齐验证后无需手动刷新，系统自动切换。',
      ].filter(s => s !== undefined && s !== null);

      this.setText('geometry-codes', lines.join('\n'));

      // Also update the info panel fields with what we know
      this.setText('map-id', player.map_section_id ?? '候选');
      this.setText('matrix', candidateScenes.length > 0
        ? `${candidateScenes.length} 个候选矩阵`
        : '候选解析中');
      this.setText('chunk', '玩家原点未验证');
      this.setText('model', modelIds.length > 0 ? modelIds.map(id => `#${id}`).join(', ') : '—');
      this.setText('texture', '示意图模式：不读取贴图');
      this.setText('verified', pa.reason || '候选阶段');
      this.updatePlayerXYZ(player);
    }

    showStructureInfo(geometry, codes) {
      const player = geometry.live_player || {};
      this.setText('texture', '未请求 BTX0 / PNG');
      this.setText('verified', geometry.player_surface_projection?.verified
        ? 'BMD0 几何 / 矩阵 / 玩家表面落点'
        : 'BMD0 几何 / 矩阵 / 玩家块；表面落点未校准');
      this.setText('source', [
        this.el('source')?.textContent || '',
        '3D 结构：ROM BMD0 裸几何；所有图片和官方材质引用已在后端剥离。',
        `几何缓存：${geometry.cache?.hit ? '命中' : '已生成'} · ARM9 原始层字段 L=${player.elevation ?? '?'}（未用于模型高度）`,
        geometry.geometry?.code_policy || '',
        geometry.player_surface_projection?.verified
          ? '玩家圆环：BMD0 表面落点已验证。'
          : `玩家圆环：未显示。${geometry.player_surface_projection?.reason || 'BMD0 平面变换未验证。'}`,
      ].filter(Boolean).join('\n'));
      const legend = codes.length
        ? codes.map(code => `${code.code}  raw=${code.raw_name}`).join('\n')
        : '该模型没有可列出的材质名；只显示 M<模型> 几何。';
      this.setText('geometry-codes', `${legend}\n\n说明：编码仅供后续人工/实测定义，当前不推断草、墙、路或可通行性。`);
    }

    async loadMachine() {
      if (this.machineBusy) return;
      this.machineBusy = true;
      try {
        this.setStatus('正在生成当前地图的 AI/寻路数据契约…');
        const response = await fetch('/api/v1/map/machine', { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        this.machineData = data;
        this.el('machine').textContent = JSON.stringify(data, null, 2);
        this.showMachineInfo(data);
        const count = Number(data.tile_record_count) || 0;
        this.setStatus(data.status === 'aligned'
          ? `AI 数据已生成：${count} 个全局坐标格；寻路语义 ${data.navigation?.route_planning_ready ? 'ready' : 'unknown'}`
          : `AI 数据等待地图锚定：${data.message}`);
      } catch (error) {
        this.setStatus(`无法生成 AI 地图数据：${error.message}`, true);
        this.el('machine').textContent = JSON.stringify({
          format: 'black2-ai-map/v1', status: 'unavailable', error: error.message,
        }, null, 2);
      } finally {
        this.machineBusy = false;
      }
    }

    showMachineInfo(data) {
      const player = data.player || {};
      const tile = data.player_tile || {};
      const size = data.chunk_tile_size || { width: 32, height: 32 };
      const chunk = player.verified
        ? `${Math.floor(Number(player.x) / size.width)}, ${Math.floor(Number(player.y) / size.height)}`
        : '未锚定';
      this.setText('map-id', data.map_header_id ?? '未确认');
      this.setText('matrix', data.matrix_id == null ? '候选解析中' : `#${data.matrix_id}`);
      this.setText('chunk', chunk);
      this.setText('model', tile.model_id == null ? '—' : `#${tile.model_id}`);
      this.setText('texture', '机器模式不读取图片');
      this.setText('verified', data.navigation?.route_planning_ready
        ? '坐标 / 通行语义可用于寻路' : '坐标可读；通行语义仍为 unknown');
      this.setText('source', [
        `格式：${data.format}`,
        `玩家：${player.x ?? '?'} , ${player.y ?? '?'} · verified=${Boolean(player.verified)}`,
        `格记录：${data.tile_record_count || 0}`,
        `字段：${(data.tile_record_schema || []).join(', ') || '等待锚定'}`,
        `寻路：${data.navigation?.reason || '等待证据'}`,
      ].join('\n'));
      this.el('knowledge').textContent = JSON.stringify({
        format: data.format,
        status: data.status,
        navigation: data.navigation,
        endpoint: '/api/v1/map/machine',
      }, null, 2);
    }

    showSchematicInfo(data) {
      const matrix = data.matrix;
      const player = data.live_player || {};
      this.setText('map-id', data.map_header_id ?? '未确认');
      this.setText('matrix', matrix ? `#${matrix.id} · ${matrix.width} × ${matrix.height}` : '未锚定');
      this.setText('chunk', data.player_chunk ? `${data.player_chunk.x}, ${data.player_chunk.y}` : '玩家原点未验证');
      const resident = matrix?.resident_cells || [];
      this.setText('model', resident.map(cell => `#${cell.model_id}`).join(', ') || '—');
      this.setText('texture', '实际图按需读取');
      this.setText('verified', data.status !== 'aligned'
        ? '仅 ROM 资源候选，未锚定'
        : data.player_surface_projection?.verified
          ? '矩阵 / 模型 / 玩家表面落点已验证'
          : '矩阵 / 模型 / 玩家块；表面落点未校准');
      this.setText('source', [
        `示意图状态：${data.status}`,
        `玩家：${player.x ?? '?'} , ${player.y ?? '?'} · ARM9 原始层字段 L=${player.elevation ?? '?'}（不是 BMD0 高度）`,
        this.formatPlayerPermission(data.player_tile_permission),
        `逐格高度：${data.height?.per_tile || '未解码'}`,
        data.message,
      ].join('\n'));
    }

    formatPlayerPermission(tile) {
      if (!tile) return '当前格 Pxx：未取得';
      const values = Object.entries(tile.permission_planes || {})
        .map(([key, value]) => `${key}=0x${Number(value).toString(16).padStart(2, '0').toUpperCase()}`)
        .join(' ');
      return `当前格：M${tile.model_id} local ${tile.local.x},${tile.local.y} · ${values}`;
    }

    showSchematicEvents(data) {
      const events = data.events || {};
      const counts = events.counts || {};
      this.el('event-counts').textContent = data.status === 'aligned'
        ? `Map Header #${data.map_header_id} · 入口 ${counts.warps || 0} · NPC ${counts.npcs || 0} · 触发器 ${counts.triggers || 0} · 固定物 ${counts.furniture || 0}`
        : '事件坐标等待矩阵锚定；不放置伪造图标。';
      this.el('event-list').textContent = data.status === 'aligned'
        ? [
          ...(events.warps || []).map(item => `Warp #${item.id} · raw (${item.tile_x}, ${item.tile_y}) -> ${item.target_map_id}/${item.target_warp_id}`),
          ...(events.npcs || []).map(item => `NPC #${item.id} · raw (${item.tile_x}, ${item.tile_y})`),
          ...(events.furniture || []).map(item => `固定物 #${item.id} · raw (${item.tile_x}, ${item.tile_y})`),
          ...(events.triggers || []).map(item => `触发器 #${item.id} · raw (${item.tile_x}, ${item.tile_y})`),
        ].join('\n') || '当前 Map Header 没有可解码的事件记录。'
        : '等待当前地图锚定。';
      this.el('collision').textContent = data.raw_permissions_included
        ? '已读取驻留块的 Pxx 原始字节；Pxx 不是墙、草地或可通行性结论。'
        : 'Pxx 原始格尚未读取。点击“读取 Pxx 原始格”可只读取本地 ROM 字节，不加载图片。';
    }

    async load(isRetry = false) {
      if (this.mode === 'schematic') {
        this.loadSchematic();
        return;
      }
      if (this.loadBusy) {
        this.pendingLoad = true;
        return;
      }
      this.loadBusy = true;
      if (!isRetry) {
        clearTimeout(this.retryTimer);
        this.retryDelay = 600;
        this.setStatus('正在校验 BizHawk ARM9 的 BMD0/BTX0 并读取原生地图…');
      }
      try {
        const response = await fetch('/api/v1/map/visual', { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        if (!(data.models || []).length) throw new Error('没有可渲染的已验证模型');
        this.info = data;
        if (data.cache?.key) this.lastSceneCacheKey = data.cache.key;
        this.showInfo(data);
        if (!data.player_alignment?.verified) {
          const isGallery = data.display_mode === 'texture-candidate-gallery'
            || data.display_mode === 'candidate-gallery';
          if (isGallery && data.models?.length) {
            await this.renderModels(data);
            if (this.mode === 'overlay') this.refreshKnowledge();
            this.setStatus(`当前显示候选模型（${data.display_mode}）；可通过右上角候选菜单切换观察。`);
            return;
          }
          this.clearGroup();
          this.clearSchematicOverlay();
          this.marker.visible = false;
          this.scheduleAlignmentRetry('当前资源仍是候选，正在后台重新扫描矩阵原点与稳定材质');
          if (this.mode === 'overlay') this.refreshKnowledge();
          return;
        }
        clearTimeout(this.retryTimer);
        this.retryDelay = 600;
        await this.renderModels(data);
        if (this.mode === 'overlay') this.refreshKnowledge();
        const scope = data.display_mode === 'texture-candidate-gallery'
          ? '官方材质候选；可切换校准' : data.player_alignment?.verified ? '当前地图块' : '独立候选模型';
        const cacheLabel = data.cache?.hit ? '缓存命中' : '已读取并缓存';
        this.setStatus(`已加载 ${data.models.length} 个官方材质 · ${scope} · ${cacheLabel}`);
      } catch (error) {
        this.marker.visible = false;
        this.refreshKnowledge();
        const retryable = /Failed to fetch|not connected|currently loaded|BTX0|BMD0/i.test(error.message);
        if (retryable) {
          this.scheduleAlignmentRetry('等待 BizHawk 提供当前场景的稳定 BMD0/BTX0 资源');
        } else {
          this.setStatus(`无法展示原生地图：${error.message}`, true);
        }
      } finally {
        this.loadBusy = false;
        if (this.pendingLoad) {
          this.pendingLoad = false;
          this.load();
        }
      }
    }

    scheduleAlignmentRetry(message) {
      clearTimeout(this.retryTimer);
      const delay = this.retryDelay;
      this.retryDelay = Math.min(4000, delay * 1.7);
      this.setStatus(`${message} · ${(delay / 1000).toFixed(1)} 秒后重试`);
      this.retryTimer = setTimeout(() => {
        this.retryTimer = null;
        if (this.mode === 'schematic') this.loadStructure();
        else this.load(true);
      }, delay);
    }

    async renderModels(data) {
      const entries = await Promise.all((data.models || []).map(async item => ({
        item,
        copy: this.prepareModel(this.cloneModel((await this.loadModel(item.asset_url)).scene)),
      })));
      entries.forEach(entry => { entry.copy.userData.mapEntry = entry; });
      this.renderEntries = entries;
      this.galleryEntries = [];
      const variant = this.el('variant');
      variant.hidden = data.display_mode !== 'texture-candidate-gallery' && data.display_mode !== 'candidate-gallery';
      variant.replaceChildren();
      if (data.display_mode === 'texture-candidate-gallery' || data.display_mode === 'candidate-gallery') {
        this.galleryEntries = entries;
        entries.forEach(({ item }, index) => {
          const option = document.createElement('option');
          option.value = String(index);
          option.textContent = data.display_mode === 'candidate-gallery'
            ? `模型 #${item.model_id}`
            : `模型 #${item.model_id} · BTX0 #${item.texture_id}`;
          variant.appendChild(option);
        });
      }
      this.clearGroup();
      this.clearSelection();
      this.clearPlayerRenderAnchor();
      if (data.player_alignment?.verified && data.display_mode !== 'texture-candidate-gallery' && data.display_mode !== 'candidate-gallery') {
        const size = data.chunk_tile_size?.width || 32;
        const unit = size * WORLD_UNITS_PER_TILE;
        entries.forEach(({ item, copy }) => {
          copy.position.set(
            (item.cell.x - data.player_chunk.x) * unit,
            0,
            (item.cell.y - data.player_chunk.y) * unit,
          );
          this.group.add(copy);
        });
      } else if (data.display_mode !== 'texture-candidate-gallery' && data.display_mode !== 'candidate-gallery') {
        entries.forEach(({ copy }) => this.group.add(copy));
      } else {
        // In candidate gallery mode, stitch adjacent candidate matrix cells (e.g. Model 282 at (2,21) + Model 283 at (3,21))
        const activeCells = (data.active_cells || []).filter(c => c.x != null && c.y != null);
        if (activeCells.length > 1) {
          const minX = Math.min(...activeCells.map(c => c.x));
          const minY = Math.min(...activeCells.map(c => c.y));
          const unit = 32 * WORLD_UNITS_PER_TILE;
          entries.forEach(({ item, copy }) => {
            const match = activeCells.find(c => c.model_id === item.model_id);
            if (match) {
              copy.position.set((match.x - minX) * unit, 0, (match.y - minY) * unit);
            }
            copy.visible = true;
            this.group.add(copy);
          });
        } else {
          this.showCandidate(0, false);
        }
      }
      this.frameGroup();
      const placementLines = entries.map(({ item, copy }) => {
        copy.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(copy);
        const min = bounds.min, max = bounds.max;
        return `模型 #${item.model_id} @ ${copy.position.x},${copy.position.z} · bounds ${Math.round(min.x)}..${Math.round(max.x)} / ${Math.round(min.z)}..${Math.round(max.z)}`;
      });
      this.meshSeams = this.describeMeshSeams(entries);
      const groupBounds = new THREE.Box3().setFromObject(this.group);
      this.setText('source', [
        this.el('source')?.textContent || '',
        `BMD0 拼接单位：${WORLD_UNITS_PER_TILE} 世界单位/格（32 格步长=${WORLD_UNITS_PER_TILE * 32}）`,
        '玩家标记：当前块 32×32 坐标投影到 BMD0 实际表面；原始层字段不参与模型高度计算',
        `全量边界：${Math.round(groupBounds.min.z)}..${Math.round(groupBounds.max.z)} · 视点目标：${Math.round(this.controls.target.z)}`,
        ...placementLines,
        ...this.meshSeams,
      ].filter(Boolean).join('\n'));
      this.setMarker(data, data.live_player);
      this.renderSchematicOverlay(data);
      this.renderNavMesh(data);
    }

    prepareModel(model) {
      model.traverse(node => {
        if (!node.isMesh) return;
        // Apicula map meshes carry a skin; their local bounding sphere is not
        // reliable after matrix-cell placement, so do not frustum-cull them.
        node.frustumCulled = false;
        // Force DoubleSide on all model materials so raycaster will not miss
        // back-facing / inverted polygons or transparent layers.
        if (Array.isArray(node.material)) {
          node.material.forEach(m => {
            if (m) {
              m.side = THREE.DoubleSide;
              m.depthWrite = true;
            }
          });
        } else if (node.material) {
          node.material.side = THREE.DoubleSide;
          node.material.depthWrite = true;
        }
      });
      return model;
    }

    cloneModel(scene) {
      return THREE.SkeletonUtils?.clone ? THREE.SkeletonUtils.clone(scene) : scene.clone(true);
    }

    describeMeshSeams(entries) {
      const byCell = new Map(entries.map(({ item, copy }) => {
        copy.updateMatrixWorld(true);
        return [`${item.cell?.x},${item.cell?.y}`, { item, bounds: new THREE.Box3().setFromObject(copy) }];
      }));
      const seams = [];
      for (const { item, bounds } of byCell.values()) {
        const below = byCell.get(`${item.cell?.x},${Number(item.cell?.y) + 1}`);
        if (!below) continue;
        const gap = below.bounds.min.z - bounds.max.z;
        if (gap > 1) {
          seams.push(
            `BMD 视觉边界间隙：模型 #${item.model_id} → #${below.item.model_id}（Y+）${Math.round(gap)} 单位；仅是几何诊断，不等于碰撞或不可通行。`,
          );
        }
      }
      return seams;
    }

    showInfo(data) {
      const aligned = data.player_alignment?.verified;
      const gallery = data.display_mode === 'texture-candidate-gallery' || data.display_mode === 'candidate-gallery';
      const sceneCount = (data.candidate_scenes || []).length;
      const modelIds = [...new Set((data.models || []).map(model => `#${model.model_id}`))].join(', ') || '—';
      const textureIds = gallery
        ? (data.texture_candidate_ids || []).map(id => `#${id}`).join(', ')
        : [...new Set((data.models || []).map(model => `#${model.texture_id}`))].join(', ');
      this.setText('map-id', data.map_header_id ?? data.map_definition_id ?? (aligned ? '—' : '未确认'));
      this.setText('matrix', aligned && !gallery
        ? `#${data.matrix_id} · ${data.matrix_size?.width || '—'} × ${data.matrix_size?.height || '—'}`
        : `${sceneCount} 个独立矩阵候选`);
      this.setText('chunk', aligned ? `${data.player_chunk?.x}, ${data.player_chunk?.y}` : '玩家原点未验证');
      this.setText('model', aligned ? `#${data.player_model_id}` : modelIds);
      this.setText('texture', `${textureIds || '—'} · ${gallery ? '待画面校准' : '精确材质匹配'}`);
      this.setText('verified', gallery ? '模型 / 材质候选；贴图未唯一确认' : aligned
        ? data.player_surface_projection?.verified
          ? '模型 / 材质 / 矩阵 / 玩家表面落点'
          : '模型 / 材质 / 矩阵 / 玩家块；表面落点未校准'
        : '模型 / 材质；玩家落点未验证');
      this.updatePlayerXYZ(data.live_player);
      this.setText('source', [
        data.verification?.method || '',
        `ARM9 扫描 BTX0：#${data.loaded_texture_id ?? '—'}`,
        `当前候选块：${data.active_cells?.length || 0}`,
        `自动场景：${this.sceneLabel(data.scene)}`,
      ].filter(Boolean).join('\n'));
    }

    async refreshKnowledge() {
      if (this.knowledgeBusy) return;
      this.knowledgeBusy = true;
      try {
        const [jsonResponse, textResponse, observationResponse] = await Promise.all([
          fetch('/api/v1/map/knowledge/current?include_raw=true', { cache: 'no-store' }),
          fetch('/api/v1/map/knowledge/current.txt?include_raw=true', { cache: 'no-store' }),
          fetch('/api/v1/map/knowledge/observations', { cache: 'no-store' }),
        ]);
        const text = await textResponse.text();
        if (!jsonResponse.ok || !textResponse.ok || !observationResponse.ok) {
          throw new Error('地图知识接口暂不可用');
        }
        this.knowledge = await jsonResponse.json();
        const observations = await observationResponse.json();
        this.observations = observations.observations || [];
        this.el('knowledge').textContent = this.schematicData?.ai_text || text;
        this.renderEvidence();
      } catch (error) {
        this.el('knowledge').textContent = `当前地图知识暂不可用：${error.message}`;
        this.renderEvidence();
      } finally {
        this.knowledgeBusy = false;
      }
    }

    async probe(button) {
      if (this.probeBusy) return;
      this.probeBusy = true;
      this.el('probe').textContent = `${button} 探针执行中…`;
      try {
        const response = await fetch('/api/v1/map/knowledge/probe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ button, frames: 4, wait_frames: 15 }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        this.el('probe').textContent = [
          `输入：${data.input.button} · 结果：${data.movement_result}`,
          `地图字段是否变化：${data.map_section_changed}`,
          data.evidence,
          `碰撞语义：${data.collision_semantics}`,
        ].join('\n');
        this.knowledge = data.after;
        this.el('knowledge').textContent = '探针完成，点击“刷新文字”获取最新完整地图知识。';
        this.renderEvidence();
      } catch (error) {
        this.el('probe').textContent = `探针失败：${error.message}`;
      } finally {
        this.probeBusy = false;
      }
    }

    renderEvidence() {
      const data = this.knowledge;
      const details = data?.candidate_map_headers || [];
      const events = details.flatMap(detail => {
        const event = detail.events || {};
        return [
          ...(event.warps || []).map(item => ({ ...item, type: '入口 / Warp', map: detail.map_header?.map_header_id })),
          ...(event.npcs || []).map(item => ({ ...item, type: 'NPC', map: detail.map_header?.map_header_id })),
          ...(event.triggers || []).map(item => ({ ...item, type: '触发器', map: detail.map_header?.map_header_id })),
          ...(event.furniture || []).map(item => ({ ...item, type: '固定物', map: detail.map_header?.map_header_id })),
        ];
      });
      const npcs = details.reduce((total, detail) => total + (detail.events?.npcs?.length || 0), 0);
      const models = details.flatMap(detail => detail.models || []);
      this.el('event-counts').textContent = data
        ? `Map Header ${details.length} · 入口 ${events.filter(item => item.type === '入口 / Warp').length} · NPC ${npcs} · 触发器 ${events.filter(item => item.type === '触发器').length} · 固定物 ${events.filter(item => item.type === '固定物').length}`
        : '等待当前地图知识…';
      const rawModels = models.filter(model => model.permission_planes);
      this.el('collision').textContent = rawModels.length
        ? rawModels.map(model => `模型 #${model.model_id} · ${model.width || '?'}×${model.height || '?'} · raw planes=${model.plane_count || '?'}`).join('\n')
        : '尚未定位当前地图碰撞模型；不把未知 permission byte 解释为可走或阻挡。';
      this.el('event-list').textContent = events.length
        ? events.slice(0, 30).map(item => `${item.type} · Map Header #${item.map ?? '—'} · (${item.tile_x ?? item.x ?? '?'}, ${item.tile_y ?? item.y ?? '?'})`).join('\n')
        : data ? '当前场景没有已定位的 ROM 事件记录。' : '等待实机地图资源…';
      const list = this.el('overlay-list');
      list.replaceChildren();
      events.slice(0, 18).forEach(item => {
        const row = document.createElement('div');
        row.className = 'world-overlay-row';
        row.dataset.logicType = this.eventMarkerKey(item.type);
        row.textContent = `${item.type} · Map Header #${item.map ?? '—'} · (${item.tile_x ?? item.x ?? '?'}, ${item.tile_y ?? item.y ?? '?'})`;
        list.appendChild(row);
      });
      if (!events.length) {
        const empty = document.createElement('div');
        empty.className = 'world-overlay-row world-overlay-muted';
        empty.textContent = data ? '当前场景没有已定位的 ROM 事件记录。' : '等待实机地图资源…';
        list.appendChild(empty);
      }
      this.updateCollisionPlaneOptions(rawModels);
      this.renderInteractionLayer(details, events);
    }

    eventMarkerKey(type) {
      return {
        '入口 / Warp': 'warp',
        NPC: 'npc',
        '触发器': 'trigger',
        '固定物': 'furniture',
      }[type] || 'unknown';
    }

    updateCollisionPlaneOptions(models) {
      const select = this.el('collision-plane');
      if (!select) return;
      const planeCount = models.reduce(
        (largest, model) => Math.max(largest, Number(model.plane_count) || 0), 0,
      );
      const previous = this.collisionPlane;
      select.replaceChildren();
      const all = document.createElement('option');
      all.value = 'all';
      all.textContent = '全部原始平面';
      select.appendChild(all);
      for (let plane = 0; plane < planeCount; plane += 1) {
        const option = document.createElement('option');
        option.value = String(plane);
        option.textContent = `原始平面 ${plane}`;
        select.appendChild(option);
      }
      this.collisionPlane = previous === 'all' || Number(previous) < planeCount ? previous : 'all';
      select.value = this.collisionPlane;
      select.disabled = planeCount === 0;
    }

    renderInteractionLayer(details = [], events = []) {
      if (!this.interactionGroup) return;
      this.clearInteractionGroup();
      this.interactionGroup.visible = this.mode === 'overlay';
      if (this.mode !== 'overlay') return;

      const mapId = this.info?.map_definition_id;
      const detail = details.find(item => item.map_header?.map_header_id === mapId);
      const aligned = this.info?.display_mode === 'aligned-map'
        && this.info?.player_alignment?.verified && detail;
      if (!aligned) {
        const selectedIndex = Number(this.el('variant')?.value || 0);
        const selectedModelId = this.galleryEntries[selectedIndex]?.item.model_id;
        const candidateModel = details
          .flatMap(item => item.models || [])
          .find(model => model.model_id === selectedModelId && model.permission_planes);
        if (this.info?.display_mode === 'texture-candidate-gallery' && candidateModel) {
          const rawCells = this.addCollisionGrid(candidateModel, { x: 0, y: 0 }, true);
          this.setText('logic-status', `当前为材质候选图库；仅显示所选模型的原始 permission 颜色层（${rawCells} 格）。入口、NPC、路线等待玩家落点与 Map Header 对齐后绘制。`);
        } else {
          this.setText('logic-status', '当前仍是材质候选或玩家落点未验证；颜色标记暂不锚定到 3D。');
        }
        return;
      }

      const models = new Map((detail.models || []).map(model => [model.model_id, model]));
      let collisionCells = 0;
      for (const cell of this.info.active_cells || []) {
        const model = models.get(cell.model_id);
        if (model?.permission_planes) {
          collisionCells += this.addCollisionGrid(model, cell);
        }
      }
      const visibleEvents = events.filter(item => item.map === mapId);
      const eventCounts = visibleEvents.reduce((counts, item) => {
        counts[item.type] = (counts[item.type] || 0) + 1;
        return counts;
      }, {});
      const placedEvents = visibleEvents.reduce(
        (count, item) => count + Number(this.addEventMarker(item)), 0,
      );
      const routeSegments = this.addObservedRoute();
      this.setText('logic-status', [
        `已锚定 Map Header #${mapId}`,
        `碰撞原始非零格 ${collisionCells} · 入口 ${eventCounts['入口 / Warp'] || 0}`,
        `NPC ${eventCounts.NPC || 0} · 触发器 ${eventCounts['触发器'] || 0} · 固定物 ${eventCounts['固定物'] || 0}`,
        `已放置事件标记 ${placedEvents} · 已观测路线 ${routeSegments} 段（不是静态可行路线）`,
      ].join('\n'));
    }

    addCollisionGrid(model, cell, centered = false) {
      const planes = model.permission_planes || {};
      const planeIds = Object.keys(planes).sort((left, right) => Number(left) - Number(right));
      const selectedPlanes = this.collisionPlane === 'all'
        ? planeIds : planeIds.filter(plane => plane === String(this.collisionPlane));
      if (!selectedPlanes.length) return 0;
      const tileWidth = centered ? model.width : this.info.chunk_tile_size.width;
      const tileHeight = centered ? model.height : this.info.chunk_tile_size.height;
      const baseX = centered ? 0 : (cell.x - this.info.player_chunk.x) * tileWidth * WORLD_UNITS_PER_TILE;
      const baseZ = centered ? 0 : (cell.y - this.info.player_chunk.y) * tileHeight * WORLD_UNITS_PER_TILE;
      const materials = new Map();
      let count = 0;
      for (let y = 0; y < (model.height || 0); y += 1) {
        for (let x = 0; x < (model.width || 0); x += 1) {
          const raw = selectedPlanes
            .map(plane => Number(planes[plane]?.[y]?.[x]) || 0)
            .find(value => value !== 0) || 0;
          if (!raw) continue;
          if (!materials.has(raw)) {
            materials.set(raw, new THREE.MeshBasicMaterial({
              color: this.rawPermissionColor(raw),
              transparent: true,
              opacity: .34,
              depthTest: false,
              side: THREE.DoubleSide,
            }));
          }
          const tile = new THREE.Mesh(
            new THREE.PlaneGeometry(WORLD_UNITS_PER_TILE - 2, WORLD_UNITS_PER_TILE - 2),
            materials.get(raw),
          );
          tile.rotation.x = -Math.PI / 2;
          tile.position.set(
            baseX + (x + .5 - tileWidth / 2) * WORLD_UNITS_PER_TILE,
            52,
            baseZ + (y + .5 - tileHeight / 2) * WORLD_UNITS_PER_TILE,
          );
          tile.name = `raw-permission-${cell.model_id}-${x}-${y}`;
          tile.userData.rawPermission = `0x${raw.toString(16).padStart(2, '0').toUpperCase()}`;
          this.interactionGroup.add(tile);
          count += 1;
        }
      }
      return count;
    }

    rawPermissionColor(value) {
      const hue = (Number(value) * 43) % 360;
      return new THREE.Color(`hsl(${hue}, 76%, 58%)`);
    }

    addEventMarker(item) {
      const x = item.tile_x ?? item.x;
      const y = item.tile_y ?? item.y;
      const position = this.eventWorldPosition(x, y);
      if (!position) return false;
      const colors = {
        '入口 / Warp': 0x34d399,
        NPC: 0x60a5fa,
        '触发器': 0xf59e0b,
        '固定物': 0xf472b6,
      };
      const marker = new THREE.Mesh(
        new THREE.CylinderGeometry(4, 4, 1.5, 16),
        new THREE.MeshBasicMaterial({
          color: colors[item.type] || 0xe5e7eb,
          transparent: true,
          opacity: .9,
          depthTest: false,
        }),
      );
      marker.position.copy(position);
      marker.position.y = 55;
      marker.name = `${item.type}-${item.id}`;
      marker.userData.mapCoordinate = `${x},${y}`;
      this.interactionGroup.add(marker);
      return true;
    }

    addObservedRoute() {
      let count = 0;
      for (const observation of this.observations || []) {
        const before = observation.before;
        const after = observation.after;
        if (!before?.verified || !after?.verified || before.map_section_id !== after.map_section_id) continue;
        const start = this.worldPosition(before.x, before.y);
        const end = this.worldPosition(after.x, after.y);
        if (!start || !end || (start.x === end.x && start.z === end.z)) continue;
        start.y = end.y = 58;
        const line = new THREE.Line(
          new THREE.BufferGeometry().setFromPoints([start, end]),
          new THREE.LineBasicMaterial({ color: 0x38bdf8, depthTest: false }),
        );
        line.name = 'observed-route-segment';
        this.interactionGroup.add(line);
        count += 1;
      }
      return count;
    }

    worldPosition(x, y) {
      const tileX = Number(x);
      const tileY = Number(y);
      const size = this.info?.chunk_tile_size;
      const chunk = this.info?.player_chunk;
      if (!Number.isFinite(tileX) || !Number.isFinite(tileY) || !size || !chunk) return null;
      const chunkX = Math.floor(tileX / size.width);
      const chunkY = Math.floor(tileY / size.height);
      return new THREE.Vector3(
        (chunkX - chunk.x) * size.width * WORLD_UNITS_PER_TILE
          + (tileX - chunkX * size.width - size.width / 2) * WORLD_UNITS_PER_TILE,
        0,
        (chunkY - chunk.y) * size.height * WORLD_UNITS_PER_TILE
          + (tileY - chunkY * size.height - size.height / 2) * WORLD_UNITS_PER_TILE,
      );
    }

    eventWorldPosition(localX, localY) {
      const tileX = Number(localX);
      const tileY = Number(localY);
      const size = this.info?.chunk_tile_size;
      const chunk = this.info?.player_chunk;
      const bounds = this.info?.map_definition_bounds;
      if (!Number.isFinite(tileX) || !Number.isFinite(tileY) || !size || !chunk || !bounds) return null;
      const eventChunkX = bounds.min_chunk_x + Math.floor(tileX / size.width);
      const eventChunkY = bounds.min_chunk_y + Math.floor(tileY / size.height);
      return new THREE.Vector3(
        (eventChunkX - chunk.x) * size.width * WORLD_UNITS_PER_TILE
          + (tileX % size.width - size.width / 2) * WORLD_UNITS_PER_TILE,
        0,
        (eventChunkY - chunk.y) * size.height * WORLD_UNITS_PER_TILE
          + (tileY % size.height - size.height / 2) * WORLD_UNITS_PER_TILE,
      );
    }

    clearInteractionGroup() {
      while (this.interactionGroup?.children.length) {
        const child = this.interactionGroup.children[0];
        child.traverse(node => {
          node.geometry?.dispose();
          if (Array.isArray(node.material)) node.material.forEach(material => material.dispose());
          else node.material?.dispose();
        });
        this.interactionGroup.remove(child);
      }
    }

    renderSchematicOverlay(data) {
      if (!this.schematicOverlayGroup) return;
      this.clearSchematicOverlay();
      this.schematicOverlayGroup.visible = this.mode === 'overlay';
      if (this.mode !== 'overlay' || !data.player_alignment?.verified) return;
      const tileSize = data.chunk_tile_size?.width || 32;
      const unit = tileSize * WORLD_UNITS_PER_TILE;
      for (const cell of data.active_cells || []) {
        const tile = new THREE.Mesh(
          new THREE.PlaneGeometry(unit - 4, unit - 4),
          new THREE.MeshBasicMaterial({
            color: this.modelCodeColor(cell.model_id),
            transparent: true,
            opacity: .18,
            depthTest: false,
            side: THREE.DoubleSide,
          }),
        );
        tile.rotation.x = -Math.PI / 2;
        tile.position.set(
          (cell.x - data.player_chunk.x) * unit,
          62,
          (cell.y - data.player_chunk.y) * unit,
        );
        tile.name = `schematic-model-${cell.model_id}`;
        this.schematicOverlayGroup.add(tile);
      }
    }

    clearSchematicOverlay() {
      while (this.schematicOverlayGroup?.children.length) {
        const child = this.schematicOverlayGroup.children[0];
        child.geometry?.dispose();
        child.material?.dispose();
        this.schematicOverlayGroup.remove(child);
      }
    }

    modelCodeColor(modelId) {
      const hue = (Number(modelId) * 47) % 360;
      return new THREE.Color(`hsl(${hue}, 70%, 56%)`);
    }

    async refreshPlayer() {
      if (this.playerRefreshBusy) return;
      this.playerRefreshBusy = true;
      this.refreshSceneIfChanged();
      try {
        const response = await fetch('/api/v1/map/current', { cache: 'no-store' });
        if (!response.ok) return;
        const data = await response.json();
        const player = data.player;
        if (!player?.verified) return;
        this.updatePlayerXYZ(player);
        const mapChanged = this.lastMapSection !== null
          && this.lastMapSection !== data.map_section_id;
        this.lastMapSection = data.map_section_id;
        if (mapChanged) {
          this.lastLivePosition = null;
          this.setStatus(this.mode === 'schematic'
            ? '检测到进入新地区，正在自动刷新 ROM 编号示意图…'
            : '检测到进入新地区，正在自动读取并缓存原生地图…');
          this.refreshView();
          if (this.mode === 'overlay') this.refreshKnowledge();
          return;
        }
        const moved = this.lastLivePosition
          && (this.lastLivePosition.x !== player.x || this.lastLivePosition.y !== player.y);
        this.lastLivePosition = { x: player.x, y: player.y, elevation: player.elevation };
        if (this.mode === 'machine') {
          if (moved) this.loadMachine();
          return;
        }
        if (this.mode === 'schematic') {
          if (this.geometry?.renderer && this.info) {
            if (!this.info.player_surface_projection?.verified) {
              this.el('hint').textContent = `玩家全局 ${player.x}, ${player.y} · 矩阵块已验证；BMD0 平面落点未校准，未显示圆环`;
              return;
            }
            if (moved && !this.isInsideLoadedBounds(player, this.info)) {
              this.loadStructure();
              return;
            }
            this.info.live_player = player;
            this.geometry.setPlayer(this.info, player);
            this.el('hint').textContent = `玩家全局 ${player.x}, ${player.y} · 3D 几何表面投影 · 原始层字段 L=${player.elevation ?? '?'} 未用于模型高度`;
            return;
          }
          this.schematic?.setLivePlayer(player);
          if (moved && this.schematicData && !this.isInsideLoadedBounds(player, this.schematicData)) this.loadSchematic(true);
          return;
        }
        if (!this.info) return;
        if (moved && this.mode === 'overlay') this.refreshKnowledge();
        if (!this.info.player_alignment?.verified) {
          this.el('hint').textContent = `ARM9 玩家坐标 ${player.x}, ${player.y} · 未证明矩阵原点，不显示伪造标记`;
          return;
        }
        if (!this.info.player_surface_projection?.verified) {
          this.marker.visible = false;
          this.el('hint').textContent = `玩家全局 ${player.x}, ${player.y} · 矩阵块已验证；BMD0 平面落点未校准，未显示圆环`;
          return;
        }
        if (!this.isInsideLoadedBounds(player, this.info)) {
          this.load();
          return;
        }
        const size = this.info.chunk_tile_size || { width: 32, height: 32 };
        this.info.live_player = player;
        this.info.player_local = {
          x: player.x % size.width,
          y: player.y % size.height,
        };
        this.setMarker(this.info, player);
        this.el('hint').textContent = `玩家全局 ${player.x}, ${player.y} · 模型本地 ${this.info.player_local.x}, ${this.info.player_local.y}`;
      } catch (_) {
        // Live player refresh is opportunistic.  The last verified map stays
        // visible until the bridge can answer again.
      } finally {
        this.playerRefreshBusy = false;
      }
    }

    async refreshSceneIfChanged() {
      const now = Date.now();
      if (this.sceneCheckBusy || now - this.lastSceneCheckAt < 1200) return;
      this.sceneCheckBusy = true;
      this.lastSceneCheckAt = now;
      try {
        const response = await fetch('/api/v1/map/cache/status', { cache: 'no-store' });
        if (!response.ok) return;
        const cache = await response.json();
        const scene = cache.scene;
        const key = scene?.id || cache.cache_key;
        if (!key) return;
        if (this.lastSceneCacheKey && cache.state === 'ready' && key !== this.lastSceneCacheKey) {
          this.lastSceneCacheKey = key;
          this.lastLivePosition = null;
          this.setStatus(this.mode === 'schematic'
            ? `检测到场景切换：${this.sceneLabel(scene)}，正在刷新示意图…`
            : `检测到场景切换：${this.sceneLabel(scene)}，正在刷新原生地图…`);
          this.refreshView();
          return;
        }
        this.lastSceneCacheKey = key;
      } catch (_) {
        // Player polling remains available when the optional cache observer is busy.
      } finally {
        this.sceneCheckBusy = false;
      }
    }

    isInsideLoadedBounds(player, data) {
      const bounds = data?.map_definition_bounds;
      const size = data?.chunk_tile_size || data?.matrix?.resident_cells?.[0]?.tile_size;
      if (!bounds || !size || !Number.isFinite(Number(player?.x)) || !Number.isFinite(Number(player?.y))) return false;
      const chunkX = Math.floor(Number(player.x) / size.width);
      const chunkY = Math.floor(Number(player.y) / size.height);
      return chunkX >= bounds.min_chunk_x && chunkX <= bounds.max_chunk_x
        && chunkY >= bounds.min_chunk_y && chunkY <= bounds.max_chunk_y;
    }

    sceneLabel(scene) {
      if (!scene) return '已验证资源已变化';
      const matrix = scene.matrix_id == null ? '候选矩阵' : `矩阵 #${scene.matrix_id}`;
      const models = (scene.active_model_ids || scene.loaded_model_ids || [])
        .map(id => `#${id}`).join('/') || '模型待定';
      return `${matrix} · 模型 ${models}`;
    }

    loadModel(url) {
      if (!this.models.has(url)) {
        this.models.set(url, new Promise((resolve, reject) => this.loader.load(url, resolve, undefined, reject)));
      }
      return this.models.get(url);
    }

    showCandidate(index, announce = true) {
      const selected = this.galleryEntries[index];
      if (!selected) return;
      this.clearGroup();
      selected.copy.position.set(0, 0, 0);
      selected.copy.visible = true;
      this.group.add(selected.copy);
      this.el('variant').value = String(index);
      this.frameGroup();
      this.renderEvidence();
      if (announce) this.setStatus(`当前显示模型 #${selected.item.model_id} · 官方贴图候选 #${selected.item.texture_id} · 仅供画面校准`);
    }

    setMarker(data, livePlayer = null) {
      if (!livePlayer) {
        this.marker.visible = false;
        return;
      }
      const playerX = Number(livePlayer.x);
      const playerY = Number(livePlayer.y);
      if (!Number.isFinite(playerX) || !Number.isFinite(playerY)) {
        this.marker.visible = false;
        return;
      }
      const size = data.chunk_tile_size || { width: 32, height: 32 };
      const chunkX = Math.floor(playerX / size.width);
      const chunkY = Math.floor(playerY / size.height);
      const localX = playerX % size.width;
      const localY = playerY % size.height;

      const projected = this.projectMarkerToModel(
        chunkX, chunkY, localX, localY, size.width, size.height,
      );
      if (projected) {
        this.marker.position.copy(projected);
      } else if (this.renderEntries.length > 0) {
        const firstEntry = this.renderEntries[0];
        firstEntry.copy.updateMatrixWorld(true);
        const bounds = new THREE.Box3().setFromObject(firstEntry.copy);
        const tileW = Math.max(1, bounds.max.x - bounds.min.x) / size.width;
        const tileD = Math.max(1, bounds.max.z - bounds.min.z) / size.height;
        const x = bounds.min.x + (localX + 0.5) * tileW;
        const z = bounds.min.z + (localY + 0.5) * tileD;
        const y = this.markerSurfaceY(firstEntry.copy, x, z, bounds);
        this.marker.position.set(x, y, z);
      } else {
        this.marker.visible = false;
        return;
      }

      const headingArrow = this.marker.getObjectByName('headingArrow');
      if (headingArrow) {
        const facing = String(livePlayer.facing || 'South').toLowerCase();
        if (facing === 'north') headingArrow.rotation.y = Math.PI;
        else if (facing === 'south') headingArrow.rotation.y = 0;
        else if (facing === 'west') headingArrow.rotation.y = Math.PI / 2;
        else if (facing === 'east') headingArrow.rotation.y = -Math.PI / 2;
      }
      this.marker.visible = true;
    }

    projectMarkerToModel(chunkX, chunkY, localX, localY, width, height) {
      let entry = this.renderEntries.find(({ item }) => (
        Number(item.cell?.x) === chunkX && Number(item.cell?.y) === chunkY
      ));
      if (!entry && this.renderEntries.length > 0) {
        // In candidate gallery or stitched mode, try to match by active_cells if available
        const activeCells = (this.info?.active_cells || []);
        const matchCell = activeCells.find(c => c.x === chunkX && c.y === chunkY);
        if (matchCell) {
          entry = this.renderEntries.find(({ item }) => item.model_id === matchCell.model_id);
        }
      }
      if (!entry && this.renderEntries.length === 1) {
        entry = this.renderEntries[0];
      }
      if (!entry) return null;
      entry.copy.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(entry.copy);
      if (bounds.isEmpty()) return null;
      const tileW = Math.max(1, bounds.max.x - bounds.min.x) / width;
      const tileD = Math.max(1, bounds.max.z - bounds.min.z) / height;
      const x = bounds.min.x + (localX + 0.5) * tileW;
      const z = bounds.min.z + (localY + 0.5) * tileD;
      return new THREE.Vector3(
        x,
        this.markerSurfaceY(entry.copy, x, z, bounds),
        z,
      );
    }

    markerSurfaceY(model, x, z, bounds) {
      const topY = bounds.max.y + 128;
      const ray = new THREE.Raycaster(
        new THREE.Vector3(x, topY, z),
        new THREE.Vector3(0, -1, 0),
        0,
        topY - bounds.min.y + 128,
      );
      const hits = ray.intersectObject(model, true);
      if (hits.length > 0) {
        const heights = hits.map(h => h.point.y);
        return Math.max(...heights) + 0.1;
      }
      return bounds.min.y >= 0 ? bounds.min.y : 16.0;
    }

    clearGroup() {
      while (this.group?.children.length) this.group.remove(this.group.children[0]);
    }

    frameGroup(topDown = false) {
      if (!this.group || !this.camera || !this.controls) return;
      this.group?.updateMatrixWorld(true);
      const bounds = new THREE.Box3().setFromObject(this.group);
      if (bounds.isEmpty()) return;
      const center = bounds.getCenter(new THREE.Vector3());
      const span = Math.max(bounds.max.x - bounds.min.x, bounds.max.z - bounds.min.z, 100);
      const aspect = Math.max(this.camera.aspect, .35);
      const fov = THREE.MathUtils.degToRad(this.camera.fov);
      const distance = Math.max(span, span / aspect) / (2 * Math.tan(fov / 2)) * 1.18;
      const direction = topDown ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(-.75, .62, .75).normalize();
      this.camera.up.copy(topDown ? new THREE.Vector3(0, 0, -1) : new THREE.Vector3(0, 1, 0));
      this.camera.position.copy(center).add(direction.multiplyScalar(distance));
      this.controls.minDistance = Math.max(20, span * .05);
      this.controls.maxDistance = Math.max(700, span * 8);
      this.controls.target.copy(center);
      this.controls.update();
    }

    adjustZoom(factor) {
      if (this.mode === 'schematic' && this.geometry?.renderer) {
        this.geometry.adjustZoom(factor);
        return;
      }
      if (!this.controls) {
        this.schematic?.adjustZoom(factor);
        return;
      }
      const offset = this.camera.position.clone().sub(this.controls.target);
      const distance = THREE.MathUtils.clamp(offset.length() * factor, this.controls.minDistance, this.controls.maxDistance);
      this.camera.position.copy(this.controls.target).add(offset.setLength(distance));
      this.controls.update();
    }

    rotateView(angle) {
      if (this.mode === 'schematic' && this.geometry?.renderer) {
        this.geometry.rotate(angle);
        return;
      }
      if (!this.controls) return;
      const offset = this.camera.position.clone().sub(this.controls.target);
      const cos = Math.cos(angle), sin = Math.sin(angle);
      this.camera.position.set(
        this.controls.target.x + offset.x * cos - offset.z * sin,
        this.camera.position.y,
        this.controls.target.z + offset.x * sin + offset.z * cos,
      );
      this.controls.update();
    }

    setViewMode(mode) {
      this.viewMode = mode;
      if (this.mode === 'schematic' && this.geometry?.renderer) {
        this.geometry.setView(mode);
        return;
      }
      this.frameGroup(mode === 'top');
    }
  }

  window.NativeWorldViewer = NativeWorldViewer;
})();
