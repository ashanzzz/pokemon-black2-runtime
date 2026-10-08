from pathlib import Path
from tempfile import TemporaryDirectory
import json
from types import SimpleNamespace

from backend.black2.world.observed_navigation import NavNode, ObservedNavigationGraph
from backend.black2.world.navigation_planning import NavigationPlanService


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


def test_preview_component_nodes_include_known_matrix_identity():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.configure_matrix_identity(matrix_for_zone=lambda zone: 0 if zone == 446 else None)
        first = player(100, 10, 0, 5, zone=446)
        second = player(101, 11, 0, 5, zone=446)
        first["session_id"] = second["session_id"] = "runtime"
        graph.observe_player(first, source="runtime:hub")
        graph.observe_player(second, source="runtime:hub")

        preview = graph.preview_component(446, anchor={"x": 10, "y": 0, "z": 5})
        assert preview["status"] == "available"
        assert [node["matrix_id"] for node in preview["nodes"]] == [0, 0]


def test_same_matrix_446_to_444_boundary_is_audited_but_not_observed_route():
    with TemporaryDirectory() as td:
        g = ObservedNavigationGraph(project_root=Path(td))
        g.configure_matrix_identity(
            matrix_for_zone=lambda zone: 0 if zone in {446, 444} else None,
            zone_owner=lambda matrix, x, z: 446 if matrix == 0 and x < 32 else (444 if matrix == 0 and x >= 32 else None),
        )
        start = player(100, 31, 0, 5, zone=446)
        start["session_id"] = "field"
        end = player(101, 32, 0, 5, zone=444)
        end["session_id"] = "field"
        g.observe_player(start, source="runtime:hub")
        observed = g.observe_player(end, source="runtime:hub")

        assert observed["edge"]["kind"] == "zone_transition"
        assert observed["edge"]["from"]["matrix_id"] == 0
        assert g.find_path(NavNode(446, 31, 0, 5, 0), NavNode(444, 32, 0, 5, 0))["reachable"] is False


def test_navigation_service_wires_runtime_observation_to_rom_matrix_identity():
    class Zone:
        def __init__(self, matrix_id):
            self.matrix_id = matrix_id

    class Rom:
        def zone(self, zone_id):
            return Zone(0 if zone_id in {446, 444} else 255)

    class Provider:
        rom = Rom()

        @staticmethod
        def resolve_zone_for_global(matrix_id, x, z):
            if matrix_id != 0:
                return None
            return 446 if x < 32 else 444

    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        NavigationPlanService(graph, lambda: None, static_provider=Provider())
        start = player(100, 31, 0, 5, zone=446)
        end = player(101, 32, 0, 5, zone=444)
        start["session_id"] = end["session_id"] = "runtime"
        graph.observe_player(start, source="runtime:hub")
        observed = graph.observe_player(end, source="runtime:hub")

        assert observed["edge"]["kind"] == "zone_transition"
        assert observed["edge"]["to"]["matrix_id"] == 0


def test_matrix_resolver_normalizes_unqualified_observation_graph_queries():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.configure_matrix_identity(matrix_for_zone=lambda zone: 0 if zone == 446 else None)
        first = player(100, 10, 0, 5, zone=446)
        second = player(101, 11, 0, 5, zone=446)
        first["session_id"] = second["session_id"] = "runtime"
        graph.observe_player(first, source="runtime:hub")
        graph.observe_player(second, source="runtime:hub")

        start, goal = NavNode(446, 10, 0, 5), NavNode(446, 11, 0, 5)
        route = graph.find_path(start, goal, require_direct_observation=True)
        assert route["reachable"] is True
        assert graph.has_direct_edge(start, goal) is True
        assert graph.tile_evidence(start)["occupied_observations"] == 1
        assert graph.nearest_known_node(446, 10, 5).matrix_id == 0


def test_vertical_same_tile_observation_is_evidence_only_until_action_mapping_exists():
    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        graph.configure_matrix_identity(matrix_for_zone=lambda zone: 0)
        first = player(100, 10, 1, 5, zone=439)
        second = player(101, 10, 2, 5, zone=439)
        first["session_id"] = second["session_id"] = "runtime"
        graph.observe_player(first, source="runtime:hub")
        observed = graph.observe_player(second, source="runtime:hub")

        assert observed["edge"]["kind"] == "vertical_same_tile"
        assert graph.has_direct_edge(
            NavNode(439, 10, 1, 5, 0), NavNode(439, 10, 2, 5, 0)
        ) is False
        route = graph.find_path(
            NavNode(439, 10, 1, 5, 0), NavNode(439, 10, 2, 5, 0),
            require_direct_observation=True,
        )
        assert route["reachable"] is False
        assert graph.tile_evidence(NavNode(439, 10, 1, 5, 0))["outgoing_direct_edges"] == []


