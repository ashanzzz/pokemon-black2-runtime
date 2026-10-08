# 导航雷达 V2 设计与使用说明

本项目的导航雷达以 ROM 静态地图解码为候选地图，以 BizHawk/Bridge 的 RAM PlayerRuntime、事件和落点回读为执行真值。截图只能用于开发者肉眼标定，不参与 AI 状态判断。

## 1. 三层空间模型

### 局部格子

```text
GET /api/v1/navigation/radar/grid?zone_id=445&x=22&y=2&z=33&radius=2
```

- `x/z` 可以任意指定；省略时使用当前主角坐标。
- `y` 省略时使用当前运行时高度层。
- `radius=2` 返回直径 5×5 的详细格子。
- 每格同时返回 `tile_class`、`flags`、`material`、`walkable`、`movement_allowed`、事件候选和单向出口。

### Zone 区域

```text
GET /api/v1/navigation/radar/area?zone_id=445&mode=fuzzy&max_cells=1000
```

或者：

```text
GET /api/v1/navigation/radar/grid?zone_id=445&scope=zone&mode=fuzzy&max_cells=1000
```

- `scope=zone` 忽略局部半径，使用该 Zone 的 ROM 地形边界。
- `mode=detail` 保留逐格信息；面积超过 `max_cells` 时拒绝，避免误把大图当战术图。
- `mode=fuzzy` 自动选择 `block_size`，输出粗粒度地图。

### Matrix 区域

```text
GET /api/v1/navigation/radar/world/map?matrix_id=255&mode=fuzzy&max_cells=1000
```

- Matrix 内的多个 Zone 可以使用同一 Matrix-global 坐标空间。
- `mode=fuzzy` 对大 Matrix 使用块采样，不再把每个源 tile 全部展开；返回 `sampling`，明确 `sampled_cells`、`source_cells`、`coverage_ratio` 和 `complete=false`。
- AI 只使用 fuzzy 做方向判断；准备移动前必须重新请求局部 detail，并由执行器逐步读取 RAM 落点验证。

## 2. “全部地图拼接”为什么不是一张无限平面

B2/W2 的不同 Matrix 会复用相同的局部 X/Z 坐标。把所有 Matrix 强行平铺会制造不存在的相邻道路，导致寻路穿墙或从室内坐标跳到室外坐标。因此提供的是逻辑世界图谱：

```text
GET /api/v1/navigation/radar/world/atlas
```

该接口返回：

- 全部 Matrix 的边界、块尺寸、所属 Zone；
- 全部 Zone 名称、Area、地形边界；
- ROM 中解析出的 Warp/门/传送连接候选；
- 目标 Zone/Matrix 能解析时的候选归属；
- `requires_runtime_verification=true`，表示切图前后仍需 RAM 证据确认。

具体地图栅格通过：

```text
GET /api/v1/navigation/radar/world/map?matrix_id=<id>&mode=fuzzy
```

按 Matrix 分块请求。这样既能覆盖整个 ROM 地图，又不会伪造跨 Matrix 的几何相邻关系。

## 3. 模糊块保留的属性

模糊块不是简单多数投票，而是保留 AI 导航必须的语义：

- `walkable` / `walkable_all` / `blocked_ratio`；
- `contains`：块内出现过的关键符号；
- `events`：门、Warp、NPC、家具/交互物件；
- `directional.allowed_exits` 与 `one_way`；
- 草丛 `*`、水域 `W`、墙体 `B`、围栏 `#`、树木 `T`；
- `bounds`：模糊块覆盖的真实 X/Z 范围；
- `sampling`：粗略结果的覆盖率和“不可直接执行”声明。

符号优先级为：`N > D > O > v > B > T > W > * > # > . > ?`。关键事件不会被大片普通地面覆盖。

## 4. 单向跳台坐标约定

API 使用 `(X, Z)`，不是 `(Z, X)`。例如当前算木牧场实机解码中：

- `(31,52)`、`(31,53)`、`(31,54)` 是 `tile_class=0x72`，`symbol=v`，`allowed_exits=["right"]` 的单向跳台候选；
- `(52,31)`、`(53,31)`、`(54,31)` 当前解码为 `tile_class=1`、`flags=129` 的阻挡地形，不应被 API 当成同一组跳台。

最终是否能跳下、落到哪一格，仍以实际输入后的 PlayerRuntime 坐标变化为准。

## 5. 参考实现原则

- BizHawk：Memory Domain、Frame Control、Savestate、External Tool；
- SwissArmyKnife / CTRMap-CE / ndspy：Matrix、Zone、NARC、碰撞和事件模型；
- swan / PKHeX / PokéBot：运行时结构、数据模型和确定性自动化；
- NTRGhidra / PMC / White2Upgrade：ARM9/Overlay 静态分析与后续 Hook 边界。

这些项目用于解析和架构借鉴，不替代本项目针对当前 ROM 区域、当前模拟器状态的 RAM 验证。


## 6. 可交互阻挡物与 NPC 动态层

雷达现在区分“不可进入”和“不可交互”：

- `#`：不可走、没有已知交互；
- `C`：宝可梦电脑，`walkable=false`，但 `interactable=true`，动作候选为 `open_pc`；
- `R`：服务柜台/接待台，不能站上去，但可以从 `stand_tiles` 相邻格交互；
- `O`：一般可调查物件；
- `N`：NPC，NPC 所在格本身保持 `walkable=true`，AI 应走到相邻 `stand_tiles`，而不是走进 NPC 格。

单独查询附近交互对象：

