# TEST REPORT — RAM dumper backlog and emulator performance

## Goal

Determine why the RAM dumper page reported `signal is aborted without reason` while the game became very slow, then save and verify a complete evidence ZIP without modifying game memory.

## Raw observations

- Live bridge reports `1.5.1-universal-dump` and `universal_dump: true`.
- The bridge transport log recorded `memory.dump_universal` from frame `5229431` to completion at `5229431` in roughly 0.116 seconds.  Therefore the Lua raw-domain write operation itself was not the sustained bottleneck.
- Before the fix, the same log showed repeated `memory.read` operations taking roughly 0.5–0.7 seconds each, continuously between captures.
- `RuntimeFieldLocator.sample_player()` performed a 4 MiB `read_main_ram()` discovery scan whenever a cached Field chain was unresolved.  The Runtime Hub called it from its background sampler.  Each scan is 32 chunks of 128 KiB and was retried after the discovery throttle, causing bridge command backlog.
- This violated the project rule that discovery scans must not run continuously in the background.

## Changes

- Background player sampling now returns `unresolved` when no cached Field chain exists; it cannot start RAM-wide discovery.
- Full Main RAM pointer discovery is now limited to the explicit `/api/v1/player/runtime` operator request.
- Background live-map fallback reads are disabled until an explicit operation requests them.
- Default semantic observer interval changed from 0.20 to 1.00 seconds.
- Ram-dumper browser request timeout changed from 30 to 120 seconds, so a valid archive cannot be aborted merely because packaging is slower on another machine.

## Verification

- After API restart, the emulator advanced `193` frames over three measured seconds (about 64.3 fps).
- Created snapshot `dump_20260904_141349_871950_f5233080_UNIVERSAL_EVIDENCE_stable-screen-capture` through the normal API in `0.849` seconds.
- Capture reports `complete: true`, `screenshot_saved: true`, `exported_domain_count: 10`, and no capture errors.
- Download endpoint returned HTTP `200`, `Content-Type: application/zip`, and a 1,717,669-byte archive.
- ZIP has 21 members, including `screen.png`, `main_ram.bin`, ARM7/ARM9 BIOS, DTCM, Firmware, ITCM, SRAM, Shared WRAM, Waterbox PageData, manifests, and integrity metadata.
- The RAM dumper UI lists this snapshot as `COMPLETE` and exposes its ZIP link.

## Confidence

`verified` that repeated background full-RAM discovery was the primary performance defect, and that the current full export completes with a valid screen-containing ZIP.

## Remaining limits

- Player/NPC semantic positions remain unresolved unless the pointer chain is explicitly verified.  The raw domain files are exported regardless; no positions are invented.
- An explicit runtime Field discovery may still briefly consume bridge bandwidth by design.  It is no longer performed by the dashboard or cache observer.
