# Encounter Region Workbench v12

## 1. 目标

本版本把已有的 Tile 级地形语义和同 Zone 导航提升为一层可供人和 AI 共用的 **Encounter Region** 能力：

```text
ROM Terrain TileClass
        ↓
精确 Tile 语义
        ↓
四方向连通分量
        ↓
Encounter Region (exact Tile Set)
        ↓
受限 Navigation Subgraph
        ↓
Encounter Patrol Task
```

本版本只实现已有证据能支持的部分。它不会因为地图上存在草地就伪造某只 Pokémon 的出现概率，也不会把一次 `OVERWORLD` 中断自动声明成“已确认野生战斗”。

## 2. 后端数据模型

### 2.1 Region 是精确 Tile Set

每个 Region 以 `(zone_id, x, y, z)` 的完整 Tile 集合作为真值，不用 Bounding Box 代替实际区域。

同一高度层、同一 encounter method、同一 terrain kind 的 Tile 只按上下左右四方向聚合；对角接触不合并。因此桥上/桥下、不同楼层以及对角草格不会被错误拼成一个区域。

核心输出：

```text
region_id
zone_id / y
terrain_kind
encounter_method
encounter_eligible
requirements
evidence
tile_count
tiles[]
bounds
boundary_tiles[]
interior_tiles[]
entry_tiles[]
outline_segments[]
patrol
source
```

### 2.2 当前 evidence 门控

| 类型 | API 语义 | Evidence | 是否可声称遇敌 |
|---|---|---|---|
| tall/very tall grass + single predicate | `walk_regular` | verified | 是，限 TileClass predicate |
| dark grass + double predicate | `walk_double_grass` | verified | 是，限 TileClass predicate |
| water + requires surf | `surf_candidate` | probable | 否，只是 Surf 导航候选 |

`Terrain Material` 与 `Encounter Eligibility` 分离。水域不会因为 UI 是蓝色就被提升成 verified encounter region。

### 2.3 Patrol 策略

Region Builder 会给出稳定、可重复的推荐策略：

- 1 Tile：`none`，不假设原地转向能触发遇敌。
- 2 Tile：`ping_pong`。
- 存在 2×2：优先最小确定性 `loop`。
- 其他细长/异形 Region：使用图直径端点生成 `line_shuttle`。

所有 route waypoint 都是 Region 的实际 Tile。

## 3. 导航安全约束

Navigation Planner / Task 新增 `allowed_nodes`。Encounter Task 在 Region 内巡逻时：

```text
AllowedNodes = region.tiles
```

规划器只有在 `start`、`goal` 和完整路径都属于 AllowedNodes 时才允许执行。

即使第三方/旧 StaticNavigationProvider 不支持新的 `allowed` 参数，最终 plan invariant 仍会验证路径；任何出界路径返回 `NAV_ALLOWED_SUBGRAPH_ESCAPE`，不会静默执行。

这不是 UI 约束，而是后端执行约束，因此第三方 API 客户端也不能绕过。

## 4. Encounter Task 状态机

```text
queued
  ↓
entering_region        # 玩家尚未位于 exact Tile Set
  ↓
确认 PlayerRuntime ∈ region.tiles
  ↓
patrolling
  ↓
┌───────────────────────────┐
│ succeeded / failed        │
│ cancelled / interrupted   │
└───────────────────────────┘
```

执行原则：

1. 当前只允许 same-Zone task。
2. 玩家不在 Region 时，先选择可达 entry tile。
3. 到达后重新读取 PlayerRuntime；不以“导航接口返回成功”代替真实落点验证。
4. Patrol 每条腿使用 `allowed_nodes=region.tiles`。
5. 每条腿下发前重新读取 bounded ActorSystem occupancy；NPC 短暂挡住目标或路径时会做有限等待/重试，不沿用过期占位。
6. 每条腿完成后再次确认玩家仍在 exact Tile Set。
7. `screen_type != OVERWORLD` / Player 不可控时，底层导航清除输入；Encounter Task 标为 `interrupted`。
8. `battle_confirmed` 始终为 `false`，直到有经过验证的 Battle Runtime decoder。

Surf Region 只有在 PlayerRuntime 已经是 `transport_mode=Surf` 时才能启动；本版本不会偷偷打开菜单或自动进入 Surf。

## 5. API

### 能力边界

