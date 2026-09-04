# Pokémon Black 2 AI Semantic Runtime - FastAPI Web & Bridge API Server

import os
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel

from ..bizhawk.process_probe import probe_bizhawk_process
from ..bizhawk.socket_transport import SocketTransport
from ..bizhawk.bridge_client import BridgeClient
from ..bizhawk.doctor import BizHawkDoctor
from ..memory.reader import MemoryReader
from ..state.engine import SemanticStateEngine
from ..state.universal_snapshot_manager import universal_snapshot_manager
from ..runtime.config import runtime_config
from ..runtime.hub import RuntimeHub
from ..actions.input_engine import ActionEngine
from ..actions.onboarding import OnboardingFlow
from ..observer.presentation import build_observer_presentation
from ..observer.capabilities import capability_store
from ..observer.logger import observer_logger
from ..decoders.dialogue import dialogue_timeline
from ..dev.tester import init_dev_workbench, DeveloperTestWorkbench
from ..world.navigation import navigation_service, ReachabilityResult
from .runtime_routes import configure_runtime_routes, router as runtime_router
from .player_routes import configure_player_routes, router as player_router
from .map_routes import (
    configure_map_routes,
    router as map_router,
    start_cache_observer,
    stop_cache_observer,
)


# Global singletons. Port roles are canonical and environment-overridable.
# HTTP (default 8765) is FastAPI/browser only; TCP (default 8766) is BizHawk Lua only.
transport: SocketTransport = SocketTransport(
    host=runtime_config.bridge_host, port=runtime_config.bridge_port
)
client: BridgeClient = BridgeClient(transport)
memory_reader: MemoryReader = MemoryReader(client)
state_engine: SemanticStateEngine = SemanticStateEngine(memory_reader)
action_engine: ActionEngine = ActionEngine(client, state_engine)
onboarding_flow: OnboardingFlow = OnboardingFlow(client, state_engine)
doctor: BizHawkDoctor = BizHawkDoctor(client)
dev_wb: DeveloperTestWorkbench = init_dev_workbench(client, state_engine)
runtime_hub = RuntimeHub(
    client=client,
    reader=memory_reader,
    state_engine=state_engine,
    transport=transport,
    sample_interval=runtime_config.semantic_sample_interval,
    process_probe=probe_bizhawk_process,
)
configure_map_routes(memory_reader, client)
configure_player_routes(memory_reader)
configure_runtime_routes(runtime_hub)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await transport.connect()
    await runtime_hub.start()
    start_cache_observer(memory_reader)
    observer_logger.log_event(
        "backend_startup",
        f"Semantic Runtime HTTP={runtime_config.http_host}:{runtime_config.http_port} "
        f"BizHawkBridge={runtime_config.bridge_host}:{runtime_config.bridge_port}",
    )
    try:
        yield
    finally:
        await runtime_hub.stop()
        await stop_cache_observer()
        await transport.disconnect()


