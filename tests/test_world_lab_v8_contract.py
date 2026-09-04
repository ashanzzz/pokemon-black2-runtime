from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def test_v9_supersedes_v8_map_pages_without_restoring_obsolete_2d_pages():
    for name in ["native-map.html","map-runtime.html","navigation.html","original-map.html","original-map-ui.js","world-lab.css"]:
        assert not (ROOT/"frontend"/name).exists()
    html=(ROOT/"frontend"/"workbench.html").read_text(encoding="utf-8")
    assert 'data-workspace="world"' in html
    assert 'data-dock="calibration"' in html
    assert 'data-dock="navigation"' in html
    assert 'id="inspector"' in html


def test_renderer_is_event_driven_and_reports_failures():
    js=(ROOT/"frontend"/"world3d-runtime.js").read_text(encoding="utf-8")
    assert "_sceneLoop" not in js
    assert "failed_assets" in js
    assert "generation!==this.loadingGeneration" in js
    assert "TARGET_RENDER_FPS=30" in js
