# TEST REPORT — Runtime Supervisor and Component Version Registry

## Goal

Remove the one-time manual-restart ambiguity, restore the live
`/runtime-monitor` route, provide an operator-safe restart shortcut, and make
the expected/observed version of each independently loaded runtime component
queryable.

## Hypothesis

The HTTP 404 did not mean the backend or BizHawk Bridge was offline.  The
listener was an older `run_runtime.py` process that served updated static files
from disk but had not loaded the new FastAPI routes into memory.

## Method

- Queried `/health`, runtime-control routes, the static monitor asset, the TCP
  listener owner, and its command line.
- Added a single Python version registry and an API report comparing expected
  and process-observed component versions.
- Added a PowerShell restart helper with service, port, and command-line safety
  checks plus a root double-click CMD entry.
- Performed one bootstrap restart and one web-control restart, then verified
  the replacement PID, Bridge reconnection, semantic state, version report,
  monitor page, and persistent lifecycle log.

## Actions performed

- Bootstrap restart: verified old PID `33676` was the `run_runtime.py` process
  listening on `127.0.0.1:8765`; stopped only that process; started the current
  workspace runtime; waited for `/api/v1/runtime/versions` and
  `/runtime-monitor`.
- Web restart: clicked **重启后端** on the live monitor page.  Runtime PID
  changed from `31524` to `28436`; the same page reconnected automatically.
- No game input was sent and no RAM was read, written, or exported by this
  experiment.

## Raw observations

- Before bootstrap: `/health` HTTP 200, Bridge connected, semantic ready;
  `/api/v1/runtime/control` HTTP 404; `/runtime-monitor` HTTP 404; static
  `/frontend/runtime-monitor.html` HTTP 200.
- After bootstrap: `/runtime-monitor` HTTP 200; runtime release `5.4.0`;
  Bridge connected; semantic ready.
- After web restart: lifecycle sequence included
  `backend_restart_requested: replacement_started` (PID `31524`),
  `runtime_launcher_start: launching` (PID `28436`), and
  `backend_startup: ready` (PID `28436`).

## Component version result

| Component | Expected | Observed | Result |
|---|---:|---:|---|
| run_runtime.py | 5.4.0 | 5.4.0 | compatible |
| FastAPI Backend | 5.4.0 | 5.4.0 | compatible |
| Runtime Control API | 5.4.0 | 5.4.0 | compatible |
| Runtime Monitor UI/API | 5.4.0 | 5.4.0 | compatible |
| BizHawk Lua Bridge | 1.5.1-universal-dump | 1.5.1-universal-dump | compatible |
| 3D Scene Contract | 6.1.1 | 6.1.1 | compatible |
| Original Map UI | 6.1.1 | 6.1.1 | compatible |

Protocol versions are tracked separately: Runtime Health v2, Runtime Snapshot
v4, World3D Scene v6, Universal Snapshot v2, and Runtime World Export v1.

## Verification

- Restart PowerShell parsed successfully.
- `test_runtime_control.py`: 1 passed.
- `test_runtime_monitor.py`: 1 passed.
- `test_runtime_versions.py`: 3 passed.
- `test_world3d_scene_v6.py`: 4 passed.
- Python compilation and `git diff --check` passed.
- Live browser check showed the monitor page, seven component rows, all
  `compatible`, the protocol badges, and the new lifecycle records.

## Confidence

- Monitor route and API availability: verified.
- Bootstrap restart safety checks: verified against the actual listener and
  process.
- Web-control replacement restart: verified end-to-end.
- BizHawk Bridge reconnect: verified from the new process handshake/health.
- Component version comparison: verified for the currently registered seven
  components.

## Unresolved fields

- Runtime gameplay/world fields were outside this service-management test and
  were not reinterpreted.  Their existing verified/unresolved states are
  unchanged.

## Files changed

- `backend/black2/runtime/versions.py`
- `backend/black2/runtime/control_log.py`
- `backend/black2/api/runtime_routes.py`
- `backend/black2/api/app.py`
- `backend/black2/api/map_v5_routes.py`
- `frontend/runtime-monitor.html`
- `frontend/original-map.html`
- `tools/restart_runtime.ps1`
- `restart_runtime.cmd`
- `docs/versioning.md`
- runtime control/monitor/version tests

## Next recommended operator action

No restart is currently required.  Keep `/runtime-monitor` open for service
status.  Use its **重启后端** button for normal restarts; if an older or broken
backend cannot expose that button's API, double-click `restart_runtime.cmd`.

