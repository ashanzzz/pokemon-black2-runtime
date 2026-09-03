# TEST REPORT

## Goal

Revalidate candidate TextPrinter fields from RAM without treating preloaded message text as visible output.

## Hypothesis

`0x0231FCB0` is an active TextPrinter whose cursor and source pointer describe the currently visible dialogue.

## Method

Read only Main RAM through the BizHawk bridge. The capture is non-atomic; it can reject a bad candidate but cannot verify a state transition.

## Actions performed

- `snapshot_current_after_input.json` — frame `3183825`, label `current_after_input`

## Memory ranges

- `0x02247500–0x022476FF`: Script/message state
- `0x022490A0–0x0224929F`: loaded MsgBuffer
- `0x0231FCB0–0x0231FD2F`: rejected printer candidate
- `0x02332C00–0x02332CFF`: pointer candidates
- `0x0223DE00–0x0223DEFF`: player actor candidate

## Candidate addresses

- `0x0231FCC8 = 0x00327073`, which is not a pointer into Main RAM.
- `0x0231FCEA = 0x3000`, inconsistent with a dialogue cursor Y position.
- `0x02332C4C = 0x022490EC`, a pointer to a preloaded final text fragment; it is not evidence that the fragment is visible.

## Raw observations

- The loaded buffer includes `科学的力量真是惊人!`, `现在可以用通信和100个人`, and `同时游戏!` as separate fragments.
- The middle fragment contains `0xFFFE` controls. Its line break cannot be inferred by mapping every control word to newline.

## SWAN correspondence

- `field_mmodel.h` remains relevant to actor discovery, but SWAN supplied no verified TextPrinter layout for this ROM build.

## Supporting and opposing evidence

- Support: historical EXP_008 values varied across samples.
- Opposing: the current raw bytes at the claimed pointer/cursor offsets are structurally incompatible with those interpretations.

## Confidence

- `0x0231FCB0` as active TextPrinter: rejected for this base.
- MsgBuffer fragment extraction: verified loaded data.
- Current visible lines, print state, and speaker actor: unresolved.

## Verified fields

- None added by this report. A candidate requires controlled stage-transition correlation before promotion.

## Unresolved fields

- Active TextPrinter object, Window binding, visible top-line index, current glyph pointer, script context / speaking actor.

## Files changed

- `tools/runtime_memory_discovery.py`
- `docs/swan_runtime_schema.md`
- dialogue decoder/state schema (visible-text claims are now unresolved until proven)

## Next recommended experiment

Capture a frame-bounded A-edge sequence (before edge, after one frame, each scroll frame, settled wait) with raw Message, candidate printer, Window, ScriptWork, and ActorSystem ranges in one bridge batch. Then stop for review.
