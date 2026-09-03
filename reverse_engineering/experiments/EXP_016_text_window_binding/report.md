# TEST REPORT — EXP_016 candidate BmpWin / Bitmap draw-target binding

日期：2026-09-03。所有动态证据来自 BizHawk Lua Bridge 的 Main RAM
同帧读取和一次 bridge-owned A edge；没有使用截图、OCR、RAM 写入或把
已加载 MsgBuffer 当作屏幕事实。

## Goal

缩小 Gen 5 对话打印机的实际绘制目标：验证 `0x02332C20` 周边的候选控制
观察窗是否能经由 BmpWin/Bitmap 形状对象指向一个随文字与滚屏变化的像素
缓冲。此实验**不**试图仅凭缓冲区字节反推当前可视文字。

## Hypothesis

对这一条已加载台词，候选控制观察窗存在下列运行时链：

```text
candidate observation +0x20 -> BmpWin-shaped object
candidate observation +0x24 -> GFLBitmap-shaped object
BmpWin candidate +0x0C -> same Bitmap candidate
GFLBitmap candidate +0x00 -> 240 x 32, 4bpp-sized pixel target
```

若 A edge 后源消费位置、候选滚屏字段与该目标字节发生可重复的时序相关，
该链可被标记为绘制目标的 `probable`。这仍不能证明该 Bitmap 已由当前
active Window 显示到屏幕，更不能证明每个字节对应哪个 glyph。

## Method

- 以 `text-render-chain` profile 做 exact-frame 批量读取：Script/window
  flags、MsgBuffer、候选控制观察窗、三个 pointer target 以及完整候选
  4bpp 像素分配区。
- 在 page 1 等待时按一次 A，采样 80 帧到 page 2 等待：
  `a_edge_capture_page1_clear_to_page2_pointer_chain.json`。
- 在 page 2 等待时按一次 A，采样 80 帧到 overlap / EOS 等待：
  `a_edge_capture_page2_scroll_to_overlap_pointer_chain.json`。
- 使用 `tools/analyze_text_window_binding.py` 只分析指针相干性、尺寸、
  像素目标哈希和逐帧字节差异；工具明确禁止输出可视行、TextPrinter enum
  或说话 NPC。

## Memory ranges

| Observation | Main-RAM range | Purpose |
| --- | ---: | --- |
| script / window flags | `0x02247500..0x022476FF`, `0x0223B4E0..0x0223B51F` | 分别观察活动脚本和既有 window flag。 |
| candidate control observation | `0x02332B40..0x02332D3F` | 指针、source cursor、scroll/cursor candidates。 |
| candidate render objects | `0x02335300..0x023353FF` | 使 pointer target 与 PixelData header 可同帧复核。 |
| candidate render allocation | `0x02335380..0x0233637F` | 覆盖宣称的 `0x023353C0..0x023362BF` 目标。 |

这些是本次会话的观察地址，绝不是永久 Runtime API 常量。

## Raw observations

### 同帧对象形状和 pointer relation

在 page 1 等待基线 frame `5079528`：

```text
candidate observation base 0x02332C20
  +0x18 = 1                 phase candidate
  +0x1C = 1                 first-page latch candidate
  +0x20 = 0x02332B8C        BmpWin pointer hypothesis
  +0x24 = 0x02332BC8        Bitmap pointer hypothesis
  +0x28 = 0x0233535C        context pointer hypothesis
  +0x2C = 0x022490C8        continuation cursor candidate

0x02332B8C +0x0C = 0x02332BC8
0x02332BC8 +0x00 = 0x023353C0   PixelData candidate
0x02332BC8 +0x04 = 240          width candidate
0x02332BC8 +0x06 = 32           height candidate
240 * 32 / 2 = 3840 (0xF00)     4bpp byte-length hypothesis
```

因此 `0x023353C0..0x023362BF` 完整落在采样到的分配区内。三个地址关系和
宽高在两次 A-edge 的所有采样帧中保持稳定。

### CLEAR 后进入第二段打印

`page1_clear_to_page2_pointer_chain_analysis.json` 的关键时间点：

| Frame | phase candidate | source-cursor candidate | cursor candidate (X,Y) | pixel bytes changed from previous |
| ---: | ---: | --- | --- | ---: |
| 5079528 | 1 | `0x022490C8` | `(8,0)` | 0 |
| 5079530 | 0 | `0x022490C8` | `(8,0)` | 528 |
| 5079531 | 0 | `0x022490CA` | `(20,0)` | 68 |
| 5079535 | 0 | `0x022490CC` | `(32,0)` | 48 |
| 5079555 | 0 | `0x022490D6` | `(92,0)` | 66 |
| 5079556 | 0 | `0x022490DA` | `(20,16)` | 55 |
| 5079580 | 1 | `0x022490EC` | `(8,0)` | 0 |

