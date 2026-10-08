# Pokémon Black 2 多材质路面移动、多宽门穿行、立体导航与特技动作技术规范与验收方案 (V24)

## 一、 架构背景与核心目标

在《宝可梦 黑2》自主决策 Agent 系统中，宏观决策依赖确定性底层控制器执行复杂物理世界的导航动作。
为了彻底解决大地图与复杂地形中的死角问题，本规范系统性研究、设计、实现并验证以下 5 大核心移动与交互体系：

1. **细杆子/窄桥 (Catwalk, TileClass 0xBE/0xBF)**：
   - 沿桥轴纵向安全移动（East-West / North-South）与平衡计时监控；
   - 侧向按键跳跃/跌落至下层平地（One-way Vertical Drop Transition）的物理与寻路机制。
2. **多宽度门 (1/2/3/4 宽门) 与进门切入矢量 (Approach Vectors)**：
   - 门在 ROM 中的矩形覆盖 `(wx, wz, ex, ez)`；
   - 1 宽门 (单门)、2 宽门 (宝可梦中心/道馆)、3 宽门 (关卡通道)、4 宽门 (工业区/要塞主入口)；
   - 进门前进矢量判定（南进北出、北进南出、东进西出、西进东出）；
   - 多格目标区域感知（Multi-Tile Goal Area）与 NPC 动态避让备用待命格。
3. **立体多层/阶梯导航 (Vertical Multi-Story & Staircases) 与 AI 语义可读性**：
   - 物理标高（如立涌工业园 0.0 -> 8.0 -> 24.0 -> 32.0 4 级高度）的全链路 A* 连贯动作；
   - 护栏扶手约束与脱轨保护；
   - AI 结构化输出（阶梯序号、标高高差、推荐载具、动作序列）。
4. **水面冲浪移动、下水跃迁与登陆 (Surf Locomotion & Shore Transitions)**：
   - 陆地水岸下水点识别（Shoreline Snap & Height Delta <= 8.0）；
   - 下水动作状态机跃迁（`OnFoot` -> `Surf`）；
   - 水面自由巡逻；
   - 岸边上岸登陆状态机回切（`Surf` -> `OnFoot`）。
5. **飞天/飞翔 (Fly Fast-Travel) 大地图跨区快速传送**：
   - 室外地图许可检测（`ZoneHeader.enable_fly_from`）；
   - 队伍技能检测（`Move 19 Fly`）；
   - 目标城镇着陆点提取（`ZoneHeader.fly_x, fly_y, fly_z`）；
   - 语义化快捷传送与门垫着陆校验。

---

## 二、 模块详细设计与物理机制

### 1. 独木桥/细杆子 (Catwalk) 移动与侧向跳下机制

- **ROM 与 RAM 属性**：
  - `TileClass 0xBE`：独木桥桥身主体；`0xBF`：独木桥入口/出口；
  - 桥轴推断：通过检测两端连通格确定轴向（如 `east_west`）；
  - 桥轴方向允许双向通行（`allowed_exits = ["left", "right"]`）；
  - 垂直方向为跌落边缘（`side_drop_directions = ["up", "down"]`）。
- **侧向跳下机制 (One-way Catwalk Drop)**：
  - 在实机物理中，角色在细杆子上向侧面按方向键，会失衡跌落/跳跃到着陆地面；
  - 落地目标格：`(x, z + dz, y_ground)`；
  - 落地合法性校验：
    1. 下层对应的地块存在且不是高差断崖/障碍物/深渊；
    2. 下层地面相对细杆子高差为负（$\Delta Y < 0$）；
    3. 下层地面为可通行平地（如 paved_road, dirt, grass）；
  - 寻路图建模：
    - 在常规平地寻路中，将侧向跌落标记为高代价（Cost = 8.0）单向有向边，避免巡逻时意外跌落；
    - 当目标位于下层地面且跳落是快捷路径时，A* 规划器可主动选择此单向跳跃边，快速降落到地面。

