# Pokémon Black 2 自动通关 Agent 实现与逆向指导计划 V20

日期：2026-10-03

## 一、最终目标

把当前“RAM 状态读取 + 局部 walk/run/bike/surf 导航”升级为可恢复、可审计、可验证的自动通关 Agent：读取剧情、徽章、Flag、队伍、背包和地图；跨 Zone/Matrix、门、洞窟、船、飞行和剧情传送；处理 NPC、道馆机关、HM、对话和战斗；完成联盟；验证 Hall of Fame、THE END 和最终存档。

截图只做人工证据，不能作为核心决策输入。所有动作必须遵循：读取状态 → 检查输入所有权和 wait_id → 最小动作 → InputLease 写入 → 回读 RAM/事件 → 成功、重规划或安全停止。

## 二、当前边界

已具备：玩家 Zone/GPos/WPos/朝向/交通模式、队伍、背包、局部陆地和水路寻路、NPC 占位、对话等待边界、事件流、证据包、Zone 443 护士连接器。

仍缺：剧情 Flag/徽章/训练家击败状态、跨 Matrix 导航、通用剧情连接器、道馆机关、PC/商店/关键道具流程、战斗写入闭环、联盟战、Hall of Fame/最终存档检测。

本轮修复了 D:\SynologyDrive团队\antigravity\宝可梦v2\backend\black2\api\player_routes.py 的数据形状问题：PlayerRuntime/v3 的顶层 locomotion 现在会正确驱动 player/capabilities。当前工作树全套测试为 522 passed、6 skipped、1 warning；M0 合同修复已经完成。

## 当前执行进度

- M0：已完成。runtime/capabilities、Agent inventory observation、battle 测试隔离和 API 合同基线已统一。
- M2：已全部达成。成功逆向 SaveData Block 52，实机解码出 6 枚徽章（mask=0x3F）与金钱（$294,722），全合众 12 大主线剧情门（StoryGate）权威评估器完成落地并在 live 接口中验证；目标系统自动对齐至 M8 双龙道馆夏卡。
- M3：已达成。新增 WorldGraph 宏观图谱（覆盖全合众 615 个 Zone、1089 个 Warp、100+ 缝隙 Matrix 边界及轮渡/航班），双通道 A* 路由与 live 接口 `/api/v1/navigation/global/route` 已实机验证通过！
- M5：战斗决策规划器（BattlePlanner）与状态机（BattleStateMachine）已落地。集成 Gen 5 完整属性相克矩阵（17 种属性、0x~4x 克制/无效、1.5x STAB、命中加权），自动评估 4 招式得分与最优动作；新增 live 接口 `/api/v1/battle/decisions`！

## 三、总体架构

统一权威状态：

RuntimeSnapshot → LayeredGameState、PlayerRuntime、WorldState、ProgressionState、PartyState、InventoryState、BattleState、DialogueState、CompletionState。

所有状态必须带 session_id、source_frame、state_revision、confidence。状态等级统一为 verified、probable、candidate、unresolved。unresolved 不能授权写入。

新增核心领域对象：

- ProgressionState：徽章、剧情 Flag、关键道具、HM 权限、训练家击败状态、当前脚本节点、剧情门。
- WorldConnector：Warp、门、楼梯、电梯、船、飞行、剧情传送及其前置条件和落点验证。
- BattleState：战斗类型、格式、阶段、request_id、光标、合法动作、结算。
- CompletionState：冠军胜利、殿堂、Credits、最终存档。

## 四、实施阶段

### M0：基线和合同

固定 git 工作树、ROM hash、session/frame、pytest 结果和当前 evidence。修复 runtime/capabilities 不一致、game capability 陈旧标记、navigation 跨 Zone 描述和 battle 409/503 合同。新增状态一致性测试，要求 runtime、agent/observe、player/capabilities 的 Zone、坐标、交通模式和 session 一致。

### M1：统一运行时

所有 API 从 RuntimeHub.snapshot 派生。所有写入携带 expected_session_id、expected_state_revision、expected_wait_id、correlation_id。状态变化后返回 STALE_STATE。能力计算分 owned、available、startable、legal_now 四层。

### M2：剧情和存档逆向

新增 backend/black2/progression：flag_catalog、flag_decoder、story_state、story_gate、save_block_decoder、progression_evidence。新增 probe_story_flag_diff、probe_badge_diff、probe_trainer_defeat、probe_story_transition。

实验必须是单动作差分：加载测试存档 → bounded RAM 前采样 → 只执行一个动作 → 等待稳定边界 → 后采样 → 求 bit diff → 重新进入场景验证 → 第二次复现 → 注册 verified。研究顺序为徽章、训练家战后 Flag、门/NPC Flag、关键道具、脚本节点、等离子队事件、殿堂和通关 Flag。

新增 API：/api/v1/progression/state、/flags、/gates、/objective、/completion。所有返回必须包含 source、confidence、frame、session_id、unknown_reason。

