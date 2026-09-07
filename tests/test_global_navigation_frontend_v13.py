from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_navigation_ui_uses_matrix_global_coordinate_instead_of_zone_input():
    js = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    assert 'id="goalMatrix"' in js
    assert 'id="goalZone"' not in js
    assert "type:'global_grid',space:'gen5-matrix-grid-v1'" in js
    assert "Math.min(10000" in js


def test_navigation_api_exposes_global_resolver():
    source = (ROOT / "backend" / "black2" / "api" / "navigation_routes.py").read_text(encoding="utf-8")
    assert '@router.get("/global/resolve")' in source
    assert 'class GlobalGridDestination' in source
