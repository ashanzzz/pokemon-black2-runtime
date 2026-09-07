from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_encounter_region_ui_and_renderer_are_wired():
    html = (ROOT / "frontend" / "workbench.html").read_text(encoding="utf-8")
    js = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    renderer = (ROOT / "frontend" / "world3d-runtime-fixed.js").read_text(encoding="utf-8")
    assert 'id="encounterRegionToggle"' in html
    assert 'data-dock="encounters"' in html
    assert "/api/v1/encounters/regions/current" in js
    assert "/api/v1/encounters/tasks" in js
    assert "setEncounterRegions(payload" in renderer
    assert "InstancedMesh" in renderer
