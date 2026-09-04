# TEST REPORT — RAM export all readable domains

## Goal

Verify that one read-only export contains the game screenshot and every non-ROM memory domain exposed by the current BizHawk session.

## Method

- Queried `memory.domains` immediately before `memory.dump_universal`.
- Excluded only `ROM` (the game file) and zero-byte System Bus aliases.
- Captured all selected domains and the native screenshot in the same bridge request.
- Rechecked every file size on disk and included SHA-256 values in `memory_domains.json`.
- Inspected the resulting ZIP member list.

## Result

Physical dump frame: `5234664`.

Validated ZIP members include `screen.png`, `main_ram.bin`, `arm7_bios.bin`, `arm7_wram.bin`, `arm9_bios.bin`, `dtcm.bin`, `firmware.bin`, `itcm.bin`, `sram.bin`, `shared_wram.bin`, and `waterbox_pagedata.bin`, plus the JSON evidence/index files.

`memory_domain_inventory.json` recorded the only exclusions:

- `ROM` — intentionally excluded game file.
- `ARM7 System Bus` and `ARM9 System Bus` — zero-byte virtual aliases with no independent range.

The ZIP download endpoint now reopens the ZIP before serving it, checks its
CRC, requires `screen.png` plus every expected artifact, and compares each
member SHA-256 to `integrity.json`. The tested archive returned HTTP 200 with
`application/zip`, 21 verified members, and a valid attachment filename.

## Semantic boundary

The complete raw evidence package does **not** imply that every runtime concept is already decoded. Player `FieldActor`, ActorHeap/NPC current positions and names, camera, terrain/collision, battle, and trainer/party fields remain `unresolved` until their pointer chains and structures are verified. Their bytes are nevertheless preserved in the raw domain files.

## Files changed

- `backend/black2/state/universal_snapshot_manager.py`
- `backend/black2/api/app.py`
- `backend/black2/state/engine.py`
- `frontend/ram-dumper.html`
- `bridge/bizhawk/black2_bridge.lua`
- `tests/test_universal_snapshot_manager.py`

## Verification

`python -m unittest tests.test_universal_snapshot_manager -q` passed.
