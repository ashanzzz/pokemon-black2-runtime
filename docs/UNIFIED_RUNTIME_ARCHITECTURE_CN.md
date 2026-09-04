# Pokémon Black 2 Runtime v4 · 统一前后端架构

## 1. 重构目标

旧架构的问题不是“网页端口数字不一样”，而是不同页面拥有自己的状态判断和 RAM 读取节奏：

- 首页同时请求 `/api/bizhawk/status`、`/api/state`、`/api/observer/presentation`；其中任意一个语义请求失败，UI 就显示“后台离线”。
- `trainer-state.html` 自己读取 `/api/state.player_facing`，与后续 Player Runtime 的结构解析形成第二套方向事实源。
- 地图、对话、主页均可能主动读取 RAM；BizHawk Lua bridge 是单请求序列化执行，重复轮询会放大延迟，并污染基于帧间位移的 Walk/Run 速度测量。
- ROM 地图服务在 import 时就打开 ROM；ROM 路径暂时不可用会拖垮并不依赖 ROM 的 HTTP / Dialogue / Player 子系统。

v4 的原则是：**一个高频事实采样源、多个只读模块视图、ROM 地图惰性加载、旧 API 作为兼容投影。**

## 2. 端口角色

默认端口只有两个，而且角色固定：

- `127.0.0.1:8765`：FastAPI + Browser UI（HTTP）
- `127.0.0.1:8766`：BizHawk Lua Bridge（TCP JSON stream）

所有前端调用同源相对 URL，例如 `/api/v1/runtime/snapshot`，HTML 不拥有端口配置。

## 3. Runtime Hub

`backend/black2/runtime/hub.py` 是浏览器的主数据面。

```text
BizHawk TCP bridge
       │
       ▼
SemanticStateEngine  ← 唯一高频 Player/Dialogue sampler
       │
       ├─ PlayerRuntimeService.latest
       ├─ semantic state
       └─ dialogue state
       │
       ▼
RuntimeHub cached snapshot
       │
       ├─ /api/v1/runtime/health
       ├─ /api/v1/runtime/snapshot
       ├─ legacy /api/state
       └─ legacy /api/observer/presentation
```

Transport 与 Semantic 状态严格分离：

- HTTP online / offline
- Bridge connected / waiting
- Semantic ready / degraded / unresolved
- Player resolved / candidate / unresolved

因此 Dialogue parser 暂时失败时，Bridge 仍然显示 connected。

## 4. Player Runtime 权威链

```text
Field
 └─ FieldPlayer
     └─ FieldPlayerCore
         ├─ PlayerState
         ├─ FieldPlayerGrid
         └─ FieldActor (player)
```

当前朝向：

- 主证据：`FieldActor.FaceDir`
- 交叉验证：`PlayerState.RotationAngle`
- `MotionDir` 仅显示为运动/动画方向，不作为当前 Facing。

四方向受控 RAM 已验证：

| 观察方向 | FaceDir | RotationAngle |
|---|---:|---:|
| Up | 0 | 0x0000 |
| Down | 1 | 0x8000 |
| Left | 2 | 0x4000 |
| Right | 3 | 0xC000 |

## 5. 前端信息架构

一级域：

1. `Runtime`：连接、帧、聚合状态，只显示统一 Snapshot。
2. `Player`：朝向、坐标、MoveStatus、ActionStatus、Grid、ExState、Tile、Walk/Run 标定。
3. `Dialogue`：可见文本、loaded stream、Printer、Choice、Timeline。
4. `World`：地图身份、BMD0 geometry、Runtime Prop、DoorUID、Warp/Trigger、Actor/NPC、collision raw data。
5. `Trainer`：Save/GameData profile。禁止再显示实时朝向和移动。
6. `Tools`：输入、导航实验、RAM dump、memory diagnostics、checkpoint。

## 6. World / 建筑系统

地图不再使用“为了看起来完整而手写”的建筑方块或 POI 作为 Runtime Truth。

```text
Current RAM
 Field
 ├─ G3DMapper → Matrix / current chunks
 ├─ FieldPropSystem → props / DoorUID
 └─ FieldActorSystem → runtime actors
          │
          ▼
Verified runtime identity
          │
          ▼
ROM
 a/0/0/8  BMD0 + permission planes
 a/0/0/9  matrices
 a/0/1/2  ZoneData / Map Header
 a/0/1/4  BTX0
 a/1/2/6  furniture / NPC spawn / warp / trigger
```

`MapSceneService` 输出：

- BMD0 geometry cells
- current runtime prop instances
- DoorUID candidates
- ROM warp regions
- runtime actors and static NPC spawns
- candidate Door ↔ Warp spatial links

“附近”只能成为 `candidate`。门的真实目的地必须通过 before/transition/after 的切图证据升级。

## 7. ROM 可选子系统

没有 `BLACK2_ROM_PATH` 时：

- Runtime：可用
- Player：可用
- Dialogue：可用
- Controller / Dump / Memory：可用
- World ROM join / 3D：明确返回 ROM unavailable

ROM 路径不再阻止 FastAPI 启动。

## 8. 兼容策略

旧接口仍保留：

- `/api/state`
- `/api/bizhawk/status`
- `/api/observer/presentation`
- `/api/v1/map/current`

但它们不再独立产生第二份事实，而是投影统一 Runtime 缓存或已验证 Player Runtime。
