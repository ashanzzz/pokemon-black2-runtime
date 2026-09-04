# TEST REPORT — EXP_012 手动对话检查点与连续消息消费者

## Goal

用操作者确认的屏幕状态区分：第 1 页、第 2 页、滚屏期间的行保留，以及最后一次 A 后的对话结束；判断 `0x02332C4C` 是否代表当前屏幕可视行。

## Hypothesis before capture

如果 `0x02332C4C` 是屏幕当前行指针，它应当停留在当前可视文本附近；如果它只是消息消费者续读位置，则它可能已经指向下一页甚至 EOS，而窗口仍保留上一行进行滚屏。

## Actions and evidence

用户在同一 NPC 对话中按游戏内 A，并在画面达到人工标记状态后点击网页按钮。每个点击执行一次 BizHawk Lua bridge `memory.read_batch`，没有截图、OCR、RAM 写入或网页按键注入。

| Label | Frame | Frame delta | 操作者确认 |
| --- | ---: | ---: | --- |
| `before_dialogue` | 3499974 | — | NPC 面前，对话前 |
| `page1_wait` | 3500605 | 631 | `科学的力量真是惊人！` |
| `page2_wait` | 3501115 | 510 | `现在可以用通信` / `和100个人` |
| `scroll_overlap` | 3501307 | 192 | `和100个人` / `同时游戏！` 同时可见 |
| `dialogue_end` | 3501737 | 430 | 最后一次 A 后对话结束 |

Captured ranges (Main-RAM offsets) were `0x247500–0x2476FF`,
`0x2490A0–0x24929F`, `0x31FCB0–0x31FD2F`, `0x332C00–0x332CFF`, and
`0x23DE00–0x23DEFF`. The raw JSON files are in this experiment directory.

## Raw message stream

At `0x022490A0` the same bytes are present at all five checkpoints. Interpreting
the 16-bit words only as the already documented Gen 5 stream gives:

```text
0x022490AC  科学的力量真是惊人！
0x022490C0  F000 BE01 0000 FFFE       CLEAR + LF
0x022490C8  现在可以用通信
0x022490D6  FFFE                    LF
0x022490D8  和１００个人              (raw FF11 FF10 FF10 digits)
0x022490E4  F000 BE00 0000 FFFE       SCROLL + LF
0x022490EC  同时游戏！
0x022490F6  FFFF                    EOS
```

This is one preloaded continuous message, not five independently loaded
screens. The fullwidth digits are preserved as source codepoints.

## Candidate observations

### Message consumer region (`0x02332C00`)

`0x02332C4C` (u32) held these exact values:

```text
before       0x022490F6 (EOS)
page1        0x022490C8 (start of 现在…)
page2        0x022490EC (start of 同时…)
scroll       0x022490F6 (EOS)
dialogue_end 0x022490F6 (EOS)
```

The pointer advances to the next unread/control boundary. At the user-confirmed
scroll-overlap frame it is already at EOS even though `和100个人` remains in the
window while `同时游戏！` is shown. This supports **probable: continuation
pointer**, and rejects **visible current line pointer**.

Other correlated fields:

| Address | Values in order | Confidence | Interpretation |
| --- | --- | --- | --- |
| `0x02332C38` | `2, 1, 1, 2, 2` | candidate | phase/status-like field; `1` settled page waits, `2` overlap/inactive; enum unresolved |
| `0x02332C3C` | `0, 1, 0, 0, 0` | candidate | first-page-only latch or transition flag; meaning unresolved |
| `0x02332C14` | `0x02335394, 0x02332BD4, 0x02332BD4, 0x02332BD4, 0x02335394` | candidate | active message context pointer; identity/structure not yet verified |
| `0x02332C48` | `0, 0x0233535C, 0x0233535C, 0x0233535C, 0` | candidate | active-context auxiliary pointer; meaning unresolved |

### Script/message active flag (`0x02247546`)

This byte was `0` before the conversation, `1` at page 1/page 2/scroll
overlap, and `0` after the final A. It is **verified for this controlled
sequence as an active-dialogue correlation**, but it does not encode the
visible line or speaker actor by itself.

### Former printer candidate (`0x0231FCB0`)

All 128 bytes were identical in all five checkpoint files. This base cannot be
the active per-page TextPrinter state for this run. It remains possible that a
different allocation or interior object is used; no global TextPrinter layout
is promoted from this range.

### Player actor candidate (`0x0223DE00`)

Only a few bytes changed and no coherent position/face/movement structure was
demonstrated. This range does not identify the speaking NPC.

## Conclusion

The user’s correction is confirmed: there is no independently stable third-page
checkpoint in this run. `同时游戏！` is rendered as the post-SCROLL text while
the previous bottom line can remain visible during the overlap; the final A
then clears the active dialogue state. The correct parser model is therefore:

```text
page2 wait → SCROLL transition (old bottom line retained + new text) → EOS/end
```

The parser must not use `currentChar`/continuation alone to enumerate visible
rows. It needs a validated phase/scroll state plus the actual Window pixel or
tile backing store (or a proven draw/scroll callback) to distinguish overlap
from settled output.

## Repeatability check

The operator repeated the same five checkpoints in a second run. The second
frames were `3521254` (before), `3521950` (page 1), `3522488` (page 2),
`3522684` (scroll transition), and `3522835` (dialogue end). Labels, pointer
values, script active values, phase values, and all message/printer ranges were
identical to the first run. Differences between corresponding runs were limited
to a few bytes in the unverified player-actor candidate range, consistent with
unrelated runtime activity. This upgrades the continuation-pointer and active
flag correlations from a one-off observation to **probable/repeatable** for this
dialogue sequence; it still does not make them a visible-row or speaker-actor
proof.

## Verified fields

- Atomic frame-stamped raw checkpoint capture works.
- `0x02247546` active flag correlates with dialogue lifetime in this sequence.
- `0x02332C4C` is a strong continuation-pointer candidate with page/EOS values.
- The continuous message/control stream and exact control boundaries above.

## Unresolved fields

- Enum meanings of `0x02332C38` and `0x02332C3C`.
- Actual Window backing surface and scroll-progress field.
- Real TextPrinter object/base and glyph progress.
- Script command/parent actor to live `FieldActorSystem` mapping and speaking NPC.

## Files changed

- `docs/swan_runtime_schema.md` (EXP_012 findings)
- `reverse_engineering/reports/EXP_012_manual_dialogue_checkpoints_report.md`
- Existing raw checkpoint JSON files in `reverse_engineering/experiments/EXP_012_manual_dialogue_checkpoints/`

## Next recommended experiment

Repeat only the transition from page 2: save a savestate at `page2_wait`, then
capture every emulator frame for 60–120 frames while pressing one A edge. Add
read-only ranges around the active context target `0x02332BD4` and the script
pointer targets near `0x022474E4`, `0x02247560`, and `0x0224A450`. The goal is to
bind the phase field and then reverse from the message command/parent actor to
the live actor heap. Do not infer an NPC from the current loaded text.
