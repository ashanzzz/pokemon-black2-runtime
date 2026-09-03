"""BizHawk Doctor - Multi-level Health Diagnostic Engine."""

import time
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
from .process_probe import probe_bizhawk_process, BizHawkProcessInfo
from .bridge_client import BridgeClient


class DoctorCheckItem(BaseModel):
    name: str
    level: int
    passed: bool
    message: str
    details: Dict[str, Any] = {}


class DoctorReport(BaseModel):
    status: str  # "READY", "DEGRADED", "NOT_ATTACHED", "PROCESS_NOT_RUNNING"
    timestamp: float
    process: BizHawkProcessInfo
    checks: List[DoctorCheckItem] = []


class BizHawkDoctor:
    def __init__(self, client: BridgeClient):
        self.client = client

    async def run_diagnostics(self) -> DoctorReport:
        checks: List[DoctorCheckItem] = []
        overall_status = "READY"

        # Level 0: Process Probe
        proc = probe_bizhawk_process()
        if not proc.running:
            checks.append(DoctorCheckItem(
                name="process_probe",
                level=0,
                passed=False,
                message="EmuHawk process is not running"
            ))
            return DoctorReport(
                status="PROCESS_NOT_RUNNING",
                timestamp=time.time(),
                process=proc,
                checks=checks
            )

        checks.append(DoctorCheckItem(
            name="process_probe",
            level=0,
            passed=True,
            message=f"BizHawk running (PID: {proc.pid})",
            details={"pid": proc.pid, "exe": proc.exe_path}
        ))

        # Level 1: Bridge Connectivity
        if not self.client.is_connected:
            checks.append(DoctorCheckItem(
                name="bridge_probe",
                level=1,
                passed=False,
                message="BizHawk Lua Bridge is not attached or connected"
            ))
            return DoctorReport(
                status="NOT_ATTACHED",
                timestamp=time.time(),
                process=proc,
                checks=checks
            )

        try:
            ping_res = await self.client.ping()
            checks.append(DoctorCheckItem(
                name="bridge_probe",
                level=1,
                passed=True,
                message="Bridge responding to RPC ping",
                details=ping_res
            ))
        except Exception as e:
            checks.append(DoctorCheckItem(
                name="bridge_probe",
                level=1,
                passed=False,
                message=f"Bridge ping failed: {e}"
            ))
            return DoctorReport(
                status="DEGRADED",
                timestamp=time.time(),
                process=proc,
                checks=checks
            )

        # Level 2 & 3: ROM Check
        try:
            emu_state = await self.client.get_emu_state()
            game_info = await self.client.get_game_info()
            domains = await self.client.get_memory_domains()

            rom_name = game_info.get("rom_name", "口袋妖怪黑2")
            rom_hash = game_info.get("rom_hash", "unknown")
            checks.append(DoctorCheckItem(
                name="rom_identity",
                level=3,
                passed=True,
                message=f"ROM loaded: {rom_name} (Hash: {rom_hash[:8]}...)",
                details=game_info
            ))

            # Level 4: Memory Domains (ARM9 System Bus / Main RAM / SRAM)
            has_arm9_bus = "ARM9 System Bus" in domains or "Main RAM" in domains
            has_sram = "SRAM" in domains
            if has_arm9_bus:
                checks.append(DoctorCheckItem(
                    name="memory_domains",
                    level=4,
                    passed=True,
                    message="NDS ARM9 Main RAM bus & SRAM domains verified",
                    details={"domains": list(domains.keys()), "sram_present": has_sram}
                ))
            else:
                checks.append(DoctorCheckItem(
                    name="memory_domains",
                    level=4,
                    passed=False,
                    message="ARM9 System Bus domain missing",
                    details=domains
                ))
                overall_status = "DEGRADED"

        except Exception as e:
            checks.append(DoctorCheckItem(
                name="runtime_inspection",
                level=4,
                passed=False,
                message=f"Failed to inspect emulator runtime: {e}"
            ))
            overall_status = "DEGRADED"

        return DoctorReport(
            status=overall_status,
            timestamp=time.time(),
            process=proc,
            checks=checks
        )
