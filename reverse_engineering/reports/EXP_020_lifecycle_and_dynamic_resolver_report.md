# TEST REPORT — EXP_020: 动态对象解析与完整对话生命周期复现

日期：2026-09-03。类型：实机 RAM Exact-Frame 采样、GFL 堆结构逆向与完整生命周期捕获。无 OCR，无写 RAM。

---

## 1. 核心突破：GFL Heap 分配器与动态对象定位 (Dynamic Object Resolution)

彻底消除了对 `0x02332C20`（TCBL）和 `0x023353C0`（PixelData）偶然绝对地址的依赖：

### 1.1 GFL 堆块管理结构 (Heap #21 / HEAPID_FIELD)
通过在 Main RAM 中对堆链表的逆向，证实了 Game Freak 官方引擎的动态内存管理头格式：
```text
struct GFL_HeapBlock {
    u32 magic;          // 0x00005544 ('UD')
    u32 payload_size;   // 堆块字节大小
    struct GFL_HeapBlock *prev;
    struct GFL_HeapBlock *next;
    u32 heap_flags;     // 低 16 位为 HeapID (如 0x15 = Heap 21)
    char source_tag[8]; // 分配源码标签 (如 "tcbl.c\0\0", "bmp.c\0\0\0")
    u16 line_number;
    u16 reserved;
    u8  payload[];      // 用户可用数据
};
```
在 `Field` 局部堆中：
* 带有 **`"tcbl.c"`** 源码标签的活跃块（大小 `0x6C`）即为 **`TCBL`（TextControlBlock）**。
* 带有 **`"bmp.c"`** 源码标签的活跃块（大小 `0xF1C`）即为 **`PixelData`（3840 字节渲染表面）**。

### 1.2 动态指针解析链 (Authoritative Dynamic Chain)
```text
Field (0x02263520) / ScriptWork (0x0224758C)
        │
        ├── +0x30 ──> StrBuf (0x022490A4 -> +0x08 字符数组 0x022490AC)
        └── +0xCC ──> Field Local Heap (#21)
                          │
                          ▼
              GFL_HeapBlock ("tcbl.c")
                          │
                          ├── +0x18: Phase (u32: 0=Printing, 1=WaitPage, 2=WaitEOS)
                          ├── +0x1C: FirstPageLatch (u32: 1=FirstPage, 0=Page2+)
                          ├── +0x20: BmpWin 指针 (0x02332B8C)
                          ├── +0x24: GFLBitmap 指针 (0x02332BC8) ──> PixelData (0x023353C0)
                          └── +0x2C: SourceCursor 指针 (0x022490XX)
```

---

## 2. 字段数据类型确证 (Data Width Disambiguation)

消除先前报告中的“`u32 / u8`”二义性：
* **`printer_phase`**：严格 **`u32`**（4 字节对齐，高 3 字节始终为 0，小端序）。
* **`first_page_latch`**：严格 **`u32`**（4 字节对齐，高 3 字节始终为 0，小端序）。
* **`source_cursor`**：严格 **`u32`**（指向 ARM9 4MB Main RAM 地址空间的 32 位绝对指针）。

---

## 3. 完整生命周期时序复现 (EXP_020)

| 时间点 (Frame) | 触发事件 | 硬件锁 / 状态机 | PixelData 表现 | 语义解释 |
| :--- | :--- | :--- | :--- | :--- |
| **5219048 ~ 5219050** | 空闲待机 | `Lock = 0` | 存留关闭前的历史脏数据 | **`IDLE`**（必须以硬件锁为门控，防止脏数据泄露） |
| **5219051** | A-Edge 按下 | `Lock = 1` | 维持 | 硬件脚本引擎接管，冻结玩家移动 |
| **5219054 ~ 5219055** | 窗口创建 | `Lock = 1` | `Diff = 1020` 显著变动 | Window 像素内存清空重置为背景底色 |
| **5219063** | 打印器任务启动 | `Phase = 0`, `Latch = 1`<br>`SrcCur = 0x022490AC` | 重置完成 | **`S_PRINTING`** 首屏启动，指向首字“科” |
| **5219064 ~ 5219100** | 逐字出字 | `Phase = 0`, `Latch = 1` | 每 4 帧产生 30~60 字节局部差分 | 每隔 4 帧消费一个字模并在 Line 0 光栅化 |
| **5219106** | 遇到 CLEAR 控制符 | `Phase = 1`, `Latch = 1`<br>`SrcCur = 0x022490C8` | 像素停止变动 | **`S_WAIT_PAGE`** 首屏出字结束，等待按 A 翻页 |

---

## 4. 细粒度置信度体系 (Granular Confidence Schema)

```json
{
  "confidence": {
    "source_buffer": "verified",
    "source_cursor": "verified",
    "printer_phase": "verified_for_tested_path",
    "line_reconstruction": "verified_for_tested_dialogue",
    "pixel_spatial_match": true,
    "resolver_lifecycle": "verified_reopen_and_idle",
    "cross_dialogue_generalization": "probable"
  }
}
```
已验证该闭环在单 NPC、单剧本、重开对话、关闭对话及空闲门控下的正确性；跨 NPC、跨地图及战斗系统的泛化支持当前标为 **`probable`**。
