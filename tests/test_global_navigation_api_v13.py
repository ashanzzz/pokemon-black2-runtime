from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.api import navigation_routes
from backend.black2.world.navigation_planning import NavigationPlanService
from backend.black2.world.observed_navigation import ObservedNavigationGraph
from tests.test_global_navigation_v13 import FakeGlobalProvider, player_sample


def _client():
    provider = FakeGlobalProvider()
    latest = player_sample()
    navigation_routes.player_runtime_service.latest = latest
    planner = NavigationPlanService(ObservedNavigationGraph(), lambda: navigation_routes.player_runtime_service.latest, static_provider=provider)
    navigation_routes.configure_navigation_routes(planner, static_provider=provider, runtime_reader=None)
    app = FastAPI()
    app.include_router(navigation_routes.router)
    return TestClient(app)


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