### M3：世界连接图

新增 WorldGraph，统一 ZoneNode、MatrixNode、SurfaceLayer、WalkEdge、WarpEdge、ScriptConnector、TransportConnector、StoryGateEdge。连接器必须验证 approach tile、朝向、前置条件、输入、transition、目标 Zone/GPos。导航任务升级为 local → connector → local 的分段任务，每段独立失败和恢复。

必须覆盖普通门、通道、洞窟、楼梯、跳台、电梯、传送带、船、飞行、剧情传送、室内外过渡和道馆入口。

### M4：交互和剧情动作

统一 InteractionContract：kind、target、stand_tile、facing、preconditions、action、expected_effect、verification。分别验证 Cut、Strength、Surf、Fly、Waterfall、Dive、Flash。补齐护士 HP/异常/PP 前后差分、PC 菜单和光标、商店商品/金钱/背包变化。

### M5：战斗只读状态机

先不写战斗动作，完整读取 battle presence、kind、format、phase、request_id、active slots、HP、status、moves、PP、cursor、legal actions、message printer、settlement。覆盖野战、训练家、道馆、双打、特殊战、捕捉、战败、胜利和逃跑。目标是完整重放一场战斗生命周期。

### M6：战斗执行

动作合同必须验证当前战斗、request_id、phase、actor、slot、PP、目标和 InputLease。动作顺序为 run → 单打 use_move → switch → use_item → throw_ball → 训练家单打 → 道馆和特殊战。每次动作都必须验证 request/phase、HP、PP、状态、队伍或战斗结束变化。

### M7：道馆和主线

新增 story_runner、gym_graph、story_actions、progression_planner。StoryRunner 只能调用导航、交互、对话、战斗、恢复和连接器服务，不直接盲按。状态机为 OBSERVE → RESOLVE_PROGRESSION → SELECT_MILESTONE → RESOLVE_GATE → PLAN_ROUTE → EXECUTE → HANDLE_DIALOGUE/BATTLE → VERIFY_FLAG_OR_REWARD → UPDATE_PROGRESS → CHECK_RECOVERY。

道馆必须验证入口、机关、训练家战、馆主战、徽章/Flag、奖励、对话和出馆后的新门；不能只以馆主对话结束为完成。

### M8：联盟和通关

逆向四天王顺序、联盟房间、冠军战、战后对话、殿堂登记、Credits 和最终存档。completion API 必须区分战斗胜利、进入殿堂、Credits、最终保存。只有 champion_defeated、hall_of_fame_registered、save_completed 都 verified 才返回 completed。

## 五、Evidence Bundle

每个剧情、战斗、导航实验都保存 manifest、before/after runtime、game state、progression、events、bounded RAM、diff、截图和结论。manifest 必须带 case_id、ROM hash、session_id、before/after frame、writes_performed、action_contract、status。截图只做人工复核。

## 六、文件计划

新增：

backend/black2/domain/game_state.py
backend/black2/domain/progression_state.py
backend/black2/domain/story_gate.py
backend/black2/domain/world_connector.py
backend/black2/domain/battle_state.py
backend/black2/domain/completion_state.py
backend/black2/progression/flag_decoder.py
backend/black2/progression/save_block_decoder.py
backend/black2/progression/story_state.py
backend/black2/world/world_graph.py
backend/black2/world/connector_service.py
backend/black2/world/gym_graph.py
backend/black2/world/story_runner.py
backend/black2/battle/battle_state_machine.py
backend/black2/battle/battle_action_service.py
backend/black2/api/progression_routes.py
backend/black2/api/connector_routes.py
backend/black2/api/completion_routes.py

测试：

tests/test_authoritative_state_consistency.py
tests/test_progression_state.py
tests/test_story_flag_diff.py
tests/test_story_gates.py
tests/test_world_connectors.py
tests/test_cross_matrix_navigation.py
tests/test_battle_state_machine.py
tests/test_battle_action_execution.py
tests/test_gym_graph.py
tests/test_story_runner.py
tests/test_completion_state.py

## 七、执行顺序

1. 固定工作树并修复 3 个 battle 测试失败。
2. 统一 runtime/capability/game capability 合同。
3. 建立剧情 Flag 差分框架，先验证徽章和训练家战后 Flag。
4. 建立 StoryGate 和 WorldConnector。
5. 完成跨 Matrix 分段导航。
6. 完成 battle read-only state machine。
7. 开放单打战斗动作。
8. 实现 GymGraph 和 StoryRunner。
9. 扩展 PC、商店、HM、剧情交互。
10. 实现联盟和最终完成检测。
11. 做长时通关和故障恢复测试。

每一步都必须有代码、测试、实机 case、证据包、失败恢复策略和文档。只有全套测试通过、剧情来自已验证 Flag、战斗可闭环、跨区域可恢复、殿堂和最终存档可检测，才可以称为自动通关 Agent。






