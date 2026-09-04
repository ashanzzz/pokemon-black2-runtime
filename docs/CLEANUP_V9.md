# v9 UI cleanup

v9 removes standalone primary UIs that duplicated Workbench responsibilities:

- `frontend/original-map.html`
- `frontend/original-map-ui.js`
- `frontend/world-lab.css`
- `frontend/runtime-monitor.html`

The following remain because they are explicit reverse-engineering/advanced tools rather than competing product shells:

- Controller
- Player legacy inspector
- Dialogue legacy inspector
- Memory Tracer
- RAM Dumper
- Dialogue Checkpoints

All main navigation now returns to Workbench workspaces.
