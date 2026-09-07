"""Pokémon Black 2 Semantic Runtime - Process Probe (Layer 0)"""

import os
import subprocess
import ctypes
from ctypes import wintypes
from locale import getpreferredencoding
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


_SAVESTATE_POPUP_MARKERS = (
    "savestate sync settings mismatch",
    "different core",
    "different sync settings",
    "loadstate cancelled",
    "load state cancelled",
)


def _empty_popup() -> Dict[str, Any]:
    return {
        "present": False,
        "blocking": False,
        "kind": None,
        "title": None,
        "text": None,
        "api_dismissible": False,
    }


def public_bizhawk_popup(popup: Dict[str, Any] | None) -> Dict[str, Any]:
    """Remove native window handles before returning popup state over HTTP."""
    return {
        str(key): value
        for key, value in (popup or {}).items()
        if not str(key).startswith("_")
    }


def classify_bizhawk_popup(title: str = "", text: str = "") -> Dict[str, Any]:
    """Classify only known BizHawk modal warnings; never guess arbitrary dialogs."""
    combined = " ".join(str(value or "") for value in (title, text)).casefold()
    if any(marker in combined for marker in _SAVESTATE_POPUP_MARKERS):
        return {
            "present": True,
            "blocking": True,
            "kind": "savestate_sync_settings_mismatch",
            "title": str(title or "") or None,
            "text": str(text or "") or None,
            "api_dismissible": False,
        }
    return _empty_popup()


class BizHawkProcessInfo(BaseModel):
    running: bool
    pid: Optional[int] = None
    exe_path: Optional[str] = None
    cmdline: List[str] = Field(default_factory=list)
    status: str = "stopped"
    popup: Dict[str, Any] = Field(default_factory=_empty_popup)


def _decode_command_output(payload: bytes) -> str:
    """Decode Windows command output without allowing locale mismatches to leak."""
    for encoding in ("utf-8", "utf-16", getpreferredencoding(False)):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode(getpreferredencoding(False), errors="replace")


def _window_text(user32: Any, hwnd: Any) -> str:
    value = ctypes.create_unicode_buffer(1024)
    user32.GetWindowTextW(hwnd, value, len(value))
    return value.value.strip()


def _window_class(user32: Any, hwnd: Any) -> str:
    value = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, value, len(value))
    return value.value.strip()


def probe_bizhawk_popup(pid: int | None) -> Dict[str, Any]:
    """Read a known blocking BizHawk modal through Win32, without input injection."""
    if os.name != "nt" or not pid:
        return _empty_popup()

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        enum_proc_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows.argtypes = [enum_proc_type, wintypes.LPARAM]
        user32.EnumWindows.restype = ctypes.c_bool
        user32.EnumChildWindows.argtypes = [wintypes.HWND, enum_proc_type, wintypes.LPARAM]
        user32.EnumChildWindows.restype = ctypes.c_bool
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = ctypes.c_bool

        candidates: list[tuple[Any, str, str, list[tuple[Any, str, str, int]]]] = []

        def collect_window(hwnd: Any, _lparam: Any) -> bool:
            owner_pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
            if int(owner_pid.value) != int(pid) or not user32.IsWindowVisible(hwnd):
                return True
            title = _window_text(user32, hwnd)
            klass = _window_class(user32, hwnd)
            children: list[tuple[Any, str, str, int]] = []

            def collect_child(child: Any, _child_lparam: Any) -> bool:
                if not user32.IsWindowVisible(child):
                    return True
                children.append((child, _window_text(user32, child), _window_class(user32, child), 0))
                return True

            child_proc = enum_proc_type(collect_child)
            user32.EnumChildWindows(hwnd, child_proc, 0)
            candidates.append((hwnd, title, klass, children))
            return True

        window_proc = enum_proc_type(collect_window)
        user32.EnumWindows(window_proc, 0)
        for hwnd, title, klass, children in candidates:
            child_text = "\n".join(text for _child, text, _child_class, _ctrl_id in children if text)
            popup = classify_bizhawk_popup(title, child_text)
            if not popup["present"]:
                continue
            button = next(
                (
                    child
                    for child, text, child_class, _ctrl_id in children
                    if child_class.casefold() == "button"
                    and text.replace("&", "").strip().casefold() in {"确定", "ok"}
                ),
                None,
            )
            popup.update({
                "window_class": klass,
                "button": "确定" if button is not None else None,
                "api_dismissible": button is not None,
                "_window_handle": hwnd,
                "_button_handle": button,
            })
            return popup
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    return _empty_popup()


