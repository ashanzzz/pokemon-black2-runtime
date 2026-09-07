# EXP-021 IREJ rev.1 战斗 FieldStatus 候选与状态叠加

日期：2026-09-07

## 输入证据

用户提供 6 个 battle bundle。物理 Main RAM 均为 4 MiB。

| Bundle | 标签 | 物理帧 | 旧 Semantic screen_type |
|---|---|---:|---|
| b1 | 战斗中 | 5,977,495 | DIALOGUE_ACTIVE |
| b2 | 战斗中3.5 | 5,998,441 | DIALOGUE_ACTIVE |
| b3 | 战斗中3 | 5,997,150 | DIALOGUE_ACTIVE |
| b4 | 战斗中2.5 | 5,994,492 | DIALOGUE_ACTIVE |
| b5 | 战斗中 | 5,977,495 | DIALOGUE_ACTIVE |
| b6 | 战斗中2 | 5,993,438 | DIALOGUE_ACTIVE |

b5 与 b1 是同一物理帧的重复样本。

截图覆盖主战斗命令、训练家派出宝可梦、对手倒下等阶段。旧单一 screen_type 因文字打印活动统一分类为 DIALOGUE_ACTIVE，证明“对话”和“战斗”必须独立表示。

## GameData 扫描结果

依据 SWAN `GameData` 的结构关系，对 Main RAM 做结构一致性扫描。

六组样本都得到同一候选：

```text
GameData = 0x0223B570
GameData + 0x194 = 0x0221E624
GameData + 0x1B8 = 0x0224211C
```

第二个指针目标满足：

```text
PokeParty + 0x00 = 6
PokeParty + 0x04 = 1
```

第一个 `FieldStatus` 目标满足：

```text
FieldStatus + 0x11 = 1
```

SWAN 定义：

```text
FLD_STATUS_BUSY_NONE    = 0
FLD_STATUS_BUSY_BATTLE  = 1
FLD_STATUS_BUSY_LOADING = 2
```

因此当前实现把 `BusyFlag=1` 解释为 `battle.active=true` 的高置信度候选。

## 为什么不标 verified

当前证据集合只包含战斗样本，没有同版本 IREJ rev.1 的自由探索、菜单、加载等负样本。虽然结构关系与公开逆向高度一致，也不能证明该定位在所有状态下都不会误判。

产品 API 保持：

```text
active_status = candidate
verified = false
confidence = 0.95  (BusyFlag=1 且整条结构链通过时)
```

`BusyFlag=0` 只能暂时返回 candidate negative，不能升级为 verified inactive。

## 另外一个重要发现

`GameData + 0x1BC` 的 `LastBattleResult` 在不同战斗中出现 0 和 1，但它是“最近一次/过渡结果”字段，不应当被解释成当前战斗 outcome。API 只暴露 raw 值并附带 historical/transition 警告。

## 实现结果

- 新增 `backend/black2/decoders/battle_runtime.py`
- 新增 `backend/black2/runtime/layered_status.py`
- 新增 `/api/v1/game/current`
- 新增 `/api/v1/game/layers`
- 扩展完整 battle read API
- 新增 `/api/v1/battle/decisions`
- 所有 battle writes 继续 evidence-gated
- 新首页同时展示探索、战斗、对话、菜单、过渡层
