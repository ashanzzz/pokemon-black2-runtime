# TEST REPORT — EXP_014 ScriptWork / ParentActor source binding

日期：2026-09-03。类型：SWAN / CTRMapV 源码符号复核，结合已保存的同帧 RAM 证据。未修改业务代码、ROM、RAM 或模拟器设置。

## Goal

回答一个比“附近有个像 NPC 的地址”严格得多的问题：当前对话的 `ScriptWork` 是否保留了一个能够解析为 Runtime `FieldActor` 的 parent actor，以及在当前 `IREJ rev.1` ROM 中哪些结论可以安全发布。

## Version boundary — 必须先声明

当前 ROM 是 `IREJ rev.1`，SHA-1 为 `8DB71663502BBF3B43AC3C9052EC390C390BE62F`。SWAN 的 `IRDO.yml` 是另一份参考版符号库；SWAN 自己的 README 明确说明 `IRDO.yml` 与 `IREO.yml` 的地址不能跨游戏互用，因为内存布局不同。

因此：

- `IRDO.yml` 的地址（例如 `0x02153F21`）**绝不是**当前 IREJ ROM 的可直接调用/断点地址。
- SWAN C 结构成员偏移在本文统一标为 **SWAN hypothesis**；它们只有在当前 RAM 中形成闭环后，才能成为当前 ROM 的 `candidate` 或 `probable` 证据。
- 当前结果没有把 `0x0224758C`、`0x0223DCE4` 写进 Runtime API；它们是本次会话的已观察对象地址，下一次分配可变。

