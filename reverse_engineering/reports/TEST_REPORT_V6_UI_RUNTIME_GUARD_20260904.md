# TEST REPORT — v6 UI Runtime Guard and DCC Controls

## Goal

Make `/original-map` fail closed when current RAM FieldActor data are unavailable, add system-language Chinese/English UI text, and use predictable 3D-editor mouse controls.

## Method

- Loaded `http://127.0.0.1:8765/original-map` in the local browser.
- Measured `/api/v1/map/v6/scene/current`; the pre-change endpoint exceeded a 15 second HTTP timeout because it began a full Main RAM discovery pass.
- Changed normal scene polling to consume the Runtime Player cache and any completed identity cache.  A full identity discovery remains explicit (`force_identity=true`) and is HTTP-bounded to 3 seconds.
- Added browser-side request timeouts and deferred scene polling until a PlayerRuntime transform is actually resolved.
- Added `original-map-ui` version `6.1.0`, a backend scene component/version/capability contract, and structured browser console logs.

## Actions performed

- Added automatic `zh-CN` / English locale selection from `navigator.languages`.
- Implemented left/right drag orbit, middle-button drag pan, and wheel zoom.  Any canvas interaction leaves player-follow mode so the gesture applies immediately.
- Added cancellation cleanup for timed-out HTTP-triggered bridge requests.
- Corrected the generated GLB URL shape so Apicula's relative PNG texture
  references resolve through verified companion-file endpoints.

## Raw observations

- Page route: HTTP 200.
- Existing v5.0.0 process: `/api/v1/map/v6/status` returned ready, but no v6.1.0 component contract.
- Existing PlayerRuntime response: `unresolved`, reason: `Field discovery is disabled for background sampling; run an explicit runtime discovery probe`.
- The updated UI correctly presented the unresolved state instead of leaving `initializing 3D scene…` indefinitely.
- Explicit operator-requested PlayerRuntime discovery completed against the live BizHawk bridge: frame `6190503`; GPos `(48, 1, 740)`; WPos `(776.00, 16.00, 11848.00)`; chunk/grid and facing cross-checks passed.  This is a current RAM observation, not a rendered estimate.

## Verification

- `python -m unittest discover -s tests -p test_world3d_scene_v6.py -v` using the workspace virtual environment: 4 tests passed.
- Python compilation passed for all changed backend modules.
- Browser check: Chinese system locale set the document title and all primary labels to Simplified Chinese; the Follow control switched `ON → OFF → ON` successfully.
- With the configured Black 2 ROM, the v6.1.1 status endpoint returned the required capability, and both a terrain GLB and its relative `yamagake01.png` texture endpoint returned HTTP 200.
- A clean v5.2.0 runtime instance returned the local-only Runtime Control contract with `restart_backend: true`; its UI schema advertised the same component/version/capability.
- `git diff --check` passed.

## Confidence

- UI locale selection: verified.
- DCC control mapping: probable from the live browser build and explicit OrbitControls mapping; full orbit/pan movement requires a resolved live scene.
- Runtime scene content: unresolved.  No static world, player position, or actors were invented.

## Version contract

- FastAPI: `5.3.0`.
- World3D scene contract: `6.1.1`, capability `fast_unresolved_scene`.
- Original map UI: `6.1.1`.
- Runtime Control API: `5.3.0`, local-only `restart_backend` and `runtime_monitor` capabilities.

## Runtime monitor extension

- Added the local-only `/runtime-monitor` page and links from the shared
  observer navigation and 3D page.
- Added `/api/v1/runtime/control/status` and
  `/api/v1/runtime/control/logs?limit=...`.  They expose process metadata,
  runtime/Bridge health, and recent lifecycle events only.
- The persistent journal is `logs/runtime_control.jsonl`.  Every record has
  `timestamp_utc`, component/version, PID, operation, result, and safe
  metadata.  RAM payloads and restart credentials are filtered before write.
- `run_runtime.py` records launcher start; FastAPI records startup success or
  failure and graceful shutdown; a web-triggered replacement restart records
  the request and retiring process.  The replacement receives only the prior
  PID as lifecycle metadata.

## Runtime monitor verification

- A separate HTTP runtime on port `8767` with its own bridge listener on
  `8768` returned `runtime-monitor` version `5.3.0`, a valid PID, two
  lifecycle records, and the advertised `persistent_lifecycle_log` capability.
- The same isolated instance returned HTTP 200 for `/runtime-monitor`; it was
  stopped after the check.  The running operator service on port `8765` was
  not restarted or otherwise changed.
- `test_runtime_control.py`: 1 passed; `test_runtime_monitor.py`: 1 passed;
  `test_world3d_scene_v6.py`: 4 passed; Python compilation and diff checks
  passed.

## Required next step

Restart the local runtime process on port 8765 once so it loads the v5.3.0 backend.  Then open `/runtime-monitor` to confirm the `backend_startup: ready` record and current Bridge state.  Thereafter the web page's **Restart backend** button owns replacement-process restarts.  Use **Refresh runtime** for the explicit controlled FieldActor discovery before expecting a populated 3D scene.
