/* Texture-free renderer for evidence-bounded ROM map schematics. */
(function () {
  class MapSchematicCanvas {
    constructor(canvas) {
      this.canvas = canvas;
      this.context = canvas?.getContext('2d') || null;
      this.data = null;
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.drag = null;
      this.mapIdentity = null;
      this.visitedTiles = new Set();
      this.bindPointerControls();
    }

    bindPointerControls() {
      if (!this.canvas) return;
      this.canvas.addEventListener('wheel', event => {
        event.preventDefault();
        this.adjustZoom(event.deltaY > 0 ? .86 : 1.16, event.offsetX, event.offsetY);
      }, { passive: false });
      this.canvas.addEventListener('pointerdown', event => {
        if (event.button !== 1) return;
        event.preventDefault();
        this.drag = { x: event.clientX, y: event.clientY };
        this.canvas.setPointerCapture?.(event.pointerId);
      });
      this.canvas.addEventListener('pointermove', event => {
        if (!this.drag) return;
        this.pan.x += event.clientX - this.drag.x;
        this.pan.y += event.clientY - this.drag.y;
        this.drag = { x: event.clientX, y: event.clientY };
        this.render();
      });
      const finishDrag = () => { this.drag = null; };
      this.canvas.addEventListener('pointerup', finishDrag);
      this.canvas.addEventListener('pointercancel', finishDrag);
      this.canvas.addEventListener('contextmenu', event => event.preventDefault());
    }

    setData(data) {
      const identity = `${data?.matrix?.id ?? '?'}:${data?.map_header_id ?? '?'}`;
      if (identity !== this.mapIdentity) {
        this.mapIdentity = identity;
        this.visitedTiles.clear();
      }
      this.data = data;
      this.rememberPlayerTile(data?.live_player);
      this.fit();
    }

    setLivePlayer(player) {
      if (!this.data?.live_player || !player?.verified) return;
      this.data.live_player = { ...this.data.live_player, ...player };
      this.rememberPlayerTile(player);
      this.render();
    }

    rememberPlayerTile(player) {
      if (!player?.verified || !Number.isFinite(Number(player.x)) || !Number.isFinite(Number(player.y))) return;
      this.visitedTiles.add(`${Number(player.x)},${Number(player.y)}`);
    }

    resize() {
      if (!this.canvas || !this.context) return;
      const box = this.canvas.getBoundingClientRect();
      if (box.width < 2 || box.height < 2) return;
      const ratio = window.devicePixelRatio || 1;
      this.canvas.width = Math.max(1, Math.round(box.width * ratio));
      this.canvas.height = Math.max(1, Math.round(box.height * ratio));
      this.context.setTransform(ratio, 0, 0, ratio, 0, 0);
      this.render();
    }

    fit() {
      this.zoom = 1;
      this.pan = { x: 0, y: 0 };
      this.render();
    }

    adjustZoom(factor, anchorX, anchorY) {
      this.zoom = Math.max(.35, Math.min(8, this.zoom * factor));
      if (Number.isFinite(anchorX) && Number.isFinite(anchorY)) {
        this.pan.x += (anchorX - this.pan.x) * (1 - factor);
        this.pan.y += (anchorY - this.pan.y) * (1 - factor);
      }
      this.render();
    }

    render() {
      if (!this.context || !this.canvas) return;
      const width = this.canvas.clientWidth || 1;
      const height = this.canvas.clientHeight || 1;
      const context = this.context;
      context.clearRect(0, 0, width, height);
      context.fillStyle = '#02070d';
      context.fillRect(0, 0, width, height);
      if (this.data?.status !== 'aligned') {
        this.drawUnavailable(width, height);
        return;
      }
      this.drawMap(width, height);
    }

    drawUnavailable(width, height) {
      const data = this.data || {};
      const title = data.status === 'unanchored' ? '当前 BMD0 候选尚未锚定到地图' : '尚未取得可验证的示意图';
      const lines = [title, data.message || '等待 BizHawk 提供稳定的 ARM9 坐标与矩阵证据。'];
      if (data.candidate_scenes?.length) {
        lines.push(`候选矩阵：${data.candidate_scenes.map(scene => `#${scene.matrix_id}`).join('、')}`);
        lines.push('为避免把候选模型拼成假地图，此处不绘图。');
      }
      this.context.fillStyle = '#b8c8df';
      this.context.font = '14px Inter, sans-serif';
      lines.forEach((line, index) => this.context.fillText(line, 28, height / 2 - 24 + index * 24));
      this.context.fillStyle = '#7387a5';
      this.context.font = '11px ui-monospace, Consolas, monospace';
      this.context.fillText('示意图不请求 GLB、BTX0 或截图。', 28, height / 2 + 78);
    }

    drawMap(width, height) {
      const matrix = this.data.matrix;
      const cells = matrix.cells || [];
      const minX = Math.min(...cells.map(cell => cell.x));
      const maxX = Math.max(...cells.map(cell => cell.x));
      const minY = Math.min(...cells.map(cell => cell.y));
      const maxY = Math.max(...cells.map(cell => cell.y));
      const columns = maxX - minX + 1;
      const rows = maxY - minY + 1;
      const base = Math.max(10, Math.min((width - 72) / columns, (height - 94) / rows));
      const unit = base * this.zoom;
      const drawWidth = columns * unit;
      const drawHeight = rows * unit;
      const originX = (width - drawWidth) / 2 + this.pan.x;
      const originY = (height - drawHeight) / 2 + this.pan.y;

      this.context.fillStyle = '#07101c';
      this.context.fillRect(originX, originY, drawWidth, drawHeight);
      cells.forEach(cell => this.drawCell(cell, minX, minY, originX, originY, unit));
      this.drawEventMarkers(minX, minY, originX, originY, unit);
      this.drawPlayer(minX, minY, originX, originY, unit);
      this.drawLegend(width, height, unit);
    }

    drawCell(cell, minX, minY, originX, originY, unit) {
      const x = originX + (cell.x - minX) * unit;
      const y = originY + (cell.y - minY) * unit;
      const color = this.modelColor(cell.model_id);
      this.context.fillStyle = color;
      this.context.globalAlpha = cell.resident ? .95 : .62;
      this.context.fillRect(x + 1, y + 1, unit - 2, unit - 2);
      this.context.globalAlpha = 1;
      this.drawRawPermissions(cell, x, y, unit);
      this.drawVerifiedWalkable(cell, x, y, unit);
      this.context.strokeStyle = cell.resident ? '#f8fafc' : '#172b4b';
      this.context.lineWidth = cell.resident ? 1.5 : 1;
      this.context.strokeRect(x + .5, y + .5, unit - 1, unit - 1);
      if (unit >= 34) {
        this.context.fillStyle = '#f8fafc';
        this.context.font = `${Math.max(9, Math.min(13, unit / 5))}px ui-monospace, Consolas, monospace`;
        this.context.fillText(cell.code, x + 5, y + unit * .48);
        this.context.fillStyle = '#d4e3f5';
        this.context.font = `${Math.max(8, Math.min(11, unit / 7))}px ui-monospace, Consolas, monospace`;
        this.context.fillText(`${cell.x},${cell.y} · ${cell.tile_size?.width || '?'}×${cell.tile_size?.height || '?'}`, x + 5, y + unit * .68);
        this.context.fillStyle = '#f2c879';
        const summary = cell.raw_summary;
        this.context.fillText(
          summary ? `Pxx 非零格 ${summary.nonzero_cells_any_plane}` : 'Pxx 尚未读取',
          x + 5, y + unit * .82,
        );
        this.context.fillStyle = '#a7b8cf';
        this.context.fillText('通行 / 地貌 / 逐格高程：未解码', x + 5, y + unit * .93);
      }
    }

    drawRawPermissions(cell, x, y, unit) {
      const planes = cell.raw_permission_planes;
      const firstPlane = planes && Object.values(planes)[0];
      if (!firstPlane || unit < 112) return;
      const rows = firstPlane.length;
      const columns = firstPlane[0]?.length || 0;
      if (!rows || !columns) return;
      const allPlanes = Object.values(planes);
      const tileWidth = unit / columns;
      const tileHeight = unit / rows;
      for (let row = 0; row < rows; row += 1) {
        for (let column = 0; column < columns; column += 1) {
          const raw = allPlanes.map(plane => Number(plane[row]?.[column]) || 0).find(value => value !== 0) || 0;
          if (!raw) continue;
          this.context.fillStyle = this.rawColor(raw);
          this.context.globalAlpha = .82;
          this.context.fillRect(x + column * tileWidth, y + row * tileHeight, tileWidth + .25, tileHeight + .25);
          if (tileWidth >= 20 && tileHeight >= 16) {
            this.context.globalAlpha = .95;
            this.context.fillStyle = '#06111c';
            this.context.font = `${Math.max(8, Math.min(11, tileWidth / 3))}px ui-monospace, Consolas, monospace`;
            this.context.fillText(
              `P${raw.toString(16).padStart(2, '0').toUpperCase()}`,
              x + column * tileWidth + 2,
              y + row * tileHeight + Math.min(tileHeight - 3, 12),
            );
          }
        }
      }
      this.context.globalAlpha = 1;
    }

    drawVerifiedWalkable(cell, x, y, unit) {
      const tileSize = cell.tile_size;
      if (!tileSize || unit < 112 || !this.visitedTiles.size) return;
      const tileWidth = unit / tileSize.width;
      const tileHeight = unit / tileSize.height;
      const originX = cell.x * tileSize.width;
      const originY = cell.y * tileSize.height;
      for (const key of this.visitedTiles) {
        const [globalX, globalY] = key.split(',').map(Number);
        const localX = globalX - originX;
        const localY = globalY - originY;
        if (localX < 0 || localY < 0 || localX >= tileSize.width || localY >= tileSize.height) continue;
        this.context.fillStyle = '#34d399';
        this.context.globalAlpha = .58;
        this.context.fillRect(
          x + localX * tileWidth,
          y + localY * tileHeight,
          tileWidth + .35,
          tileHeight + .35,
        );
        this.context.globalAlpha = 1;
        this.context.strokeStyle = '#d1fae5';
        this.context.lineWidth = Math.max(1, Math.min(2, tileWidth / 5));
        this.context.strokeRect(
          x + localX * tileWidth + .5,
          y + localY * tileHeight + .5,
          Math.max(1, tileWidth - 1),
          Math.max(1, tileHeight - 1),
        );
      }
    }

    drawEventMarkers(minX, minY, originX, originY, unit) {
      const bounds = this.data.map_definition_bounds;
      const size = this.data.chunk_tile_size;
      if (this.data.event_coordinate_space !== 'map_definition_local_map_plane' || !bounds || !size) return;
      const categories = [
        ['warps', '#34d8ff', 'W'],
        ['npcs', '#c4a7ff', 'N'],
        ['furniture', '#ffab66', 'F'],
        ['triggers', '#ff6b88', 'T'],
      ];
      categories.forEach(([key, color, label]) => {
        (this.data.events?.[key] || []).forEach(item => {
          const tileX = Number(item.tile_x);
          const tileY = Number(item.tile_y);
          if (!Number.isFinite(tileX) || !Number.isFinite(tileY)) return;
          const cellX = bounds.min_chunk_x + Math.floor(tileX / size.width);
          const cellY = bounds.min_chunk_y + Math.floor(tileY / size.height);
          const localX = tileX - Math.floor(tileX / size.width) * size.width;
          const localY = tileY - Math.floor(tileY / size.height) * size.height;
          const x = originX + (cellX - minX + (localX + .5) / size.width) * unit;
          const y = originY + (cellY - minY + (localY + .5) / size.height) * unit;
          if (x < originX || y < originY || x > originX + unit * (bounds.width || 0) || y > originY + unit * (bounds.height || 0)) return;
          this.context.fillStyle = color;
          this.context.strokeStyle = '#07101c';
          this.context.lineWidth = 1.5;
          this.context.beginPath();
          this.context.arc(x, y, Math.max(4, Math.min(8, unit / 22)), 0, Math.PI * 2);
          this.context.fill();
          this.context.stroke();
          if (unit >= 140) {
            this.context.fillStyle = '#07101c';
            this.context.font = 'bold 8px ui-monospace, Consolas, monospace';
            this.context.fillText(label, x - 3, y + 3);
          }
        });
      });
    }

    drawPlayer(minX, minY, originX, originY, unit) {
      const player = this.data.live_player;
      const size = this.data.chunk_tile_size;
      if (!player?.verified || !size) return;
      const chunkX = Math.floor(Number(player.x) / size.width);
      const chunkY = Math.floor(Number(player.y) / size.height);
      const localX = Number(player.x) - chunkX * size.width;
      const localY = Number(player.y) - chunkY * size.height;
      const x = originX + (chunkX - minX + (localX + .5) / size.width) * unit;
      const y = originY + (chunkY - minY + (localY + .5) / size.height) * unit;
      const radius = Math.max(5, Math.min(12, unit * .2));

      // Player circle base
      this.context.fillStyle = '#ffd166';
      this.context.strokeStyle = '#111827';
      this.context.lineWidth = 2;
      this.context.beginPath();
      this.context.arc(x, y, radius, 0, Math.PI * 2);
      this.context.fill();
      this.context.stroke();

      // Heading indicator arrow
      const facing = String(player.facing || 'South').toLowerCase();
      let angle = Math.PI / 2; // South default (+Y down)
      if (facing === 'north') angle = -Math.PI / 2; // -Y up
      else if (facing === 'west') angle = Math.PI; // -X left
      else if (facing === 'east') angle = 0; // +X right

      const arrowLen = radius * 1.6;
      this.context.fillStyle = '#ef4444';
      this.context.strokeStyle = '#ffffff';
      this.context.lineWidth = 1;
      this.context.beginPath();
      this.context.moveTo(x + Math.cos(angle) * arrowLen, y + Math.sin(angle) * arrowLen);
      this.context.lineTo(x + Math.cos(angle + 2.4) * (radius * 0.75), y + Math.sin(angle + 2.4) * (radius * 0.75));
      this.context.lineTo(x, y);
      this.context.lineTo(x + Math.cos(angle - 2.4) * (radius * 0.75), y + Math.sin(angle - 2.4) * (radius * 0.75));
      this.context.closePath();
      this.context.fill();
      this.context.stroke();
    }

    drawLegend(width, height, unit) {
      const raw = this.data.raw_permissions_included ? '已加载当前驻留块 Pxx 原始字节' : '未加载 Pxx 原始字节';
      const elevation = this.data.height?.player_elevation_raw;
      this.context.fillStyle = '#b8c8df';
      this.context.font = '11px ui-monospace, Consolas, monospace';
      this.context.fillText(`M# = ROM 模型 · Pxx = 原始 permission · 绿框 = 实机站立过 · 黄点 = 玩家 · ${raw}`, 14, 21);
      this.context.fillStyle = '#7f95b5';
      this.context.fillText(`绿框只证明可站立；未经过格仍为 unknown，不把 Pxx 猜成墙 · 高度 E=${elevation ?? '?'}；逐格高度未解码`, 14, height - 16);
      if (unit < 20) {
        this.context.fillStyle = '#7f95b5';
        this.context.fillText('放大后显示 M# 和矩阵坐标', width - 190, 21);
      }
    }

    modelColor(modelId) {
      const hue = (Number(modelId) * 47) % 360;
      return `hsl(${hue} 42% 34%)`;
    }

    rawColor(value) {
      const hue = (Number(value) * 43) % 360;
      return `hsl(${hue} 76% 58%)`;
    }
  }

  window.MapSchematicCanvas = MapSchematicCanvas;
})();
