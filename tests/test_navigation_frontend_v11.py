from pathlib import Path
import re


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


def test_runtime_npc_pick_preserves_zero_slot_and_uid_ids():
    renderer = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    assert "_semanticPickId" in renderer
    assert "actor.slot ?? actor.actor_uid ?? actor.uid ?? 'player'" in renderer
    assert "node.userData?.actor?.slot||'player'" not in renderer


def test_navigation_route_visibility_is_matrix_scoped_not_zone_scoped():
    workbench = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    renderer = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    assert "matrix_id:raw.matrix_id??pos.matrix_id??global.matrix_id??matrixId" in workbench
    assert "setNavigationPath(points,{matrixId,target:goal})" in workbench
    assert "_pointIsInMatrixDomain(point,matrixId)" in renderer
    assert "source.filter(p=>this._pointIsInMatrixDomain(p,matrixId))" in renderer
    assert "Number(p.zone_id)===Number(zoneId)" not in renderer


def test_zone_change_defers_route_disposal_until_a_known_matrix_scene_is_loaded():
    workbench = (ROOT / "frontend" / "workbench.js").read_text(encoding="utf-8")
    on_player = re.search(r"onPlayer\(p\)\{.*?\},\n\s*onScene", workbench, re.S)
    assert on_player
    assert "invalidateNavigationRoute" not in on_player.group(0)
    assert "等待 Matrix 身份确认" in on_player.group(0)
    assert "planMatrix===sceneMatrix" in workbench
    assert "invalidateNavigationRoute(t('nav.sceneChanged'))" in workbench


def test_raycast_provenance_is_preserved_for_connected_static_hits():
    renderer = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    assert "this.staticNpcRoot" in renderer
    assert "source_matrix_id:sourceMatrix" in renderer
    assert "hitItem.source_matrix_id??hitItem.matrix_id??hitItem.global?.matrix_id" in renderer
    assert "hitWarp.source_matrix_id??hitWarp.matrix_id??hitWarp.global?.matrix_id" in renderer
    assert "hitBuilding.source_matrix_id??hitBuilding.matrix_id??hitBuilding.global?.matrix_id" in renderer
    assert "withSourceMatrix=item=>" in renderer
    assert "zone_id:sourceZone" in renderer


def test_known_matrix_rejects_navigation_points_without_the_same_matrix_identity():
    renderer = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    assert "return matrixId==null?pointMatrix==null:pointMatrix===matrixId" in renderer
    assert "matrixId==null||pointMatrix==null||pointMatrix===matrixId" not in renderer


def test_static_npc_candidates_render_in_a_non_occupancy_layer():
    renderer = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    assert "_renderStaticNpcCandidates(entities,origin)" in renderer
    assert "presence:'rom_candidate'" in renderer
    assert "static_candidate:true" in renderer
    assert "this._renderStaticNpcCandidates(entities,nextOrigin)" in renderer


def test_runtime_npc_suppression_uses_backend_ids_and_restores_markers_from_each_snapshot():
    renderer = (ROOT / "frontend" / "world3d-runtime.js").read_text(encoding="utf-8")
    assert "/npcs/merged?zone_ids=" in renderer
    assert "suppressedStaticEntityIds:r.suppressed_static_entity_ids||[]" in renderer
    assert "_applyStaticNpcSuppression(ids)" in renderer
    assert "marker.visible=!suppressed.has(String(id))" in renderer
    assert "this._applyStaticNpcSuppression(suppressedStaticEntityIds)" in renderer
