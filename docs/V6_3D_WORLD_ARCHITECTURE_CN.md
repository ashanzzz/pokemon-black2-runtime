# v6 纯 3D World Viewer 架构

## 目标

v6 不再把地图模块理解成“2D 地图 + 3D 预览”。产品形态只有一个：**3D World Viewer**。

2D 只允许作为开发阶段的调试投影，不进入默认 UI，也不作为任何语义真值来源。

## 唯一坐标系

所有场景对象先进入 `gen5-field-world-v1`：

- +X = East
- +Z = South
- +Y = Up
- 1 tile = 16 world units
- 静止时 tile 中心：`WPos.x = GPos.x*16+8`，`WPos.z = GPos.z*16+8`

Terrain、Building、Player、Runtime Actor 都先保留 canonical world coordinate。浏览器为了镜头稳定可减去 `scene_origin`，但这只是显示变换。

## 静态 / 动态拆分

### StaticWorld（不实时）

优先级：

1. `reverse_engineering/derived/v5/zones/zone_XXXX.json`
2. 缺失时 `OriginalWorldService` 从 ROM 读取

内容：Zone、Area、Matrix、Terrain、Buildings、Door metadata、静态 Entities 定义。

### RuntimeOverlay（实时）

- Player：`player_runtime_service.latest`，高频读取缓存，不额外打 RAM
- Scene identity：低频刷新（约 1 s），用于 Zone/Matrix 交叉验证
- Runtime Actors：低频刷新
- Zone 变化：只替换 StaticWorld；Player runtime contract 不变

## 为什么室内/室外统一

v5 旧页用 matrix 中心构造相机，且 Player 未真正加入 Scene Graph；室内小矩阵和室外大矩阵会出现不同的显示基准。

v6 在 Scene 加载时取 Player WPos 作为 `scene_origin`，然后统一执行：

`display = canonical_world - scene_origin`

因此：

- 室外 Player 可见
- 室内 Player 可见
- 切图后自动重建 StaticWorld
- Player 不需要任何“室内偏移表”或手工校准

## Player Renderer

CTRMapV 对 B2/W2 的公开逆向表明：

- `FIELD_MMODEL_INDEX` = NARC 47 = `a/0/4/7`
- `FIELD_MMODEL_RES` = NARC 48 = `a/0/4/8`
- 默认男主 OBJCODE = 231
- 默认女主 OBJCODE = 240

v6 会优先读取 ROM 原始资源：

- NSBMD actor：Apicula → GLB
- NSBTX billboard actor：尝试 Apicula extract → PNG billboard
- 若本地 Apicula 对该 BTX0 无法导出 PNG：使用明确标记的 `pixel_marker`，绝不把 fallback 当成原版资源

## 新 API

- `GET /api/v1/map/v6/status`
- `GET /api/v1/map/v6/player/live`（高频，0 次额外 RAM 请求）
- `GET /api/v1/map/v6/scene/current`
- `GET /api/v1/map/v6/scene/zone/{zone_id}`
- `GET /api/v1/map/v6/actors/live`
- `GET /api/v1/map/v6/player/asset/meta`
- `GET /api/v1/map/v6/player/asset/model.glb`
- `GET /api/v1/map/v6/player/asset/sprite/{frame}.png`

原 `/api/v1/map/v5/*` 保持兼容。
