# v10.1 Dialogue / World Runtime Fix Test Report

Base GitHub commit: `7bd1de29a8ead9fdbd5c8e565ae7ad9728f7cddc`

## Automated checks

- Python syntax / bytecode compile: PASS
  - `dialogue_object_resolver.py`
  - `dialogue_runtime_decoder.py`
  - `state/engine.py`
  - `runtime_actor_overlay.py`
  - new dialogue regression test
- JavaScript syntax: PASS for `world3d-runtime-fixed.js`
- JSON and Workbench import-map parse: PASS
- New synthetic dialogue tests: 2 PASS
  - runtime ScriptWork/talkmsgwin/TCBL chain resolution
  - WAIT_PAGE next-page non-leak
- Cache-first fake-Bridge replay of supplied Main RAM: PASS
  - bounded rediscovery resolves the same ScriptWork/talkmsgwin/TCBL chain
  - steady-state cached sample requests 5,492 bytes, not 4 MiB
  - cached resample preserves SourceCursor `0x022490D6` and ParentActor GPos `(4,0,5)`
- Supplied physical Main RAM replay: PASS
  - active ScriptWork `0x0224758C`
  - talkmsgwin `0x02321DDC`
  - TCBL `0x02324240`
  - PixelData `0x023232F4`
  - visible page = first two lines only
  - PixelData line oracle match = true
  - ParentActor = `0x0223DBE4` / Model 251
- Actor scene-membership policy replay: PASS
  - Slot 0 raw Zone 428 at GPos `(4,0,5)` => current scene PROBABLE
  - Slot 2 raw Zone 0 at GPos `(5,0,6)` => current scene CANDIDATE
  - a raw-Zone-0 actor outside current 1x1/32-tile mapper bounds => not current scene
- Clean-artifact check: PASS, no `__pycache__` / `.pyc` in package.

## Evidence-backed status

- VERIFIED: supplied RAM resolves Player GPos `(5,0,5)`, WPos `(88,0,88)`, facing West.
- VERIFIED: supplied RAM resolves Slot 0 GPos `(4,0,5)`, WPos `(72,0,88)`.
- VERIFIED: supplied RAM resolves Slot 2 GPos `(5,0,6)`, WPos `(88,0,104)`.
- VERIFIED for supplied dialogue: current page contains only the first two lines and next-page text is not visible yet.
- VERIFIED for supplied dialogue: PixelData is 240x32 and agrees with the two-line visible reconstruction.
- PROBABLE: ScriptWork ParentActor is the speaking actor for this field-message path. The project report EXP_014 verifies the ParentActor accessor; generic cross-dialogue speaker semantics remain conservative.
- CANDIDATE: Slot 2 belongs to the current scene despite raw Actor.ZoneID = 0. This is deliberately not rewritten to VERIFIED Zone 428.
- CANDIDATE: restoring original glTF material sidedness fixes the Workbench visual crowding / backface selection issue. Live browser validation is still required.

## Tests not claimed

This environment cannot run the user's actual BizHawk instance or browser at `127.0.0.1:8765`. Therefore no claim is made that the real-time UI has been VERIFIED on the user's PC.

The connected GitHub app has read/pull permission but no push permission, and this runtime cannot directly download GitHub's binary zipball into the local container. Therefore the delivered ZIP is an exact-base overlay plus a materializer for a full Git working copy, not a falsely labeled copy of all untouched remote files.