来源：[SWAN README](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/README.md#L6)、[IRDO.yml](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/IRDO.yml)。

## Current IREJ overlay proof — getter / setter behaviour

这次没有把 IRDO 地址直接套到 IREJ。相反，针对本地已记录 SHA-1 的 IREJ ROM，按 NDS header 的 ARM9 overlay table 读取 **overlay 12**：

```text
overlay id              12
RAM load address        0x0214FDA0
decompressed RAM size   0x0001E220
ROM FAT file id         12
ROM file range           0x000A9C00..0x000C0EE4
compressed footer        0x080172E4, expansion 0x00006F3C
```

对该 ROM 文件执行 Nintendo backward-LZ（BLZ）解压得到 `0x1E220` bytes。解压产物开头 16 bytes 与当前 Main RAM `0x0214FDA0` 的代码字节逐字节相同（`70 B5 05 1C 00 24 D0 F6 03 F8 00 28 0D DD 32 26`），因此下面是**当前 IREJ OVL_12 的静态机器码**，不是假定的参考版反汇编。

在 IRDO 的 `ScriptWork_*` getter 群中，当前 IREJ 对应代码块整体相差 `-0x668`；该差值连续覆盖 `GetEvent`、`GetGameSystem`、`GetFieldWork`、`GetSubwork`、`GetWordSet`、两个 StrBuf getter、`GetSEBitMask`、`GetSCRID`、`GetParentActor` 与 `SetParentActor` 的顺序和边界。关键指令为：

```text
0x021538B4: 88 80 70 47  ldrh r0, [r0, #0x04] ; bx lr
0x021538B8: 80 68 70 47  ldr  r0, [r0, #0x08] ; bx lr
0x021538BC: 38 B5 0D 1C  push ... ; mov r5, r1
0x021538C0: 85 60        str  r5, [r0, #0x08]
```

| SWAN IRDO Thumb entry | Current IREJ Thumb entry | Delta | Reference name | Current instruction shape |
| ---: | ---: | ---: | --- | --- |
| `0x02153EE1` | `0x02153879` | `-0x668` | `ScriptWork_GetEvent` | `ldr r0,[r0,#0x14]; bx lr` |
| `0x02153EE5` | `0x0215387D` | `-0x668` | `ScriptWork_GetGameSystem` | `ldr r0,[r0,#0x10]; bx lr` |
| `0x02153EE9` | `0x02153881` | `-0x668` | `ScriptWork_GetFieldWork` | non-trivial helper; same function slot/order |
| `0x02153EFD` | `0x02153895` | `-0x668` | `ScriptWork_GetSubwork` | indexed load from `+0x148` |
| `0x02153F05` | `0x0215389D` | `-0x668` | `ScriptWork_GetWordSet` | `ldr r0,[r0,#0x2C]; bx lr` |
| `0x02153F09` | `0x021538A1` | `-0x668` | `ScriptWork_GetMainStrBuf` | `ldr r0,[r0,#0x30]; bx lr` |
| `0x02153F0D` | `0x021538A5` | `-0x668` | `ScriptWork_GetAltStrBuf` | `ldr r0,[r0,#0x34]; bx lr` |
| `0x02153F19` | `0x021538B1` | `-0x668` | `ScriptWork_GetSEBitMask` | returns address `work+0x40` |
| `0x02153F1D` | `0x021538B5` | `-0x668` | `ScriptWork_GetSCRID` | `ldrh r0,[r0,#0x04]; bx lr` |
| `0x02153F21` | `0x021538B9` | `-0x668` | `ScriptWork_GetParentActor` | `ldr r0,[r0,#0x08]; bx lr` |
| `0x02153F25` | `0x021538BD` | `-0x668` | `ScriptWork_SetParentActor` | starts by storing its input to `work+0x08` |

这个表只记录为本 ROM SHA-1 复核过的静态研究结果；它不是“任何 IREJ/汉化版都可加 `-0x668`”的规则。每个新 ROM hash 都必须重新解析 overlay table、解压并比对这一完整 getter cluster。

IRDO 的同一相邻三项正是 `ScriptWork_GetSCRID`、`ScriptWork_GetParentActor`、`ScriptWork_SetParentActor`。因此可分别作出两个层级不同的结论：

- **verified field behaviour**：当前 IREJ 的这一对象族确实通过函数读取 `u16 [work+0x04]`，读取 `u32 [work+0x08]`，并由紧邻 setter 写入 `u32 [work+0x08]`。
- **probable-to-verified field name**：以同一源码函数群的连续符号/固定相对位置为依据，`+0x04` 是 `SCRID`，`+0x08` 是 `ParentActor` 的证据很强；但尚未在单步/PC trace 中捕获 `s003D` 的实际 call，因此 API 仍应把“此消息的 speaker”保留为 `probable`，而不是由字段名自动升级。

这也修正了较早的字段猜测：当前静态 getter 显示 `GetEvent` 读取 `+0x14`、`GetGameSystem` 读取 `+0x10`、`GetWordSet` 读取 `+0x2C`、`GetMainStrBuf` / `GetAltStrBuf` 读取 `+0x30` / `+0x34`；不能再把 `+0x18` 叫作 WordSet。

## Source-level path

CTRMapV 的 B2W2 SDK `src/event/dialogs/Message.h` 给出脚本命令的公开语义：

| Opcode | SDK 名称 | Actor 来源 |
| --- | --- | --- |
| `0x3C` | `Message.ActorEx(textFile, msgId, actorId, pos, type)` | 显式传入 `actorId` |
| `0x3D` | `Message.Actor(textFile, msgId, pos, type)` | 自动决定 actor |
| `0x48` / `0x49` | gendered / versioned actor message | 显式传入 `actorId` |

同一套 SWAN IRDO 参考符号中存在以下名字及顺序：

| IRDO reference symbol | Reference address | Segment | 能说明什么 | 不能说明什么 |
| --- | ---: | --- | --- | --- |
| `ScriptWork_GetParentActor` | `0x02153F21` | OVL_12 | ScriptWork 明确具有“parent actor”抽象 | 返回的是指针、UID、句柄还是别的类型；当前 IREJ 的地址 |
| `ScriptWork_SetParentActor` | `0x02153F25` | OVL_12 | parent actor 会被写入 ScriptWork | 字段偏移 |
| `FieldScriptEnv_GetScriptWork` | `0x021551C5` | OVL_12 | 脚本环境可取得 ScriptWork | 当前对象根链 |
| `s003C_ActorMsg` | `0x021A8F65` | OVL_36 | 与 `0x3C ActorEx` 对应的消息路径 | 当前 IREJ 的实际实现 |
| `s003D_ParentActorMsg` | `0x021A8FB1` | OVL_36 | 与 `0x3D Actor` 对应、名称明确含 ParentActor 的路径 | 其读取的当前 ROM 偏移 |

故正确的源码级模型是：

```text
0x3C ActorEx:  script operand actorId ──> ActorSystem lookup ──> FieldActor
0x3D Actor:    ScriptWork parent actor ──> ActorSystem / FieldActor
```

这是 `0x3D` 的 **source-level hypothesis**，不是对当前 IREJ 指令流的已验证反编译。SWAN 仓库当前没有 `ScriptWork` 的 C struct/header，也没有 `s003D_ParentActorMsg` 的函数体；所以公开资料不足以单独给出一个可直接使用的 ScriptWork 成员偏移。

来源：[CTRMapV B2W2 SDK archive](https://github.com/ds-pokemon-hacking/CTRMapV/blob/3c2778095867f3007ad48d2c268feb0331d43d70/src/ctrmap/resources/scripting/cm_ide/sdk/EV_GEN_V/SDK5-B2W2-Generated.lib) 内 `src/event/dialogs/Message.h`；[SWAN IRDO OVL_12 symbols](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/IRDO.yml)；[SWAN IRDO OVL_36 symbols](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/IRDO.yml)。

## Current-ROM raw evidence

原始 artifact：`reverse_engineering/experiments/EXP_014_scriptwork_actor_binding/a_edge_capture_overlap_to_dialogue_end.json`。

- 采集方法：Lua bridge 在 BizHawk 帧循环内执行一次 A edge，并保存 edge 前及其后 64 帧；不是拼接 HTTP 读。
- 关键活动帧：`3584641` (`before_edge`)。
- 当最后一次 A 结束对话时，`3584656` (`after_frame_15`) 中该分配块的活动字段开始清零/释放标记改变。因此不能把释放后的残留字节继续当 ScriptWork 成员。

`0x02247560` 前部含当前 debug allocation 标签 `script_work.c`；第一个能与下面所有关系一致的候选 payload 起点为 `0x0224758C`。allocator header 的内部格式尚未验证，所以“payload 起点”本身仍是 `candidate`，不是 ABI 声明。

### ScriptWork candidate at active frame 3584641

| Current address | Relative to `0x0224758C` | Raw value | 安全解释 | Confidence |
| --- | ---: | ---: | --- | --- |
| `0x0224758C` | `+0x00` | `0x0003643F` | event / command-related scalar；在结束后变 `0`，不可命名为指针 | candidate |
| `0x02247590` | `+0x04` | `0x0000000D` | 当前 IREJ 的 direct getter 读 `u16 [work+4]`；值与下方 Actor 的 `SCRID=13` 相同 | **verified accessor; probable SCRID semantic** |
| `0x02247594` | `+0x08` | `0x0223DCE4` | 当前 IREJ 的 direct getter / setter 读写此 `u32`；它指向下方完整且自洽的 Runtime FieldActor | **verified accessor; probable parent actor for this dialogue** |
| `0x02247598` | `+0x0C` | `0x00000004` | heap / configuration scalar，未命名 | candidate |
| `0x0224759C` | `+0x10` | `0x0223B4C0` | 当前 direct getter 的槽，且有 GameSystem 闭环 | **verified accessor; probable GameSystem** |
| `0x022475A0` | `+0x14` | `0x022474D0` | 当前 direct getter 的槽；参考同位置名为 `GetEvent`，但对象类型尚未单步绑定 | verified accessor; probable Event |
| `0x022475A4` | `+0x18` | `0x02271EB0` | 同时是 Field `m_MsgBGSys` 槽；不应以旧猜测命名 | candidate |
| `0x022475A8` | `+0x1C` | `0x02263520` | Field 闭环入口 | probable Field |
| `0x022475B8` | `+0x2C` | `0x02247704` | 当前 direct getter 的槽；参考同位置名为 `GetWordSet` | verified accessor; probable WordSet |
| `0x022475BC` | `+0x30` | `0x022490A4` | 当前 direct getter 的 MsgBuffer / main StrBuf 槽 | verified accessor; probable main StrBuf |
| `0x022475C0` | `+0x34` | `0x022499DC` | 当前 direct getter 的 alternate StrBuf 槽 | verified accessor; candidate alternate StrBuf |

### `ScriptWork+0x08` 的 FieldActor 闭环

`0x0223DCE4` 不是“数值看起来像地址”而已。将其按 SWAN `FieldActor` 布局解释时，多个互相独立的成员同时合理：

| SWAN hypothesis offset | Expected member | RAM at `0x0223DCE4` | Decoded observation |
| ---: | --- | ---: | --- |
| `+0x00` | `Flags` | `0x00000003` | active-like flags |
| `+0x04` | `MovementFlags` | `0x0000400A` | movement-like bitfield |
| `+0x08` | `ActorUID`, `ZoneID` | `0x01AB0004` | UID `4`, ZoneID `0x01AB` |
| `+0x0C` | `ModelID`, `MoveCode` | `0x00000014` | ModelID `0x14`, MoveCode `0` |
| `+0x14` | `SCRID`, `DefaultDir` | `0x0001000D` | SCRID `13`, DefaultDir `1` |
| `+0x18` | `FaceDir`, `MotionDir` | `0x00010000` | FaceDir `0`, MotionDir `1` |
| `+0x30/+0x36/+0x3C` | Default / Init / current `GPosXYZ` | bytes form three equal positions | each `(X=39, Y=1, Z=764)` |
| `+0x84` | `m_TCB` | `0x023086F8` | TCB-shaped Main-RAM pointer |
| `+0x88` | `m_ActorSystem` | `0x0223DB68` | back-reference to actor manager |
| `+0x8C/+0x90` | vtable pointers | `0x021CBCC8` / `0x021CF024` | code-region pointers |

这个结构吻合覆盖了 identifier、脚本 ID、方向、三组位置、TCB、管理器回链和代码指针；不能由一两个偶然的整数匹配解释。

后续只读观察（`watch_diff` 路由不返回 bridge frame，因此仅作补强而非同帧证明）还得到：

```text
0x0223DB68 +0x04 = 0x000B0040  -> capacity=64, count=11
0x0223DB68 +0x1C = 0x0223DBE4  -> ActorHeap
0x0223DCE4 - 0x0223DBE4 = 0x100 -> exact one FieldActor stride
```

这与 SWAN `FieldActorSystem` 的 `ActorCapacity/ActorCount +0x04`、`ActorHeap +0x1C` 和 FieldActor `0x100` 尺寸相容；并且 Actor 自己在 `+0x88` 回指同一 manager。它把 `0x02247594` 的含义收窄为“本次对话关联的 Runtime actor”，而不是静态 spawn 或文本指针。

来源：[SWAN `field/field_mmodel.h`](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/field/field_mmodel.h)、[SWAN `field/field_position.h`](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/field/field_position.h)。

### GameSystem / Field cross-check

这不是定位说话人的必要前提，但它排除了 `0x02247594` 只是随机 heap 地址的解释。当前候选关系为：

```text
ScriptWork +0x10 = 0x0223B4C0
0x0223B4C0 +0x20 = 0x02263520
0x02263520 +0x04 = 0x0223B4C0
0x02263520 +0x40 = 0x0223DB68
FieldActor +0x88 = 0x0223DB68
```

它符合 SWAN hypothesis：`GameSystem.m_Field +0x20`、`Field.m_GameSystem +0x04`、`Field.m_ActorSystem +0x40`。因此目前可以发布为 **probable runtime object graph**，但仍不能把这些会话地址固化进 API。

来源：[SWAN `system/gamesystem.h`](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/system/gamesystem.h)、[SWAN `field/fieldmap.h`](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/field/fieldmap.h)。

## What can be safely said now

可以安全输出的不是“屏幕前的 NPC 一定就是说话者”，而是：

```json
{
  "script_parent_actor": {
    "status": "probable",
    "source": "IREJ ScriptWork Get/SetParentActor accessor at +0x08; active ScriptWork value",
    "actor_address": "0x0223DCE4",
    "actor_uid": 4,
    "scrid": 13,
    "grid_position": {"x": 39, "y": 1, "z": 764},
    "evidence": [
      "SWAN-structure coherence",
      "ActorSystem back-reference",
      "ActorHeap stride correspondence",
      "0x3D ParentActor source-symbol correspondence"
    ]
  }
}
```

不能安全输出的内容：

- “getter/setter 读写 `+0x08`”已经由 IREJ 指令流验证；但它在**当前这条可见消息**中是否等于最终 speaker，仍须跨 NPC / `0x3C` 对照验证。
- 它一定等于本帧玩家刚面对的 NPC。剧情脚本、远处 actor、显式 `0x3C` actor 消息都可能不同；这正是不能把 `script_parent_actor` 直接渲染为 `speaker` 的原因。
- NPC 的人名、静态 spawn、屏幕 sprite 身份；这些还需要当前 ActorSystem/资源绑定。
- 释放后的 `0x02247594` 残留值；对象生命周期结束后必须作废。

## Minimum next experiment

目标是把“`+0x08` 已验证的 parent-actor storage/accessor”与“这条消息的最终 speaker”之间的语义关系升到 `verified`，而不是再扩大无边界内存扫描。

1. 在对话开始且首屏稳定时，使用 bridge `memory.read_batch` 同帧采样：ScriptWork 小块、`ScriptWork+0x08` 指向的 `0x100` bytes、其 `m_ActorSystem` 小块、`ActorHeap` 开头及目标槽。
2. 结束对话，移动到另一个可交谈 NPC，再在相同状态重复采样；每次记录 NPC 在画面中的可控位置与输入，但不以截图判定身份。
3. 通过 actor 数据验证：`+0x08` 必须随已交谈 NPC 改变；新目标须在 `ActorHeap` 内、`Actor+0x88` 回指相同 ActorSystem，且 UID/SCRID/GPos 与前者不同或以可解释方式相同。
4. 若可获得包含 `0x3C ActorEx` 和 `0x3D Actor` 的两个脚本样本：显式 actor 的 operand lookup 与 `+0x08` parent-actor 路径必须分别得到预期 actor。只有在此条件满足，才把 `message_actor` / `speaker` 从 `probable` 标为 `verified`；`+0x08` 的 getter/setter行为本身已验证。

停止条件：交付包含 `{frame, ScriptWork root, field offset, actor address, ActorHeap base/stride, UID, SCRID, GPos}` 的两次或更多次跨 NPC 可重复绑定证据；若有一次脱离 heap 或 manager 回链失败，保持 `unresolved`，不猜测。

## Confidence

- **verified current-ROM static behaviour**：IREJ OVL_12 中 `ldrh [work+0x04]`、`ldr [work+0x08]` 及相邻 `str [work+0x08]` 的 getter/setter 代码。
- **verified raw observation**：EXP_014 活动帧中的字节、ScriptWork 候选地址、`+0x08` 值、FieldActor-shaped 内容、Actor 的 `m_ActorSystem` 值、结束后 `+0` 变化。
- **SWAN hypothesis with strong current-ROM coherence**：FieldActor / ActorSystem / GameSystem / Field 的具体偏移。
- **probable**：当前这条 `0x3D`-style message 的最终 speaker 等于 `ScriptWork+0x08`；`+0x10` 是 GameSystem；`+0x1C` 是 Field；来自相邻 reference-symbol mapping 的 `+0x04 SCRID` / `+0x08 ParentActor` 语义名称。
- **unresolved**：ScriptWork 的完整 C layout、`+0`/`+0x0C`/`+0x14`/`+0x18`/`+0x2C`/`+0x40` 的正式类型、当前 IREJ 中 `s003D` 的实际指令地址与调用 trace、显式 actor 覆盖逻辑。
