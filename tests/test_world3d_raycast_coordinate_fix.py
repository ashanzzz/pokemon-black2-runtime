from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "frontend" / "world3d-runtime.js"
FIXED = ROOT / "frontend" / "world3d-runtime-fixed.js"
WORKBENCH = ROOT / "frontend" / "workbench.html"

BAD = "meshPoint.applyMatrix4(pickHit.object.matrixWorld)"


def grid(v: float) -> int:
    return int(v // 16)


def test_zone441_runtime_evidence_formula() -> None:
    assert (grid(88.0), grid(120.0)) == (5, 7)


def test_legacy_double_transform_reproduces_observed_bad_grid() -> None:
    # Zone 441: origin=(88,120), terrain display translation=(168,136).
    # A raw Three.js world hit near player at (0,-8) must NOT receive (168,136) again.
    legacy_x = 0.0 + 168.0 + 88.0
    legacy_z = -8.0 + 136.0 + 120.0
    assert (grid(legacy_x), grid(legacy_z)) == (16, 15)


def test_correct_intersection_point_recovers_player_and_neighbours() -> None:
    ox, oz = 88.0, 120.0
    assert (grid(0.0 + ox), grid(0.0 + oz)) == (5, 7)
    assert (grid(16.0 + ox), grid(0.0 + oz)) == (6, 7)
    assert (grid(-16.0 + ox), grid(0.0 + oz)) == (4, 7)
    assert (grid(0.0 + ox), grid(-16.0 + oz)) == (5, 6)
    assert (grid(0.0 + ox), grid(16.0 + oz)) == (5, 8)


def test_base_renderer_no_longer_double_transforms_skinnedmesh_hit() -> None:
    text = BASE.read_text(encoding="utf-8")
    assert BAD not in text
    assert "Raycaster Intersection.point is already expressed in Three.js world space" in text
    assert "const meshPoint=semanticPoint||pickHit?.point?.clone?.();" in text


def test_click_diagnostic_is_installed() -> None:
    text = BASE.read_text(encoding="utf-8")
    assert "[Terrain Click Debug]" in text
    assert "'raycastPointSpace':'three_world'" in text
    assert "'matrixWorldTranslation'" in text


def test_workbench_inheritance_path_still_uses_base_renderer() -> None:
    fixed = FIXED.read_text(encoding="utf-8")
    wb = WORKBENCH.read_text(encoding="utf-8")
    assert "Black2World3D as BaseWorld3D" in fixed
    assert "/frontend/world3d-runtime.js?base=" in fixed
    assert "world3d-runtime-fixed.js" in wb