def dismiss_bizhawk_popup(pid: int | None) -> Dict[str, Any]:
    """Dismiss only the known savestate mismatch modal via a bounded Win32 API call."""
    popup = probe_bizhawk_popup(pid)
    public_popup = {key: value for key, value in popup.items() if not key.startswith("_")}
    if not popup.get("present"):
        return {"ok": True, "dismissed": False, "reason": "popup_not_found", "popup": public_popup}
    button = popup.get("_button_handle")
    if button is None:
        return {"ok": False, "dismissed": False, "reason": "dismiss_button_not_found", "popup": public_popup}

    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.SendMessageTimeoutW.argtypes = [
            wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
            wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.DWORD),
        ]
        user32.SendMessageTimeoutW.restype = wintypes.LPARAM
        result = wintypes.DWORD()
        sent = bool(user32.SendMessageTimeoutW(
            button, 0x00F5, 0, 0, 0x0002, 2000, ctypes.byref(result),
        ))
        still_visible = bool(user32.IsWindowVisible(popup.get("_window_handle")))
        dismissed = sent and not still_visible
        return {
            "ok": dismissed,
            "dismissed": dismissed,
            "reason": "api_bm_click" if dismissed else "popup_still_visible",
            "popup": public_popup,
        }
    except (AttributeError, OSError, TypeError, ValueError):
        return {"ok": False, "dismissed": False, "reason": "win32_api_error", "popup": public_popup}


def _process_info(*, pid: int, exe_path: Any, status: str = "running") -> BizHawkProcessInfo:
    return BizHawkProcessInfo(
        running=True,
        pid=pid,
        exe_path=exe_path,
        status=status,
        popup=probe_bizhawk_popup(pid),
    )


def probe_bizhawk_process() -> BizHawkProcessInfo:
    """Find running EmuHawk / BizHawk process on Windows without touching memory."""
    try:
        # Query tasklist or powershell
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Process | Where-Object { $_.ProcessName -match '(?i)emuhawk|bizhawk' } | Select-Object -First 1 Id, ProcessName, Path | ConvertTo-Json"
        ]
        res = subprocess.run(cmd, capture_output=True, timeout=5)
        stdout = _decode_command_output(res.stdout)
        if res.returncode == 0 and stdout.strip():
            import json
            data = json.loads(stdout.strip())
            if isinstance(data, dict) and "Id" in data:
                return BizHawkProcessInfo(
                    **_process_info(
                        pid=int(data.get("Id")),
                        exe_path=data.get("Path"),
                    ).model_dump()
                )
    except Exception as e:
        pass

    # Fallback to wmic or tasklist
    try:
        res = subprocess.run(["tasklist", "/FI", "IMAGENAME eq EmuHawk.exe", "/FO", "CSV"], capture_output=True, timeout=5)
        stdout = _decode_command_output(res.stdout)
        if "EmuHawk.exe" in stdout:
            lines = stdout.strip().splitlines()
            for line in lines[1:]:
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == "emuhawk.exe":
                    pid = int(parts[1])
                    return _process_info(pid=pid, exe_path="EmuHawk.exe")
    except Exception:
        pass

    return BizHawkProcessInfo(running=False, status="not_found")
