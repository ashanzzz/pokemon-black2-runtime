# TEST REPORT — EXP_018 & EXP_019: TextPrinter 状态机与 PixelData 闭环报告

日期：2026-09-03。所有动态证据均来自 BizHawk Lua Bridge 对 ARM9 4MB Main RAM 的 Exact-Frame 采样和 PixelData 逐像素差分，无截图、无 OCR、未写 RAM。

---

## 1. 核心状态转移常数与语义已验证 (Verified Ground Truth)

在控制观察窗基址 `0x02332C20` 处的控制结构体中，确定了以下状态字段与流转规则：

| 物理地址 | 相对偏移 | 数据类型 | 验证数值 | 核心语义 |
| :--- | :---: | :---: | :---: | :--- |
| `0x02332C38` | `+0x18` | `u32` / `u8` | `0` | **`S_PRINTING`**：打字机出字中（每 4 帧消费一个 Unicode 字符，光栅化入 PixelData） |
| `0x02332C38` | `+0x18` | `u32` / `u8` | `1` | **`S_WAIT_PAGE`**：屏间等待按键（包含翻页清屏等待与滚屏等待） |
| `0x02332C38` | `+0x18` | `u32` / `u8` | `2` | **`S_WAIT_EOS`**：全文结束等待按键（段落末尾终止符 `0xFFFF` 后的关闭等待） |
| `0x02332C3C` | `+0x1C` | `u32` / `u8` | `1` | **`FirstPageLatch`**：首屏锁定标记（`1`=首屏等待翻页清屏，`0`=非首屏） |
| `0x02332C4C` | `+0x2C` | `u32` | `0x022490XX` | **`SourceCursor`**：字符流消费指针（指向当前正在光栅化或等待处理的字符源地址） |

---

## 2. 状态转移全景时序矩阵 (State Transition Matrix)

```text
[NPC 对话启动]
       │
       ▼
Phase = 0, Latch = 1 (S_PRINTING, 首屏第一行 "科学的力量真是惊人！")
       │ (逐字绘制，耗时约 36 帧，PixelData Line 0 写入)
       ▼
遇到控制符 [F000 BE01 0000 FFFE] (CLEAR + LF)
       │ (SourceCursor 跳过控制符，停留在 0x022490C8)
       ▼
Phase = 1, Latch = 1 (S_WAIT_PAGE: 等待玩家按 A 翻页清屏)  <-- [EXP-B 初始态]
       │
   [按下 A 键]
       │
       ▼
Phase = 0 (S_PRINTING: 瞬态清屏 528 字节变动，重置光标至 Line 0)
       │ (出字 "现在可以用通信"，耗时 26 帧，PixelData Line 0 写入)
       ▼
遇到控制符 [FFFE] (纯换行 LF)
       │ (光标换至 Line 1 首，SourceCursor 指向 0x022490DA)
       ▼
继续 Phase = 0 (出字 "和１００个人"，耗时 24 帧，PixelData Line 1 写入)
       │
       ▼
遇到控制符 [F000 BE00 0000 FFFE] (SCROLL + LF)
       │ (SourceCursor 跳过控制符，停留在 0x022490EC)
       ▼
Phase = 1, Latch = 0 (S_WAIT_PAGE: 等待玩家按 A 滚屏)  <-- [EXP-C 初始态]
       │
   [按下 A 键]
       │
       ▼
Phase = 0 (平滑滚屏动画：历时 4 帧，每帧上移 4 像素，共 16 像素行高)
       │ (原 Line 1 "和１００个人" 上移变为 Line 0，Line 1 腾空清空)
       ▼
继续 Phase = 0 (在 Line 1 逐字出字 "同时游戏！"，耗时 16 帧)
       │
       ▼
遇到终止符 [FFFF] (EOS)
       │ (SourceCursor 停留在 0x022490F6)
       ▼
Phase = 2 (S_WAIT_EOS: 全文播放完毕，等待按 A 关闭对话框)
       │
   [按下 A 键]
       │
       ▼
对话结束，释放对话锁 (script_lock = 0)，窗口图层关闭，回到自由移动。
```

---

## 3. PixelData 栅格 Oracle 确凿证据

* **分辨率与格式**：`240 × 32` 像素，`4bpp`（4 位每像素），占用 Main RAM `3840` 字节（`0x023353C0` ~ `0x023362BF`）。
* **行物理划分**：
  * **Line 0（屏幕第一行）**：行号 0 ~ 15（偏移 `+0x000` ~ `+0x77F`，前 1920 字节）。
  * **Line 1（屏幕第二行）**：行号 16 ~ 31（偏移 `+0x780` ~ `+0xF00`，后 1920 字节）。
* **滚屏搬移实测**：
  在 EXP-C 的 Frame 5223423 ~ 5223426 连续 4 帧中，每帧搬移 4 像素（`4 × 120 = 480` 字节），将 Line 1 的点阵平移至 Line 0，彻底证实了**第二屏滚屏后，屏幕上保留的第一行正是上一屏的第二行“和１００个人”！**
