# Pokémon Black 2 逆向解析完整性增强计划 V19

更新时间：2026-10-03

## 目标

把“能够让 AI 做决定”的信息分成四层，并保持证据边界：

1. **ROM 静态层**：地图、Zone/Area/Matrix、碰撞、高度、Warp、Trigger、NPC/Furniture/Item、脚本候选。
2. **RAM 动态层**：玩家 GPos/WPos/朝向/运动阶段、FieldActor、ActorSystem、战斗/菜单/对话状态。
3. **语义层**：NPC 姓名/角色/服务、可操作部件、巨石/坑洞/门/标牌/电脑/商店、训练家视线与剧情阻挡。
4. **证据层**：当前帧/session、来源、置信度、运行时验证、未解析原因；未知字段保持 `null`，禁止截图猜测。

## 当前已落地

### 区域与地图

- ROM `a/0/0/2[109]` 地点名表解析。
- ZoneHeader `location_name_id` 与 `parent_zone_id` 合并显示。
- 自动标注：室外、室内、洞穴、宝可梦中心、通道大门。
- 新模块：`backend/black2/world/location_catalog.py`。
- 地图图谱与 AI 地图返回 `zone_label`，不再只显示 `Zone N`。

### NPC 与动态角色

- NPC 保留 `record_index / script_id / flag_id / sprite_id / movement_id / sight_raw / facing`。
- 已知剧情角色使用证据注册表显示阿戴克、登山大叔等名称和角色。
- 普通居民如果没有 ROM 名称来源，明确返回 `name_status=unresolved_rom_name`，不伪造姓名。
- `sight_raw > 0` 显示 `NPC_TRAINER_CANDIDATE`，但不把候选提升为确定战斗。
- 新的完整场景接口会把 live ActorSystem 行与 ROM NPC 身份按 script/model/flag 证据合并。

### 可操作部件

- 交互候选统一输出：`action / role / script_id / status / can_execute / input / requires`。
- 已识别角色：标识牌、垃圾桶、宝可梦电脑、商店柜台候选、隐藏道具候选、Warp、NPC、Trigger。
- 交互仍要求运行时位置、碰撞/接近条件、脚本和旗标证据。

### 怪力巨石和地图机关

- `[G]`：未推动的怪力巨石，阻挡通路，需要 HM04 Strength。
- `[U]`：未填平的巨石坑洞，不可通行。
- `[=]`：实时 RAM 发现巨石已经沉入坑洞，2x2 坑洞转为可通行路面。
- 运行时通过 ActorSystem 的巨石模型与高度层判断“已推动/未推动”，而不是通过截图。

### 完整 AI 场景接口

新增：

```text
GET /api/v1/ai/scene/complete?zone_id=&radius=6&include_raw=false
```

返回：

- 官方区域名与环境；
- 玩家缓存、屏幕/战斗/对话语义；
- 地图、碰撞、Warp/门；
- ROM 静态 NPC/道具/障碍；
- 可选 live ActorSystem 位置；
- NPC 名称和角色绑定证据；
- 家具、标牌、电脑、门、Trigger 的操作候选；
- Zone 脚本和 TrainerBattle 候选；
- 每类数据的来源、置信度和限制。

## 仍待继续增强的部分

1. **普通 NPC 真名**：需要把脚本函数、文本索引、speaker 变量和实体记录做跨文件绑定；没有唯一绑定时仍返回 unresolved。
2. **道具实际名称**：需要把 Item Ball 脚本参数、Flag、Item ID 与 `a/0/0/2` 道具消息表做差分验证。
3. **道馆结构化语义**：需要把道馆 Zone、TrainerBattle 候选、训练家巡逻路线、战后旗标、奖励物品合并为 GymGraph。
4. **玩家可操作能力**：继续从 FieldSystem/Bag/Party/Flag 解码自行车、冲浪、怪力、碎石、闪光、攀瀑等能力，并加入当前场景合法性检查。
5. **动态地图机关**：为门、隐藏道具、剧情 Trigger、巨石、树、NPC 道闸建立 before/after RAM 观察记录。
6. **玩家操作闭环**：动作请求统一绑定 `wait_id + session_id + source_frame`，执行后回读位置/朝向/移动阶段/目标状态。

## 参考项目落地边界

- BizHawk：Memory Domain、Frame Control、Savestate、External Tool 作为模拟器控制基线。
- SwissArmyKnife / CTRMap-CE / ndspy：NARC、Zone、Matrix、事件实体、脚本、Warp、地图资源。
- swan / NTRGhidra：FieldSystem、PlayerActor、TextPrinter、ActorSystem、Overlay 和 ARM9 结构对齐。
- PKHeX：队伍/宝可梦/技能/道具数据模型和校验规则。
- bizhawk-mcp-native / PokéBot：批量内存读取、LLM→Tool→Emulator、确定性输入和验证循环。

这些项目用于结构和差分验证；本项目仍坚持截图只做人工调试证据，不能作为状态机输入。