```text
GET /api/v1/navigation/radar/interactions?radius=8
```

每个 NPC 同时保留：

- 稳定静态 ID：`static_entity_id`；
- `npc_id`、`script_id`、`sprite_id`、`flag_id`、`movement_id`；
- `role`、`service`、`actions`、`confidence`；
- `runtime.actor_uid`、`runtime.slot`、实时格子、朝向；
- `presence`：`runtime_present`、`static_spawn_not_current` 或 ROM 候选。

例如 Zone 443 的 `script_id=2100` 已登记为宝可梦中心护士，暴露：

```json
{
  "role": "pokemon_center_nurse",
  "service": "recovery",
  "actions": ["heal_party"],
  "confidence": "verified_registry"
}
```

其他 NPC 如果只有 ROM 的脚本号、精灵编号和旗标，API 会返回 `candidate/unknown`，不会把“可能卖道具”误报成确定商店。商店出售内容必须进一步解码脚本或读取实际商店菜单；菜单出现后才能登记 `inventory`。

NPC 移动时不修改静态 ID：

1. ROM 出生点继续保留为静态追踪证据；
2. ActorSystem 当前格子作为实时位置；
3. 如果绑定 NPC 离开出生点，出生点标记为 `static_spawn_not_current`；
4. 雷达在新的实时格子显示 `N`；
5. AI 交互必须使用实时格子、实时朝向和相邻站位，不能使用旧出生点盲按 A。

`runtime` 层来源于当前 ARM9 ActorSystem；`role/service` 层来源于脚本、实体、注册表和行为验证，二者严格分开。

## 7. 门 / 出入口 (Warp) 坐标与目的地语义

在 Gen-5 ROM 数据中，Warp 触发器坐标使用 16-world-units，且通常记录在网格地块中心（`+8`，即 `.5` 格）：
- 必须使用整数向下取整 `floor(x_raw / 16)`，绝对不能使用 `round()`；
- 使用 `round()` 会导致带 `.5` 的地块全部向右下四舍五入偏移 `+1, +1`；
- 改用 `floor()` 后，所有门自动对齐到正确格（例如算木镇宝可梦中心大门精确落在 `X=105, Z=693`，其站立交互格为 `X=105, Z=694`）。

### 结构化目的地与设施语义

雷达单元格的 `interaction` 字段自动关联 Warp 目的地 ZoneHeader 及区域名称：
- `target_zone_id`：目标区域 ID（如 `443`）；
- `target_zone_name`：目标区域全名（如 `算木镇 精灵中心 (Pokémon Center)`）；
- `facility_type`：设施类型（`pokemon_center` | `poke_mart` | `gym` | `player_house` | `lab` | `route` | `ranch` | `building_interior` 等）；
- `is_pokemon_center`：布尔值便捷标记；
- `stand_tiles`：门前合法的站立格与朝向（例如在 `(105, 694)` 朝向上方进入大门）；
- `text_map` 模式下会在地图下方自动附带 `门/出入口与传送目的地 (Doors & Warps)` 列表，清晰罗列视野内所有门的前往地点。

## 8. 剧情封路角色与道闸 (Story Gates & Roadblocks)

在宝可梦 RPG 中，关键路口经常有 NPC 暂时挡路（如算木镇北出口前往20号道路的阻挡NPC、桧扇市出口拦截等）：
- **底层机制**：受特定的剧情旗标（如 `flag_id = 731`，`script_id = 8`）控制；当前剧情下 NPC 存在于 ARM9 `ActorSystem` 中并堵塞通道；
- **雷达呈现**：
  - 符号显示为 **`[!]`**；
  - 属性被判定为 `walkable: false`（道闸当前关闭，物理通行被阻断）；
  - `kind` 显示为 `剧情封路角色 (Story Gate NPC)`；
  - `story_gate` 提供解开该阻挡所需的前提条件、目标目的地与关联剧情旗标。
- **动态剧情生命周期**：
  - 当主角完成对应剧情任务（如访问阿戴克家/触发对话后），游戏脚本在 ARM9 RAM 中更新该旗标；
  - 场景刷新时，游戏引擎不再于 `ActorSystem` 生成该封路 NPC；
  - **雷达的 `[!]` 自动消失**，原路段恢复为平坦可通行的道路 `.`，实现完全数据闭环。

## 9. 细分交互物件体系（告别一律粗暴归为 O）

在宝可梦世界中，存在多种截然不同的地面交互物：
- **`[S]` 标识牌/路牌 (Signpost)**：路牌、城镇牌、牧场入口牌等；必须在正面正中间（`Z+1`）面向上方读取；
- **`[K]` 垃圾桶 (Trash Can)**：道馆机关、藏有吃剩的东西/隐藏道具；可从相邻格调查；
- **`[C]` 宝可梦电脑 (PC Terminal)**：队伍/道具存储；站在电脑正前方交互；
- **`[R]` 服务柜台 (Service Counter)**：护士台、商店柜台；不可翻越，隔着柜台对话；
- **`[V]` 自动售货机 (Vending Machine)**：购买饮品；
- **`[E]` 书架 (Bookcase)**：阅读书籍与期刊；
- **`[W]` 水岸/湖岸 (Shore / Surf Edge)**：水陆交界岸边，属于水域机动边界，绝不误标为家具 `O`；
- **`[.]` 地下隐藏道具 (Hidden Item)**：属于地面埋藏物，不占用可见模型，保持地面通行符号 `.`，并在交互 API 中提供探宝坐标。
- **`[O]` 仅保留给一般杂项交互物**（如时钟、盆栽、雕像）。
