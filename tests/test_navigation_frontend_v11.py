from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workbench_defaults_to_pure_movement_and_never_auto_promotes_snap_to_interact():
    source = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    assert "intent:'walk_to_tile'" in source
    assert "navigation_intent:state.navigation.intent||'walk_to_tile'" in source
    assert "snap.interaction?'interact':state.navigation.intent" not in source


def test_connected_world_ui_and_renderer_endpoints_are_wired():
    html = (ROOT / "frontend" / "workbench.html").read_text(encoding="utf-8")
    renderer = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    assert 'id="connectedMapToggle"' in html
    assert "/scene/connected/current" in renderer
    assert "/scene/connected/zone/" in renderer
    assert "setConnectedWorld" in renderer
