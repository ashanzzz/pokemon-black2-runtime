from types import SimpleNamespace

import pytest

from backend.black2.api import navigation_routes
from backend.black2.world.navigation_planning import NavigationPlanService, NavigationPlanningError
from backend.black2.world.observed_navigation import NavNode, ObservedNavigationGraph
from backend.black2.world.static_navigation import RomStaticNavigationGraph, StaticNavigationCell


def _grid(zone=441, x=7, y=0, z=5):
    return {"type": "grid", "space": "gen5-field-grid-v1", "zone_id": zone, "x": x, "y": y, "z": z}


def test_coordinate_move_never_auto_upgrades_to_npc_interaction():
    destination = _grid()
    occupied = [{"zone_id": 441, "grid": {"x": 7, "y": 0, "z": 5}, "actor_id": "npc-1"}]
    resolved, interaction = navigation_routes._prepare_navigation_request(
        destination,
        provider=object(),
        player_sample=None,
        occupancy=occupied,
        interaction=None,
        navigation_intent="walk_to_tile",
    )
    assert resolved == destination
    assert interaction is None


def test_interact_requires_explicit_npc_semantics():
    with pytest.raises(NavigationPlanningError) as error:
        navigation_routes._prepare_navigation_request(
            _grid(), provider=None, player_sample=None, occupancy=[], interaction=None,
            navigation_intent="interact",
        )
    assert error.value.code == "NAV_INTERACTION_TARGET_REQUIRED"


class _MovementRom:
    def zone(self, _zone_id):
        return SimpleNamespace(area_id=1, enable_running=True, enable_cycling=True)

    def area(self, _area_id):
        return SimpleNamespace(is_exterior=True)


class _MovementProvider:
    rom = _MovementRom()

    def movement_rules(self, *_args, **_kwargs):
        return {"bike_blockers": []}


def test_auto_movement_picks_fastest_verified_active_mode():
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: None, static_provider=_MovementProvider())
    on_foot = {"locomotion": {"transport_mode": "OnFoot"}}
    cycling = {"locomotion": {"transport_mode": "Cycling"}}
    surfing = {"locomotion": {"transport_mode": "Surf"}}
    assert planner.movement_capabilities(441, requested="auto", player=on_foot)["selected"] == "run"
    assert planner.movement_capabilities(441, requested="auto", player=cycling)["selected"] == "bike"
    assert planner.movement_capabilities(441, requested="auto", player=surfing)["selected"] == "surf"


class _OpenGrid(RomStaticNavigationGraph):
    def __init__(self):
        self.revision = "test-grid"
        self._cells = {}
        for z in range(3):
            for x in range(3):
                node = NavNode(441, x, 0, z)
                self._cells[(x, z)] = StaticNavigationCell(
                    node=node, layer_index=0, tile_class=0, flags=0, static_blocked=False,
                )

    def _anchor_from_sample(self, *_args, **_kwargs):
        return None

    def _cells_for_layer(self, *_args, **_kwargs):
        return self._cells


def test_static_astar_prefers_fewer_turns_without_adding_steps():
    graph = _OpenGrid()
    result = graph.find_path(NavNode(441, 0, 0, 0), NavNode(441, 2, 0, 2))
    assert result["reachable"] is True
    assert result["steps"] == 4
    assert result["turns"] == 1
    assert result["optimization"] == "shortest_steps_then_fewest_turns"


class _DisconnectedClickGrid(RomStaticNavigationGraph):
    def __init__(self):
        self.revision = "test-disconnected-click"
        self._cells = {
            (0, 0): StaticNavigationCell(
                NavNode(441, 0, 0, 0), 0, 0, 0, False
            ),
            (1, 0): StaticNavigationCell(
                NavNode(441, 1, 0, 0), 0, 0, 0, False
            ),
            (2, 0): StaticNavigationCell(
                NavNode(441, 2, 0, 0), 0, 0, 0, False,
                blocked_directions=("right",),
            ),
            (3, 0): StaticNavigationCell(
                NavNode(441, 3, 0, 0), 0, 0, 0, False,
                blocked_directions=("left",),
            ),
        }

    def _anchor_from_sample(self, *_args, **_kwargs):
        return None

    def _cells_for_layer(self, *_args, **_kwargs):
        return self._cells


def test_snap_rejects_exact_static_tile_when_disconnected_from_live_player():
    graph = _DisconnectedClickGrid()
    player = {
        "zone_id": 441,
        "grid": {"x": 0, "y": 0, "z": 0},
    }

    result = graph.snap(
        441,
        3,
        0,
        0,
        player_sample=player,
        max_radius=4,
    )

    assert result["ok"] is True
    assert result["snapped"] is True
    assert result["target"] == {
        "zone_id": 441,
        "x": 2,
        "y": 0,
        "z": 0,
    }

