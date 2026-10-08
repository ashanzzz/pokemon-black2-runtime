from __future__ import annotations

import asyncio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.black2.world.story_runner import story_runner
from backend.black2.world.runtime_player_state import player_runtime_service
from backend.black2.progression.state import progression_state_service
from backend.black2.api.story_automation_routes import router as story_router


@pytest.fixture
def api():
    app = FastAPI()
    app.include_router(story_router)
    return TestClient(app)


def test_story_runner_evaluates_active_milestone_plan(monkeypatch):
    # Mock player in Virbank City (Zone 448)
    monkeypatch.setattr(
        player_runtime_service,
        "latest",
        {
            "status": "resolved",
            "zone_id": 448,
            "position": {"grid": {"x": 200, "y": 0, "z": 650}},
        },
    )

    # Mock progression with 6 badges
    async def mock_sample():
        return {
            "status": "verified",
            "badges": {"status": "verified", "count": 6, "mask": 0x3F},
            "money": {"status": "verified", "amount": 294722},
        }

    monkeypatch.setattr(progression_state_service, "sample", mock_sample)

    plan = asyncio.run(story_runner.plan_next_story_action())
    assert plan["format"] == "black2-story-progression-plan/v1"
    assert plan["status"] == "ready"
    assert plan["milestone"]["id"] == "M8_DRAYDEN_FREEZE_BADGE"
    assert plan["location"]["current_zone"] == 448
    assert plan["location"]["target_zone"] == 121
    assert plan["progression"]["badges_count"] == 6
    assert plan["world_route"]["traversable"] is True
    assert plan["world_route"]["total_steps"] > 0
    assert plan["world_route"]["next_step"] is not None


def test_story_plan_api_endpoint(api: TestClient, monkeypatch):
    monkeypatch.setattr(
        player_runtime_service,
        "latest",
        {
            "status": "resolved",
            "zone_id": 448,
            "position": {"grid": {"x": 200, "y": 0, "z": 650}},
        },
    )

    async def mock_sample():
        return {
            "status": "verified",
            "badges": {"status": "verified", "count": 6, "mask": 0x3F},
            "money": {"status": "verified", "amount": 294722},
        }

    monkeypatch.setattr(progression_state_service, "sample", mock_sample)

    res = api.get("/api/v1/agent/story/plan")
    assert res.status_code == 200
    data = res.json()
    assert data["format"] == "black2-story-progression-plan/v1"
    assert data["milestone"]["id"] == "M8_DRAYDEN_FREEZE_BADGE"


def test_story_runner_executes_step(monkeypatch):
    monkeypatch.setattr(
        player_runtime_service,
        "latest",
        {
            "status": "resolved",
            "zone_id": 448,
            "position": {"grid": {"x": 200, "y": 0, "z": 650}},
        },
    )

    async def mock_sample():
        return {
            "status": "verified",
            "badges": {"status": "verified", "count": 6, "mask": 0x3F},
            "money": {"status": "verified", "amount": 294722},
        }

    monkeypatch.setattr(progression_state_service, "sample", mock_sample)

    step_res = asyncio.run(story_runner.execute_story_step())
    assert step_res["format"] == "black2-story-step-execution/v1"
    assert step_res["status"] == "ready_for_execution"
    assert step_res["executed"] is True
    assert step_res["current_zone"] == 448
    assert step_res["step_kind"] in ("ferry", "flight", "transport", "warp", "matrix_seam")


def test_story_step_api_endpoint(api: TestClient, monkeypatch):
    monkeypatch.setattr(
        player_runtime_service,
        "latest",
        {
            "status": "resolved",
            "zone_id": 448,
            "position": {"grid": {"x": 200, "y": 0, "z": 650}},
        },
    )

    async def mock_sample():
        return {
            "status": "verified",
            "badges": {"status": "verified", "count": 6, "mask": 0x3F},
            "money": {"status": "verified", "amount": 294722},
        }

    monkeypatch.setattr(progression_state_service, "sample", mock_sample)

    res = api.post("/api/v1/agent/story/step")
    assert res.status_code == 200
    data = res.json()
    assert data["format"] == "black2-story-step-execution/v1"
    assert data["executed"] is True
    assert data["current_zone"] == 448


def test_story_runner_at_target_zone(monkeypatch):
    monkeypatch.setattr(
        player_runtime_service,
        "latest",
        {
            "status": "resolved",
            "zone_id": 121,
            "position": {"grid": {"x": 200, "y": 0, "z": 650}},
        },
    )

    async def mock_sample():
        return {
            "status": "verified",
            "badges": {"status": "verified", "count": 6, "mask": 0x3F},
            "money": {"status": "verified", "amount": 294722},
        }

    monkeypatch.setattr(progression_state_service, "sample", mock_sample)

    step_res = asyncio.run(story_runner.execute_story_step())
    assert step_res["format"] == "black2-story-step-execution/v1"
    assert step_res["status"] == "at_target_zone"
    assert step_res["executed"] is True
