from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workbench_is_single_primary_shell_and_legacy_map_ui_is_removed():
    frontend = ROOT / "frontend"
    assert (frontend / "workbench.html").is_file()
    assert (frontend / "workbench.js").is_file()
    assert (frontend / "workbench.css").is_file()
    assert (frontend / "ui" / "i18n.js").is_file()
    for obsolete in ["original-map.html", "original-map-ui.js", "world-lab.css", "runtime-monitor.html", "native-map.html", "map-runtime.html", "navigation.html"]:
        assert not (frontend / obsolete).exists()


def test_workbench_layout_matches_re_workbench_contract():
    html = (ROOT / "frontend" / "workbench.html").read_text(encoding="utf-8")
    for token in ["activityRail", "explorer", "editor", "inspector", "bottomDock", "statusbar", "localeSelect"]:
        assert token in html
    assert 'data-workspace="world"' in html
    assert 'data-workspace="memory"' in html
    assert 'data-workspace="evidence"' in html
    assert 'data-workspace="monitor"' in html
    assert "2D map" not in html.lower()


def test_i18n_defaults_to_chinese_but_supports_english():
    js = (ROOT / "frontend" / "ui" / "i18n.js").read_text(encoding="utf-8")
    assert "const DEFAULT='zh-CN'" in js
    assert "'zh-CN'" in js and "'en'" in js
    assert "black2.workbench.locale" in js


def test_world_renderer_exposes_object_selection_without_reintroducing_scene_loop():
    js = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    assert "onSelect" in js
    assert "Raycaster" in js
    assert "setLayerVisibility" in js
    assert "generation!==this.loadingGeneration" in js
    assert "_sceneLoop" not in js
    assert "TARGET_RENDER_FPS=30" in js


def test_world_renderer_exposes_ground_cell_hover_and_locked_planning_coordinate():
    js = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    workbench = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    for token in ["cellHoverRoot", "cellLockedRoot", "is_ground", "setLockedCell", "clearCellSelection", "pointerleave"]:
        assert token in js
    for token in ["planning_coordinate", "applyGoal", "后端吸附"]:
        assert token in workbench


def test_floor_pick_locks_only_after_backend_snap_and_preview_uses_selected_movement_mode():
    js = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    fixed = (ROOT / "frontend" / "world3d-runtime-fixed.js").read_text(encoding="utf-8")
    workbench = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    assert "only after the backend has proved a collision-valid" in js
    assert "if(point.is_ground){this._highlight(null)" in js
    assert "state.viewer?.clearCellSelection();updateWorldCoordinate" in workbench
    assert "movement_mode:state.navigation.movementMode||'auto'" in workbench
    assert "this._refreshCellOverlayHeights();" in fixed


def test_workbench_formats_structured_navigation_snap_errors_for_the_coordinate_banner():
    workbench = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    assert "function readableError" in workbench
    assert "navigation_snap_error',message" in workbench
    assert "snap_error:state.navigation.message||message" in workbench


def test_workbench_exposes_static_zone_preview_and_explicit_door_overlay_mode():
    html = (ROOT / "frontend" / "workbench.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    workbench = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    assert 'id="staticZoneInput"' in html
    assert 'id="staticZoneButton"' in html
    assert 'id="liveSceneButton"' in html
    assert "loadStaticZone(zoneId)" in js
    assert "preview_only" in js
    assert "semantic_overlay_no_independent_rom_mesh" in js
    assert "loadStaticZoneFromUI" in workbench


def test_static_zone_navigation_uses_preview_start_without_enabling_execution():
    workbench = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    assert "function navigationExplicitStart()" in workbench
    assert "state.scene?.navigation_preview?.start" in workbench
    assert "observed?.nodes||[]" in workbench
    assert "if(explicitStart)request.start=" in workbench
    assert "state.sceneMode!=='static'&&state.navigation.capabilities?.execution?.available===true" in workbench
    assert "if(state.sceneMode==='static'||!goal" in workbench


def test_workbench_exposes_safe_savestate_controls_and_never_assumes_load_success():
    workbench = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    assert "/api/dev/savestate/${action}?slot=${slot}" in workbench
    assert "savestate_${action}_rejected" in workbench
    assert "当前游戏继续运行" in workbench
    assert "只在 BizHawk 明确确认成功时才会改变状态" in workbench


def test_backend_exposes_a_non_mutating_savestate_safety_status():
    app = (ROOT / "backend" / "black2" / "api" / "app.py").read_text(encoding="utf-8")
    versions = (ROOT / "backend" / "black2" / "runtime" / "versions.py").read_text(encoding="utf-8")
    assert '@app.get("/api/dev/savestate/status")' in app
    assert "explicit_boolean_confirmation" in app
    assert "preserves_current_game_on_failure" in app
    assert "native_popup_dismissal" in app
    assert "savestate.loadslot(slot, true)" in app
    assert 'BIZHAWK_BRIDGE_VERSION = "1.9.0-savestate-safe"' in versions


def test_workbench_api_is_cache_first():
    py = (ROOT / "backend" / "black2" / "api" / "workbench_routes.py").read_text(encoding="utf-8")
    assert 'prefix="/api/v1/workbench"' in py
    assert '"/bootstrap"' in py
    assert '"/events"' in py
    assert '"/evidence"' in py
    assert '"/schema"' in py
    assert "read_main_ram" not in py
    assert "allow_discovery=True" not in py