app = FastAPI(
    title="Pokémon Black 2 - AI Semantic Runtime API",
    version="4.0.0",
    description="Greenfield BizHawk Semantic Engine and AI Control API",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(runtime_router)
app.include_router(player_router)
app.include_router(map_router)


class PressButtonRequest(BaseModel):
    button: Optional[str] = None
    buttons: Optional[Any] = None
    frames: int = 4


class TouchRequest(BaseModel):
    x: int = 128
    y: int = 96
    frames: int = 4


class MenuSelectRequest(BaseModel):
    index: int = 0


class EnterNameRequest(BaseModel):
    name: str = "zero"


class DialogueChoiceRequest(BaseModel):
    index: int = 0


class FindPathRequest(BaseModel):
    start_x: Optional[int] = None
    start_y: Optional[int] = None
    goal_x: int = 47
    goal_y: int = 735
    matrix_id: int = 0
    allow_water: bool = False


class ReachabilityRequest(BaseModel):
    start_x: Optional[int] = None
    start_y: Optional[int] = None
    goal_x: int = 47
    goal_y: int = 735
    matrix_id: int = 0
    mode: str = "run"


class NavigateToRequest(BaseModel):
    goal_x: int = 47
    goal_y: int = 735
    mode: str = "run"  # "walk" | "run" | "bike" | "surf"
    max_steps: int = 100


class OnboardingRequest(BaseModel):
    player_name: str = "zero"
    gender: str = "male"


class TestInputRequest(BaseModel):
    button: str = "A"
    frames: int = 8


class TestTouchRequest(BaseModel):
    x: int = 128
    y: int = 96
    frames: int = 8


class AutomationPauseRequest(BaseModel):
    paused: bool = True


class SnapshotRequest(BaseModel):
    label: str = "Manual Snapshot"


class FullRamDumpRequest(BaseModel):
    category: str = "OVERWORLD_EXPLORE"
    label: str = "ground_truth_sample"
    notes: str = ""


class CaptureRequest(BaseModel):
    label: str = "Map Evidence"


class AEdgeCaptureRequest(BaseModel):
    button: str = "A"
    sample_frames: int = 16
    ranges: List[Dict[str, Any]]


class MemoryWriteTraceRequest(BaseModel):
    """Finite, observation-only ARM9 write trace for a focused RAM span."""

    start_addr: int
    length: int
    # Address-specific watches keep this below the overhead of a global bus
    # callback.  Include aligned neighbours when a multi-byte write may span
    # an observed changed pixel byte.
    addresses: List[int]
    max_frames: int = 3
    max_events: int = 32
    # If supplied, the bridge arms the callback first and presses this button
    # for exactly one frame.  The default is passive observation.
    button: Optional[str] = None
    # Main-RAM offsets sampled after each traced frame; these make source
    # cursor / candidate-state changes reviewable beside the raw PC events.
    ranges: List[Dict[str, Any]] = []


class DialogueCheckpointRequest(BaseModel):
    """Operator label for a read-only, frame-stamped dialogue checkpoint."""

    label: str = "unlabelled"
    note: str = ""


class MemoryPatternScanRequest(BaseModel):
    """Read-only bounded byte-pattern scan for reproducible RAM research."""

    bytes: List[int]
    start: int = 0
    size: int = 0x400000
    limit: int = 64
    domain: str = "Main RAM"


class MemoryBatchSnapshotRequest(BaseModel):
    """Read-only, one-frame RAM snapshot used by focused RE experiments."""

    ranges: List[Dict[str, Any]]


# Main-RAM offsets used by the repeatable text-printer experiment.  These are
# observation windows, not a claim that every byte is a TextPrinter field.
DIALOGUE_CHECKPOINT_RANGES: List[Dict[str, Any]] = [
    {"id": "script_and_message_state", "domain": "Main RAM", "offset": 0x247500, "length": 0x200},
    {"id": "msg_buffer", "domain": "Main RAM", "offset": 0x2490A0, "length": 0x200},
    {"id": "printer_candidate", "domain": "Main RAM", "offset": 0x31FCB0, "length": 0x80},
    {"id": "printer_pointer_candidates", "domain": "Main RAM", "offset": 0x332C00, "length": 0x100},
    {"id": "player_actor_candidate", "domain": "Main RAM", "offset": 0x23DE00, "length": 0x100},
]

CHECKPOINT_EXPERIMENT_DIR = (
    Path(__file__).resolve().parents[3]
    / "reverse_engineering"
    / "experiments"
    / "EXP_012_manual_dialogue_checkpoints"
)
BRIDGE_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "bridge_transport.log"
checkpoint_capture_lock = asyncio.Lock()


def _checkpoint_slug(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9一-鿿_-]+", "_", value.strip())
    return (cleaned.strip("_") or "unlabelled")[:80]


def _checkpoint_path(label: str, frame: Any) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    frame_part = str(frame) if isinstance(frame, int) else "unknown_frame"
    base = CHECKPOINT_EXPERIMENT_DIR / f"checkpoint_{stamp}_f{frame_part}_{_checkpoint_slug(label)}.json"
    path = base
    suffix = 1
    while path.exists():
        path = base.with_name(f"{base.stem}_{suffix}{base.suffix}")
        suffix += 1
    return path


@app.get("/health")
async def health():
    """Compatibility health endpoint with transport/semantic states separated."""
    h = runtime_hub.health()
    return {
        "status": "ok",
        "service": "Pokémon Black 2 Semantic Runtime",
        "backend_http": "online",
        "bridge_connected": h["bridge_connected"],
        "bridge_state": h["bridge_state"],
        "runtime_status": h["runtime_status"],
        "semantic_status": h["semantic_status"],
        "player_status": h.get("player_status"),
        "ports": runtime_config.public_schema(),
    }


@app.get("/api/bizhawk/status")
async def get_bizhawk_status():
    """Compatibility endpoint: reports only emulator/bridge connectivity facts."""
    probe = probe_bizhawk_process()
    transport_status = transport.diagnostics()
    return {
        "running": probe.running,
        "pid": probe.pid,
        "exe_path": probe.exe_path,
        "connected": bool(client.is_connected),
        "bridge_state": "connected" if client.is_connected else "waiting",
        "bridge_version": transport.bridge_version,
        "frame": transport.last_frame,
        "last_heartbeat": transport.last_heartbeat,
        "session_id": transport.session_id,
        "transport": transport_status,
        "hello": transport.hello_data,
        "roles": runtime_config.public_schema(),
    }


@app.get("/api/dev/bridge_log")
async def get_bridge_log(limit: int = 120):
    """Return the tail of the dedicated bridge transport log for diagnosis."""
    try:
        lines = BRIDGE_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Cannot read bridge log: {exc}") from exc
    return {
        "ok": True,
        "file": str(BRIDGE_LOG_PATH),
        "lines": lines[-max(1, min(limit, 500)):],
    }


@app.get("/api/bizhawk/doctor")
async def get_bizhawk_doctor():
    report = await doctor.run_diagnostics()
    return report.model_dump()


@app.get("/api/state")
async def get_semantic_state():
    """Legacy semantic contract backed by the single-flight Runtime Hub cache.

    A decoder gap must not become an HTTP/Bridge offline signal. If no semantic
    sample exists yet, return an explicit unresolved state rather than inventing
    facing/location/profile facts.
    """
    state = runtime_hub.semantic_state()
    if state is not None:
        return state
    snap = runtime_hub.snapshot()
    return {
        "timestamp": snap.get("sampled_at") or 0,
        "frame": (snap.get("transport") or {}).get("frame") or 0,
        "context": {
            "screen_type": "RUNTIME_UNRESOLVED",
            "screen_description": "运行时语义尚未解析；HTTP/Bridge 状态请读取 /api/v1/runtime/health",
            "available_actions": [],
            "can_move_player": False,
            "is_dialogue_active": False,
            "dialogue_text": "",
            "speaker": "UNRESOLVED",
            "speaker_category": "UNRESOLVED",
            "printer": {},
            "choices": [],
        },
        "location": "实时状态未解析",
        "map_loaded": False,
        "player_name": None,
        "rival_name": None,
        "gender": None,
        "party_count": None,
        "money": None,
        "badges": None,
        "ready_for_input": False,
        "suggested_buttons": [],
        "map_section_id": None,
        "player_facing": "Unresolved",
        "movement_state": "Unresolved",
        "player_world_pos": {"x": None, "y": None, "z": None},
        "player_position_verified": False,
        "runtime_meta": snap.get("runtime"),
    }


@app.get("/api/observer/presentation")
async def get_observer_presentation():
    state = runtime_hub.semantic_state()
    if state is None:
        return {
            "status": "unresolved",
            "runtime": runtime_hub.health(),
            "reason": "No semantic snapshot has been published yet",
        }
    pres = build_observer_presentation(state)
    return pres.model_dump()


@app.get("/api/dialogue/history")
async def get_dialogue_history(limit: int = 50):
    return {
        "ok": True,
        "count": len(dialogue_timeline.history),
        "history": [entry.model_dump() for entry in dialogue_timeline.get_history(limit=limit)],
        "current": dialogue_timeline.current_entry.model_dump() if dialogue_timeline.current_entry else None,
    }


@app.post("/api/dialogue/clear")
async def clear_dialogue_history():
    dialogue_timeline.clear_history()
    return {"ok": True, "message": "Dialogue history cleared"}


@app.get("/api/dev/debug_dialogue")
async def get_dev_debug_dialogue():
    from ..decoders.dialogue import decode_gen5_sentence_words
    from ..decoders.text import extract_printable_strings

    # 1. Read key pointers
    ptrs = {}
    for p_addr in [0x24764C, 0x33605C, 0x333C74, 0x2490A0, 0x249134, 0x233C74]:
        raw_b = await memory_reader.read_bytes(p_addr, 128, "Main RAM")
        val = (raw_b[0] | (raw_b[1] << 8) | (raw_b[2] << 16) | (raw_b[3] << 24)) if len(raw_b) >= 4 else 0
        words = [(raw_b[i] | (raw_b[i+1] << 8)) for i in range(0, min(len(raw_b)-1, 64), 2)]
        decoded = decode_gen5_sentence_words(words)
        ptrs[f"0x02{p_addr:06X}"] = {
            "u32_val": f"0x{val:08X}",
            "raw_hex": bytes(raw_b[:32]).hex(" "),
            "decoded_text": decoded
        }

    # 2. Search for active pointers pointing to active buffer (0x022490A0 ~ 0x02249400)
    found_ptrs = []
    chunk_size = 0x20000
    for start in range(0x100000, 0x380000, chunk_size):
        data = await memory_reader.read_bytes(start, chunk_size, "Main RAM")
        if not data or len(data) < 4:
            continue
        for i in range(0, len(data) - 3, 4):
            u32 = data[i] | (data[i+1] << 8) | (data[i+2] << 16) | (data[i+3] << 24)
            if 0x022490A0 <= u32 <= 0x02249400:
                found_ptrs.append({
                    "pointer_address": f"0x{0x02000000 + start + i:08X}",
                    "pointer_offset": f"0x{start + i:06X}",
                    "target_address": f"0x{u32:08X}"
                })

    return {
        "ptrs": ptrs,
        "found_active_pointers": found_ptrs,
    }


def _require_universal_dump_bridge() -> None:
    """Fail before capture when BizHawk still runs a stale Lua bridge."""
    capabilities = transport.hello_data.get("capabilities") or {}
    required_version = "1.5.1-universal-dump"
    if (
        capabilities.get("universal_dump") is True
        and transport.bridge_version == required_version
    ):
        return

    raise HTTPException(
        status_code=409,
        detail={
            "message": "当前 BizHawk Lua Bridge 的 universal dump 实现不是已验证版本，未创建任何导出文件。",
            "bridge_version": transport.bridge_version,
            "required_bridge_version": required_version,
            "required_capability": "universal_dump",
            "action": (
                "在 BizHawk 的 Lua Console 停止当前脚本，然后重新打开并运行 "
                "bridge/bizhawk/black2_bridge.lua；连接恢复后确认 /api/bizhawk/status "
                "显示 bridge_version 为 1.5.1-universal-dump 且 capabilities.universal_dump 为 true。"
            ),
        },
    )


@app.post("/api/dev/dump_full_ram")
async def post_dump_full_ram(req: FullRamDumpRequest):
    """Create a raw, non-ROM multi-domain evidence bundle when the bridge supports it."""
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    _require_universal_dump_bridge()

    try:
        res = await universal_snapshot_manager.create_snapshot(
            transport=transport,
            state_engine=state_engine,
            category=req.category,
            label=req.label,
            notes=req.notes,
        )
        return res
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Universal snapshot creation failed: {exc}") from exc


@app.get("/api/dev/dumps")
async def get_dev_dumps(limit: int = 50):
    """List all saved full 4MB RAM ground-truth snapshots."""
    snapshots = universal_snapshot_manager.list_snapshots(limit=limit)
    return {"count": len(snapshots), "dumps": snapshots}


@app.get("/api/dev/dumps/{snapshot_id}/download")
async def download_verified_dump(snapshot_id: str):
    """Serve only a ZIP whose screenshot, raw domains, and hashes validate."""
    bundle_path, verification = universal_snapshot_manager.verified_bundle_path(snapshot_id)
    if bundle_path is None:
        raise HTTPException(status_code=409, detail={"message": "ZIP verification failed", "verification": verification})
    return FileResponse(bundle_path, media_type="application/zip", filename=bundle_path.name)


@app.post("/api/dev/dumps/clear")
async def clear_dev_dumps():
    """Delete locally generated dump_* artifacts after the UI confirmation."""
    return universal_snapshot_manager.clear_snapshots()


@app.get("/api/dev/dump_region")
async def get_dev_dump_region(offset: str = "0x246000", length: str = "0x4000", domain: str = "Main RAM"):
    offset_int = int(offset, 0) if isinstance(offset, str) else int(offset)
    length_int = int(length, 0) if isinstance(length, str) else int(length)
    data = await memory_reader.read_bytes(offset_int, length_int, domain)
    return {
        "domain": domain,
        "offset": f"0x{offset_int:06X}",
        "address": f"0x{0x02000000 + offset_int:08X}",
        "length": len(data),
        "hex": bytes(data).hex(),
    }


@app.get("/api/observer/capabilities")
async def get_observer_capabilities():
    return capability_store.get_summary()


@app.get("/api/dev/test_logs")
async def get_dev_test_logs():
    return [t.model_dump() for t in dev_wb.test_logs]


@app.post("/api/dev/test_input")
async def post_dev_test_input(req: TestInputRequest):
    record = await dev_wb.execute_input_test(req.button, req.frames)
    return record.model_dump()


@app.post("/api/dev/test_touch")
async def post_dev_test_touch(req: TestTouchRequest):
    record = await dev_wb.execute_touch_test(req.x, req.y, req.frames)
    return record.model_dump()


@app.post("/api/dev/automation_pause")
async def post_dev_automation_pause(req: AutomationPauseRequest):
    res = dev_wb.set_automation_pause(req.paused)
    return {"automation_paused": res}


@app.post("/api/dev/snapshot")
async def post_dev_snapshot(req: SnapshotRequest):
    return await dev_wb.create_test_snapshot(req.label)


@app.post("/api/dev/savestate/save")
async def post_savestate_save(slot: int = 1):
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    res = await client.save_state(slot)
    return {"ok": True, "slot": slot, "result": res}


@app.post("/api/dev/savestate/load")
async def post_savestate_load(slot: int = 1):
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    res = await client.load_state(slot)
    return {"ok": True, "slot": slot, "result": res}


@app.post("/api/dev/capture")
async def post_dev_capture(req: CaptureRequest):
    """Expose a real BizHawk PNG for visual map calibration only."""
    return await dev_wb.capture_evidence(req.label)


@app.post("/api/dev/a_edge_capture")
async def post_a_edge_capture(req: AEdgeCaptureRequest):
    """Start a bridge-owned one-frame button edge and per-frame RAM capture."""
    return await client.begin_a_edge_capture(req.button, req.sample_frames, req.ranges)


@app.post("/api/dev/memory_write_trace")
async def post_memory_write_trace(req: MemoryWriteTraceRequest):
    """Arm a finite ARM9 write-PC trace for one explicitly bounded RAM span.

    This endpoint never writes game memory.  Its optional one-frame button is
    deliberately part of the same Lua request so the callback is live before
    the emulation frame containing the input edge.
    """
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    if not 0x02000000 <= req.start_addr < 0x02400000:
        raise HTTPException(status_code=422, detail="start_addr must be an ARM9 Main-RAM address")
    if not 1 <= req.length <= 0x4000 or req.start_addr + req.length > 0x02400000:
        raise HTTPException(status_code=422, detail="trace range must stay within Main RAM and be <= 0x4000 bytes")
    if not 1 <= len(req.addresses) <= 16:
        raise HTTPException(status_code=422, detail="addresses must contain 1..16 address-specific watches")
    if len(set(req.addresses)) != len(req.addresses):
        raise HTTPException(status_code=422, detail="addresses must be distinct")
    if any(not req.start_addr <= address < req.start_addr + req.length for address in req.addresses):
        raise HTTPException(status_code=422, detail="each watch address must be inside the explicit trace range")
    if not 1 <= req.max_frames <= 3:
        raise HTTPException(status_code=422, detail="max_frames must be in 1..3 for address-specific tracing")
    if not 1 <= req.max_events <= 64:
        raise HTTPException(status_code=422, detail="max_events must be in 1..64")
    if req.button is not None and req.button not in {"A", "B", "Up", "Down", "Left", "Right", "L", "R", "Start", "Select"}:
        raise HTTPException(status_code=422, detail="button must be a single supported NDS button or null")
    if len(req.ranges) > 16:
        raise HTTPException(status_code=422, detail="at most 16 watch ranges are allowed")

    total_length = 0
    normalized: List[Dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(req.ranges):
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail=f"watch range {index} must be an object")
        domain = item.get("domain", "Main RAM")
        offset = item.get("offset", item.get("addr"))
        length = item.get("length", item.get("size"))
        if domain != "Main RAM" or not isinstance(offset, int) or not isinstance(length, int):
            raise HTTPException(status_code=422, detail=f"watch range {index} must use Main RAM integer offset/length")
        if offset < 0 or length < 1 or offset + length > 0x400000:
            raise HTTPException(status_code=422, detail=f"watch range {index} must stay within 4 MiB Main RAM")
        range_id = str(item.get("id", f"range_{index}"))
        if range_id in used_ids:
            raise HTTPException(status_code=422, detail=f"duplicate watch range id: {range_id}")
        used_ids.add(range_id)
        total_length += length
        normalized.append({"id": range_id, "domain": domain, "offset": offset, "length": length})
    if total_length > 0x8000:
        raise HTTPException(status_code=422, detail="watch ranges must total <= 0x8000 bytes")

    try:
        return await client.begin_memory_write_trace(
            req.start_addr,
            req.length,
            req.addresses,
            req.max_frames,
            req.max_events,
            req.button,
            normalized,
        )
    except (ConnectionError, TimeoutError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"write trace could not start: {exc}") from exc


@app.get("/api/dev/memory_write_trace")
async def get_memory_write_trace():
    """Return the active bounded write trace or its completed raw evidence."""
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    return await client.get_memory_write_trace()


@app.get("/api/dev/memory_write_trace/capabilities")
async def get_memory_write_trace_capabilities():
    """Read live event scope/register support before attempting a PC trace."""
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    return await client.get_memory_write_trace_capabilities()


@app.delete("/api/dev/memory_write_trace")
async def delete_memory_write_trace():
    """Cancel a trace early; the bridge unregisters its callback."""
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    return await client.cancel_memory_write_trace()


@app.post("/api/dev/memory_pattern_scan")
async def post_memory_pattern_scan(req: MemoryPatternScanRequest):
    """Run one bridge-frame read-only pattern scan with explicit bounds.

    This is intentionally useful for reverse-pointer discovery, not a RAM
    mutation endpoint.  The caller supplies the exact target bytes and the
    bounded Main-RAM region so the resulting frame and matches can be placed
    in an experiment artifact.
    """
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    if not 1 <= len(req.bytes) <= 16 or any(not 0 <= value <= 0xFF for value in req.bytes):
        raise HTTPException(status_code=422, detail="bytes must contain 1..16 values in 0..255")
    if req.start < 0 or req.size < 1 or req.start + req.size > 0x400000:
        raise HTTPException(status_code=422, detail="scan must stay within the 4 MiB Main RAM range")
    if not 1 <= req.limit <= 256:
        raise HTTPException(status_code=422, detail="limit must be in 1..256")
    try:
        payload = await memory_reader.scan_pattern_snapshot(
            req.bytes,
            start=req.start,
            size=req.size,
            limit=req.limit,
            domain=req.domain,
        )
    except (ConnectionError, TimeoutError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"RAM scan failed: {exc}") from exc
    matches = [int(offset) for offset in payload.get("matches", [])]
    return {
        "frame": payload.get("frame"),
        "domain": req.domain,
        "pattern_hex": bytes(req.bytes).hex(),
        "start": req.start,
        "size": req.size,
        "matches": matches,
        "arm9_matches": [f"0x{0x02000000 + offset:08X}" for offset in matches],
    }


@app.post("/api/dev/memory_batch_snapshot")
async def post_memory_batch_snapshot(req: MemoryBatchSnapshotRequest):
    """Return named raw ranges and their shared Lua-bridge frame.

    This is deliberately a focused observation primitive rather than a
    general-purpose RAM export.  Full-Main-RAM discovery remains available
    through the bounded pattern-scan endpoint; snapshots keep related actor,
    ScriptWork, and printer fields on one exact emulation frame.
    """
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    if not 1 <= len(req.ranges) <= 32:
        raise HTTPException(status_code=422, detail="ranges must contain 1..32 items")

    total_length = 0
    normalized: List[Dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, item in enumerate(req.ranges):
        if not isinstance(item, dict):
            raise HTTPException(status_code=422, detail=f"range {index} must be an object")
        domain = item.get("domain", "Main RAM")
        if domain != "Main RAM":
            raise HTTPException(status_code=422, detail="only Main RAM is supported by this research endpoint")
        raw_offset = item.get("offset", item.get("addr"))
        raw_length = item.get("length", item.get("size"))
        if not isinstance(raw_offset, int) or not isinstance(raw_length, int):
            raise HTTPException(status_code=422, detail=f"range {index} requires integer offset and length")
        if raw_offset < 0 or raw_length < 1 or raw_offset + raw_length > 0x400000:
            raise HTTPException(status_code=422, detail=f"range {index} must stay within 4 MiB Main RAM")
        range_id = str(item.get("id", f"range_{index}"))
        if range_id in used_ids:
            raise HTTPException(status_code=422, detail=f"duplicate range id: {range_id}")
        used_ids.add(range_id)
        total_length += raw_length
        normalized.append({
            "id": range_id,
            "domain": domain,
            "offset": raw_offset,
            "length": raw_length,
        })
    # A single bridge request must remain small enough not to disturb the
    # timeline we are trying to observe.  Larger discovery passes use the
    # dedicated scan route, which is also frame-stamped.
    if total_length > 0x40000:
        raise HTTPException(status_code=422, detail="total snapshot length must not exceed 0x40000 bytes")

    try:
        payload = await memory_reader.read_batch_snapshot(normalized)
    except (ConnectionError, TimeoutError, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail=f"RAM snapshot failed: {exc}") from exc
    return {
        "frame": payload.get("frame"),
        "domain": "Main RAM",
        "total_length": total_length,
        "results": payload.get("results", {}),
    }


@app.get("/api/dev/a_edge_capture")
async def get_a_edge_capture():
    """Return progress or the completed bridge-owned per-frame capture."""
    return await client.get_a_edge_capture()


@app.post("/api/dev/dialogue_checkpoint")
async def post_dialogue_checkpoint(req: DialogueCheckpointRequest):
    """Capture a labelled, read-only RAM checkpoint at the bridge's current frame.

    The label is an operator annotation (for example, "page2_wait").  It is
    deliberately not decoded into a visible-line or speaker claim.  A single
    ``memory.read_batch`` request supplies all ranges and its exact bridge frame.
    """
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")

    async with checkpoint_capture_lock:
        try:
            payload = await memory_reader.read_batch_snapshot(DIALOGUE_CHECKPOINT_RANGES)
        except (ConnectionError, TimeoutError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=f"RAM checkpoint failed: {exc}") from exc

        frame = payload.get("frame")
        results = payload.get("results", {})
        captured_at = datetime.now(timezone.utc).isoformat()
        evidence = {
            "schema": "dialogue_checkpoint/v1",
            "captured_at_utc": captured_at,
            "frame": frame,
            "label": req.label,
            "operator_note": req.note,
            "read_method": "BizHawk Lua bridge memory.read_batch",
            "ranges": results,
            "interpretation": {
                "visible_lines": "unresolved; label is operator annotation only",
                "text_printer_state": "unresolved",
                "speaker_actor": "unresolved",
            },
        }
        CHECKPOINT_EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = _checkpoint_path(req.label, frame)
        output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "schema": evidence["schema"],
        "frame": frame,
        "label": req.label,
        "operator_note": req.note,
        "file_name": output_path.name,
        "file_path": str(output_path),
        "range_ids": list(results.keys()),
        "interpretation": evidence["interpretation"],
    }


@app.get("/api/dev/dialogue_checkpoint")
async def get_dialogue_checkpoints(limit: int = 30):
    """List saved manual checkpoints without exposing them as parsed dialogue."""
    CHECKPOINT_EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(CHECKPOINT_EXPERIMENT_DIR.glob("checkpoint_*.json"), reverse=True)
    entries = []
    for path in files[: max(1, min(limit, 200))]:
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
            entries.append({
                "file_name": path.name,
                "captured_at_utc": item.get("captured_at_utc"),
                "frame": item.get("frame"),
                "label": item.get("label"),
                "operator_note": item.get("operator_note", ""),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return {"ok": True, "experiment": "EXP_012_manual_dialogue_checkpoints", "count": len(entries), "checkpoints": entries}


@app.post("/api/actions/press")
async def post_action_press(req: PressButtonRequest):
    btn = req.button
    if not btn and req.buttons:
        if isinstance(req.buttons, list) and len(req.buttons) > 0:
            btn = req.buttons[0]
        elif isinstance(req.buttons, str):
            btn = req.buttons
    if not btn:
        btn = "A"

    res = await action_engine.press_button(btn, hold_frames=req.frames)
    return {"ok": True, "button": btn, "result": res}


@app.post("/api/actions/touch")
async def post_action_touch(req: TouchRequest):
    res = await action_engine.touch_screen(req.x, req.y, hold_frames=req.frames)
    return {"ok": True, "x": req.x, "y": req.y, "result": res}


@app.post("/api/actions/title_start")
async def post_action_title_start():
    return await action_engine.handle_title_screen_start()


@app.post("/api/actions/continue_game")
async def post_action_continue_game():
    return await action_engine.handle_continue_game()


@app.post("/api/actions/new_game")
async def post_action_new_game():
    return await action_engine.handle_new_game()


@app.post("/api/actions/menu_select")
async def post_action_menu_select(req: MenuSelectRequest):
    return await action_engine.select_menu_option(req.index)


@app.post("/api/actions/enter_name")
async def post_action_enter_name(req: EnterNameRequest):
    return await action_engine.enter_name_on_keyboard(req.name)


@app.post("/api/actions/dialogue/advance")
async def post_action_dialogue_advance(request: Request):
    steps_str = request.query_params.get("steps", "1")
    try:
        steps = int(steps_str)
    except ValueError:
        steps = 1
    steps_res = await action_engine.auto_advance_dialogue(max_steps=steps)
    return {"ok": True, "steps": steps_res}


@app.post("/api/actions/dialogue/choice")
async def post_action_dialogue_choice(req: DialogueChoiceRequest):
    return await action_engine.select_dialogue_choice(req.index)


@app.get("/api/actions/dialogue/scan_text")
async def get_action_dialogue_scan_text():
    strings = await action_engine.scan_current_dialogue_text()
    return {"count": len(strings), "strings": strings}


@app.post("/api/actions/onboarding")
async def post_action_onboarding(req: OnboardingRequest):
    return await onboarding_flow.run_full_new_game_sequence(req.player_name, req.gender)


# Legacy hand-authored POI catalog was removed from the Runtime API.
# World identity and entrances must come from /api/v1/map/scene/current.


@app.post("/api/v1/nav/reachability")
async def post_nav_reachability(req: ReachabilityRequest):
    """Pre-calculate reachability and path feasibility before initiating movement."""
    sx = req.start_x
    sy = req.start_y
    if sx is None or sy is None:
        st = await state_engine.sample_once()
        pos = st.player_world_pos or {}
        sx = pos.get("x", 47)
        sy = pos.get("y", 771)

    result = navigation_service.evaluate_reachability(
        start_x=sx,
        start_y=sy,
        goal_x=req.goal_x,
        goal_y=req.goal_y,
        matrix_id=req.matrix_id,
        has_running_shoes=True,
        has_bicycle=False,
        has_surf=(req.mode == "surf"),
    )
    return result.model_dump()


@app.post("/api/v1/nav/find_path")
async def post_nav_find_path(req: FindPathRequest):
    sx = req.start_x
    sy = req.start_y
    if sx is None or sy is None:
        st = await state_engine.sample_once()
        pos = st.player_world_pos or {}
        sx = pos.get("x", 47)
        sy = pos.get("y", 771)

    nav = navigation_service.build_navigation_grid_for_points(sx, sy, req.goal_x, req.goal_y, padding=16, matrix_id=req.matrix_id)
    path_coords, steps = navigation_service.find_path(sx, sy, req.goal_x, req.goal_y, nav, allow_water=req.allow_water)
    return {
        "start": {"x": sx, "y": sy},
        "goal": {"x": req.goal_x, "y": req.goal_y},
        "path_length": len(path_coords),
        "steps_count": len(steps),
        "path": [{"x": x, "y": y} for x, y in path_coords],
        "steps": steps,
        "reachable": len(steps) > 0 or (sx == req.goal_x and sy == req.goal_y),
    }


@app.post("/api/v1/nav/navigate_to")
async def post_nav_navigate_to(req: NavigateToRequest):
    """Autonomously drive player along A* path with live RAM coordinate verification and multi-mode speeds."""
    st = await state_engine.sample_once()
    pos = st.player_world_pos or {}
    sx = pos.get("x")
    sy = pos.get("y")
    if sx is None or sy is None:
        raise HTTPException(status_code=400, detail="Player coordinates not verified in RAM")

    allow_water = (req.mode == "surf")
    nav = navigation_service.build_navigation_grid_for_points(sx, sy, req.goal_x, req.goal_y, padding=16)
    path_coords, steps = navigation_service.find_path(sx, sy, req.goal_x, req.goal_y, nav, allow_water=allow_water)

    if not steps and (sx != req.goal_x or sy != req.goal_y):
        return {"ok": False, "message": "No walkable path found to goal", "current": {"x": sx, "y": sy}}

    executed_steps = 0
    step_records = []
    initial_dialogue = st.context.dialogue_text
    last_verified_pos = (sx, sy)

    # Configure step speed / buttons based on movement mode
    # Run: hold B button + direction for high speed
    # Bike: rapid direction press
    # Walk: standard direction press
    for step_btn in steps[:req.max_steps]:
        if req.mode == "run":
            await client.press_buttons(["B", step_btn], frames=8)
            await asyncio.sleep(0.12)
        elif req.mode == "bike":
            await client.press_buttons([step_btn], frames=6)
            await asyncio.sleep(0.09)
        elif req.mode == "surf":
            await client.press_buttons([step_btn], frames=10)
            await asyncio.sleep(0.15)
        else: # walk
            await client.press_buttons([step_btn], frames=14)
            await asyncio.sleep(0.20)

        executed_steps += 1
        st_after = await state_engine.sample_once()
        new_pos = st_after.player_world_pos or {}
        n_tuple = (new_pos.get("x"), new_pos.get("y"))

        step_records.append({
            "step": executed_steps,
            "btn": step_btn,
            "mode": req.mode,
            "pos": new_pos,
            "facing": st_after.player_facing,
            "dialogue_active": st_after.context.is_dialogue_active,
        })

        # Only interrupt if a genuinely NEW dialogue / cutscene script took over
        if (
            st_after.context.is_dialogue_active
            and st_after.context.dialogue_text != initial_dialogue
            and n_tuple == last_verified_pos
        ):
            return {
                "ok": True,
                "status": "interrupted_by_dialogue",
                "mode": req.mode,
                "executed_steps": executed_steps,
                "remaining_steps": len(steps) - executed_steps,
                "current_pos": new_pos,
                "dialogue": st_after.context.dialogue_text,
                "speaker": st_after.context.speaker,
                "records": step_records,
            }

        if n_tuple[0] is not None and n_tuple[1] is not None:
            last_verified_pos = n_tuple

    final_st = await state_engine.sample_once()
    return {
        "ok": True,
        "status": "completed",
        "mode": req.mode,
        "executed_steps": executed_steps,
        "final_pos": final_st.player_world_pos,
        "records": step_records,
    }


# Serve Frontend Web UI
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../frontend"))
CAPTURE_DIR = DeveloperTestWorkbench._capture_dir()
if os.path.exists(FRONTEND_DIR):
    app.mount("/frontend", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR), name="assets")
CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/dev/captures", StaticFiles(directory=CAPTURE_DIR), name="dev-captures")

DUMPS_DIR = universal_snapshot_manager.base_dir
DUMPS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/dumps", StaticFiles(directory=DUMPS_DIR), name="runtime-evidence-dumps")


@app.get("/dialogue-checkpoints")
async def dialogue_checkpoints_page():
    """Friendly short URL for the manual checkpoint page."""
    page = os.path.join(FRONTEND_DIR, "dialogue-checkpoints.html")
    if os.path.exists(page):
        return FileResponse(page)
    raise HTTPException(status_code=404, detail="dialogue-checkpoints.html not found")


@app.get("/ram-dumper")
async def ram_dumper_page():
    """Friendly short URL for the universal evidence dumper."""
    page = os.path.join(FRONTEND_DIR, "ram-dumper.html")
    if os.path.exists(page):
        return FileResponse(page)
    raise HTTPException(status_code=404, detail="ram-dumper.html not found")


@app.get("/")
async def root():
    index_file = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {
        "title": "Pokémon Black 2 Semantic Runtime API",
        "version": "4.0.0",
        "docs_url": "/docs",
        "bizhawk_status_url": "/api/bizhawk/status",
        "observer_url": "/api/observer/presentation"
    }
