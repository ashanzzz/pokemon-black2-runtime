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
