from pathlib import Path


def test_radar_uses_target_period_not_interval_plus_http_time():
    for name in ("tools/watch_radar.ps1", "tools/radar_compare.ps1"):
        source = Path(name).read_text(encoding="utf-8-sig")
        assert "$frameStartedTick = [System.Diagnostics.Stopwatch]::GetTimestamp()" in source
        assert "$remainingMs = [Math]::Max(0, $IntervalMs - [int]$elapsedMs)" in source
        assert "Start-Sleep -Milliseconds $remainingMs" in source
        assert "Start-Sleep -Milliseconds $IntervalMs" not in source
        assert "HTTP/RPC" in source or "RPC:" in source


def test_radar_transport_hot_paths_are_cached_and_coalesced():
    route_source = Path("backend/black2/api/navigation_routes.py").read_text(encoding="utf-8")
    static_source = Path("backend/black2/world/static_navigation.py").read_text(encoding="utf-8")
    assert "_runtime_actor_sample_lock = asyncio.Lock()" in route_source
    assert "_RUNTIME_ACTOR_CACHE_TTL" in route_source
    assert "fast_preview=(mode in {\"coarse\", \"fuzzy\"})" in route_source
    assert "_event_overlay_lookup_cache" in static_source
    assert "_warp_doorstep_cache" in static_source
    assert "target_world_y = float(y) * 16.0" in static_source


def test_zero_jitter_guideline_is_recorded_in_agents():
    source = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "零抖动防抽搐工程准则" in source
    assert "底行严禁输出尾随换行符" in source
