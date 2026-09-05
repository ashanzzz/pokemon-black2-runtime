from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def load_launcher():
    path = Path(__file__).resolve().parents[1] / "tools" / "black2_launcher.py"
    spec = importlib.util.spec_from_file_location("black2_launcher_under_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_backend_pid_filter_is_checkout_scoped():
    launcher = load_launcher()
    ours = str((launcher.ROOT / "run_runtime.py").resolve())
    other = str((launcher.ROOT.parent / "other-copy" / "run_runtime.py").resolve())
    rows = [
        {"ProcessId": 101, "CommandLine": f'"python.exe" "{ours}" --port 8765'},
        {"ProcessId": 202, "CommandLine": f'"python.exe" "{other}" --port 8765'},
        {"ProcessId": 303, "CommandLine": '"python.exe" unrelated.py'},
    ]
    assert launcher.project_backend_pids(rows) == [101]


def test_stop_alias_only_dispatches_backend_stop(monkeypatch):
    launcher = load_launcher()
    called = []
    monkeypatch.setattr(launcher, "stop_backend", lambda: called.append("backend") or {"ok": True})
    monkeypatch.setattr(launcher, "close_emulator", lambda: called.append("emulator") or {"ok": True})
    assert launcher.stop() == {"ok": True}
    assert called == ["backend"]


def test_close_emulator_does_nothing_without_owned_emulator(monkeypatch):
    launcher = load_launcher()
    monkeypatch.setattr(launcher, "cfg", lambda: {})
    monkeypatch.setattr(launcher, "_load", lambda _path: {"emuhawk_owned": False})
    monkeypatch.setattr(launcher, "status", lambda: {"backend_online": True})
    monkeypatch.setattr(launcher, "_post_wm_close", lambda _pid: (_ for _ in ()).throw(AssertionError("must not close external emulator")))
    result = launcher.close_emulator()
    assert result["backend_online"] is True
    assert result["emulator_close"]["reason"] == "no_project_owned_emulator"


def test_stop_backend_kills_all_owned_runtime_processes_not_emulator(monkeypatch):
    launcher = load_launcher()
    ours = str((launcher.ROOT / "run_runtime.py").resolve())
    rows = [
        {"ProcessId": 111, "CommandLine": f'"python.exe" "{ours}" --port 8765'},
        {"ProcessId": 222, "CommandLine": f'"python.exe" "{ours}" --port 8765 --start-delay 2.0'},
        {"ProcessId": 333, "ExecutablePath": "C:/BizHawk/EmuHawk.exe", "CommandLine": '"C:/BizHawk/EmuHawk.exe" game.nds'},
    ]
    state = {"backend_pid": 111, "backend_pid_history": [111, 222], "emuhawk_pid": 333, "emuhawk_owned": True}
    saved = {}
    calls = []

    monkeypatch.setattr(launcher.os, "name", "nt")
    monkeypatch.setattr(launcher, "cfg", lambda: {"http_host": "127.0.0.1", "http_port": 8765})
    monkeypatch.setattr(launcher, "_powershell_process_rows", lambda: rows)
    monkeypatch.setattr(launcher, "_load", lambda _path: dict(state))
    monkeypatch.setattr(launcher, "_save", lambda _path, value: saved.update(value))
    monkeypatch.setattr(launcher, "runtime_control_status", lambda _cfg: {"pid": 222, "project_root": str(launcher.ROOT)})
    monkeypatch.setattr(launcher, "health", lambda _cfg: None)
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: int(pid) in {111, 222, 333})
    monkeypatch.setattr(launcher, "wait", lambda _fn, _seconds: True)
    monkeypatch.setattr(launcher, "_post_wm_close", lambda _pid: (_ for _ in ()).throw(AssertionError("backend stop must not close emulator")))

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", fake_run)
    result = launcher.stop_backend()
    killed = {int(command[2]) for command in calls if command and command[0] == "taskkill"}
    assert killed == {111, 222}
    assert 333 not in killed
    assert result["backend_stop"]["stopped"] == [222, 111]
    assert saved.get("emuhawk_pid") == 333


def test_fallback_launcher_lock_rejects_second_instance(monkeypatch, tmp_path):
    launcher = load_launcher()
    runtime = tmp_path / "runtime"
    state = runtime / "launcher_state.json"
    monkeypatch.setattr(launcher.os, "name", "posix")
    monkeypatch.setattr(launcher, "RUNTIME", runtime)
    monkeypatch.setattr(launcher, "STATE", state)
    monkeypatch.setattr(launcher, "pid_alive", lambda pid: int(pid) == launcher.os.getpid())
    try:
        assert launcher._acquire_launcher_single_instance() is True
        assert launcher._acquire_launcher_single_instance() is False
    finally:
        launcher._release_launcher_single_instance()