def test_planner_uses_matrix_normalized_observed_route_before_static_fallback():
    class Provider:
        class Rom:
            @staticmethod
            def zone(zone_id):
                return SimpleNamespace(matrix_id=0, area_id=0, enable_running=True, enable_cycling=True)

            @staticmethod
            def area(area_id):
                return SimpleNamespace(is_exterior=True)

        rom = Rom()

        def __init__(self):
            self.static_calls = 0

        @staticmethod
        def resolve_zone_for_global(matrix_id, x, z):
            return 446 if matrix_id == 0 else None

        def find_path(self, *_args, **_kwargs):
            self.static_calls += 1
            return {"reachable": False, "reason": "must not replace direct observation", "path": []}

    def live_player(frame, x):
        return {
            "status": "resolved", "confidence": "verified", "frame": frame, "zone_id": 446,
            "grid": {"x": x, "y": 0, "z": 5},
            "world": {"x": x * 16 + 8, "y": 0, "z": 5 * 16 + 8},
            "position": {"grid": {"x": x, "y": 0, "z": 5}, "world": {"x": x * 16 + 8, "y": 0, "z": 5 * 16 + 8}},
            "locomotion": {"transport_mode": "OnFoot"},
        }

    with TemporaryDirectory() as td:
        graph = ObservedNavigationGraph(project_root=Path(td))
        provider = Provider()
        planner = NavigationPlanService(graph, lambda: live_player(102, 10), static_provider=provider)
        first, second = live_player(100, 10), live_player(101, 11)
        first["session_id"] = second["session_id"] = "runtime"
        graph.observe_player(first, source="runtime:hub")
        graph.observe_player(second, source="runtime:hub")

        plan = planner.create_plan({"type": "grid", "space": "gen5-field-grid-v1", "zone_id": 446, "x": 11, "y": 0, "z": 5})
        assert plan["route_source"] == "verified_observed"
        assert provider.static_calls == 0
        assert [point["matrix_id"] for point in plan["segments"][0]["path"]] == [0, 0]


def test_different_or_unknown_matrix_never_records_a_cross_zone_edge():
    with TemporaryDirectory() as td:
        g = ObservedNavigationGraph(project_root=Path(td))
        g.configure_matrix_identity(
            matrix_for_zone=lambda zone: {10: 0, 11: 1}.get(zone),
            zone_owner=lambda matrix, x, z: 10 if matrix == 0 else 11 if matrix == 1 else None,
        )
        first = player(100, 31, 0, 5, zone=10)
        first["session_id"] = "field"
        different_matrix = player(101, 32, 0, 5, zone=11)
        different_matrix["session_id"] = "field"
        g.observe_player(first, source="runtime:hub")
        assert g.observe_player(different_matrix, source="runtime:hub")["edge"] is None

        g.reset_trace()
        unknown = player(200, 31, 0, 5, zone=10)
        unknown["session_id"] = "field"
        unknown.pop("matrix_id", None)
        # A graph without identity resolvers also refuses the transition.
        unconfigured = ObservedNavigationGraph(project_root=Path(td) / "unknown")
        unconfigured.observe_player(unknown, source="runtime:hub")
        next_unknown = player(201, 32, 0, 5, zone=11)
        next_unknown["session_id"] = "field"
        assert unconfigured.observe_player(next_unknown, source="runtime:hub")["edge"] is None


def test_v1_persistence_without_matrix_identity_is_legacy_not_cross_zone_authority():
    with TemporaryDirectory() as td:
        root = Path(td)
        path = root / "runtime" / "navigation" / "observed_graph.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "format": "black2-observed-navigation/v1",
            "nodes": {
                "10:31:0:5": {"zone_id": 10, "x": 31, "y": 0, "z": 5},
                "11:32:0:5": {"zone_id": 11, "x": 32, "y": 0, "z": 5},
            },
            "edges": {"10:31:0:5": {"11:32:0:5": {
                "to": {"zone_id": 11, "x": 32, "y": 0, "z": 5},
                "kind": "zone_transition", "direct_observations": 1,
            }}},
        }), encoding="utf-8")
        g = ObservedNavigationGraph(project_root=root)
        assert g.status()["node_count"] == 2
        assert g._nodes["10:31:0:5"]["legacy_matrix_unverified"] is True
        assert g._edges["10:31:0:5"]["11:32:0:5"]["legacy_matrix_unverified"] is True
        result = g.find_path(NavNode(10, 31, 0, 5, 0), NavNode(11, 32, 0, 5, 0))
        assert result["reachable"] is False


def test_direct_observation_clears_stale_migration_marker():
    with TemporaryDirectory() as td:
        root = Path(td)
        path = root / "runtime" / "navigation" / "observed_graph.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "format": "black2-observed-navigation/v2",
            "nodes": {
                "m13:443:7:0:12": {"zone_id": 443, "x": 7, "y": 0, "z": 12, "matrix_id": 13},
                "m13:443:7:0:13": {"zone_id": 443, "x": 7, "y": 0, "z": 13, "matrix_id": 13},
            },
            "edges": {
                "m13:443:7:0:12": {
                    "m13:443:7:0:13": {
                        "to": {"zone_id": 443, "x": 7, "y": 0, "z": 13, "matrix_id": 13},
                        "kind": "step", "observations": 1, "direct_observations": 1,
                        "legacy_unverified": True,
                    }
                }
            },
        }), encoding="utf-8")
        g = ObservedNavigationGraph(project_root=root)
        first = player(10, 7, 0, 12, zone=443)
        second = player(11, 7, 0, 13, zone=443)
        first["matrix_id"] = second["matrix_id"] = 13
        first["session_id"] = second["session_id"] = "current"
        g.observe_player(first, source="runtime:hub")
        g.observe_player(second, source="runtime:hub")
        edge = g._edges["m13:443:7:0:12"]["m13:443:7:0:13"]
        assert "legacy_unverified" not in edge
        assert g.has_direct_edge(NavNode(443, 7, 0, 12, 13), NavNode(443, 7, 0, 13, 13))
