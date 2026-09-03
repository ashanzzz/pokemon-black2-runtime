# EXP_011 — message consumer binding

## User-confirmed visual checkpoints

The controlled dialogue has three pages. Page 2 shows two lines, `现在可以用通信` and `和100个人`; page 3 shows one line, `同时游戏！`. These are operator annotations used to gate the experiment. RAM evidence must still establish the corresponding source interval and drawing state.

## Preparation

1. Restart the local `run_runtime.py` API so `/api/dev/a_edge_capture` appears in its OpenAPI document.
2. Stop the old BizHawk Lua bridge instance and reload `bridge/bizhawk/black2_bridge.lua`.
3. Put the game at page 1 after printing has stopped and the advance indicator is waiting. Release A/B and clear any pending bridge input. Save a reversible BizHawk savestate here.

## Capture sequence

From the project root, run:

```powershell
.\.venv\Scripts\python.exe tools\runtime_memory_discovery.py --experiment reverse_engineering\experiments\EXP_011_message_consumer_binding a-edge-capture --label page1_to_page2 --sample-frames 120
```

Do not press anything while it runs. It injects one A edge and stores `before_edge` plus 60 individual post-edge samples. When it finishes, the game should be waiting on page 2. Run immediately:

```powershell
.\.venv\Scripts\python.exe tools\runtime_memory_discovery.py --experiment reverse_engineering\experiments\EXP_011_message_consumer_binding a-edge-capture --label page2_to_page3 --sample-frames 120
```

Again, do not add input. The second artifact should finish with page 3 visible. The tool creates `a_edge_capture_page1_to_page2.json` and `a_edge_capture_page2_to_page3.json`; repeated runs receive a numeric suffix instead of overwriting evidence.

## Acceptance criteria

- Each artifact contains bridge frame numbers for `before_edge` and every post-edge sample.
- The two transitions show different changes in the message sequence candidate, printer candidate region, and active flags.
- Any TextPrinter/Window claim must be backed by a field or pointer that changes at the transition and remains coherent across the whole sample.
- No visible line or speaker is promoted from MsgBuffer text alone.

## Scope

This experiment resolves page/control timing first. It does not yet claim a speaker actor. After the consumer is bound, a separate capture will follow `ActorMsg` or `ParentActorMsg` to the live `FieldActorSystem` entry and verify UID, ZoneID, SCRID, GPos, WPos, and facing together.
