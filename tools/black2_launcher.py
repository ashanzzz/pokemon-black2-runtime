#!/usr/bin/env python3
"""One-click local supervisor for the Pokémon Black 2 runtime workbench.

v10.1 lifecycle rules:
- only one launcher GUI instance per project checkout;
- one-click start never creates a second owned backend/emulator instance;
- stopping backend services does not close EmuHawk;
- closing EmuHawk does not stop backend services;
- backend stop discovers every project-owned ``run_runtime.py`` process so a
  Web-triggered replacement process cannot survive because launcher_state only
  remembered the original PID.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtime"
CONFIG = RUNTIME / "runtime.local.json"
STATE = RUNTIME / "launcher_state.json"
LOG = ROOT / "logs" / "launcher.log"
WINDOW_TITLE = "Pokémon Black 2 Workbench · 一键启动器"
_RUN_RUNTIME = (ROOT / "run_runtime.py").resolve()
_INSTANCE_KEY = hashlib.sha256(str(ROOT.resolve()).lower().encode("utf-8")).hexdigest()[:20]
_LAUNCHER_MUTEX_HANDLE: int | None = None
_FALLBACK_LOCK_FD: int | None = None


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat(timespec='seconds')} {message}\n")


def cfg() -> dict[str, Any]:
    config = _load(CONFIG)
    config.setdefault("http_host", "127.0.0.1")
    config.setdefault("http_port", 8765)
    return config


def url(config: dict[str, Any]) -> str:
    return f"http://{config['http_host']}:{int(config['http_port'])}"


def _json_get(config: dict[str, Any], path: str, timeout: float = 1.3) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url(config) + path, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
            return value if isinstance(value, dict) else None
    except Exception:
        return None


def health(config: dict[str, Any]) -> dict[str, Any] | None:
    return _json_get(config, "/health")


def runtime_control_status(config: dict[str, Any]) -> dict[str, Any] | None:
    return _json_get(config, "/api/v1/runtime/control/status")


def bridge(config: dict[str, Any]) -> bool:
    value = _json_get(config, "/api/bizhawk/status")
    return bool(value and value.get("connected"))


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            return str(int(pid)) in result.stdout
        except (OSError, subprocess.SubprocessError):
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def backend_listener_pid(config: dict[str, Any]) -> int | None:
    """Find the PID bound to the configured local HTTP port."""
    if os.name != "nt":
        return None
    port = str(int(config["http_port"]))
    try:
        result = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) < 5 or fields[-2].upper() != "LISTENING" or not fields[-1].isdigit():
                continue
            local = fields[1]
            if not local.endswith(":" + port):
                continue
            host = local.rsplit(":", 1)[0]
            if host in {"127.0.0.1", "[::1]", "0.0.0.0", "[::]"}:
                return int(fields[-1])
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _powershell_process_rows() -> list[dict[str, Any]]:
    """Return a bounded process table used only for lifecycle ownership checks."""
    if os.name != "nt":
        return []
    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        parsed = json.loads(result.stdout)
        if isinstance(parsed, dict):
            parsed = [parsed]
        return [row for row in parsed if isinstance(row, dict)] if isinstance(parsed, list) else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _norm_path_text(value: str | Path | None) -> str:
    if value is None:
        return ""
    try:
        return os.path.normcase(os.path.abspath(os.path.normpath(str(value))))
    except Exception:
        return os.path.normcase(str(value))


def _command_mentions_project_backend(command_line: str | None) -> bool:
    if not command_line:
        return False
    command = os.path.normcase(str(command_line)).replace("/", "\\")
    target = _norm_path_text(_RUN_RUNTIME).replace("/", "\\")
    root = _norm_path_text(ROOT).replace("/", "\\")
    # Require both the canonical script and checkout root so a similarly named
    # runtime in another checkout is not treated as ours.
    return target in command and root in command


def project_backend_pids(rows: Iterable[dict[str, Any]] | None = None) -> list[int]:
    process_rows = list(rows) if rows is not None else _powershell_process_rows()
    owned: set[int] = set()
    for row in process_rows:
        try:
            pid = int(row.get("ProcessId") or row.get("process_id") or 0)
        except (TypeError, ValueError):
            continue
        command = row.get("CommandLine") or row.get("command_line")
        if pid > 0 and _command_mentions_project_backend(str(command or "")):
            owned.add(pid)
    return sorted(owned)


def _pid_is_project_backend(pid: int, rows: Iterable[dict[str, Any]] | None = None) -> bool:
    if pid <= 0:
        return False
    process_rows = list(rows) if rows is not None else _powershell_process_rows()
    for row in process_rows:
        try:
            row_pid = int(row.get("ProcessId") or row.get("process_id") or 0)
        except (TypeError, ValueError):
            continue
        if row_pid == pid:
            return _command_mentions_project_backend(str(row.get("CommandLine") or row.get("command_line") or ""))
    return False


def _configured_emuhawk_rows(config: dict[str, Any], rows: Iterable[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    expected = _norm_path_text(config.get("bizhawk_path"))
    if not expected:
        return []
    process_rows = list(rows) if rows is not None else _powershell_process_rows()
    matches = []
    for row in process_rows:
        executable = _norm_path_text(row.get("ExecutablePath") or row.get("executable_path"))
        if executable and executable == expected:
            matches.append(row)
    return matches


def _append_pid_history(state: dict[str, Any], pid: int) -> None:
    history = []
    for value in state.get("backend_pid_history") or []:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0 and parsed not in history:
            history.append(parsed)
    if pid > 0 and pid not in history:
        history.append(pid)
    state["backend_pid_history"] = history[-24:]


def ensure_env() -> Path:
    target = ROOT / ".venv" / "Scripts" / "python.exe"
    created = not target.is_file()
    if created:
        _log("creating local .venv")
        subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], cwd=ROOT, check=True)
    if not target.is_file():
        raise RuntimeError("创建 .venv 失败")
    requirement = ROOT / "requirements.txt"
    pip_ready = subprocess.run([str(target), "-m", "pip", "--version"], cwd=ROOT, capture_output=True).returncode == 0
    if not pip_ready:
        _log("bootstrapping local pip")
        subprocess.run([str(target), "-m", "ensurepip", "--upgrade"], cwd=ROOT, check=True)
    if requirement.is_file() and (created or not pip_ready):
        _log("installing requirements")
        subprocess.run([str(target), "-m", "pip", "install", "-r", str(requirement)], cwd=ROOT, check=True)
    return target


def validate(config: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    bizhawk = Path(str(config.get("bizhawk_path") or ""))
    rom = Path(str(config.get("rom_path") or ""))
    if not bizhawk.is_file() or bizhawk.name.lower() != "emuhawk.exe":
        problems.append("请选择 BizHawk 的 EmuHawk.exe")
    if not rom.is_file() or rom.suffix.lower() != ".nds":
        problems.append("请选择你合法持有的 .nds ROM")
    if not (ROOT / "bridge" / "bizhawk" / "black2_bridge.lua").is_file():
        problems.append("找不到 black2_bridge.lua")
    return problems


def env_for(config: dict[str, Any]) -> dict[str, str]:
    environment = os.environ.copy()
    environment["BLACK2_ROM_PATH"] = str(Path(config["rom_path"]).resolve())
    environment["BLACK2_BIZHAWK_DIR"] = str(Path(config["bizhawk_path"]).resolve().parent)
    environment["BLACK2_PROJECT_ROOT"] = str(ROOT.resolve())
    environment.setdefault("BLACK2_ENABLE_LEGACY_MAP_CACHE", "0")
    return environment


def wait(fn, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if fn():
            return True
        time.sleep(0.25)
    return False


def _backend_is_this_checkout(config: dict[str, Any]) -> bool:
    control = runtime_control_status(config)
    if not control:
        return False
    reported_root = control.get("project_root")
    if reported_root:
        return _norm_path_text(reported_root) == _norm_path_text(ROOT)
    pid = control.get("pid")
    try:
        return _pid_is_project_backend(int(pid or 0))
    except (TypeError, ValueError):
        return False


def _start_backend(config: dict[str, Any], state: dict[str, Any], environment: dict[str, str]) -> None:
    if health(config):
        if _backend_is_this_checkout(config):
            actual = runtime_control_status(config) or {}
            try:
                pid = int(actual.get("pid") or backend_listener_pid(config) or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0:
                state["backend_pid"] = pid
                _append_pid_history(state, pid)
            return
        raise RuntimeError(f"HTTP 端口 {config['http_port']} 已被其他服务占用；不会启动第二个后端")

    listener = backend_listener_pid(config)
    if listener:
        raise RuntimeError(f"HTTP 端口 {config['http_port']} 已被 PID {listener} 占用；不会误杀或覆盖外部服务")

    # If a delayed replacement from /restart exists but has not bound yet,
    # treat it as the current backend owner instead of starting a duplicate.
    existing = project_backend_pids()
    if existing:
        _log(f"existing project backend process(es) detected before bind: {existing}")
        if wait(lambda: bool(health(config)), 5):
            actual = runtime_control_status(config) or {}
            pid = int(actual.get("pid") or backend_listener_pid(config) or existing[-1])
            state["backend_pid"] = pid
            for existing_pid in existing:
                _append_pid_history(state, existing_pid)
            _append_pid_history(state, pid)
            return
        raise RuntimeError(f"检测到本项目后端进程 {existing}，但 HTTP 未就绪；请先运行 STOP_BLACK2.cmd 清理后再启动")

    logs = ROOT / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    runtime_log = (logs / "runtime-supervisor.log").open("a", encoding="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    runtime_python = ensure_env()
    process = subprocess.Popen(
        [str(runtime_python), str(_RUN_RUNTIME)],
        cwd=ROOT,
        env=environment,
        stdout=runtime_log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
    )
    state["backend_pid"] = process.pid
    _append_pid_history(state, process.pid)
    _log(f"backend started pid={process.pid}")
    if not wait(lambda: bool(health(config)), 15):
        raise RuntimeError("后端启动失败，请查看 logs/runtime-supervisor.log")


def _start_emulator(config: dict[str, Any], state: dict[str, Any], environment: dict[str, str]) -> None:
    if bridge(config):
        return

    owned_pid = int(state.get("emuhawk_pid") or 0)
    if state.get("emuhawk_owned") and pid_alive(owned_pid):
        # It is already ours; do not launch another copy just because the Lua
        # bridge is still initializing or temporarily disconnected.
        wait(lambda: bridge(config), 12)
        return

    if os.name == "nt":
        external = _configured_emuhawk_rows(config)
        if external:
            pids = []
            for row in external:
                try:
                    pids.append(int(row.get("ProcessId") or 0))
                except (TypeError, ValueError):
                    pass
            state.pop("emuhawk_pid", None)
            state["emuhawk_owned"] = False
            state["external_emuhawk_pids"] = pids
            _save(STATE, state)
            raise RuntimeError(
                "检测到已运行但不属于本启动器的 EmuHawk。为避免重复模拟器，本项目不会再启动第二个；"
                "请关闭该 EmuHawk 后重试，或在其中手动加载 bridge/bizhawk/black2_bridge.lua。"
            )

    process = subprocess.Popen(
        [
            config["bizhawk_path"],
            f"--lua={(ROOT / 'bridge' / 'bizhawk' / 'black2_bridge.lua').resolve()}",
            config["rom_path"],
        ],
        cwd=Path(config["bizhawk_path"]).parent,
        env=environment,
    )
    state["emuhawk_pid"] = process.pid
    state["emuhawk_owned"] = True
    state["emuhawk_started_path"] = str(Path(config["bizhawk_path"]).resolve())
    state.pop("external_emuhawk_pids", None)
    _log(f"EmuHawk started pid={process.pid}")
    wait(lambda: bridge(config), 12)


def start(open_browser: bool = True) -> dict[str, Any]:
    config = cfg()
    problems = validate(config)
    if problems:
        raise RuntimeError("；".join(problems))
    config["bizhawk_path"] = str(Path(config["bizhawk_path"]).resolve())
    config["rom_path"] = str(Path(config["rom_path"]).resolve())
    _save(CONFIG, config)
    state = _load(STATE)
    environment = env_for(config)

    _start_backend(config, state, environment)
    state.update(
        {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "rom_path": config["rom_path"],
            "bizhawk_path": config["bizhawk_path"],
        }
    )
    _save(STATE, state)

    # Emulator start is deliberately a separate ownership operation. If it
    # fails, the backend stays available so diagnostics can explain why.
    _start_emulator(config, state, environment)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save(STATE, state)

    if open_browser:
        webbrowser.open(url(config) + "/")
    return status()


def stop_backend() -> dict[str, Any]:
    """Stop every backend process owned by this checkout; never close EmuHawk."""
    config = cfg()
    state = _load(STATE)
    rows = _powershell_process_rows() if os.name == "nt" else []
    candidates: set[int] = set(project_backend_pids(rows if os.name == "nt" else None))

    for value in [state.get("backend_pid"), *(state.get("backend_pid_history") or [])]:
        try:
            pid = int(value or 0)
        except (TypeError, ValueError):
            continue
        if pid > 0 and (os.name != "nt" or _pid_is_project_backend(pid, rows)):
            candidates.add(pid)

    control = runtime_control_status(config)
    if control and _norm_path_text(control.get("project_root")) == _norm_path_text(ROOT):
        try:
            pid = int(control.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid > 0 and (os.name != "nt" or _pid_is_project_backend(pid, rows)):
            candidates.add(pid)

    stopped: list[int] = []
    failed: list[int] = []
    if os.name == "nt":
        # Kill highest PIDs first so a Web-restart replacement cannot remain
        # orphaned after only the original launcher PID was terminated.
        for pid in sorted(candidates, reverse=True):
            try:
                result = subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                if result.returncode == 0 or not pid_alive(pid):
                    stopped.append(pid)
                else:
                    failed.append(pid)
            except (OSError, subprocess.SubprocessError):
                failed.append(pid)
    else:
        for pid in sorted(candidates, reverse=True):
            try:
                os.kill(pid, 15)
                stopped.append(pid)
            except OSError:
                if pid_alive(pid):
                    failed.append(pid)

    if candidates:
        wait(lambda: not bool(health(config)), 4)
    state.pop("backend_pid", None)
    live_history: list[int] = []
    for value in state.get("backend_pid_history") or []:
        try:
            history_pid = int(value)
        except (TypeError, ValueError):
            continue
        if pid_alive(history_pid):
            live_history.append(history_pid)
    state["backend_pid_history"] = live_history
    state["last_backend_stop"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "requested": sorted(candidates),
        "stopped": stopped,
        "failed": failed,
    }
    _save(STATE, state)
    _log(f"backend stop requested candidates={sorted(candidates)} stopped={stopped} failed={failed}")
    result = status()
    result["backend_stop"] = state["last_backend_stop"]
    return result


def _post_wm_close(pid: int) -> bool:
    if os.name != "nt" or pid <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        posted = {"value": False}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            process_id = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if process_id.value == pid and ctypes.windll.user32.IsWindowVisible(hwnd):
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
                posted["value"] = True
            return True

        ctypes.windll.user32.EnumWindows(callback, 0)
        return bool(posted["value"])
    except Exception:
        return False


def close_emulator() -> dict[str, Any]:
    """Gracefully close only the EmuHawk instance launched by this checkout."""
    config = cfg()
    state = _load(STATE)
    try:
        pid = int(state.get("emuhawk_pid") or 0)
    except (TypeError, ValueError):
        pid = 0

    if not state.get("emuhawk_owned") or pid <= 0:
        result = status()
        result["emulator_close"] = {"requested": False, "reason": "no_project_owned_emulator"}
        return result

    if os.name == "nt":
        rows = _powershell_process_rows()
        matching = {int(row.get("ProcessId") or 0) for row in _configured_emuhawk_rows(config, rows)}
        if pid not in matching:
            # PID reuse or config change: never close an unrelated process.
            state["emuhawk_owned"] = False
            state.pop("emuhawk_pid", None)
            _save(STATE, state)
            result = status()
            result["emulator_close"] = {"requested": False, "reason": "ownership_mismatch"}
            return result

    posted = _post_wm_close(pid) if pid_alive(pid) else True
    if posted:
        wait(lambda: not pid_alive(pid), 5)
    alive = pid_alive(pid)
    if not alive:
        state.pop("emuhawk_pid", None)
        state["emuhawk_owned"] = False
    state["last_emulator_close"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "pid": pid,
        "wm_close_posted": posted,
        "still_running": alive,
    }
    _save(STATE, state)
    _log(f"EmuHawk close requested pid={pid} posted={posted} still_running={alive}")
    result = status()
    result["emulator_close"] = state["last_emulator_close"]
    return result


def stop() -> dict[str, Any]:
    """Compatibility alias: STOP_BLACK2 means stop backend services only."""
    return stop_backend()


def status() -> dict[str, Any]:
    config = cfg()
    state = _load(STATE)
    control = runtime_control_status(config)
    owned_backend = project_backend_pids() if os.name == "nt" else []
    try:
        emu_pid = int(state.get("emuhawk_pid") or 0)
    except (TypeError, ValueError):
        emu_pid = 0
    return {
        "backend_online": bool(health(config)),
        "bridge_connected": bridge(config),
        "backend_pid": (control or {}).get("pid") or state.get("backend_pid"),
        "backend_owned_pids": owned_backend,
        "emuhawk_pid": emu_pid or None,
        "emuhawk_owned": bool(state.get("emuhawk_owned") and pid_alive(emu_pid)),
        "rom_configured": Path(str(config.get("rom_path") or "")).is_file(),
        "bizhawk_configured": Path(str(config.get("bizhawk_path") or "")).is_file(),
        "url": url(config),
    }


def _focus_launcher_pid(pid: int) -> None:
    if os.name != "nt" or pid <= 0:
        return
    try:
        import ctypes
        from ctypes import wintypes

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            process_id = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if process_id.value == pid:
                title_len = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if title_len >= 0:
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
            return True

        ctypes.windll.user32.EnumWindows(callback, 0)
    except Exception:
        pass


def _acquire_launcher_single_instance() -> bool:
    """Acquire a per-checkout launcher lock and focus the existing GUI on collision."""
    global _LAUNCHER_MUTEX_HANDLE, _FALLBACK_LOCK_FD
    state = _load(STATE)
    existing_pid = int(state.get("launcher_pid") or 0)

    if os.name == "nt":
        try:
            import ctypes
            name = f"Local\\PokemonBlack2RuntimeLauncher_{_INSTANCE_KEY}"
            handle = ctypes.windll.kernel32.CreateMutexW(None, False, name)
            if not handle:
                raise OSError("CreateMutexW failed")
            if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                if pid_alive(existing_pid):
                    _focus_launcher_pid(existing_pid)
                ctypes.windll.kernel32.CloseHandle(handle)
                return False
            _LAUNCHER_MUTEX_HANDLE = int(handle)
            return True
        except Exception as error:
            _log(f"named mutex unavailable, using lock file: {error!r}")

    RUNTIME.mkdir(parents=True, exist_ok=True)
    lock_path = RUNTIME / "launcher.lock"
    for _ in range(2):
        try:
            _FALLBACK_LOCK_FD = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(_FALLBACK_LOCK_FD, str(os.getpid()).encode("ascii"))
            return True
        except FileExistsError:
            try:
                lock_pid = int(lock_path.read_text(encoding="ascii").strip() or 0)
            except (OSError, ValueError):
                lock_pid = 0
            if pid_alive(lock_pid):
                if os.name == "nt":
                    _focus_launcher_pid(lock_pid)
                return False
            try:
                lock_path.unlink()
            except OSError:
                return False
    return False


def _release_launcher_single_instance() -> None:
    global _LAUNCHER_MUTEX_HANDLE, _FALLBACK_LOCK_FD
    state = _load(STATE)
    if int(state.get("launcher_pid") or 0) == os.getpid():
        state.pop("launcher_pid", None)
        _save(STATE, state)
    if os.name == "nt" and _LAUNCHER_MUTEX_HANDLE:
        try:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(_LAUNCHER_MUTEX_HANDLE)
        except Exception:
            pass
        _LAUNCHER_MUTEX_HANDLE = None
    if _FALLBACK_LOCK_FD is not None:
        try:
            os.close(_FALLBACK_LOCK_FD)
        except OSError:
            pass
        _FALLBACK_LOCK_FD = None
        try:
            (RUNTIME / "launcher.lock").unlink()
        except OSError:
            pass


class WindowsTray:
    """Small dependency-free Windows notification-area menu for the Tk launcher."""

    CALLBACK = 0x8000 + 20
    WM_LBUTTONUP = 0x0202
    WM_RBUTTONUP = 0x0205
    NIM_ADD = 0
    NIM_DELETE = 2
    NIF_MESSAGE = 1
    NIF_ICON = 2
    NIF_TIP = 4

    def __init__(self, root, show, stop_backend_action, close_emulator_action, quit_action):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.root = root
        self.show = show
        self.stop_backend_action = stop_backend_action
        self.close_emulator_action = close_emulator_action
        self.quit_action = quit_action
        self.visible = False
        self.user32 = ctypes.windll.user32
        self.shell32 = ctypes.windll.shell32
        self.hwnd = root.winfo_id()

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HANDLE),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uTimeoutOrVersion", wintypes.UINT),
                ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
                ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HANDLE),
            ]

        self.data = NOTIFYICONDATAW()
        self.data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.data.hWnd = self.hwnd
        self.data.uID = 1
        self.data.uFlags = self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP
        self.data.uCallbackMessage = self.CALLBACK
        self.data.hIcon = self.user32.LoadIconW(None, 32512)
        self.data.szTip = "Pokémon Black 2 Launcher"
        PROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t)
        set_proc = self.user32.SetWindowLongPtrW
        set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        set_proc.restype = ctypes.c_void_p
        call_proc = self.user32.CallWindowProcW
        call_proc.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
        call_proc.restype = ctypes.c_ssize_t
        self.old_proc = None

        @PROC
        def wndproc(hwnd, msg, wparam, lparam):
            if msg == self.CALLBACK:
                if lparam == self.WM_LBUTTONUP:
                    root.after(0, self.show)
                    return 0
                if lparam == self.WM_RBUTTONUP:
                    root.after(0, self.menu)
                    return 0
            return call_proc(self.old_proc, hwnd, msg, wparam, lparam)

        self.wndproc = wndproc
        self.old_proc = set_proc(self.hwnd, -4, ctypes.cast(wndproc, ctypes.c_void_p).value)

    def show_icon(self):
        if not self.visible:
            self.shell32.Shell_NotifyIconW(self.NIM_ADD, self.ctypes.byref(self.data))
            self.visible = True

    def hide_icon(self):
        if self.visible:
            self.shell32.Shell_NotifyIconW(self.NIM_DELETE, self.ctypes.byref(self.data))
            self.visible = False

    def menu(self):
        from ctypes import wintypes

        point = wintypes.POINT()
        self.user32.GetCursorPos(self.ctypes.byref(point))
        menu = self.user32.CreatePopupMenu()
        self.user32.AppendMenuW(menu, 0, 1, "显示启动器")
        self.user32.AppendMenuW(menu, 0x800, 0, None)
        self.user32.AppendMenuW(menu, 0, 2, "停止后端服务")
        self.user32.AppendMenuW(menu, 0, 3, "关闭模拟器")
        self.user32.AppendMenuW(menu, 0x800, 0, None)
        self.user32.AppendMenuW(menu, 0, 4, "退出启动器")
        self.user32.SetForegroundWindow(self.hwnd)
        choice = self.user32.TrackPopupMenu(menu, 0x102, point.x, point.y, 0, self.hwnd, None)
        self.user32.DestroyMenu(menu)
        if choice == 1:
            self.show()
        elif choice == 2:
            self.stop_backend_action()
        elif choice == 3:
            self.close_emulator_action()
        elif choice == 4:
            self.quit_action()


def gui() -> None:
    if not _acquire_launcher_single_instance():
        return
    try:
        ensure_env()
        import tkinter as tk
        from tkinter import filedialog, messagebox

        root = tk.Tk()
        root.title(WINDOW_TITLE)
        root.geometry("760x510")
        config = cfg()
        biz = tk.StringVar(value=str(config.get("bizhawk_path") or ""))
        rom = tk.StringVar(value=str(config.get("rom_path") or ""))
        state_text = tk.StringVar(value="")
        state = _load(STATE)
        state["launcher_pid"] = os.getpid()
        state["launcher_started_at"] = datetime.now().isoformat(timespec="seconds")
        _save(STATE, state)
        tray: WindowsTray | None = None

        def show_window():
            if tray is not None:
                tray.hide_icon()
            root.deiconify()
            root.lift()
            root.focus_force()

        def quit_launcher():
            if tray is not None:
                tray.hide_icon()
            root.destroy()

        def minimize_to_tray():
            root.withdraw()
            if tray is not None:
                tray.show_icon()

        def save_paths():
            current = cfg()
            current["bizhawk_path"] = biz.get().strip()
            current["rom_path"] = rom.get().strip()
            _save(CONFIG, current)

        def choose_biz():
            path = filedialog.askopenfilename(
                title="选择 EmuHawk.exe",
                filetypes=[("EmuHawk", "EmuHawk.exe"), ("EXE", "*.exe")],
            )
            if path:
                biz.set(path)
                save_paths()

        def choose_rom():
            path = filedialog.askopenfilename(
                title="选择 .nds ROM",
                filetypes=[("Nintendo DS ROM", "*.nds")],
            )
            if path:
                rom.set(path)
                save_paths()

        def refresh():
            current = status()
            state_text.set(
                f"后端 {'在线' if current['backend_online'] else '未启动'}   ·   "
                f"Bridge {'已连接' if current['bridge_connected'] else '未连接'}   ·   "
                f"EmuHawk {'本启动器拥有' if current['emuhawk_owned'] else '未拥有'}"
            )
            root.after(1500, refresh)

        def go():
            try:
                save_paths()
                start(True)
            except Exception as error:
                messagebox.showerror("启动失败", str(error))

        def halt_backend():
            try:
                result = stop_backend()
                if result.get("backend_online"):
                    messagebox.showwarning("停止后端", "仍检测到 HTTP 服务在线，请查看 logs/launcher.log。")
            except Exception as error:
                messagebox.showerror("停止后端失败", str(error))

        def halt_emulator():
            try:
                result = close_emulator()
                close_result = result.get("emulator_close") or {}
                if close_result.get("still_running"):
                    messagebox.showwarning("关闭模拟器", "已发送 WM_CLOSE，但 EmuHawk 仍在运行；不会强杀模拟器。")
                elif close_result.get("reason") == "no_project_owned_emulator":
                    messagebox.showinfo("关闭模拟器", "当前没有由本启动器创建的 EmuHawk，因此未关闭任何外部模拟器。")
            except Exception as error:
                messagebox.showerror("关闭模拟器失败", str(error))

        if os.name == "nt":
            tray = WindowsTray(root, show_window, halt_backend, halt_emulator, quit_launcher)

        tk.Label(root, text="Pokémon Black 2 Runtime Workbench", font=("Segoe UI", 18, "bold")).pack(
            anchor="w", padx=20, pady=(18, 4)
        )
        tk.Label(
            root,
            text="单实例启动器。‘停止后端服务’只停止本项目后端；‘关闭模拟器’只对本启动器创建的 EmuHawk 发送 WM_CLOSE。",
            fg="#555",
        ).pack(anchor="w", padx=20, pady=(0, 14))
        form = tk.Frame(root)
        form.pack(fill="x", padx=20)
        form.columnconfigure(1, weight=1)
        for row, (label, variable, command) in enumerate(
            [("BizHawk", biz, choose_biz), ("NDS ROM", rom, choose_rom)]
        ):
            tk.Label(form, text=label, width=10, anchor="w").grid(row=row, column=0, padx=6, pady=8)
            tk.Entry(form, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6, pady=8)
            tk.Button(form, text="选择…", command=command).grid(row=row, column=2, padx=6, pady=8)

        tk.Label(root, textvariable=state_text, font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=26, pady=12)
        actions = tk.Frame(root)
        actions.pack(padx=20, pady=8)
        tk.Button(actions, text="▶ 一键启动", width=18, height=2, command=go).grid(row=0, column=0, padx=6)
        tk.Button(actions, text="■ 停止后端服务", width=18, height=2, command=halt_backend).grid(row=0, column=1, padx=6)
        tk.Button(actions, text="□ 关闭模拟器", width=18, height=2, command=halt_emulator).grid(row=0, column=2, padx=6)
        tk.Button(
            actions,
            text="打开 3D Workbench",
            width=18,
            height=2,
            command=lambda: webbrowser.open(url(cfg()) + "/#world"),
        ).grid(row=1, column=0, padx=6, pady=8)
        tk.Button(
            actions,
            text="运行时监控",
            width=18,
            height=2,
            command=lambda: webbrowser.open(url(cfg()) + "/#monitor"),
        ).grid(row=1, column=1, padx=6, pady=8)
        tk.Button(actions, text="退出启动器", width=18, height=2, command=quit_launcher).grid(row=1, column=2, padx=6, pady=8)

        root.protocol("WM_DELETE_WINDOW", minimize_to_tray)
        refresh()
        root.mainloop()
    finally:
        _release_launcher_single_instance()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "cmd",
        nargs="?",
        default="gui",
        choices=["gui", "start", "stop", "stop-backend", "close-emulator", "status"],
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.cmd == "gui":
        gui()
        return 0
    if args.cmd == "start":
        result = start(not args.no_browser)
    elif args.cmd in {"stop", "stop-backend"}:
        result = stop_backend()
    elif args.cmd == "close-emulator":
        result = close_emulator()
    else:
        result = status()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
