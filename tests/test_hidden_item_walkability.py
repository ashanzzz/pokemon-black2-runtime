import pytest
from backend.black2.world.static_navigation import RomStaticNavigationGraph
from backend.black2.world.observed_navigation import NavNode
from backend.black2.api.navigation_routes import _radar_cell

def test_dynamic_hidden_item_recognition_and_walkability():
    try:
        graph = RomStaticNavigationGraph()
    except Exception:
        from backend.black2.api.navigation_routes import navigation_static_provider
        graph = navigation_static_provider()
    
    # 1. Zone 457 at (23, 47) - Ground Floor Y=0
    overlays_ground = graph.event_overlay_at(457, 23, 47)
    assert len(overlays_ground) >= 1
    h_item = next((item for item in overlays_ground if item.get("furniture_id") == 17), None)
    assert h_item is not None
    assert h_item["kind"] == "hidden_item"
    assert h_item["symbol"] == "h"
    assert h_item["is_hidden_item"] is True
    assert h_item["height_y"] == 0

    # 2. Radar cell generation at Floor Y=0
    cell_y0 = _radar_cell(graph, 457, 23, 0, 47, is_player=False)
    assert cell_y0["walkable"] is True
    assert cell_y0["blocked"] is False
    assert cell_y0["can_traverse"] is True
    assert cell_y0["physical_obstacle"] is False
    assert "隐藏道具" in str(cell_y0["kind"]) or "hidden_item" in str(cell_y0.get("status"))

    # 3. Radar cell generation at Floor Y=2 (Upper layer)
    # The ground hidden item must be dynamically excluded from Upper Layer Y=+2
    cell_y2 = _radar_cell(graph, 457, 23, 2, 47, is_player=False)
    # It should not have the ground hidden item or furniture O on upper layer
    assert cell_y2["symbol"] != "O"
    assert cell_y2["symbol"] != "h"
    assert not any(ev.get("furniture_id") == 17 for ev in cell_y2.get("events", []))

    # 4. Pathfinding passes cleanly through (23, 47)
    start = NavNode(457, 23, 0, 46)
    goal = NavNode(457, 23, 0, 47)
    path_res = graph.find_path(start, goal)
    assert path_res["reachable"] is True
    assert len(path_res["path"]) == 2