### 2. 多宽门与进门切入矢量 (Multi-Width Doorways)

- **ROM 数据解构**：
  - 每个 Warp 在 ROM 中定义为：`(x_raw, y_raw, x_extent_raw, y_extent_raw, target_zone_or_map_raw)`；
  - 对应网格矩形：
    $X \in [wx, wx + ex - 1], \quad Z \in [wz, wz + ez - 1]$；
  - 宽度分型：
    - $ex = 1$：单人窄门（主角家卧室、民居）；
    - $ex = 2$：双开大门（宝可梦中心、算木道馆）；
    - $ex = 3$：关卡检查站（Route Gateways）；
    - $ex = 4$：大型工业区、要塞主大门（立涌工业园北门）。
- **前进方向判定算法 (Approach Vector Resolution)**：
  - 门的前向待命格与后向墙体探测：
    - 若门北侧 `(wz - 1)` 为墙体，南侧 `(wz + ez)` 为可通行平地：
      **朝向为南（South-facing），进门矢量为向北（Step North），出门矢量为向南（Step South）**；
    - 若门南侧 `(wz + ez)` 为墙体，北侧 `(wz - 1)` 为可通行平地：
      **朝向为北（North-facing），进门矢量为向南（Step South）**；
    - 若门西侧 `(wx - 1)` 为墙体，东侧 `(wx + ex)` 为可通行平地：
      **朝向为东（East-facing），进门矢量为向西（Step West）**；
    - 若门东侧 `(wx + ex)` 为墙体，西侧 `(wx - 1)` 为可通行平地：
      **朝向为西（West-facing），进门矢量为向东（Step East）**。
- **多格目标区域集 (Multi-Tile Goal Area)**：
  - 门不是单一孤立点，而是长度为 $ex$ 的线段或 $ex \times ez$ 的区域；
  - 待命格集（Doorstep Candidates）：
    例如南向 4 宽门，待命格为：
    `{(wx, wz + ez), (wx + 1, wz + ez), (wx + 2, wz + ez), (wx + 3, wz + ez)}`；
  - 寻路优化：
    - A* 规划器以待命格集合为目标集，选取从起点到任一待命格的**曼哈顿/实际代价最小路径**；
    - 若某待命格被 NPC 动态阻挡，自动转移到其余空闲待命格；
    - 抵达待命格后，执行最后 1 步进门动作（沿进门矢量踏入传送格），确保 100% 触发 Warp 切换。

### 3. 立体阶梯导航与 AI 语义可读性 (Vertical Staircase)

- **物理与高程数据**：
  - 双阶楼梯：`X=12 (Step 1, y=8.0) <-> X=11 (Step 2, y=24.0)`；
  - 底端地面：`X=13 (Ground, y=0.0)`；
  - 顶端高台：`X=10 (Overpass, y=32.0)`；
  - 护栏保护：`Z=47` 南侧障碍封死，禁止侧移。
- **寻路规划与连贯执行**：
  - 地面到高台：连续西行 3 步（13 -> 12 -> 11 -> 10），状态由地面平路连续经第 1 阶、第 2 阶登顶；
  - 高台到地面：连续东行 3 步（10 -> 11 -> 12 -> 13）；
  - 载具策略：楼梯瓦片 `blocks_cycling = True`，推荐 OnFoot 跑步（B 键），禁止单车。
- **AI 语义可读性输出结构**：
  - 规划器输出提供专用 `elevation_transit` 块：
    ```json
    {
      "elevation_transit": {
        "staircase_detected": true,
        "total_steps": 2,
        "steps": [
          {"step": 1, "x": 12, "z": 46, "height": 8.0, "role": "lower_step"},
          {"step": 2, "x": 11, "z": 46, "height": 24.0, "role": "upper_step"}
        ],
        "lower_level": {"floor_y": 0, "height": 0.0, "entry_tile": {"x": 13, "z": 46}},
        "upper_level": {"floor_y": 2, "height": 32.0, "entry_tile": {"x": 10, "z": 46}},
        "handrails": "north_south_blocked",
        "recommended_transport": "run"
      }
    }
    ```

