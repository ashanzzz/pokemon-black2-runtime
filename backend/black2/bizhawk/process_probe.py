"""Pokémon Black 2 Semantic Runtime - Process Probe (Layer 0)"""

import os
import subprocess
from locale import getpreferredencoding
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


class BizHawkProcessInfo(BaseModel):
    running: bool
    pid: Optional[int] = None
    exe_path: Optional[str] = None
    cmdline: List[str] = Field(default_factory=list)
    status: str = "stopped"


def _decode_command_output(payload: bytes) -> str:
    """Decode Windows command output without allowing locale mismatches to leak."""
    for encoding in ("utf-8", "utf-16", getpreferredencoding(False)):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode(getpreferredencoding(False), errors="replace")


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
                    running=True,
                    pid=data.get("Id"),
                    exe_path=data.get("Path"),
                    status="running"
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
                    return BizHawkProcessInfo(
                        running=True,
                        pid=pid,
                        exe_path="EmuHawk.exe",
                        status="running"
                    )
    except Exception:
        pass

    return BizHawkProcessInfo(running=False, status="not_found")
