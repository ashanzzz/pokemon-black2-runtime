# Pokémon Black 2 分层游戏状态与战斗语义 API v2

## 1. 为什么不能只使用一个 screen_type

黑 2 的实际运行状态不是互斥页面集合。字段层、战斗层、菜单层、文字打印层和加载层可以同时存在。

用户提供的六组 IREJ rev.1 战斗 RAM 样本正好说明了这个问题。旧语义引擎在六组样本中都得到 `DIALOGUE_ACTIVE`，但截图同时明确处于战斗场景，包括主命令、训练家派出宝可梦和战斗结算文字。因此 `DIALOGUE_ACTIVE` 只能说明文字打印/消息层正在工作，不能推出“当前不是战斗”。

v2 使用两组概念。

- `primary_context` 用于首页决定主要工作区，例如 `exploration`、`battle`、`menu`。
- `layers` 保留所有同时成立的状态，例如 `battle + dialogue`、`exploration + dialogue`。

输入归属再单独计算。当前优先顺序为加载过渡、对话、战斗、菜单、探索。这个优先顺序只决定下一次输入交给谁，不会删除低层状态。

## 2. 状态层

### exploration

字段世界可供导航或交互时激活。NPC 对话出现时它仍可保持激活，但 `can_move` 通常为 false，输入由 dialogue 层接管。

### battle

通过战斗 RAM 证据建立。当前版本只实现 IREJ rev.1 的战斗存在候选检测，仍不声称已经验证战斗类型、战斗形式、回合、BattleMon 或命令菜单。

### dialogue

沿用现有 TextPrinter / Window / ScriptWork 解码。它是叠加层，不再被当作唯一场景类型。

### menu

主菜单、背包、队伍等菜单状态。后续可继续拆为更细的菜单层。

### transition

用于 loading、fade、scripted transition 等暂时不能安全执行动作的状态。

## 3. 当前状态接口

### GET /api/v1/game/current

推荐所有 AI Agent 每次决策先读取这个接口。

核心字段示例：

```json
{
  "format": "black2-current-state/v2",
  "primary_context": "battle",
  "active_layers": ["battle", "dialogue"],
  "overlays": ["dialogue"],
  "input": {
    "owner": "dialogue",
    "kind": "advance_dialogue",
    "required": true
  },
  "battle": {
    "active": true,
    "phase": "message",
    "execution_available": false
  },
  "dialogue": {
    "active": true
  }
}
```

### GET /api/v1/game/layers

只返回层、输入归属和主要上下文，适合低 token Agent 或前端状态灯。

旧的 `/api/v1/game/state` 保留兼容，但其中 `screen_type` 不再应被解释为互斥真相。

## 4. IREJ rev.1 战斗存在候选

根据用户提供的 Main RAM 和公开 SWAN 结构定义，六组战斗样本中一致出现以下指针链。

```text
GameData candidate       0x0223B570
GameData + 0x194         -> 0x0221E624  PokeParty candidate
GameData + 0x1B8         -> 0x0224211C  FieldStatus candidate
FieldStatus + 0x11       = 1
PokeParty + 0x00          = 6 capacity
PokeParty + 0x04          = 1 count
```

SWAN 的 `FieldStatusBusyFlag` 定义中 1 是 `FLD_STATUS_BUSY_BATTLE`，2 是 loading，0 是 none。SWAN 的 `GameData` 结构也把 `m_Party`、`m_FieldStatus`、`LastBattleResult` 放在与上面偏移一致的位置。

这是一组很强的 IREJ rev.1 候选证据，但本补丁没有用户提供的成对大地图非战斗 RAM。因此 API 明确返回 `candidate`，`verified=false`。当以后补充大地图、菜单、加载、剧情、战斗设施等控制样本后，再把 locator 升级为 verified。

## 5. 战斗分类设计

不要把所有维度拼成一个超长枚举。分开保存：

- `opponent_kind`: wild / npc_trainer / player / unresolved
- `context`: overworld / story / facility / pwt / subway / institute / link / tutorial / unresolved
- `format`: single / double / triple / rotation / multi / unresolved

当前 IREJ RAM 证据尚不足以填写这些字段，所以统一保持 `unresolved`。

## 6. 战斗 API

