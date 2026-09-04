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


def test_workbench_api_is_cache_first():
    py = (ROOT / "backend" / "black2" / "api" / "workbench_routes.py").read_text(encoding="utf-8")
    assert 'prefix="/api/v1/workbench"' in py
    assert '"/bootstrap"' in py
    assert '"/events"' in py
    assert '"/evidence"' in py
    assert '"/schema"' in py
    assert "read_main_ram" not in py
    assert "allow_discovery=True" not in py
