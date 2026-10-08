from fastapi import FastAPI
import asyncio

from fastapi.testclient import TestClient

from backend.black2.api import navigation_routes
from backend.black2.world.navigation_planning import NavigationPlanService
from backend.black2.world.observed_navigation import ObservedNavigationGraph
from tests.test_global_navigation_v13 import FakeGlobalProvider, player_sample


def _client(provider=None, latest=None):
    provider = provider or FakeGlobalProvider()
    latest = latest or player_sample()
    navigation_routes.player_runtime_service.latest = latest
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: navigation_routes.player_runtime_service.latest, static_provider=provider)
    navigation_routes.configure_navigation_routes(planner, static_provider=provider, runtime_reader=None)
    app = FastAPI()
    app.include_router(navigation_routes.router)
    return TestClient(app)


def test_execution_capabilities_advertise_same_matrix_cross_zone_when_bridge_is_connected():
    provider = FakeGlobalProvider()
    latest = player_sample()
    latest["locomotion"].update({"phase": "Idle", "semantic_state": "Standing"})
    navigation_routes.player_runtime_service.latest = latest

    class Bridge:
        is_connected = True

    planner = NavigationPlanService(
        ObservedNavigationGraph(), lambda: latest, static_provider=provider,
    )
    navigation_routes.configure_navigation_routes(
        planner,
        client=Bridge(),
        control_sample=lambda: {
            "runtime": {"status": "ready"},
            "semantic": {
                "map_loaded": True,
                "ready_for_input": True,
                "context": {
                    "screen_type": "OVERWORLD",
                    "can_move_player": True,
                    "is_dialogue_active": False,
                },
            },
        },
        static_provider=provider,
        runtime_reader=None,
    )
    try:
        capabilities = asyncio.run(navigation_routes.navigation_capabilities())
        assert capabilities["execution"]["available"] is True
        assert capabilities["execution"]["cross_zone"] is True
        assert capabilities["execution"]["cross_matrix"] is False
    finally:
        navigation_routes.configure_navigation_routes(
            NavigationPlanService(
                navigation_routes.observed_navigation_graph,
                lambda: navigation_routes.player_runtime_service.latest,
            )
        )


def test_plan_api_accepts_zone_less_global_destination():
    client = _client()
    response = client.post('/api/v1/navigation/plans', json={
        'destination': {
            'type': 'global_grid',
            'space': 'gen5-matrix-grid-v1',
            'x': 34, 'y': 0, 'z': 5,
        }
    })
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan['resolved_goal']['zone_id'] == 11
    assert plan['resolved_goal']['global'] == {
        'type': 'global_grid', 'space': 'gen5-matrix-grid-v1',
        'matrix_id': 0, 'x': 34, 'y': 0, 'z': 5,
    }
    assert plan['cost']['zone_transitions'] == 1


def test_plan_api_uses_global_finder_without_live_anchor_for_explicit_global_start():
    class RecordingProvider(FakeGlobalProvider):
        def __init__(self):
            super().__init__()
            self.global_calls = []
            self.local_calls = []

        def find_path(self, *args, **kwargs):
            self.local_calls.append((args, kwargs))
            return {'reachable': False, 'reason': 'local finder must not plan a Matrix-global route', 'path': []}

        def find_global_path(self, start, **kwargs):
            self.global_calls.append((start, kwargs))
            return super().find_global_path(start, **kwargs)

    provider = RecordingProvider()
    # The live player is deliberately elsewhere. An explicit global start is
    # an offline/static request and must not use this sample as a layer anchor.
    live = player_sample(x=28)
    client = _client(provider, live)
    response = client.post('/api/v1/navigation/plans', json={
        'start': {
            'type': 'global_grid', 'space': 'gen5-matrix-grid-v1',
            'matrix_id': 0, 'x': 30, 'y': 0, 'z': 5,
        },
        'destination': {
            'type': 'global_grid', 'space': 'gen5-matrix-grid-v1',
            'matrix_id': 0, 'x': 34, 'y': 0, 'z': 5,
        },
    })

    assert response.status_code == 200, response.text
    assert provider.local_calls == []
    assert len(provider.global_calls) == 1
    start, kwargs = provider.global_calls[0]
    assert (start.zone_id, start.x, start.y, start.z) == (10, 30, 0, 5)
    assert kwargs['matrix_id'] == 0
    assert (kwargs['x'], kwargs['y'], kwargs['z']) == (34, 0, 5)
    assert kwargs['player_sample'] is None
    assert response.json()['cost']['zone_transitions'] == 1


def test_global_resolver_and_global_snap_do_not_require_zone():
    client = _client()
    resolved = client.get('/api/v1/navigation/global/resolve?x=34&y=0&z=5')
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['resolved_zone_id'] == 11

    snapped = client.post('/api/v1/navigation/global/snap', json={
        'grid': {'x': 34, 'y': 0, 'z': 5},
        'picked': {'kind': 'terrain', 'id': 'test'},
    })
    assert snapped.status_code == 200, snapped.text
    payload = snapped.json()
    assert payload['coordinate']['matrix_id'] == 0
    assert payload['coordinate']['x'] == 34
    assert payload['resolved_zone_id'] == 11
    assert payload['legacy_grid']['zone_id'] == 11


def test_global_resolver_with_explicit_matrix_does_not_require_zone_or_implicit_player_branch():
    client = _client()
    response = client.get('/api/v1/navigation/global/resolve?matrix_id=0&x=34&y=0&z=5')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['coordinate']['matrix_id'] == 0
    assert payload['resolved_zone_id'] == 11


def test_navigation_context_exposes_typed_constraint_policy():
    client = _client()
    response = client.get('/api/v1/navigation/context')
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload['format'] == 'black2-navigation-context/v1'
    assert payload['policy_default']['dynamic_actor'] == 'hard_avoid'
    assert isinstance(payload['constraints'], list)
