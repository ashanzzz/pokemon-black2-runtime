import json
from pathlib import Path

from backend.black2.world.navigation_audit import NavigationAuditLog


def test_navigation_audit_keeps_separate_plan_and_execution_trails(tmp_path: Path):
    audit = NavigationAuditLog(
        tmp_path / "navigation_plans.jsonl",
        tmp_path / "navigation_execution.jsonl",
    )
    audit.record("plan", "plan_failed", plan_id="plan_test", code="NAV_INVALID_REQUEST")
    audit.record("execution", "task_terminal", task_id="nav_test", status="failed")

    plan_entries = audit.recent("plan", limit=10)
    execution_entries = audit.recent("execution", limit=10)
    assert plan_entries[0]["details"]["code"] == "NAV_INVALID_REQUEST"
    assert execution_entries[0]["details"]["task_id"] == "nav_test"
    assert json.loads((tmp_path / "navigation_plans.jsonl").read_text(encoding="utf-8"))["kind"] == "plan"


def test_navigation_audit_filters_by_task_or_plan(tmp_path: Path):
    audit = NavigationAuditLog(tmp_path / "plans.jsonl", tmp_path / "execution.jsonl")
    audit.record("execution", "segment_landed", task_id="a", plan_id="p1")
    audit.record("execution", "segment_landed", task_id="b", plan_id="p2")
    assert len(audit.recent("execution", task_id="a")) == 1
    assert len(audit.recent("execution", plan_id="p2")) == 1


def test_navigation_audit_preserves_nested_route_action_coordinates(tmp_path: Path):
    audit = NavigationAuditLog(tmp_path / "plans.jsonl", tmp_path / "execution.jsonl")
    audit.record(
        "plan", "plan_ready", segments=[{"actions": [{
            "direction": "East",
            "from": {"zone_id": 441, "x": 4, "y": 0, "z": 7},
            "to": {"zone_id": 441, "x": 5, "y": 0, "z": 7},
        }]}],
    )
    action = audit.recent("plan")[0]["details"]["segments"][0]["actions"][0]
    assert action["from"]["x"] == 4
    assert action["to"]["z"] == 7
