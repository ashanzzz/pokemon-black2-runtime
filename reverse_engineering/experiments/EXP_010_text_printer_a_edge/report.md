# TEST REPORT

## Goal

Validate an A-edge-correlated message state candidate, and begin the required reverse traversal toward the runtime ActorSystem without treating loaded text as visible output.

## Hypothesis

`0x02332C4C` and nearby state could identify the current message sequence; a PlayerActor root may be found by reverse pointer search.

## Method

Read only Main RAM through BizHawk's `memory.read_batch`: all ranges in each snapshot are one frame. The input is a one-frame A press; the following sample is a settled observation, not a per-frame capture.

## Actions performed

- `snapshot_after_interaction_start.json` — frame `3234823`
- injected `A` for one frame
- `snapshot_after_one_a_from_first.json` — frame `3234899`

## Memory ranges

- `0x02247500–0x022476FF`: Script/message state
- `0x022490A0–0x0224929F`: loaded MsgBuffer
- `0x0231FCB0–0x0231FD2F`: rejected printer candidate
- `0x02332C00–0x02332CFF`: pointer candidates
- `0x0223DE00–0x0223DEFF`: player actor candidate

## Candidate addresses

- `0x0231FCC8 = 0x00327073`, which is not a pointer into Main RAM.
- `0x0231FCEA = 0x3000`, inconsistent with a dialogue cursor Y position.
- `0x02332C4C`: `0x022490C8 → 0x022490EC` across the A-edge sample.
- `0x02332C3C`: `1 → 0` across the same sample.

## Raw observations

- The loaded buffer contains `科学的力量真是惊人!`, `现在可以用通信和100个人`, and `同时游戏!` as separate fragments; `0x022490C8` and `0x022490EC` are the starts of the latter two fragments.
- The middle fragment contains `0xFFFE` controls. Its line break cannot be inferred by mapping every control word to newline.
- Whole-Main-RAM reverse-pointer searches produced zero references to `0x0223DE00` and `0x02143620`.
- A broad SWAN `FieldActor` layout scan yielded 3381 candidates, so it has no discriminatory value without the ActorHeap root.

## SWAN correspondence

- `field_mmodel.h` remains relevant to actor discovery, but SWAN supplied no verified TextPrinter layout for this ROM build.

## Supporting and opposing evidence

- Support: `0x02332C4C` moves exactly between MsgBuffer fragment starts when A is injected.
- Opposing: the sampled timing is settled rather than per-frame, so this movement does not identify the displayed Window page.
- Opposing: claimed `0x0231FCB0` pointer/cursor offsets remain structurally incompatible with an active TextPrinter.

## Confidence

- `0x02332C4C` message-sequence pointer: candidate.
- `0x0231FCB0` as active TextPrinter: rejected for this base.
- MsgBuffer fragment extraction: verified loaded data.
- Current visible lines, print state, and speaker actor: unresolved.

## Verified fields

- The A input was delivered and `0x02332C3C/4C` changed in the following atomic snapshot.

## Unresolved fields

- Active TextPrinter object, Window binding, visible top-line index, current glyph pointer, ScriptWork → FieldActor relation, ActorSystem root, speaking actor.

## Files changed

- `tools/runtime_memory_discovery.py` (the per-edge command now takes `--label` and preserves repeated captures)
- `docs/swan_runtime_schema.md`
- `first_to_next_diff.json` and atomic snapshots
- `bridge/bizhawk/black2_bridge.lua`: prepared a bridge-owned A-edge capture; it is not active until the Lua script is reloaded in BizHawk.
- `backend/black2/bizhawk/bridge_client.py` and `backend/black2/api/app.py`: prepared the corresponding API endpoints; they are not active until the local API is restarted.

## Next recommended experiment

Reload the prepared bridge probe and local API, then run `tools/runtime_memory_discovery.py --experiment reverse_engineering/experiments/EXP_011_message_consumer_binding a-edge-capture --label page1_to_page2 --sample-frames 120` while page 1 is waiting for input. Repeat from the same savestate with `--label page2_to_page3` while page 2 shows `现在可以用通信` / `和100个人`. Each capture includes before-edge and every individual post-edge frame in the BizHawk Lua loop. The current range set includes only observed message/printer candidates; Window, ScriptWork, and ActorSystem ranges remain deliberately absent until their roots are discovered. Then stop for review.