### GET /api/v1/battle/state

返回战斗是否存在、分类、phase、两侧战场占位、对话叠加层和证据。

### GET /api/v1/battle/request

这是 AI 决策的核心入口。设计思想类似 request/rqid 协议。它应该最终告诉 Agent 当前哪些 actor 需要动作以及 legal actions。当前 legal-action RAM 还没有验证，所以不会编造 `waiting_for_player=true`。

### GET /api/v1/battle/field

战场双方、天气、地形、当前宝可梦。当前 BattleMon 结构未验证，所以详细内容保持 unknown。

### GET /api/v1/battle/party

当前已能安全展示 PokeParty header 的 capacity/count 候选。Gen V Pokémon 槽位主体是加密/打乱结构，本补丁没有把未校验的解密结果作为产品事实。

### GET /api/v1/battle/moves

为未来四技能卡片准备。计划字段包括 move id、当前 PP、最大 PP、disabled、合法目标，并可与现有 Dex 静态招式资料合并。

### GET /api/v1/battle/items

分类为：

- recovery
- status_restore
- pokeballs
- battle_items

当前 live Bag 的战斗可用筛选和精灵球捕获权限尚未验证，所以返回 `contents_known=false`。

### GET /api/v1/battle/events

预留稳定事件流。当前没有伪造事件，空列表明确表示 unknown 而不是“没有发生事件”。

### GET /api/v1/battle/evidence

直接查看 GameData、FieldStatus、PokeParty 指针、BusyFlag、帧号、置信度和限制。

## 7. 战斗决策协议

### POST /api/v1/battle/decisions

一个 request 可以包含 1 到 3 个 actor 命令，能覆盖单打、双打、三打。

支持协议：

- `use_move`
- `switch`
- `use_item`
- `throw_ball`
- `run`
- `shift`
- `rotate`

示例：

```json
{
  "battle_id": "future-stable-id",
  "request_id": 17,
  "commands": [
    {
      "type": "use_move",
      "actor": "player:0",
      "move_slot": 2,
      "target": "opponent:0"
    }
  ]
}
```

当前所有写入都会返回 409。这不是接口没做好，而是明确的安全门禁。需要先验证命令菜单、目标、request freshness 和执行后的 RAM 状态，之后才允许把语义动作转成 BizHawk 输入。

旧 `/api/v1/battle/actions` 和原来的四类动作继续保留，避免旧客户端失效。

## 8. 推荐的 AI 决策顺序

1. `GET /api/v1/game/current`
2. 查看 `input.owner`
3. 如果是 dialogue，按现有对话 API 处理继续或选择
4. 如果是 battle，读取 `/battle/request`
5. 读取需要的 `/battle/moves`、`/battle/items`、`/battle/party`
6. 生成 `/battle/decisions`
7. 执行层验证 request_id、菜单、目标和动作结果
8. 读取新的 `/game/current` 确认状态变化

AI 不需要知道 DS 菜单需要按几次方向键。菜单导航属于 Battle Semantic Executor 的实现细节。

## 9. 参考逆向资料

本版本仅把公开项目当作结构和协议参考，不把其他 ROM 版本的地址直接复制到 IREJ。

- ds-pokemon-hacking/swan
  - `system/game_data.h`
  - `field/field_status.h`
  - `pml/poke_party.h`
  - `pml/poke_data.h`
- Pokémon Showdown battle request / rqid 协议
- PKHeX Gen IV/V Pokémon 数据加密和 block shuffle 实现
- 其他工具中出现的 Black 2 US battle pointers 仅作为研究线索，绝不直接当成 IREJ 地址

## 10. 下一批最有价值的 RAM 样本

要把当前 candidate 升级为 verified，优先采：

1. 普通大地图自由移动
2. 大地图 NPC 对话
3. X 菜单
4. Bag 菜单
5. 战斗进入 transition
6. 野生单打 command screen
7. 训练家单打 command screen
8. 双打 command screen
9. 选招式页面
10. 选择目标页面
11. 选择宝可梦页面
12. 选择战斗道具和精灵球页面
13. 逃跑成功和失败
14. 捕获成功和失败
15. 胜利、失败、经验结算和升级

这些样本可以继续做差分定位，然后才开放真实 battle executor。
