# Pokémon Black 2 Reverse Engineering Workbench v9

面向《宝可梦 黑2》IREJ / 日版 v1.1 的只读 RAM 观察、ROM 原版世界重建、3D 运行时可视化与逆向诊断项目。

v9 的核心不是增加更多页面，而是把整个项目统一成一个 **Reverse Engineering Workbench**：

```text
Activity Rail
+ Explorer
+ Main Editor / 3D World
+ Context Inspector
+ Bottom Dock
+ Status Bar
```

默认语言为中文，可在右上角切换 English；选择会保存在浏览器 `localStorage`。

## 最简单的启动方式

Windows 双击：

```text
BLACK2_LAUNCHER.cmd
```

第一次只需选择：

1. BizHawk 的 `EmuHawk.exe`
2. 你自己合法持有的 Pokémon Black 2 `.nds`

以后启动器会自动启动 Backend、BizHawk、Lua Bridge 并打开 Workbench。

## 主入口

- `/` — **Workbench v9**
- `/workbench` — Workbench 同义入口
- `/original-map` — 兼容入口，进入 Workbench 的 World workspace
- `/player` — 跳转 Workbench → Player
- `/dialogue` — 跳转 Workbench → Dialogue
- `/memory` — 跳转 Workbench → Memory
- `/evidence` — 跳转 Workbench → Evidence
- `/runtime-monitor` — 跳转 Workbench → Monitor

旧的独立 World UI 与 Runtime Monitor UI 已从发布结构移除，避免同一事实被多套主界面重复解释。

## Workbench Workspaces

### World

唯一主地图界面。纯 3D：Terrain、Building、Player、Runtime NPC、Warp / Trigger 定义使用同一 Gen5 Field World 坐标体系。

3D 对象可以直接点击；选中的 Player / Building / NPC / Terrain 会进入右侧 Inspector，并显示：

- Runtime / ROM 来源
- 位置与旋转
- Model / DoorUID
- confidence
- 原始 JSON
- 与对象相关的操作

### Player

集中查看：GPos、WPos、Facing、Movement、Chunk、Grid↔World residual 和 Root Chain。

日常读取缓存；只有用户点击“显式发现主角”时才允许一次受控 discovery。

### Dialogue

Current Dialogue、Visible Text、Loaded Stream、Printer Evidence 与 Dialogue Timeline 在同一 workspace。

### Memory

默认只看已解析结构和 RuntimeHub raw cache。全量 RAM、write trace、pattern scan 仍属于显式高级工具，不会因打开 workspace 自动执行。

### Evidence

空间校准、桥/楼梯高度、Observed 3D Navigation Graph 和报告下载统一管理。

### Monitor

HTTP、Bridge、Semantic、组件版本、生命周期日志和性能策略。

### Tools

保留 Controller、RAM Dumper、Memory Tracer、Dialogue Checkpoints、API Docs 等高级实验入口。

## Bottom Dock

Workbench 底部统一承载辅助信息：

- Events
- Asset Errors
- Navigation
- Calibration
- Performance
- Raw / Structure

这些信息不再永久挤占左右 Inspector。

## API v9

新增只读聚合层：

```text
GET /api/v1/workbench/bootstrap
GET /api/v1/workbench/events
GET /api/v1/workbench/evidence
GET /api/v1/workbench/versions
GET /api/v1/workbench/schema
```

`bootstrap` 只读取 RuntimeHub / PlayerRuntime 缓存与元数据，**不会隐藏触发完整 RAM discovery**。

重型操作仍保留在原有显式 API：

- `/api/v1/player/runtime` — operator-initiated Player discovery
- `/api/dev/memory_*` — bounded RE experiments
- `/ram-dumper` — Universal Evidence
- legacy NativeMap 4 MiB scan 仍默认关闭

## 世界与导航事实原则

- ROM = Static World Database
- RAM = Runtime Overlay
- Player renderer 使用 FieldActor.WPos
- AI / navigation 使用 FieldActor.GPos
- Observed navigation node = `(zone_id, x, y, z)`
- 桥上 / 桥下不会压成一个二维节点
- ROM permission 在语义完全验证前仍是 candidate

## Confidence

Workbench 统一使用：

- VERIFIED
- PROBABLE
- CANDIDATE
- UNRESOLVED
- ERROR

`UNRESOLVED` 是正常逆向状态，不等于 HTTP / Bridge 出错。

## 性能策略

- Player：cache-first
- Scene：事件驱动，不每秒重建
- Runtime NPC：默认关闭；开启后 bounded ActorSystem + heap 读取
- 3D：30 FPS、DPR ≤ 1.25、MSAA off
- legacy Main-RAM visual scan：默认关闭
- Workbench aggregation：不做全 RAM scan

详细架构见：

- `docs/WORKBENCH_V9_CN.md`
- `docs/API_WORKBENCH_V9.md`
- `docs/CALIBRATION_PROTOCOL_CN.md`
- `docs/UNIFIED_RUNTIME_ARCHITECTURE_CN.md`


## Encounter Region Workbench v12

在 v11 的纯移动/互动分离与同 Matrix 室外拼接基础上，本版本增加可供 UI 与 AI 共用的 Encounter Region 层：

- 草地/深草按 **同 Zone + 同 Y + 同语义 + 四方向连通** 聚合；
- Region 真值是完整 `tiles[]`，不是 Bounding Box；
- 输出 boundary / interior / entry / outline / 推荐 patrol；
- Navigation 增加 `allowed_nodes`，Region 巡逻路径不得离开 exact Tile Set；
- Workbench 新增“遇敌区域”叠层与 Encounter Dock；
- 同 Matrix 拼接场景可一次查看相邻 Zone Region，但跨 Zone 执行继续关闭；
- Wild Encounter Profile / Pokémon location / Battle identity 没有验证前保持 `research`，不伪造概率或战斗确认。

API：

```text
GET  /api/v1/encounters/capabilities
GET  /api/v1/encounters/regions
GET  /api/v1/encounters/regions/current
GET  /api/v1/encounters/regions/{region_id}
GET  /api/v1/encounters/profiles
GET  /api/v1/encounters/search
POST /api/v1/encounters/tasks
GET  /api/v1/encounters/tasks/{task_id}
POST /api/v1/encounters/tasks/{task_id}/cancel
```

详见 `docs/ENCOUNTER_REGION_WORKBENCH_V12_CN.md` 与 `LIVE_TEST_ENCOUNTER_V12_CN.md`。

## Matrix Global Navigation v13

v13 将同一 `MapMatrix` 内的 Zone 从导航地址中移除。推荐目标只使用：

```text
matrix_id? + x + y + z
```

实时执行时 `matrix_id` 也可从 PlayerRuntime 自动推断；目标 Zone 由 ROM Matrix ownership 自动解析。

新增：

```text
POST /api/v1/navigation/plans             global_grid
POST /api/v1/navigation/tasks             global_grid
GET  /api/v1/navigation/global/resolve
POST /api/v1/navigation/global/snap
```

同 Matrix 相邻 Zone 可以由一个懒加载 A* 直接生成长路线并连续执行，Zone transition 只保留为 metadata。不同 Matrix 仍要求 verified connector，不会把独立室内场景硬压进同一套 `(x,y,z)`。

详见 `docs/GLOBAL_NAVIGATION_V13_CN.md` 与 `LIVE_TEST_GLOBAL_NAV_V13_CN.md`。
