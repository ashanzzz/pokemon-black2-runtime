# Pokémon Black 2 Web 前端 V2 架构设计与全功能测试规范 (V22)

> **版本**：v22.0 · 2026-10-04  
> **核心规范**：
> 1. **拒绝所有 Emoji**：全站移除任何 Emoji 表情字符，采用科技感等宽文本徽章 `[OK]`、`[RUNNING]`、`[BLOCK]`、`[RADAR]` 等。
> 2. **保留 V1 版本 Web**：旧版 `workbench.html`（3D 逆向工作台）与 `home.html` 完整保留作为参考存档（通过 `/v1` 或 `/workbench` 访问）。
> 3. **极速运行**：V2 版纯原生 HTML5/CSS3/Vanilla JS，零外部网络 CDN 依赖，体积仅 35KB，毫秒级即开即用。
> 4. **服务于所有功能测试**：全面打通主线自驱、空间地图、连续移动、战斗决策、队伍背包、底层 RAM 与通关验证。

---

## 一、 快速访问入口路由

- **V2 全功能控制台（全新默认）**：
  - `http://localhost:8765/`
  - `http://localhost:8765/v2`
  - `http://localhost:8765/console`
  - `http://localhost:8765/frontend/v2.html`
- **V1 逆向工作台（保留归档参考）**：
  - `http://localhost:8765/v1`
  - `http://localhost:8765/workbench`
  - `http://localhost:8765/frontend/workbench.html`

---

## 二、 五大核心测试工作区设计

### 1. [01. OVERVIEW] 综合态势与主线推进测试 (StoryRunner & StoryGates)
- **10 大主线里程碑航线轨**：
  - 线性状态卡：`[OK] M1 牧场`、`[OK] M2 基础徽章` ... `[OK] M7 飞翼徽章`、`[RUNNING] M8 冰冻徽章`、`[PENDING] M9 海浪徽章`、`[PENDING] M10 联盟冠军`。
- **WorldGraph 15 步宏观路径规划**：
  - 起点 Zone 456 (立涌海湾) -> 码头 -> 轮渡 (Ferry) -> 飞云市 -> ... -> 终点 Zone 121 (双龙道馆·夏卡)。
  - 显示总步数、中间连接器种类及全线连通性 (`Traversable: true`)。
- **12 大剧情门实时检测 (StoryGates Monitor)**：
  - 自动评估 12 个关键道闸的通行权限（例如反转山脉关卡放行，海底隧道与海边洞穴因缺少徽章阻挡）。
- **一键测试按钮**：
  - `[STEP FORWARD (/story/step)]`：自动驱动主角推进到下一步；
  - `[POKECENTER HEAL (NURSE 2100)]`：调用宝可梦中心护士恢复并差分校验全员满血满 PP。

### 2. [02. RADAR] 空间网格地图与连续移动寻路测试 (Radar & Navigation)
- **2D 网格雷达实时视图**：
  - 纯字符高精度显示 9x9 空间网格：`P` 主角 (GPos 221, 684)、`.` 可通行地面、`#` 墙体、`*` 草地与水岸过渡。
- **连续步态与载具控制器 (Locomotion Controller)**：
  - `[RUN (HOLD B, 8F/TILE)]`：连续跑步模式（同向直线批量输入，零 sleep 帧推进校验）；
  - `[BIKE (KEY Y, 4F/TILE)]`：自行车极速骑行；
  - `[WALK (16F/TILE)]`：常规步行；
  - `[SURF]`：水面冲浪模式；
  - 动态避障：前瞻 3 格感知 NPC 巡逻并进行局部 A* 重规划。
- **坐标寻路测试器**：
  - 支持输入任意目标坐标，一键生成 A* 最优路径并执行。

### 3. [03. COMBAT] 战斗状态机与 AI 闭环执行测试 (Combat & Move Execution)
- **实时对阵双方数据看板**：
  - 敌我出战宝可梦物种名称、等级、实时 HP/MaxHP 数值、百分比血条、能力阶级。
- **Gen 5 四招式智能评分与相克矩阵**：
  - 四宫格展示已习得技能：威力、命中率、实时剩余 PP。
  - 属性相克乘数（0x~4x）、1.5x STAB 本系加成、期望得分计算，推荐最优出招。
- **闭环动作执行测试**：
  - `[EXECUTE RECOMMENDED MOVE]`：调用 `/api/v1/battle/decisions`，执行招式并回读校验 PP 扣减与对手扣血；
  - `[THROW POKEBALL]`：自动评估捕获率，翻包投球并校验队伍数量变化；
  - `[FLEE / RUN]`：野怪一键撤退脱战。

### 4. [04. ROSTER] 队伍健康与 5 口袋背包测试 (Party & 5-Pocket Inventory)
- **全队 6 宝可梦完整状态卡**：
  - 实时 HP 血条、异常状态、携带道具、4 招式 PP 消耗度。
- **5 大口袋背包库存检索**：
  - 道具 (55)、回复药 (18)、重要道具 (7，含自行车 450)、技能机器 (24)、树果 (6)。
  - 包含实时搜索过滤框。
- **徽章与通关荣誉看板**：
  - 8 大道馆徽章掩码实时解析（当前 0x3F，6/8 枚徽章点亮）；
  - 持有资金计数器（$294,722）；
  - 登入名人堂次数与通关状态。

### 5. [05. MEMORY] 物理内存神谕与底层手柄测试 (Memory Oracle & Controller)
- **ARM9 物理内存真值解码**：
  - 实时解析 `GameData (0x0223B570)`、`Block 52 (0x02226924)`、`Block 0 (0x02205824)`。
- **InputLease 写入锁监控**：
  - 实时显示锁持有者、排队任务数，保障单写入线程安全。
- **虚拟手柄直通测试**：
  - 支持 D-Pad 上下左右、A、B、X、Y、Start、Select 按键直通测试。
