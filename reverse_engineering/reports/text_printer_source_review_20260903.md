# TEST REPORT — TextPrinter / Window / speaker 源码复核

日期：2026-09-03。类型：源码审查与既有证据重解释，不是新的按键实验。

## Goal

确定 Black 2 对话逆向的可靠入口：还原当前可见字符、分行、滚屏及打印器生命周期，并定位消息关联的 Runtime Actor。

## Method / Actions performed

- 阅读 SWAN 的头文件、root.swandb，以及两个版本的符号库；不能只读 IREO.yml。
- 对照 HeartGold 与 Platinum 的实际打印、Window、脚本实现。
- 阅读 CTRMapV / CTRMap-CE 的 Gen 5 文本格式实现和 B2W2 脚本 SDK。
- 重新读取 EXP_010 原始字节；审查本项目采样、解析与证据保存路径。
- 只读查询 Bridge 状态，读取本地 ROM 头和 SHA-1；未按键、未写 RAM、未改模拟器设置、未修改 Runtime 代码。
- 完整来源版本和逐 word 证据见 [evidence.json](text_printer_source_review_20260903_evidence.json)。

## ROM information / memory domains

本地 ROM 与 Bridge 报告的 SHA-1 一致：

- SHA-1：`8DB71663502BBF3B43AC3C9052EC390C390BE62F`。
- ROM header：`IREJ`，revision `1`，512 MiB。
- ARM9 ROM offset `0x00004000`，加载地址 `0x02004000`，存储大小 `0x000731D8`；尚未判断该代码块的解压/汉化修改情况。
- Bridge 报告 BizHawk `2.11.1`；本地翻译版的能力仍须实际检查。
- 除 4 MiB Main RAM，还暴露 Data TCM、Instruction TCM、Shared WRAM、ARM9/ARM7 System Bus 等域。

SWAN README 明示 IRDO / IREO 地址不可跨版本互用。因此下面的 IRDO 函数地址只是符号迁移参照，绝不能写成当前 IREJ ROM 的已验证地址。

## 核心结论

推荐主线是：当前 ROM 的消息消费者/绘制函数 → 实际对象与生命周期 → Window/Bitmap → 提交到 VRAM 的内容核验；说话者从当前消息命令与 ScriptWork 的 actor 关系解析。

没有证据支持一个可以直接读取所有可视行的通用 `topLineIndex` 字段。Gen 4 的实际 Window 保留像素缓冲，而打印器并不持久保存屏幕全文。Gen 5 必须沿自身代码重新验证。

### 1. 首先纠正控制码与字符串边界

Gen 4 与 Gen 5 不能共用同一套控制码表：

| 语义 | HeartGold / Platinum | Gen 5 格式参考 |
| --- | --- | --- |
| 换行 | `E000` | `FFFE` |
| 扩展命令前缀 | `FFFE` | `F000` |
| 清屏控制 | `25BC` 等 | `F000 BE01 argc args...` |
| 滚屏控制 | `25BD` 等 | `F000 BE00 argc args...` |
| 结束 | `FFFF` | `FFFF` |

CTRMapV 的 GenVMessageHandler 明确给出 Gen 5 的 LF、命令前缀和终止符；GenVTextVariableCode 定义 CLEAR / SCROLL / WAIT / SPEED 等命令；CTRMap-CE 的 TextVariable 说明其编码顺序是：

`F000 → command_id → argument_count → argument_count 个 u16 参数`

因此 `F000 BE01 0000` 中的 `0000` 是零个参数，不能当作字符串结束。孤立的 `0001` 也不能直接翻译成劲敌名，必须先确认它处于哪一个命令的哪个参数位置。名字应来自已验证 WordSet 槽或展开后的 StrBuf。

