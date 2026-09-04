from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_world_lab_replaces_obsolete_map_pages():
    for name in ["native-map.html","map-runtime.html","navigation.html"]:
        assert not (ROOT/"frontend"/name).exists()
    html=(ROOT/"frontend"/"original-map.html").read_text(encoding="utf-8")
    assert "Observed Nav Nodes" in html
    assert "结束并导出" in html
    assert "建筑" in html and "NPC" in html and "主角" in html


def test_renderer_is_event_driven_and_reports_failures():
    js=(ROOT/"frontend"/"world3d-runtime.js").read_text(encoding="utf-8")
    assert "_sceneLoop" not in js
    assert "failed_assets" in js
    assert "generation!==this.loadingGeneration" in js
    assert "TARGET_RENDER_FPS=30" in js
