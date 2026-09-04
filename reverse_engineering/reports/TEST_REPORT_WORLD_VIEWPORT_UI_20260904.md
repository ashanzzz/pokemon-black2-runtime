# TEST REPORT — 3D World Viewport UI

## Goal

Replace the ambiguous original-map screen with a small operator-facing world
viewport whose primary jobs are to display the current player, ROM-derived map
assets, observable runtime state, 3D navigation, and a candidate navigation
path.

## Design reference and scope

The layout uses only the useful parts of a Blender-style workspace: a large
central 3D viewport, a compact runtime outliner, a properties panel, and a
bottom status/progress strip. Discrete service states follow the same basic
operator pattern as a status-history panel. The implementation intentionally
does not reproduce a full DCC application or monitoring dashboard.

References consulted:

- Blender 3D Viewport navigation:
  <https://docs.blender.org/manual/en/latest/editors/3dview/navigate/introduction.html>
- Blender workspaces:
  <https://docs.blender.org/manual/en/latest/interface/window_system/workspaces.html>
- Blender status bar:
  <https://docs.blender.org/manual/en/3.6/interface/window_system/status_bar.html>
- Grafana status history:
  <https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/status-history/>

## Hypothesis

The previous UI mixed status prose, actions, and scene output without showing
which prerequisite had failed. A five-stage pipeline with real API state and a
dominant viewport would make the page usable without implying that unresolved
RAM data had already been parsed.

## Method

- Persisted the machine-local ROM file selection in ignored
  `runtime/runtime.local.json`; the UI and logs expose only safe availability
  metadata and the ROM file name.
- Separated live PlayerRuntime reads from ROM initialization so that a missing
  ROM cannot convert the player endpoint into HTTP 503.
- Added Backend, Bridge, ROM, PlayerRuntime, and 3D Scene stages. Completed
  stages contribute to the progress bar; scene asset progress uses the actual
  loaded/total asset count. Explicit player discovery reports elapsed time
  rather than a fabricated byte percentage.
- Added system-language Simplified Chinese/English UI text.
- Applied DCC-style navigation: left/right drag orbit, middle drag pan, and
  wheel zoom.
- Added a read-only path preview that renders only coordinates returned by
  `/api/v1/nav/find_path`. It never sends game input and is labelled as a
  candidate path.
- Inspected the live page in Edge after runtime restart and player discovery.

## Actions performed

- Restarted the backend through the local runtime-control workflow to load
  release `5.5.0` and the persisted ROM configuration.
- Ran one explicit read-only PlayerRuntime discovery while the game remained in
  a controllable field scene.
- Requested a candidate path from GPos `(48, 740)` to `(48, 735)` and rendered
  its six returned nodes.
- No game input was sent and no RAM was written.

## Raw observations

- HTTP API: online, PID `7240`, release `5.5.0`.
- BizHawk Bridge: connected, semantic state ready, bridge
  `1.5.1-universal-dump`.
- ROM: available as `口袋妖怪黑2.nds`.
- Player: resolved around frame `6302438`.
- GPos: `(48, 1, 740)`.
- WPos: `(776.00, 16.00, 11848.00)`.
- Facing: `South / 下`; locomotion: `Idle`; transport: `OnFoot`.
- Scene: `Zone 427`, `exterior`, Matrix `0`, two terrain assets, one building
  asset, scene confidence `verified`.
- Path API: start `(48, 740)`, goal `(48, 735)`, six nodes, five steps,
  `reachable: true`.
- Browser visual check confirmed that the map, player billboard, runtime facts,
  status strip, and cyan path geometry were visible together.
- The rendered candidate visually intersects the current building geometry.
  Therefore the renderer is exposing a real navigation/collision discrepancy;
  this route must not be promoted to a verified or executable path.

## Version result

The live `/api/v1/runtime/versions` report returned `compatible` for all seven
registered components:

- runtime launcher/backend/control/monitor: `5.5.0`
- BizHawk Bridge: `1.5.1-universal-dump`
- World3D scene contract: `6.1.1`
- world viewport/original-map UI: `6.2.0`

## Verification

- Ten focused Python unit tests passed: runtime control, local config, lifecycle
  log filtering, version compatibility, and World3D scene behavior.
- JavaScript syntax checks passed for the world renderer, viewport controller,
  and shared runtime client.
- `git diff --check` passed; only existing line-ending conversion warnings were
  reported.
- Live browser checks passed for the five-stage `5 / 5` state, real player and
  scene facts, path API result, path rendering, and path camera framing.

## Confidence

- Backend/Bridge/ROM/player/scene status presentation: verified end-to-end.
- Player and map asset rendering: verified in the live browser.
- DCC mouse mapping: verified in the renderer configuration; browser context
  menu suppression is active for right-drag orbit.
- Candidate path API response and visible rendering: verified end-to-end. Path
  correctness: unresolved because this sample intersects building geometry.
- Chinese selection on the current system: verified. English strings and
  system-language selection are implemented and syntax-checked.

## Unresolved fields and limits

- The displayed route remains a navigation API candidate. The tested route
  intersects building geometry, so collision/path correctness is unresolved.
  The page does not authorize or perform automatic movement.
- Runtime NPCs remain limited to actors resolved by the existing ActorRuntime
  data source; no NPC positions were inferred from static spawn data.
- The player's renderer currently reports `pixel_marker` with
  `nsbtx_billboard`; no unsupported animated 3D actor model was invented.

## Files changed for this stage

- `frontend/original-map.html`
- `frontend/original-map-v6.css`
- `frontend/original-map-ui.js`
- `frontend/world3d-runtime.js`
- `frontend/index.html`
- `frontend/ui/runtime-client.js`
- `backend/black2/api/map_v5_routes.py`
- `backend/black2/api/runtime_routes.py`
- `backend/black2/runtime/versions.py`
- `run_runtime.py`
- runtime and World3D tests

## Next recommended operator action

Use the viewport to inspect a short candidate path in the current zone. Rotate,
pan, and zoom around it before considering any automatic execution. If a route
visibly crosses geometry that should be blocked, record that exact start/goal
pair as collision evidence instead of treating the route as verified.
