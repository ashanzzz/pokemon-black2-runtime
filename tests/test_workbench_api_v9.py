import asyncio

from backend.black2.api.workbench_routes import bootstrap, schema


class _Client:
    is_connected = True


class _Transport:
    bridge_version = "1.8.0-world-lab"


class FakeHub:
    client = _Client()
    transport = _Transport()

    def health(self):
        return {"bridge_connected": True, "frame": 123, "semantic_status": "ready"}

    def snapshot(self):
        return {"format": "black2-runtime-snapshot/v4", "dialogue": {"active": False}}


def test_bootstrap_defaults_to_chinese_and_is_cache_contract():
    result = asyncio.run(bootstrap(FakeHub()))
    assert result["format"] == "black2-workbench-bootstrap/v1"
    assert result["locale"]["default"] == "zh-CN"
    assert result["locale"]["supported"] == ["zh-CN", "en"]
    assert result["performance_policy"]["bootstrap"] == "RuntimeHub cache only"
    assert any(item["id"] == "world" for item in result["workspaces"])
    assert any(item["id"] == "memory" for item in result["workspaces"])


def test_workbench_schema_keeps_heavy_discovery_explicit():
    result = asyncio.run(schema(None))
    assert result["format"] == "black2-workbench-ui-contract/v1"
    assert result["authority"]["player_explicit_discovery"] == "/api/v1/player/runtime"
    assert any("Heavy RAM discovery" in rule for rule in result["rules"])
