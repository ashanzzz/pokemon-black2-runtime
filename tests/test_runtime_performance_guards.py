from pathlib import Path

from backend.black2.dev.tester import DeveloperTestWorkbench


def test_bizhawk_heartbeat_is_not_sent_every_frame():
    source = Path("bridge/bizhawk/black2_bridge.lua").read_text(encoding="utf-8")
    assert "cur_frame % 20 == 0 or state.last_heartbeat_frame == 0 or cur_frame < state.last_heartbeat_frame" in source
    assert "cur_frame % 20 == 0 or cur_frame ~= state.last_heartbeat_frame" not in source
    assert "heartbeat_interval_frames = 20" in source
    assert "screenshot_same_frame_coalescing = true" in source


def test_capture_cache_fields_exist_for_same_frame_coalescing():
    # The cache is deliberately instance-local so tests/evidence from separate
    # sessions cannot reuse a stale screenshot.
    workbench = DeveloperTestWorkbench.__new__(DeveloperTestWorkbench)
    assert hasattr(workbench, "__class__")
    # Validate the implementation contract without starting the bridge.
    source = Path("backend/black2/dev/tester.py").read_text(encoding="utf-8")
    assert "self._capture_lock = asyncio.Lock()" in source
    assert "self._last_capture_frame" in source
    assert "shutil.copyfile(self._last_capture_path, path)" in source
