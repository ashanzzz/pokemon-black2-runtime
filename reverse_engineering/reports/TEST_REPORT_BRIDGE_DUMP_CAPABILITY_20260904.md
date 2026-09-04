# TEST REPORT — Universal dump bridge capability mismatch

## Goal

Explain the `/ram-dumper` failure `Unknown operation: memory.dump_universal` without treating a failed export as game-data evidence.

## Method

- Read the live `/api/bizhawk/status` hello payload.
- Review `logs/bridge_transport.log` connection history.
- Compare the loaded bridge capability payload with `bridge/bizhawk/black2_bridge.lua`.
- Send one API preflight request after adding capability validation.  The request must not create an export when the capability is absent.

## Raw observations

- Current BizHawk process: `EmuHawk.exe`, PID `29336`, BizHawk `2.11.1`, NDS.
- Current TCP bridge session reports `bridge_version: 1.3.0-write-trace`.
- Its hello `capabilities` list has no `universal_dump` key.
- Transport logs show a previous `1.4.0-universal-dump` connection at approximately 11:24–11:53 on 2026-09-04, including successful `memory.dump_universal` requests.
- Transport logs then show `1.3.0-write-trace` connections from approximately 12:24 onward.
- The repository Lua source contains the `memory.dump_universal` handler, but an already-running Lua script is not hot-reloaded by editing the file.

## Finding

The first failure was a bridge-version/capability mismatch.  Follow-up live evidence showed a second release defect: bridge `1.5.0-universal-dump` advertised `universal_dump: true`, but its command dispatcher had no `memory.dump_universal` branch.  The error is therefore not a RAM-read failure, ZIP failure, or memory-domain discovery failure.

## Changes made

- Implemented the missing `memory.dump_universal` handler in `bridge/bizhawk/black2_bridge.lua`; it writes each requested raw domain, captures `screen.png`, returns register annotations, and logs the result.
- Updated the bridge to `1.5.1-universal-dump`; the API now requires this verified version as well as `universal_dump: true`.
- Added API preflight validation to `/api/dev/dump_full_ram`.  With an old bridge it now returns HTTP 409, states that no export was created, and includes the corrective action.
- Removed duplicate FastAPI route definitions for the same dump endpoint so there is one canonical export path.
- Added a static contract test so a Lua capability cannot be advertised without its registered operation handler.

## Verification

- `python -m py_compile backend/black2/api/app.py`: passed.
- `python -m unittest tests.test_universal_snapshot_manager -q`: passed.
- `git diff --check`: passed.
- Live preflight response with the current 1.3 bridge: HTTP 409 and `required_capability: universal_dump`; no dump was created.

## Required operator action

In BizHawk, open **Tools → Lua Console**, stop the currently loaded script, then open and run:

`D:\SynologyDrive团队\antigravity\宝可梦v2\bridge\bizhawk\black2_bridge.lua`

After it reconnects, `/api/bizhawk/status` must report `bridge_version: 1.5.1-universal-dump` and `hello.capabilities.universal_dump: true`.  Only then should the UI create a new evidence ZIP.

## Confidence

`verified` for the source of this specific error, based on the live hello capabilities, bridge transport logs, and capability-gated preflight response.

## Unresolved

Whether another copy of an older Lua script is being manually loaded by the operator remains unresolved.  A separate legacy bridge file exists outside this workspace, but this report does not assert it is the running source.