来源：[GenVMessageHandler](https://github.com/ds-pokemon-hacking/CTRMapV/blob/3c2778095867f3007ad48d2c268feb0331d43d70/src/ctrmap/formats/pokemon/text/GenVMessageHandler.java)、[GenVTextVariableCode](https://github.com/ds-pokemon-hacking/CTRMapV/blob/3c2778095867f3007ad48d2c268feb0331d43d70/src/ctrmap/formats/pokemon/text/GenVTextVariableCode.java)、[TextVariable](https://github.com/ds-pokemon-hacking/CTRMap-CE/blob/74e2b035ac730f5cf76d588d597dcb43569d4c4b/src/ctrmap/formats/pokemon/text/TextVariable.java)。

### 2. EXP_010 实际保存的是连续的带控制命令文本

以下地址和值来自历史快照，不代表此刻游戏状态：

| 地址 | 实际 word 序列 | 解释层级 |
| --- | --- | --- |
| `022490AC…022490BE` | 科学的力量真是惊人！ | 已保存字符数据 |
| `022490C0…022490C6` | `F000 BE01 0000 FFFE` | Gen 5 格式：CLEAR + LF |
| `022490C8…022490D4` | 现在可以用通信 | 已保存字符数据 |
| `022490D6` | `FFFE` | Gen 5 格式：LF |
| `022490D8…022490E2` | 和１００个人 | 已保存字符数据，数字是全角 |
| `022490E4…022490EA` | `F000 BE00 0000 FFFE` | Gen 5 格式：SCROLL + LF |
| `022490EC…022490F4` | 同时游戏！ | 已保存字符数据 |
| `022490F6` | `FFFF` | 该序列的终止符 |

这段连续数据中没有三个独立的 EOS。旧报告的“三个 fragment”是提取器按控制码切片的结果，不能当作三个独立字符串或三个不同的消息对象。

结构上应保留为：

`科学的力量真是惊人！[CLEAR][LF]现在可以用通信[LF]和１００个人[SCROLL][LF]同时游戏！[EOS]`

这是文本控制程序，尚不是可见屏幕结果。尤其 SCROLL 不等于清屏，第三阶段究竟保留哪一条旧行，不能从“同时游戏！”这个后缀推断。

`0x02332C4C` 在两份历史快照中为 `022490C8 → 022490EC`。新的、更有解释力的 hypothesis 是：它可能记录控制命令之后的续读位置。在等待用户滚屏时，续读位置已指向未来文字完全可能；这不证明那几个字已绘制，也不证明该字段一定就是 printer.currentChar。

顺带观察到 `022490A4=0480`、`A6=0025`、`A8=B6F8D2EC(u32)`，且从 AC 到 EOS 前有 37=`0x25` 个 u16。这提供了“StrBuf 头可能在 A4，字符数组在 +8”的结构候选；容量、长度、magic 含义与所有权仍须通过当前 ROM 的 StrBuf getter/constructor 验证。

### 3. Gen 4 真正的打印与等待状态

Platinum 的 RenderText 状态枚举与行为如下；这些数字不能直接套到 Black 2：

| Gen 4 state | 行为 |
| --- | --- |
| 0 HANDLE_CHAR | 读 token，处理布局或绘制字形；读取后源指针已经前移 |
| 1 WAIT | 等继续条件后回到 0 |
| 2 CLEAR | 先等输入；收到继续条件才清像素并重置光标 |
| 3 START_SCROLL | 先等输入；随后设置滚动剩余距离并进入 4 |
| 4 SCROLL | 搬移 Window 像素，递减剩余距离，传到 VRAM |
| 5 DUMMY | 回到 0 |
| 6 PAUSE | 延迟计数递减后回到 0 |

HeartGold 还存在 7/8 等额外路径，进一步说明不同游戏不可共享 state 枚举。

`scrollDistance` 在这份实现中是本次滚动的剩余像素，不是累计 Y 偏移。滚动总量由字体高度加行间距确定；不能把“16 像素”提升为通用定律。Platinum 每步最多移动 4 像素只是该源码事实。

来源：[Platinum RenderText](https://github.com/pret/pokeplatinum/blob/bca37652996330898fdd2408281ea419b8c995c7/src/render_text.c)、[HeartGold RenderText](https://github.com/pret/pokeheartgold/blob/0985e8718df4f25e64d6507d89c0c97c0d288981/src/render_text.c)。

打印器 task 完成会释放 TextPrinter，但 Window 可能继续存在。脚本还可以在打印结束后执行独立的 WaitABPress。所以必须分开描述：

- printer 是否存在、正在打印/延迟/等待控制命令；
- message window 是否存在、可见、内容是否提交；
- script VM 是否在等打印完成、等按键或其他操作。

相关源码没有可直接等同“屏幕可见字符数”的通用计数器。`(current-start)/2` 会把控制码和参数也计入；滚走的字还会继续被计入。

来源：[Platinum TextPrinter 生命周期](https://github.com/pret/pokeplatinum/blob/bca37652996330898fdd2408281ea419b8c995c7/src/text.c)、[脚本等待打印](https://github.com/pret/pokeplatinum/blob/bca37652996330898fdd2408281ea419b8c995c7/src/scrcmd.c#L1303)。

### 4. Window 的字符图块不是字符索引

Gen 4 的 Window 包含 BG、tilemap 位置、宽高、palette、baseTile 和 pixels。没有 topLineIndex、visibleLineCount 或源字符串偏移。baseTile 是图形图块分配信息，不能当 Unicode/字形索引。

滚动操作会修改 RAM 像素缓冲。因而可见内容要从“实际字形绘制、clear/scroll/blit 操作以及目标窗口”恢复，再核对当前 RAM / VRAM。读取 pixels 做字节验证不需要截图或 OCR。

来源：[Platinum Window](https://github.com/pret/pokeplatinum/blob/bca37652996330898fdd2408281ea419b8c995c7/include/bg_window.h#L145)、[HeartGold 像素滚动](https://github.com/pret/pokeheartgold/blob/0985e8718df4f25e64d6507d89c0c97c0d288981/src/bg_window.c#L2025)。

## SWAN correspondence：真正应研究的 Gen 5 入口

指定 SWAN commit 中不存在 `system/wordset.h` / `system/wordset.c` / 通用 NPC `text_printer.h` / `window.h`。存在的 `gfl/str/gfl_textprint.h` 是另一套系统字体绘制接口，带有 g_DebugPrintState；不能因名字相似就把其 TextPrint 套到 NPC 对话。

关键线索实际在更完整的 IRDO.yml，许多名称没有对应完整头文件：

| IRDO 参考函数 | IRDO 参考地址 | 所在段 | 研究用途 |
| --- | --- | --- | --- |
| GFL_TextRndDrawString | `02021F29` | ARM9 | 找消息文本消费者 |
| GFL_TextRndDrawCharProc_IDX4 / IDX8 | `02021FE1 / 0202202D` | ARM9 | 找字形写入、坐标与目标对象 |
| GFL_TextRndExecLayoutCmd | `0202207D` | ARM9 | 找排版控制命令处理 |
| GFL_WordSetFormatStrbuf | `0202494D` | ARM9 | 找变量展开后的输出串 |
| GFL_FontGetGlyph | `020230A1` | ARM9 | 验证 codepoint/字形与字体资源 |
| BmpWin_GetBitmap | `02048521` | ARM9 | 用 getter 反推出真实 Bitmap 成员 |
| BmpWin_FlushChar | `02048271` | ARM9 | 确认窗口内容提交路径 |
| ScriptWork_GetParentActor | `02153F21` | OVL_12 | 区分脚本 parent actor 与当前说话者 |
| FieldScriptEnv_GetScriptWork | `021551C5` | OVL_12 | 脚本环境到 ScriptWork |
| GetFieldScriptActorWk | `0215520D` | OVL_12 | 研究 actor 子工作区，返回类型未确定 |
| Field_GetMsgBGSys | `021804D1` | OVL_36 | Field 到消息窗口系统 |
| ActorMsgWin_CreateCore | `0218B691` | OVL_36 | 找实例初始化和所有者 |
| ScriptNative_ActorMsgWinWait | `021A8ECD` | OVL_36 | 找等待状态与生命周期 |
| s003C_ActorMsg / s003D_ParentActorMsg | `021A8F65 / 021A8FB1` | OVL_36 | 当前消息的 actor 选择 |
| LoadFieldScriptMessage | `021A98E5` | OVL_36 | 消息资源到活动文字 |

来源：[SWAN IRDO.yml](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/IRDO.yml)。

这些是白2参考符号，不是已知黑2当前调用链；函数名也不自动证明其完整语义。应先在当前 ROM 中找对应函数/调用点，再由反汇编确认参数、成员偏移和具体行为。Thumb 函数符号末位可能带模式位，设置断点时须按实际工具地址规则处理。

### 源码布局候选，全部尚未在此 ROM 验证

按所读 SWAN 头文件、32-bit 指针及声明的标量类型推导：

| Structure | Field | Offset | Type | Meaning | Runtime/Static | Verified |
| --- | --- | --- | --- | --- | --- | --- |
| GameSystem | m_Field | +0x20 | pointer | Field 根 | Runtime | SWAN hypothesis |
| Field | m_GameSystem | +0x04 | pointer | 根回链 | Runtime | SWAN hypothesis |
| Field | m_MsgBGSys | +0x28 | pointer | 消息系统候选入口 | Runtime | SWAN hypothesis |
| Field | m_ActorSystem | +0x40 | pointer | actor 管理器 | Runtime | SWAN hypothesis |
| Field | m_Player | +0x94 | pointer | player 控制对象 | Runtime | SWAN hypothesis |
| FieldPlayer | m_Core | +0x04 | pointer | player core | Runtime | SWAN hypothesis |
| FieldPlayerCore | Actor | +0x1C | pointer | player actor | Runtime | SWAN hypothesis |
| FieldActorSystem | ActorCapacity / ActorCount | +0x04 / +0x06 | u16 | 容量/数量 | Runtime | SWAN hypothesis |
| FieldActorSystem | ActorHeap | +0x1C | pointer | actor 数组，不能误用 +0x18 | Runtime | SWAN hypothesis |
| FieldActorSystem | m_Field | +0x40 | pointer | Field 回链 | Runtime | SWAN hypothesis |
| FieldActor | ActorUID / SCRID | +0x08 / +0x14 | u16 | 运行时 ID / 脚本索引 | Runtime | SWAN hypothesis |
| FieldActor | FaceDir / MotionDir | +0x18 / +0x1A | u16 | 朝向 / 移动方向 | Runtime | SWAN hypothesis |
| FieldActor | GPos | +0x3C | {u16,s16,u16} | 当前 Grid 坐标 | Runtime | SWAN hypothesis |
| FieldActor | WPos | +0x44 | VecFx32 | 当前连续坐标 | Runtime | SWAN hypothesis |
| FieldActor | m_ActorSystem | +0x88 | pointer | 管理器回链 | Runtime | SWAN hypothesis |
| GFLBitmap | PixelData / PixelWidth / PixelHeight | +0 / +4 / +6 | ptr/u16/u16 | 像素及尺寸 | Runtime | SWAN hypothesis |

FieldActor 的候选 stride 按此版完整声明计算为 0x100；必须再由当前 ROM 的分配大小、索引步进及多个活跃条目共同验证。ActorCapacity 不等于每个槽都活跃。

验证优先使用闭环：Field → ActorSystem → Field、ActorHeap 中的 Actor → 同一 ActorSystem，以及 PlayerCore.Actor 确实属于该堆。随后才进行坐标行为验证。名称、模型和静态 spawn 不参与推定当前位置。

来源：[gamesystem.h](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/system/gamesystem.h)、[fieldmap.h](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/field/fieldmap.h)、[field_player.h](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/field/field_player.h)、[field_mmodel.h](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/field/field_mmodel.h)、[gfl_bitmap.h](https://github.com/ds-pokemon-hacking/swan/blob/4324f73a7659353a21bf4c523905c5d09cf6a066/gfl/g2d/gfl_bitmap.h)。

## 说话 NPC 的逆向路径

CTRMapV 随附的 B2W2 SDK 的 event/dialogs/Message.h 区分：

- 0x3C / ActorEx：显式给出 textFile、msgId、actorId、pos、type。
- 0x3D / Actor：从上下文自动确定 actor。
- 0x38 / Info 等消息不关联 NPC。
- 0x48 / 0x49 等变体也具有 actor 参数。

因此应同时保留 `interaction_target`、`script_parent_actor`、`message_actor`。当前显示消息可能由剧情中的另一个 NPC 发出，不能把最初交互对象永久当作说话者。

建议验证关系：

`当前消息命令 → 显式 actorId 或 ScriptWork parent actor → 当前 ActorSystem 查找 → 存活 FieldActor → UID / ZoneID / SCRID / GPos / WPos`

ScriptWork_GetParentActor 返回的是 ID、句柄还是指针，SWAN 仅有函数名不足以确定，必须读当前二进制的返回值与消费者。若直接获得 actor 指针，也需要验证其仍属于当前 ActorHeap。

普通 NPC 不一定有个人姓名字段。先输出 actor 身份和当前位置；没有资源支持的显示姓名保持 unresolved。系统消息可在确认其类型后标为不适用，而不是硬猜 NPC。

来源：[CTRMapV B2W2 SDK archive](https://github.com/ds-pokemon-hacking/CTRMapV/blob/3c2778095867f3007ad48d2c268feb0331d43d70/src/ctrmap/resources/scripting/cm_ide/sdk/EV_GEN_V/SDK5-B2W2-Generated.lib)，已读取内部 `src/event/dialogs/Message.h`；并与 IRDO 的 s003C / s003D / ScriptWork_GetParentActor 交叉核对。

## 如何做到每帧真实分行

先定位活动窗口与其拥有的绘制工作对象，再捕获/解码当前真实执行的操作：

1. 创建、绑定、销毁窗口与打印工作对象。
2. 已展开 codepoint / glyph、字体资源、实际 x/y、目标 bitmap、字形尺寸。
3. 换行、clear、scroll、任意 copy/fill 及布局变换。
4. RAM surface 到 VRAM 的上传或排队提交。
5. 实际 BG/窗口的启用、位置、裁切与遮挡状态。

仅凭“源指针移动”不能把 glyph 标为可见，至少要确认对应绘制和提交。滚动中可能有部分字形同时落在窗口边界，应保留 bbox 和 clipping；按实际 y/布局分组才能生成 visible_lines。已滚走、已清除或被覆盖的字不能留在当前输出里。

如必须按项目规则仅从当前事实输出，绘制事件记录只能作为带来源的派生状态：每次发布须用当前窗口身份、当前 RAM/VRAM 数据验证，任何事件丢失、savestate 回退、窗口复用或字节不一致都使状态失效。当前 actor 位置始终重新读取。

“任意时刻连接，只读一个 currentChar 就 100% 还原”没有依据。若文字已光栅化且打印器已释放，一次快照可能不保留唯一的字符级来源。应从窗口创建/清空事件建立同步，或对仍可读的文本、字体、布局状态做有界重演并与当前字节精确比对；存在多个解释时输出 unresolved。这样可以不使用截图/OCR，但不能承诺所有任意中途快照都能唯一逆推。

## 对现有结论与 Probe 的审查

### 证据等级修正

- EXP_009 的不合法指针和异常 Y，只能否定“该次采样中的此基址符合原假定字段布局”。堆对象释放、复用、地址漂移、偏移错误尚未排除，不能永久证明该地址从未是打印器。
- Main RAM 搜索不到指向某地址的 u32，不能单独否定该对象；引用可能在 TCM、寄存器、句柄表或通过内部成员定位。原先将 0x0223DE00 / 0x02143620 永久 rejected 的推断过强。
- 3381 个布局命中是未筛选候选，不能在未逐一验证时称作 3381 个已证明 false positive。
- EXP_010 的 ranges 在单次 Lua handler 中读取，具备批内同帧性质；但 map/watch_diff 丢弃了 bridge 返回的 frame。历史文件的 frame_from_state 是另一次 /api/state 采样，不能当原始内存的精确帧号。
- EXP_009 原始 artifact 自己标记为 non-atomic HTTP reads，应保留此方法事实，不能用后来的工具实现追认它为同帧采样。

### 当前代码中仍需处理的具体问题（本轮未修改）

- dialogue.py 中仍有 `0001 → NO`、按文本后缀补 NO、问号推定“是/否”选项；这些均无足够 RAM 字段依据。
- _decode_buffer_candidates 把 F000 当换行、跳过 BE00/BE01，并把参数 0000 用来分段；应改成保留源地址和长度的 token parser。
- dialogue_printer_tracer.py 用 cursor_x > 0 推定 WAIT_BUTTON，并串行获取不同帧字段；不具备其文件注释声称的精确同步。
- 新 a_edge probe 仍未获得真实执行证据；没有读取 PC/绘制事件/窗口所有者，32 帧时间线本身不能定位全部结构。
- probe 未排除既有 input_queue 或其他控制器输入干扰；必须记录实际注入输入、先建立释放帧并隔离采集期间的其他请求。
- safe_read_u8 对 Main RAM / ARM9 System Bus 采用同一地址归一化，且读取失败返回 0。今后要明确域与地址类型、记录错误；不能把无法读取解释为真实 0。尤其不适合直接拿它读取低地址 ITCM。
- a_edge_capture.json 当前会覆盖同名结果，后续实验必须使用唯一编号或新目录保存原始证据。

## Next recommended experiment：先找消费者，再追 Window

下一阶段只建议一个有明确停止条件的实验：`EXP_011_message_consumer_binding`。

### Preparation

- 已确认本 ROM 身份。按 ROM header / overlay table 提取 ARM9 与 OVL_12、OVL_36，正确处理压缩、加载地址、BSS；按当前 RAM 确认实际加载映像。
- 迁移上表少量目标函数：利用断言字符串（如 wordset_c、talkmsgwin）、交叉引用、调用图、控制码分支和 getter，不能简单对所有地址加一个常数差。
- 不拥有参考版本机器码时，SWAN 符号表只能提供名称/位置线索，不能据此声称完成了跨 ROM 字节匹配。
- 检查真实调试能力。BizHawk 2.11.1 上游在 EnableJIT=false 时提供 MemoryCallbacks；单指令 Step 则未实现。本轮没查到当前 JIT 状态，也未注册回调。
- 上游注释提示 callback scope 实际可能混合两个 CPU，总线标签不足以确认 ARM9；应结合两组 PC、反汇编及访问操作数核对。

来源：[BizHawk 2.11.1 NDS IDebuggable](https://github.com/TASEmulators/BizHawk/blob/2.11.1/src/BizHawk.Emulation.Cores/Consoles/Nintendo/NDS/MelonDS.IDebuggable.cs)。

### Bounded capture

先在当前 RAM 重新确定消息缓冲与候选地址，再将历史地址用于小范围参考：

- 对消息中 CLEAR / SCROLL / 下一字形的读取，以及候选 `0x02332C4C` 的写入设置有限回调。
- 记录 frame、cycle/序号、访问地址/大小/值、ARM9/ARM7 PC、LR/SP、必要寄存器与相关对象小块。
- 从已验证的等待点，记录释放帧、单次 A、释放后的短时间线；实际输入与等待转移必须在证据中出现。
- 在读到命令之后，确认哪个分支进入等待，谁改变续读指针，实际绘制函数何时消费下一字符。
- 在绘制入口/调用点记录真实对象与 Bitmap 参数，沿初始化/所有权追到 Field 消息系统。
- 若内存回调不可用，优先静态分析同一代码与小范围帧采样；若仍无法观察关键时序，应明确需要支持相应调试能力的环境，不能把更多轮询当作等价证据。

### Acceptance / stop condition

交付一个可检查的 `{ROM hash, code segment/hash, PC, object pointer, field offsets, bitmap/window owner, source token interval}` 绑定证据。

只有获得绑定后，再在下一阶段验证 CLEAR、SCROLL、字符进度、窗口销毁与 speaker actor。第一轮不要求直接实现完整前端。若未绑定成功，提交失败原因和原始 trace 后停止；不连续堆叠字段猜测。

## Confidence / Verified fields / Unresolved fields

- verified：本地 ROM 头、与 Bridge 报告一致的 ROM SHA-1；历史 artifact 保存的逐 word 值。
- probable（格式解释）：历史连续字节满足 Gen 5 命令格式；尚未在当前二进制逐分支验证控制命令的实际效果。
- candidate：0x02332C4C 为消息续读/阶段相关字段；StrBuf header at 0x022490A4。
- hypothesis：IRDO 函数迁移关系、上表 SWAN 对象偏移、父 actor / 当前说话 actor 的运行时关联。
- unresolved：当前真实打印器地址、state 数值、活动 Window/BmpWin 结构、每帧可视内容、当前 speaker actor、回调能力/JIT 状态。

## Files changed

- 本报告。
- `text_printer_source_review_20260903_evidence.json`。
- `docs/swan_runtime_schema.md`：补充上述控制流发现与布局候选，收窄旧的 rejected 推断。
- 无 Runtime / Bridge / 游戏状态修改。
