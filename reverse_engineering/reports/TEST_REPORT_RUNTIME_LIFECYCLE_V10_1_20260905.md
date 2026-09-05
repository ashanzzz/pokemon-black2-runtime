# Runtime lifecycle repair — v10.1

Base GitHub commit: `7bd1de29a8ead9fdbd5c8e565ae7ad9728f7cddc`.

## Problem reproduced from source review

The v9 launcher exposed one `stop()` operation that terminated the backend listener and then sent `WM_CLOSE` to the recorded EmuHawk PID. At the same time the Web runtime control endpoint can spawn a replacement `run_runtime.py` process during restart, while `launcher_state.json` stored only one current backend PID. The GUI also had no per-checkout single-instance guard.

## v10.1 lifecycle contract

- `BLACK2_LAUNCHER.cmd`: GUI is single-instance per project checkout.
- `START_BLACK2.cmd`: one-click start reuses an existing owned backend and owned EmuHawk; it refuses to create a duplicate over an unrelated listener/external EmuHawk.
- `STOP_BLACK2.cmd`: stops **all** `run_runtime.py` processes whose command line belongs to this exact checkout. It never closes EmuHawk.
- `CLOSE_EMUHAWK.cmd`: sends `WM_CLOSE` only to the EmuHawk PID recorded as launched by this checkout. It never stops the backend and never force-kills the emulator.
- Web `/api/v1/runtime/restart` remains supported. Replacement runtimes are included in the checkout-scoped backend process discovery, so `STOP_BLACK2.cmd` cannot leave an orphan replacement alive.

## Verification status

- Python compile: required before package delivery.
- Launcher lifecycle unit tests: required before package delivery.
- Existing regression suite: required when the complete repository is materialized.
- Live Windows/BizHawk lifecycle: **UNRESOLVED until exercised on the user's Windows/BizHawk installation**. The implementation is source-verified and unit-tested but no claim of live process behavior is made from this environment.
