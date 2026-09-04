from pathlib import Path
from tempfile import TemporaryDirectory

from backend.black2.world.observed_navigation import NavNode, ObservedNavigationGraph


def player(frame, x, y, z, zone=427):
    return {"frame": frame, "zone_id": zone, "grid": {"x": x, "y": y, "z": z}, "world": {"x": x*16+8, "y": y*16, "z": z*16+8}}


def test_observed_graph_preserves_elevation_layers():
    with TemporaryDirectory() as td:
        g=ObservedNavigationGraph(project_root=Path(td))
        g.observe_player(player(1,10,2,10));g.observe_player(player(2,11,2,10));g.observe_player(player(3,12,3,10))
        assert g.status()["node_count"]==3
        result=g.find_path(NavNode(427,10,2,10),NavNode(427,12,3,10))
        assert result["reachable"] is True
        assert [p["y"] for p in result["path"]]==[2,2,3]


def test_same_xz_different_y_are_distinct_nodes():
    with TemporaryDirectory() as td:
        g=ObservedNavigationGraph(project_root=Path(td))
        g.observe_player(player(1,5,1,5));g.reset_trace();g.observe_player(player(2,5,4,5))
        assert g.status()["node_count"]==2
