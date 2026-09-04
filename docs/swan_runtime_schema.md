# Pokémon Black 2 - Runtime Schema: verified observations and SWAN hypotheses

2026-09-03 source review: see [TextPrinter / Window / speaker review](../reverse_engineering/reports/text_printer_source_review_20260903.md). The loaded ROM hash matches the local `IREJ`, revision 1 ROM. SWAN's IRDO/IREO addresses require relocation and current-ROM verification.

## TextPrinter candidate (`0x0231FCB0`)

`EXP_008` found useful correlations at this location, but did not capture an
atomic A-edge / scroll sequence. `EXP_009` re-read the same region, associated
with a separate state response at frame `3183825`, and found that `+0x18` was `0x00327073`, not a Main-RAM message
pointer, while `+0x3A` was `0x3000`. Therefore this base must **not** be used
as the active `TextPrinter` in the Runtime API yet.

This rejects the proposed layout for that observation, not every historical or future allocation at this address. Object destruction/reuse, a shifted base, wrong field types, and reader errors must be distinguished. EXP_009's original artifact records non-atomic HTTP reads.

| Field | Offset | Type | Verified Address | Stage 1 Value | Stage 2 Value | Meaning | Status |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- | :---: |
| `current_line` | `+0x04` | `u16` | `0x0231FCB4` | `1` | `2` | former line-index correlation | **candidate** |
| `curr_char_ptr` | `+0x18` | `u32` | `0x0231FCC8` | `0x022490D8` | `0x022490E4` | former source-pointer correlation; contradicts EXP_009 raw read | **rejected for EXP_009 interpretation** |
| `line_pixel_y` | `+0x22` | `u16` | `0x0231FCD2` | `0 px` | `16 px` | former scroll correlation | **candidate** |
| `cursor_pixel_x`| `+0x38` | `u16` | `0x0231FCE8` | `8 px` | `78 px` | former cursor correlation | **candidate** |
| `cursor_pixel_y`| `+0x3A` | `u16` | `0x0231FCEA` | `0 px` | `16 px` | former cursor correlation; EXP_009 observed `0x3000` | **rejected for EXP_009 interpretation** |

---

## Variable Tag & String Buffer (`MsgBuffer`)

Located around `0x022490A0 ~ 0x02249200`.

| Field | Offset | Type | Verified Address | Value | Meaning | Status |
| :--- | :---: | :---: | :---: | :---: | :--- | :---: |
| `line1_text` | `+0x0C` | `UCS-2` | `0x022490AC` | `你要也和宝可梦在一起的话` | loaded message fragment, not a proven visible line | **verified loaded data** |
| `var_tag_rival` | `+0x2E` | `u32` | `0x022490CE` | historical `Tag 0x01` / name "NO" observation | generic tag-to-WordSet-slot mapping needs validation; current parser hardcodes NO | **candidate mapping** |
| `line2_text` | `+0x36` | `UCS-2` | `0x022490D6` | `比试一下了。` | loaded message fragment, not a proven visible line | **verified loaded data** |

## Text visibility rule

`MsgBuffer` and a pointer into it can show text that is preloaded for a later
page. Until an active printer and its Window/scroll state are proved by a
frame-bounded experiment, Semantic API fields `visible_lines`,
`printed_chars`, `waiting_for_input`, and `speaker_actor` remain `unresolved`.

## Message-sequence candidate (`0x02332C00` region)

`EXP_010` used `memory.read_batch` to obtain ranges within one handler before and
after an injected one-frame A press. Its HTTP route dropped the bridge frame,
so `frame_from_state` is not the exact RAM frame. The following observations are useful,
but are deliberately not named TextPrinter fields:

| Address | Observation | Confidence |
| :--- | :--- | :---: |
| `0x02332C4C` | held `0x022490C8`, then `0x022490EC`; these are continuation positions after CLEAR/LF and SCROLL/LF in the same observed EOS-terminated sequence | candidate |
| `0x02332C3C` | changed `1 → 0` across that A-edge sample | candidate |
| `0x02332C38` | changed in a different terminal-page A sample | hypothesis |

This establishes a message-sequence correlation only. It does not establish
which line is in the Window rectangle, the glyph progress, scroll distance, or
the actor executing the script.

### EXP_012 manual checkpoint result

The five manual checkpoints were captured with one atomic `memory.read_batch`
per click. This resolves the role of the source pointer more narrowly:

