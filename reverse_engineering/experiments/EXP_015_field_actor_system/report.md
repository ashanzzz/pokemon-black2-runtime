# TEST REPORT — EXP_015 Field / Player / ActorSystem 闭环

日期：2026-09-03。所有动态结论来自 BizHawk Lua Bridge 的 Main RAM
读取；没有使用截图、OCR、攻略坐标、静态 spawn 位置或 RAM 写入。

## Goal

从真实运行时对象链确认 PlayerActor，而不是从“看起来像坐标”的孤立
地址或某个恰好移动的 NPC 推断玩家；同时复核当前对话的 ScriptWork
parent-actor 候选是否属于同一 ActorHeap。

## Hypothesis

按 SWAN 布局假设，当前链应满足：

```text
GameSystem +0x20 -> Field
Field +0x40 -> FieldActorSystem -> ActorHeap
Field +0x94 -> FieldPlayer +0x04 -> FieldPlayerCore +0x1C -> PlayerActor
PlayerActor +0x88 -> FieldActorSystem
```

字段名称和偏移最初是 SWAN hypothesis；只有同帧指针闭环和受控输入的
行为指纹可以把当前 ROM 的具体对象关系提升。

## Method

- 新增 exact-frame `POST /api/dev/memory_batch_snapshot`，保留 Lua Bridge
  返回的同一帧号，而不是把 HTTP 后读的心跳帧当作采样帧。
- 使用 `actor-chain` 观察窗读取 ScriptWork、GameSystem、Field、
  FieldPlayer、ActorSystem 和 ActorHeap。
- 对 PlayerActor 使用实际输入序列 `Right:8`、`Left:8`、`Up:8`、
  `Down:8`；每一步保存 before/after 的原始 JSON。
- 使用一次 bridge-owned A edge 启动当前 NPC 对话，逐帧验证 ScriptWork
  的存储槽、ActorHeap membership 与活动标志边界。

## Memory ranges

| Runtime observation | Main-RAM range | 说明 |
| --- | ---: | --- |
| ScriptWork context | `0x02247300..0x022478FF` | 活跃分配及其周边；不能把结束后的残留作 live field。 |
| GameSystem candidate | `0x0223B480..0x0223B57F` | 当前会话中的候选窗口。 |
| Field candidate | `0x02263500..0x022635FF` | 由 GameSystem/ScriptWork 双向关系复核。 |
| FieldPlayer candidate | `0x02324740..0x0232483F` | 覆盖 FieldPlayer、Core 与 Grid 子对象。 |
| ActorSystem candidate | `0x0223DB40..0x0223DC3F` | 覆盖当前 manager 头。 |
| ActorHeap candidate | `0x0223DBE4..0x0223E7E3` | 12 个 `0x100` stride 槽的观察窗口。 |

这些是本次会话的观察地址，不是永久 Runtime API 常量。

## Raw observations

### 同帧 Field / Player / ActorHeap 闭环

在 `snapshot_field_player_entry_candidate.json`，bridge frame `4262774`：

```text
GameSystem 0x0223B4C0 +0x20 = 0x02263520
Field      0x02263520 +0x04 = 0x0223B4C0
Field      0x02263520 +0x40 = 0x0223DB68
Field      0x02263520 +0x94 = 0x02324764

FieldPlayer     0x02324764 +0x00 = 0x02263520
FieldPlayer     0x02324764 +0x04 = 0x023247A0
FieldPlayerCore 0x023247A0 +0x04 = 0x0223B4C0
FieldPlayerCore 0x023247A0 +0x08 = 0x02263520
FieldPlayerCore 0x023247A0 +0x1C = 0x0223E4E4

ActorSystem 0x0223DB68 +0x04 = 0x000B0040  (capacity=64, count=11)
ActorSystem 0x0223DB68 +0x1C = 0x0223DBE4
0x0223E4E4 = ActorHeap + 0x900            (slot 9)
Actor      0x0223E4E4 +0x88 = 0x0223DB68
```

该 actor 的 `UID=255`，但 UID 本身不作为玩家判据；身份来自上面的
FieldPlayerCore 指针链和下列真实输入行为。

### 受控行为指纹

| Requested input | Before frame / position | After frame / position | Observation |
| --- | --- | --- | --- |
| `Right:8` | `5066176`, `(39, 1, 763)`, `WPos.X=0x00278000` | `5066223`, `(40, 1, 763)`, `WPos.X=0x00288000` | X 增一格。 |
| `Left:8` | `5067264`, `(40, 1, 763)` | `5067298`, `(39, 1, 763)`, `WPos.X=0x00278000` | 回到 X 原点。 |
| `Up:8` | `5069468`, `(39, 1, 763)`, `WPos.Z=0x02FB8000` | `5069534`, `(39, 1, 762)`, `WPos.Z=0x02FA8000` | Z 减一格。 |
| `Down:8` (第二次) | `5071227`, `(39, 1, 762)` | `5071281`, `(39, 1, 763)`, `WPos.Z=0x02FB8000` | 回到 Z 原点。 |

