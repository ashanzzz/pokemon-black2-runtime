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


def test_trace_does_not_join_sessions_frame_rewinds_or_zone_changes():
    with TemporaryDirectory() as td:
        g=ObservedNavigationGraph(project_root=Path(td))
        first=player(100,1,0,1);first["session_id"]="a"
        new_session=player(101,2,0,1);new_session["session_id"]="b"
        g.observe_player(first);g.observe_player(new_session)
        assert g.status()["directed_edge_count"]==0

        rewound=player(50,3,0,1);rewound["session_id"]="b"
        g.observe_player(rewound)
        assert g.status()["directed_edge_count"]==0

        different_zone=player(51,4,0,1,zone=428);different_zone["session_id"]="b"
        g.observe_player(different_zone)
        assert g.status()["directed_edge_count"]==0


def test_preview_component_uses_nearest_directly_observed_component():
    with TemporaryDirectory() as td:
        g = ObservedNavigationGraph(project_root=Path(td))
        g.observe_player(player(1, 10, 1, 10))
        g.observe_player(player(2, 11, 1, 10))
        g.reset_trace()
        g.observe_player(player(10, 50, 4, 50))
        g.observe_player(player(11, 51, 4, 50))

        result = g.preview_component(427, anchor={"x": 49, "y": 4, "z": 50})

        assert result["status"] == "available"
        assert result["start"]["x"] == 50
        assert [(node["x"], node["y"], node["z"]) for node in result["nodes"]] == [
            (50, 4, 50),
            (51, 4, 50),
        ]
        assert result["edges"][0]["direct_observations"] == 1
        assert result["execution_eligible"] is False


def test_preview_component_does_not_promote_inferred_reverse_edges():
    with TemporaryDirectory() as td:
        g = ObservedNavigationGraph(project_root=Path(td))
        g.observe_player(player(1, 10, 0, 10))
        g.observe_player(player(2, 11, 0, 10))

        result = g.preview_component(427, anchor={"x": 11, "y": 0, "z": 10})

        assert result["start"]["x"] == 10
        assert result["nodes"][-1]["x"] == 11
        assert len(result["edges"]) == 1