| Checkpoint | Bridge frame | `script_msg_active` (`0x02247546`) | `0x02332C38` | `0x02332C3C` | `0x02332C4C` |
| --- | ---: | ---: | ---: | ---: | --- |
| before dialogue | 3499974 | 0 | 2 | 0 | `0x022490F6` (`FFFF`) |
| page 1 wait | 3500605 | 1 | 1 | 1 | `0x022490C8` (start of `现在…`) |
| page 2 wait | 3501115 | 1 | 1 | 0 | `0x022490EC` (start of `同时…`) |
| scroll overlap (`和100个人` / `同时游戏！`) | 3501307 | 1 | 2 | 0 | `0x022490F6` (`FFFF`) |
| dialogue end | 3501737 | 0 | 2 | 0 | `0x022490F6` (`FFFF`) |

The loaded stream at `0x022490A0` is one continuous sequence:
`科学的力量真是惊人！`, `F000 BE01 0000 FFFE`, `现在可以用通信`, `FFFE`,
`和１００个人`, `F000 BE00 0000 FFFE`, `同时游戏！`, `FFFF`. The raw digits
are fullwidth `FF11 FF10 FF10`; the source bytes must not be normalized.

`0x02332C4C` therefore has **probable** status as a repeatable message-consumer
continuation pointer: it advances to the next unread/control boundary, and at
the overlap checkpoint it has already reached EOS while the previous line is
still visually retained by the scroll operation. It is **not** a visible-line
index and does not by itself identify the Window contents. `0x02332C38` is a
**candidate** phase/status field (`1` at settled page waits, `2` at overlap and
inactive states); its enum meaning is unresolved. The previously proposed
`0x0231FCB0` printer base was byte-identical across all five checkpoints and is
rejected as the active printer for this run.

## Rejected historical PlayerActor candidates

At the earlier captured state, full Main-RAM reverse-pointer searches found
zero pointers to `0x0223DE00` and to the position mirror `0x02143620`. Neither
is a PlayerActor root and neither may be used by the Runtime API. A broad SWAN
FieldActor signature scan produced 3381 unvalidated matches; those are not
3381 individually proven actors.

This historical negative result is superseded for the current session by the
explicit pointer closure recorded in EXP_015 below, not by reinterpreting the
old addresses.

## Gen 5 control program recovered from the historical EXP_010 bytes

The source-confirmed Gen 5 format is `F000, command_id, argument_count, args...`,
with `FFFE` for LF and `FFFF` for EOS. The corresponding historical bytes are:

| Address | Words | Meaning | Confidence |
| --- | --- | --- | --- |
| `0x022490C0` | `F000 BE01 0000 FFFE` | CLEAR with zero arguments, then LF | probable format interpretation |
| `0x022490D6` | `FFFE` | LF between the two middle text runs | probable format interpretation |
| `0x022490E4` | `F000 BE00 0000 FFFE` | SCROLL with zero arguments, then LF | probable format interpretation |
| `0x022490F6` | `FFFF` | EOS of the observed continuous sequence | probable format interpretation |

The underlying word values are verified historical data; current-ROM execution
semantics and current visibility remain unresolved. `0000` here is an argument
count, not a separator. `0001` alone is not enough to resolve a WordSet variable.
The raw numeric glyph codes are fullwidth `FF11 FF10 FF10`; display normalization
must not overwrite the source codepoints.

## EXP_015 current-session PlayerActor closure

`reverse_engineering/experiments/EXP_015_field_actor_system/report.md` records
the raw artifacts and exact bridge frames.  At frame `4262774`, the following
same-frame relation held:

```text
0x0223B4C0 +0x20 -> 0x02263520   GameSystem -> Field candidate
0x02263520 +0x40 -> 0x0223DB68   Field -> ActorSystem candidate
0x02263520 +0x94 -> 0x02324764   Field -> FieldPlayer candidate
0x02324764 +0x04 -> 0x023247A0   FieldPlayer -> Core candidate
0x023247A0 +0x1C -> 0x0223E4E4   Core -> PlayerActor
0x0223E4E4 +0x88 -> 0x0223DB68   PlayerActor -> ActorSystem
0x0223DB68 +0x1C -> 0x0223DBE4   ActorSystem -> ActorHeap
0x0223E4E4 = ActorHeap +0x900    slot 9, UID 255
```

The terminal actor passed controlled behavior tests: `Right:8` changed
`GPos.X 39→40` and `WPos.X 0x00278000→0x00288000`; `Left:8` returned both;
`Up:8` changed `GPos.Z 763→762` and `WPos.Z 0x02FB8000→0x02FA8000`; a second
`Down:8` returned both. This verifies the **current-session PlayerActor
identity** and X/Z grid/world behavior, not a permanent allocator address.