第一次向下输入在 NPC 前只改变 FaceDir，未改变 GPos/WPos；这是已记录的
碰撞/转向结果，不能被伪造为走格成功。`FaceDir/MotionDir` 的本次映射为
`Up=0`、`Down=1`、`Left=2`、`Right=3`，仅在此受控状态标为 verified。

### 当前对话的 parent-actor 观察

`a_edge_capture_npc_dialogue_start_parent_actor_binding.json` 记录：

```text
before_edge f5072553: script_msg_active=0
after_frame_2 f5072555: script_msg_active=1

active ScriptWork candidate 0x0224758C +0x08 = 0x0223DCE4
0x0223DCE4 = ActorHeap 0x0223DBE4 + 0x100 (slot 1)
slot 1: UID=4, SCRID=13, GPos=(39,1,764), +0x88=0x0223DB68
Player slot 9: GPos=(39,1,763)
```

它是实际按 A 启动的这次 ScriptWork 中的 parent-actor storage target，
并与玩家相邻。但“parent actor 必等于最终屏幕说话者”的语义尚需跨 NPC 和
`0x3C/0x3D` 脚本对照，不能越级发布为 `speaker_actor`。

## SWAN / current-ROM correspondence

- `FieldActorSystem +0x04/+0x1C`、`FieldActor +0x88/+0x3C/+0x44` 与
  SWAN `field_mmodel.h` 一致，且经多个 heap 槽和行为实验形成结构相干性。
- `FieldPlayer +0x04`、`FieldPlayerCore +0x1C` 与 SWAN
  `field_player.h` 一致，并被可控移动的终端 actor 闭环支持。
- IREJ OVL_12 的静态 getter/setter 复核见
  `reverse_engineering/reports/EXP_014_scriptwork_parent_actor_source_binding_20260903.md`：
  `ScriptWork+0x08` 被当前 ROM 的 `ldr` 读取，紧邻 setter 写入同一槽。

## Confidence

| Field / relationship | Status | Reason |
| --- | --- | --- |
| 当前会话 `FieldPlayerCore -> 0x0223E4E4` | **verified** | 同帧 pointer closure 加四方向/往返输入指纹。 |
| 当前会话 `0x0223E4E4` 为 PlayerActor | **verified** | 仅该 closure 终端响应用户方向输入并回到原点。 |
| Player GPos X/Z 与 WPos X/Z 的格中心关系 | **verified for this state** | 四次受控移动的离散与连续值同步。 |
| ActorSystem / ActorHeap 具体 SWAN 成员名称 | probable | 当前结构极相干，尚未对所有状态/生命周期验证。 |
| `ScriptWork+0x08` storage/access behavior | verified current-ROM static behavior | IREJ OVL_12 getter/setter 机器码复核。 |
| 当前对话的 `script_parent_actor` | probable | 活跃帧 target 在 heap 内且与交互位置相邻；还没有跨 NPC / opcode 对照。 |
| 当前对话的最终 `speaker_actor` / NPC 名称 | unresolved | 不从 parent relation、位置或文本内容猜测。 |

## Rejected or failed hypotheses

- `0x0223E3E4` (slot 8) 和 `0x0223E2E4` (slot 7) 曾在单帧方向实验中
  移动，但没有通过同一 FieldPlayerCore 链或 return-to-origin；它们不能称为玩家。
- 先前 `0x0223B6D8 +0x94 = 0x00004A8C` 不在 Main-RAM 指针范围，不能
  当 FieldPlayer。它属于一次错误/非同根候选观察，不否定上面的真实 Field 链。
- `0x0231FCB0` 仍不是已验证的 TextPrinter。

## Files changed

- `tools/runtime_memory_discovery.py`
- `tools/analyze_field_actor_capture.py`
- `backend/black2/api/app.py`（exact-frame snapshot endpoint）
- `reverse_engineering/experiments/EXP_015_field_actor_system/*.json`
- 本报告

## Next recommended experiment

1. 在另一个可交谈 NPC 前重复 active ScriptWork 捕获；要求 `+0x08` 指向
   不同 UID/SCRID/GPos、仍属于 live ActorHeap。
2. 对 `0x3C ActorEx` 与 `0x3D ParentActorMsg` 分别做指令/调用路径观察，
   才能确定最终 message actor 与 parent actor 的覆盖规则。
3. 文本侧先追踪候选 `GFLBitmap` 写入的 ARM9 PC 与 `BmpWin_FlushChar`
   调用链；在此之前屏幕可视文字继续发布为 `unresolved`。