### 4. 水面冲浪移动与陆地跃迁 (Surf Navigation)

- **能力判定与状态**：
  - 队伍中存在 Move 57 (Surf)；
  - 角色状态：`ex_state = 2 (Surfing)` 或 `ex_state = 0 (OnFoot)`。
- **下水跃迁 (Mounting Surf)**：
  - 主角站在陆地水岸格（Shoreline Tile），面向水面；
  - 岸边水面高差校验：$|y_{land} - y_{water}| \le 8.0$；
  - 触发下水：按 A 键或调用 `surf_mount`，引擎将角色移动至水面并更新 `ex_state = Surfing`。
- **水面巡逻与移动**：
  - 在水体材质（`kind == "water"` 或 `requires == "surf"`）上网格 A* 导航；
  - 避开水上岩石与障碍物。
- **登陆上岸 (Dismounting Surf)**：
  - 主角在水面上移动到岸边相邻格；
  - 面向陆地可通行格，直行一步；
  - 引擎自动触发角色踏步上岸，状态恢复为 `OnFoot`。

### 5. 飞天技能 (Fly Fast-Travel)

- **ROM 规则判定**：
  - `ZoneHeader.enable_fly_from == True`（仅限室外，室内/洞穴禁用）；
  - 队伍中存在 Move 19 (Fly)；
- **着陆点数据库与提取**：
  - 从各目标城镇 ZoneHeader 中直接提取 `(fly_x, fly_y, fly_z)`；
  - 网格坐标：`target_x = fly_x // 16, target_z = fly_z // 16`；
  - 着陆点位于目标城镇宝可梦中心门外正前方；
- **动作执行链**：
  - 语义指令：`fast_travel_fly(target_zone_id)`；
  - 后端校验前置条件后，执行长途航行并监视着陆帧。

---

## 三、 验收测试方案与验证矩阵 (Acceptance Testing Suite)

| 编号 | 测试用例名称 | 核心验证点 | 预期成果 |
| :--- | :--- | :--- | :--- |
| **TC-01** | `test_catwalk_axis_and_drop` | 细杆子东西主轴通行 + 侧向跳落到地面边缘生成 | 侧向边生成为单向降落边，下层地面合法可达 |
| **TC-02** | `test_multi_width_door_extents` | 1/2/3/4 宽门在 ROM 实体中的识别与矩形解析 | 477 个多格门全部正确解析宽与长 |
| **TC-03** | `test_door_approach_vector` | 门的前进方向（北/南/东/西）与多格待命格集合 | 准确解析出进门矢量和所有可选待命格点 |
| **TC-04** | `test_multi_wide_door_pathfinding` | 4 宽门任意空闲待命格 A* 规划与最优选取 | 自动选择最近空闲格，最后 1 步沿矢量切入 |
| **TC-05** | `test_staircase_bidirectional_plan` | 4 级绝对标高立体楼梯双向上下规划 | 西向上台阶 2 步连贯，东向下台阶 1 步到地面 |
| **TC-06** | `test_staircase_ai_semantics` | 寻路结果中阶梯语义块对 AI 输出的结构化完整度 | 包含 step 序号、标高高差、扶手约束和载具推荐 |
| **TC-07** | `test_surf_shoreline_transitions` | 水岸下水点与高差过滤（dy <= 8.0） | 识别出合法下水点，排除断崖高差下水 |
| **TC-08** | `test_surf_dismount_edge` | 水面到陆地登陆边缘与状态机切回 | 正确规划水面 -> 岸上上岸步进 |
| **TC-09** | `test_fly_capabilities_and_targets` | 飞天权限判定与 ROM 各 Zone 着陆点提取 | 正确提取各城镇 fly_x/fly_z 且室内禁用 |