The active dialogue capture at `f5072555` additionally observed
`ScriptWork candidate 0x0224758C +0x08 = 0x0223DCE4`, an ActorHeap slot with
`UID=4`, `SCRID=13`, and GPos `(39,1,764)`. Current IREJ OVL_12 has a direct
getter/setter for the `+0x08` storage slot; see
`reverse_engineering/reports/EXP_014_scriptwork_parent_actor_source_binding_20260903.md`.
This is a **probable script parent actor**, not yet a verified final speaker.

## EXP_016 candidate BmpWin / GFLBitmap draw-target chain

`reverse_engineering/experiments/EXP_016_text_window_binding/report.md`
documents two exact-frame A-edge captures for the current continuous dialogue
stream. The following is a **candidate-control observation window**, not a
validated `TextPrinter` base or public visible-text layout:

```text
0x02332C20 +0x20 -> 0x02332B8C  (BmpWin-shaped candidate)
0x02332C20 +0x24 -> 0x02332BC8  (GFLBitmap-shaped candidate)
0x02332B8C +0x0C -> 0x02332BC8  (independent cross-link)
0x02332BC8 +0x00 -> 0x023353C0  (PixelData candidate)
0x02332BC8 +0x04/+0x06 = 240/32 (width/height candidates)
```

The `0x023353C0..0x023362BF` 4bpp-sized span was captured in full. Its bytes
changed when the source-consumer candidate advanced and, on the second A edge,
when the candidate scroll field stepped `0 -> 4 -> 8 -> 12 -> 16`. These
relations make the BmpWin/Bitmap/pixel-target chain **probable for this
layout**. They do not establish Window ownership, VRAM transfer, glyph
identity, visible lines, TextPrinter state enum, or input readiness. The
semantic API must continue to publish those as `unresolved` until a bounded
writer-PC / draw-flush trace proves the path.

## Source-derived root layouts for the next experiment

All offsets below are SWAN hypotheses for this ROM, derived from commit
`4324f73a7659353a21bf4c523905c5d09cf6a066` with 32-bit pointers. The source review
contains the symbol migration table and exact file links.

| Structure | Field | Offset | Type | Meaning | Runtime/Static | Verified |
| --- | --- | --- | --- | --- | --- | --- |
| GameSystem | m_Field | +0x20 | pointer | field root | Runtime | probable; EXP_015 closure |
| Field | m_MsgBGSys | +0x28 | pointer | message system | Runtime | SWAN hypothesis |
| Field | m_ActorSystem | +0x40 | pointer | actor manager | Runtime | probable; EXP_015 closure |
| Field | m_Player | +0x94 | pointer | player controller | Runtime | probable; EXP_015 closure |
| FieldPlayer | m_Core | +0x04 | pointer | player core | Runtime | probable; EXP_015 closure |
| FieldPlayerCore | Actor | +0x1C | pointer | player actor | Runtime | verified current-session closure |
| FieldActorSystem | ActorCapacity / ActorCount | +0x04 / +0x06 | u16 | allocation capacity / count | Runtime | probable; coherent in EXP_015 |
| FieldActorSystem | ActorHeap | +0x1C | pointer | actor array, not +0x18 | Runtime | probable; coherent in EXP_015 |
| FieldActorSystem | m_Field | +0x40 | pointer | Field candidate | Runtime | probable; coherent in EXP_015 |
| FieldActor | GPos | +0x3C | u16,s16,u16 | runtime grid position | Runtime | verified for current PlayerActor X/Z |
| FieldActor | WPos | +0x44 | VecFx32 | runtime continuous position | Runtime | verified for current PlayerActor X/Z |
| FieldActor | m_ActorSystem | +0x88 | pointer | manager backlink | Runtime | probable; coherent in EXP_015 |
| GFLBitmap | PixelData | +0x00 | pointer | pixel surface | Runtime | SWAN hypothesis |
| candidate BmpWin-shaped object | Bitmap | +0x0C | pointer | points to same Bitmap candidate | Runtime | probable for EXP_016 layout; type name remains hypothesis |
| candidate GFLBitmap-shaped object | PixelData / width / height | +0x00 / +0x04 / +0x06 | pointer / u16 / u16 | `0x023353C0`, `240`, `32` in observed layout | Runtime | probable for EXP_016 layout; not active Window proof |
| candidate control observation | scroll byte | +0x37 | u8 | observed `0,4,8,12,16` during one scroll | Runtime | candidate field with probable correlation; not generic TextPrinter enum |

Gen 5 speaker discovery should investigate `s003C_ActorMsg`,
`s003D_ParentActorMsg`, `ScriptWork_GetParentActor`, and the current actor lookup.
A function name alone does not establish whether its return is an actor ID,
handle, or pointer. Keep interaction target, script parent, and message actor
separate until those relations are proved.