```http
GET /api/v1/encounters/capabilities
```

### 单 Zone Region

```http
GET /api/v1/encounters/regions?zone_id=439&y=1
```

### 当前玩家 Region，可与 v11 stitched world 联动

```http
GET /api/v1/encounters/regions/current?connected=true&max_zones=24
```

`connected=true` 会返回同一 Matrix、物理相邻的室外 Zone Region。非当前 Zone 的区域是 read-only；跨 Zone 自动执行仍保持关闭。

### Region 详情

```http
GET /api/v1/encounters/regions/{region_id}?zone_id=439&y=1
```

### 尚处于研究状态的 Profile / Pokémon Search

```http
GET /api/v1/encounters/profiles?zone_id=439
GET /api/v1/encounters/search?pokemon_id=531
```

当前明确返回 `status=research`、空结果和原因。这样第三方 AI 不会把尚未验证的 ROM encounter mapping 当成事实。

### 创建巡逻任务

```http
POST /api/v1/encounters/tasks
Content-Type: application/json

{
  "region_id": "z439-y1-walk_regular-tall_grass-cc1",
  "zone_id": 439,
  "y": 1,
  "strategy": "auto",
  "movement_mode": "auto",
  "until": "overworld_interrupted",
  "max_cycles": 1000,
  "max_steps_per_leg": 2000
}
```

### 状态与取消

```http
GET  /api/v1/encounters/tasks/{task_id}
POST /api/v1/encounters/tasks/{task_id}/cancel
```

## 6. Workbench UI 设计

### 6.1 信息层级

地图仍然是主任务面，不新增一个需要跳转的独立“Encounter 页面”。

- 顶部：`遇敌区域` 图层开关 + 当前区域数量。
- 3D：半透明 Tile overlay + Region outline。
- Bottom Dock / Encounter：区域列表、筛选、证据、巡逻参数、任务状态。
- 原有 Inspector：继续负责玩家/NPC/场景等对象证据。

这样避免地图、操作与证据被拆成三个互相跳转的页面。

### 6.2 颜色不是唯一编码

Region 使用不同色相辅助快速区分，但列表和详情始终同时显示：

```text
普通草 / 深草 / Surf 候选
verified / probable
Zone
Tile count
patrol strategy
```

因此色弱、低亮度屏幕或截图压缩后仍可理解状态。

### 6.3 渐进披露

高频信息直接显示：Region 类型、格数、证据等级、是否可巡逻。

低频细节不长期挤占地图：exact Tile Set、entry/interior、推荐 route、task stop reason 在 Encounter Dock 的详情区域展开。

### 6.4 动作靠近对象

选择 Region 后，Strategy / Movement / Start / Stop 与该 Region 详情位于同一上下文，不要求用户去另一个工具页执行。

对 stitched world 中的其他 Zone，仍可观察 Region，但“开始巡逻”禁用并解释原因，避免呈现一个看似能点、实际上会失败的动作。

## 7. 前端渲染性能

Region Tile 使用 Three.js `InstancedMesh`，而不是每格创建独立 Mesh：

- 每个 Region 一份 PlaneGeometry / Material；
- 每格只写 instance transform；
- outline 使用 LineSegments；
- 图层可以整体关闭；
- 不影响 RAM 采样频率。

Region overlay 只属于 presentation layer；规划真值始终来自后端 exact Tile Set。

## 8. 当前明确不做的事情

本版本不伪造：

- Pokémon -> Encounter Profile；
- Zone -> verified wild encounter resource；
- slot probability；
- active subentry / season / swarm 条件；
- rustling grass 当前 RAM 坐标；
- confirmed wild battle / opponent species；
- cross-Zone encounter task execution。

上述能力继续通过 `capabilities / status=research / battle_confirmed=false` 明确暴露缺口，而不是用 UI 猜测填空。

## 9. 测试

专项覆盖：

- verified grass / probable water evidence 不混淆；
- 四方向连通分量；
- exact Tile Set；
- 2×2 loop；
- 两格 ping-pong；
- 单格不假设 turn-in-place；
- allowed navigation subgraph 不得出界；
- 前端 Encounter layer / API wiring；
- 旧 provider 参数兼容。

最终执行：

```bash
python -m pytest -q
node --check frontend/workbench.js
node --check frontend/world3d-runtime-fixed.js
```
