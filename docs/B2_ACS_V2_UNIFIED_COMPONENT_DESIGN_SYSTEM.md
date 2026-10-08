# B2-ACS // V2 统一原子组件库设计规范 (B2DS)

> **版本**：v2.0 (2026-10-04)  
> **核心原则**：
> 1. **全站严格 0 Emoji**：完全使用等宽文本徽章 `[OK]`、`[RUNNING]`、`[BLOCK]` 等，拒绝任何 Emoji 字符。
> 2. **三权分立与高内聚**：结构 `v2.html`、样式 `v2.css`、逻辑 `v2.js` 彻底分离，严禁非标随意内联样式。
> 3. **统一造型与标准圆角**：
>    - 容器与面板：统一 `--radius-lg: 8px`；
>    - 按钮与输入框：统一 `--radius-md: 6px`；
>    - 状态徽章：统一 `--radius-sm: 4px`。
> 4. **全本地极速加载**：0 外部网络 CDN 依赖，体积仅 35KB，毫秒级秒开。
> 5. **V1 完整保留**：旧版 3D Workbench 完整保留在 `/v1` 作为归档参考。

---

## 一、 标准组件索引表 (B2DS Component Index)

| 组件类名 | 功能与语义 | 状态 / 变体 |
| :--- | :--- | :--- |
| **`ds-panel`** | 统一面板容器 | 包含 `ds-panel-header`, `ds-panel-title`, `ds-panel-body`, `ds-panel-footer` |
| **`ds-btn`** | 统一按钮 | `ds-btn-primary` (蓝), `ds-btn-success` (绿), `ds-btn-danger` (红), `ds-btn-ghost` (幽灵), `ds-btn-sm` (紧凑) |
| **`ds-badge`** | 状态徽章与药丸 | `ds-badge-green` (OK), `ds-badge-cyan` (运行), `ds-badge-amber` (待定), `ds-badge-red` (阻挡) |
| **`ds-input`** | 单行输入框 | 支持 `ds-input-group` 与按钮无缝并排 |
| **`ds-progress`** | 统一血条/进度条 | 高度 6px，包含 `fill-green`, `fill-amber`, `fill-red`, `fill-cyan` |
| **`ds-table`** | 统一数据表格 | 固定表头 32px，数据行 36px，带悬停交互 |
| **`ds-radar-box`** | 2D 纯字符雷达框 | 纯黑底色 + 科技青等宽字符渲染 |
| **`ds-log`** | 统一终端控制台 | 纯黑底色 + 绿色等宽自动滚屏日志 |
| **`ds-timeline`** | 主线里程碑航线步进器 | 包含 `ds-step` (completed / active / pending) |
| **`ds-move-card`** | 对战招式评分卡 | 严格等宽网格，支持 `recommended` 最优招式高亮 |

---

## 二、 五大测试模块功能映射

1. **`[01 OVERVIEW]` 综合态势与主线推进测试**：
   - 10 大主线里程碑航线轨 (`ds-timeline`)
   - WorldGraph 15 步宏观跨区路由规划 (`ds-panel`)
   - 12 大 StoryGates 通行门阻挡审计 (`ds-panel`)
   - 按钮 `[STEP FORWARD]` 与 `[POKECENTER HEAL]` 闭环触发
2. **`[02 RADAR]` 空间网格地图与连续移动寻路测试**：
   - 2D 空间网格雷达实时视图 (`ds-radar-box`)
   - 步态控制器：`[RUN 8f]`, `[BIKE 4f]`, `[WALK 16f]`, `[SURF]`
   - 坐标 A* 寻路测试器 (`ds-input-group`)
3. **`[03 COMBAT]` 战斗状态机与 AI 闭环执行测试**：
   - 敌我出战看板 (Patrat vs Crobat, 实时 HP 血条 `ds-progress`)
   - 4 招式评分网格 (`ds-move-card`)：威力、命中、PP、克制倍率、STAB、期望得分
   - 闭环动作执行按钮：`[EXECUTE MOVE]`, `[THROW POKEBALL]`, `[FLEE / RUN]`
4. **`[04 ROSTER]` 队伍全量健康与背包 5 口袋测试**：
   - 8 徽章掩码药丸 (`0x3F`)、持金 (`$294,722`)、通关状态
   - 110 项背包道具动态检索表格 (`ds-table`)，毫秒级实时搜索过滤
5. **`[05 MEMORY]` 物理内存神谕与底层手柄直通测试**：
   - SaveData Block 52 & Block 0 十六进制寻址真值
   - 虚拟手柄按键直通按钮组：D-Pad、A、B、X、Y、Start、Select，受 `InputLease` 保护
   - 统一终端测试输出流 (`ds-log`)