`5079530` 的 528-byte target difference 和后续 cursor/source-step 的
局部差异，说明该内存不是静态未使用对象；但它们仍是“绘制目标候选”的
相关性，不是逐字符 draw-call 证据。

### SCROLL 阶段的 4px 时序

在第二次 A edge 前，frame `5083980` 同时观测到
`script_msg_active=1`、既有 `window_active` byte 为 `1`、候选 cursor
`0x022490EC` 和 scroll candidate `0`。随后：

| Frame | phase candidate | scroll candidate | cursor candidate (X,Y) | pixel bytes changed from previous |
| ---: | ---: | ---: | --- | ---: |
| 5083982 | 0 | 0 | `(8,0)` | 20 |
| 5083983 | 0 | 4 | `(8,0)` | 503 |
| 5083984 | 0 | 8 | `(8,0)` | 586 |
| 5083985 | 0 | 12 | `(8,0)` | 552 |
| 5083986 | 0 | 16 | `(8,0)` | 379 |
| 5083987 | 0 | 16 | `(8,16)` | 15 |
| 5083988 | 0 | 16 | `(20,16)` | 68 |
| 5084004 | 2 | 16 | `(65,16)` | 20 |

这验证了该**候选** scroll byte 与 4px 像素目标更新的强时序相关。后续
稳定在 `scroll=16`、cursor `0x022490F6`、phase candidate `2`，但不得把
这些数值命名成 TextPrinter 的通用 state enum 或把 cursor 解释成可见行。

## SWAN / current-ROM correspondence

- `GFLBitmap` 的 `PixelData/+0x00` 和宽/高形状与参考项目的 Bitmap 概念
  一致，但当前 IREJ 的完整类型/所有权尚未通过代码路径复核，故成员名仍是
  **SWAN hypothesis**。
- BmpWin-like object 的 `+0x0C` 与同一 Bitmap 目标完全匹配，是独立于
  单一指针值的结构相干性证据。
- 没有观察到确定的 `Window` owner、VRAM flush/transfer 调用或实际 draw PC；
  `0x0223B4F5=1` 仅是同帧已知 window-active flag，尚未建立它到此 BmpWin
  的 ownership relation。

## Confidence

| Field / relationship | Status | Reason |
| --- | --- | --- |
| `0x02332C20` 周边为 active TextPrinter 结构 | hypothesis | 起始 `tcbl.c\0` 字节仍不符合已证实对象 base；字段名未静态验证。 |
| candidate `+0x20/+0x24` 到 BmpWin/Bitmap-shaped objects | **probable** | 三条 pointer relation、稳定宽高和两条独立 A-edge 一致。 |
| Bitmap candidate 的 `PixelData=0x023353C0`, `240x32`, `0xF00` 4bpp span | **probable** | 同帧 header/边界完整捕获，且内容在 print/scroll 时改变。 |
| 候选 scroll byte 和像素目标的 4px 更新 | **probable for this layout** | 0→4→8→12→16 连续帧与高密度 byte differences 相符。 |
| active Window owns this Bitmap / transfers it to visible screen | unresolved | 缺少 Window owner 与 flush/VRAM call path。 |
| 当前真实可视文字、逐字进度、可按 A 状态 | unresolved | 像素字节尚未解码/绑定到 draw call；不能由 source buffer 推断。 |
| 对话说话 NPC | unresolved | 此实验不读取/解释 ScriptWork actor relation。 |

## Rejected or failed hypotheses

- `0x0231FCB0` 不是本次已验证 TextPrinter，不能借此 report 重新启用。
- `0x02332C4C` 是 source continuation candidate，而不是“当前可视文字首
  指针”：它在 overlap/EOS 等待时已经到 `FFFF`，视觉上仍存在上一行的内容。
- GFLBitmap-shaped header + changing bytes **不能**独立证明屏幕内容，更不能
  得出 `visible_lines` 或自动按 A 的结论。

## Files changed

- `tools/runtime_memory_discovery.py`（`text-render-chain` profile）
- `tools/analyze_text_window_binding.py`
- `reverse_engineering/experiments/EXP_016_text_window_binding/*.json`
- 本报告

## Next recommended experiment

在已确认的 `0x023353C0..0x023362BF` 目标范围上做**短时、有上限**的
ARM9 memory-write PC trace，记录：

```text
source token address / codepoint -> writer PC -> target pixel address -> x/y state
```

捕获从 page 2 等待到 overlap 的 20–30 帧，最大事件数固定并自动解绑回调。
随后把 writer PC 静态反查到 `BmpWin_FlushChar` / clear / scroll 路径，才有资格
把这一个固定两行窗口布局的可视 glyph 提升到 `probable`。还需额外验证一条
自动换行（无显式 LF）台词，才能考虑通用可视行算法。
