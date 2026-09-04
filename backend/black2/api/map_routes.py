"""Map-only HTTP boundary between ROM readers and browser renderers."""
from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from ..bizhawk.bridge_client import BridgeClient
from ..memory.reader import MemoryReader
from ..decoders.field import get_map_name
from ..world.map_knowledge import MapKnowledgeService, format_catalog_text, format_current_text
from ..world.map_schematic import MapSchematicService, format_schematic_text
from ..world.map_truth import MapTruthService
from ..world.map_scene import MapSceneService
from ..world.runtime_field_resolver import resolve_runtime_field
from ..world.runtime_player_state import player_runtime_service
from ..world.native_map import NativeMapError, NativeMapService, read_live_map_state, _bytes_from_result


router = APIRouter(prefix="/api/v1/map", tags=["map"])
native_maps: NativeMapService | None = None
schematics = MapSchematicService()
knowledge = MapKnowledgeService()
truth = MapTruthService()
scene = MapSceneService(truth=truth)
_cache_task: asyncio.Task[None] | None = None
_reader: MemoryReader | None = None
_client: BridgeClient | None = None


class MapProbeRequest(BaseModel):
    button: str
    frames: int = 4
    wait_frames: int = 15


def _native_maps() -> NativeMapService:
    global native_maps
    if native_maps is None:
        native_maps = NativeMapService()
    return native_maps


def start_cache_observer(reader: MemoryReader) -> None:
    """Start the optional ROM-native map cache only when a ROM is configured."""
    if os.getenv("BLACK2_ENABLE_LEGACY_MAP_CACHE", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    global _cache_task
    if _cache_task is not None and not _cache_task.done():
        return
    try:
        service = _native_maps()
    except (FileNotFoundError, OSError):
        # Map rendering is an optional subsystem. Missing ROM must not prevent
        # HTTP, Bridge, Dialogue or Player Runtime from starting.
        _cache_task = None
        return
    _cache_task = asyncio.create_task(service.start_auto_cache(reader))


def configure_map_routes(reader: MemoryReader, client: BridgeClient) -> None:
    """Bind application-owned bridge services without importing the FastAPI app."""
    global _reader, _client
    _reader = reader
    _client = client


def _map_reader() -> MemoryReader:
    if _reader is None:
        raise RuntimeError("map routes are not configured")
    return _reader


def _map_client() -> BridgeClient:
    if _client is None:
        raise RuntimeError("map routes are not configured")
    return _client


async def stop_cache_observer() -> None:
    global _cache_task
    if _cache_task is None:
        return
    _cache_task.cancel()
    with suppress(asyncio.CancelledError):
        await _cache_task
    _cache_task = None


@router.get("/debug_memory")
async def debug_memory(offset: int = 0x143600, length: int = 128, reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    try:
        raw = await reader.read_bytes(offset, length, "Main RAM")
        return {
            "offset": f"0x{offset:06X}",
            "length": length,
            "hex": bytes(raw).hex(),
            "bytes": list(raw),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/snapshot_diff")
async def snapshot_diff(action: str = "take_a", reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    """Take Snapshot A or Snapshot B across candidate 256KB dynamic heap (0x200000 - 0x280000), and return diffs."""
    global _snapshot_a, _snapshot_b
    try:
        # Read 512KB dynamic heap in 16KB chunks (total 32 chunks)
        base_offset = 0x200000
        total_len = 0x80000 # 512KB
        chunk_size = 0x4000 # 16KB
        full_blocks = []
        for blk in range(total_len // chunk_size):
            offset = base_offset + blk * chunk_size
            data = await reader.read_bytes(offset, chunk_size, "Main RAM")
            full_blocks.append(bytes(data))
        heap_ram = b"".join(full_blocks)

        if action == "take_a":
            _snapshot_a = heap_ram
            return {"ok": True, "action": "Snapshot A (基准快照) 已记录", "bytes_count": len(heap_ram), "range": f"0x02200000 - 0x02280000"}

        if action == "take_b":
            if not _snapshot_a or len(_snapshot_a) != len(heap_ram):
                return {"ok": False, "error": "请先拍摄 Snapshot A"}

            diffs = []
            for i in range(len(heap_ram)):
                va = _snapshot_a[i]
                vb = heap_ram[i]
                if va != vb:
                    diffs.append({
                        "arm9_addr": f"0x{0x02200000 + i:08X}",
                        "offset": f"0x{0x200000 + i:06X}",
                        "val_a": va,
                        "val_b": vb,
                        "is_small_enum": (va <= 7 and vb <= 7),
                    })
            small_enums = [d for d in diffs if d["is_small_enum"]]
            return {
                "ok": True,
                "action": "Snapshot B 对比完成",
                "total_diff_bytes": len(diffs),
                "small_enum_count": len(small_enums),
                "small_enum_candidates": small_enums[:40],
                "diffs": diffs[:30],
            }
        return {"ok": False, "error": f"Unknown action: {action}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

_snapshot_a = None
_snapshot_b = None


class WatchMemoryRequest(BaseModel):
    ranges: list[dict[str, int]]  # [{"offset": 0x143600, "length": 64}, ...]


@router.post("/watch_diff")
async def watch_diff(req: WatchMemoryRequest, reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    try:
        items = [
            {"id": str(i), "domain": "Main RAM", "addr": r["offset"], "length": r.get("length", 32)}
            for i, r in enumerate(req.ranges)
        ]
        results = await reader.read_batch_ranges(items)
        blocks = []
        for i, r in enumerate(req.ranges):
            res = results.get(str(i), {})
            b = _bytes_from_result(res)
            blocks.append({
                "offset": r["offset"],
                "arm9_addr": f"0x{0x02000000 + r['offset']:08X}",
                "length": len(b),
                "bytes": list(b),
                "hex": b.hex(),
            })
        return {"blocks": blocks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/scan_facing")
async def scan_facing(reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    try:
        # Scan for full 4MB RAM in safe 128KB blocks to find all instances where X=114, Y=680 or Player Avatar struct is stored
        # Player coordinates in memory: 0x0072 (114) and 0x02A8 (680)
        # Search for exact 6-byte pattern [0x72, 0x00, 0x8A, 0x5C, 0x0D, 0x00]
        pat = [0x72, 0x00, 0x8A, 0x5C, 0x0D, 0x00]
        offsets = await reader.scan_pattern(pat, start=0x0, size=0x400000, limit=16)
        candidates = []
        for off in offsets:
            chunk = await reader.read_bytes(off - 14 if off >= 14 else 0, 48, "Main RAM")
            candidates.append({
                "arm9_addr": f"0x{0x02000000 + off:08X}",
                "offset": f"0x{off:06X}",
                "hex": bytes(chunk).hex(),
                "bytes": list(chunk),
            })
        return {
            "match_count": len(offsets),
            "candidates": candidates,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/current")
async def current_position(reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    """Compatibility map HUD backed by the shared verified Player Runtime cache.

    The RuntimeHub continuously refreshes ``player_runtime_service.latest``.
    This endpoint does not issue another competing RAM request unless no sample
    exists yet (for example when called before the hub's first completed tick).
    """
    sample = player_runtime_service.latest
    if not sample:
        try:
            sample = await player_runtime_service.sample(reader)
        except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
    status = sample.get("status")
    resolved = status in {"resolved", "candidate"}
    pos = (sample.get("position") or {}).get("grid") or {}
    mapper = sample.get("mapper") or {}
    chunk = mapper.get("player_chunk") or {}
    size = int(mapper.get("chunk_tile_size") or 32)
    x = pos.get("x") if resolved else None
    z = pos.get("z") if resolved else None
    orientation = sample.get("orientation") or {}
    locomotion = sample.get("locomotion") or {}
    return {
        "source": "RuntimeHub cache -> Field -> FieldPlayer -> Core -> FieldActor",
        "player": {
            "x": x,
            "y": z,
            "elevation": pos.get("y") if resolved else None,
            "facing": orientation.get("facing") if orientation.get("verified") else "Unresolved",
            "movement_state": locomotion.get("semantic_state", "Unresolved"),
            "chunk": {"x": chunk.get("x"), "y": chunk.get("y")},
            "local": {
                "x": (int(x) % size) if isinstance(x, int) else None,
                "y": (int(z) % size) if isinstance(z, int) else None,
            },
            "verified": bool(resolved and orientation.get("verified")),
            "confidence": sample.get("confidence"),
        },
        "map_section_id": None,
        "map_name": None,
        "field_system": {
            "matrix_id": None,
            "active_actors_count": None,
            "camera_target": None,
            "status": "Use /api/v1/map/scene/current for runtime+ROM scene facts",
        },
        "trainer": None,
    }


@router.get("/runtime/field")
async def runtime_field(reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    return await _map_response(resolve_runtime_field(reader))


@router.get("/truth/current")
async def truth_current(include_raw: bool = False, reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    return await _map_response(truth.current(reader, include_raw=include_raw))


@router.get("/scene/current")
async def scene_current(force: bool = False, reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    return await _map_response(scene.current(reader, force=force))


@router.get("/visual")
async def visual_map(reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    visual = await _retry_live_build(lambda: _native_maps().build_live(reader))
    # The BMD/BTX visual alignment identifies the loaded scene.  ZoneData adds
    # the verified Map Header/event context used by the rest of the map API.
    visual.update(schematics.visual_context(visual))
    return visual


@router.get("/geometry")
async def geometry_map(reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    geometry = await _retry_live_build(lambda: _native_maps().build_geometry_live(reader))
    geometry.update(schematics.visual_context(geometry))
    return geometry


@router.get("/visual/cache/{cache_key}/{asset_key}/{asset_name}")
async def visual_asset(cache_key: str, asset_key: str, asset_name: str):
    path = _native_maps().asset_path(cache_key, asset_key, asset_name)
    if path is None:
        raise HTTPException(status_code=404, detail="Native map asset not found")
    return FileResponse(path)


@router.get("/geometry/cache/{cache_key}/{asset_key}/{asset_name}")
async def geometry_asset(cache_key: str, asset_key: str, asset_name: str):
    path = _native_maps().geometry_asset_path(cache_key, asset_key, asset_name)
    if path is None:
        raise HTTPException(status_code=404, detail="Geometry map asset not found")
    return FileResponse(path)


@router.get("/cache/status")
async def cache_status() -> dict[str, Any]:
    try:
        return _native_maps().cache_status()
    except (FileNotFoundError, OSError) as error:
        return {"state": "rom_unavailable", "error": str(error)}


@router.get("/schematic")
async def schematic(reader: MemoryReader = Depends(_map_reader), include_raw: bool = False) -> dict[str, Any]:
    return await _map_response(schematics.current(reader, include_raw=include_raw))


@router.get("/schematic/tile")
async def schematic_tile(
    x: int,
    y: int,
    reader: MemoryReader = Depends(_map_reader),
) -> dict[str, Any]:
    return await _map_response(schematics.tile(reader, x, y))


@router.get("/schematic.txt", response_class=PlainTextResponse)
async def schematic_text(reader: MemoryReader = Depends(_map_reader), include_raw: bool = False) -> str:
    return format_schematic_text(await _map_response(schematics.current(reader, include_raw=include_raw)))


@router.get("/machine")
async def machine(reader: MemoryReader = Depends(_map_reader)) -> dict[str, Any]:
    return await _map_response(schematics.machine(reader))


@router.get("/knowledge/current")
async def current_knowledge(reader: MemoryReader = Depends(_map_reader), include_raw: bool = False) -> dict[str, Any]:
    return await _map_response(knowledge.current(reader, include_raw=include_raw))


@router.get("/knowledge/current.txt", response_class=PlainTextResponse)
async def current_knowledge_text(reader: MemoryReader = Depends(_map_reader), include_raw: bool = False) -> str:
    return format_current_text(await _map_response(knowledge.current(reader, include_raw=include_raw)))


@router.get("/knowledge/observations")
async def observations() -> dict[str, Any]:
    return knowledge.observations()


@router.get("/knowledge/catalog")
async def catalog() -> dict[str, Any]:
    return knowledge.catalog()


@router.get("/knowledge/catalog.txt", response_class=PlainTextResponse)
async def catalog_text() -> str:
    return format_catalog_text(knowledge.catalog())


@router.get("/knowledge/map/{map_header_id}")
async def map_detail(map_header_id: int, include_raw: bool = False) -> dict[str, Any]:
    try:
        return knowledge.map_detail(map_header_id, include_raw=include_raw)
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post("/knowledge/probe")
async def probe_map(
    request: MapProbeRequest,
    reader: MemoryReader = Depends(_map_reader),
    client: BridgeClient = Depends(_map_client),
) -> dict[str, Any]:
    before = await _map_response(knowledge.current(reader, include_raw=True))
    try:
        input_result = await client.press_buttons([request.button], frames=request.frames)
    except (ConnectionError, TimeoutError, OSError) as error:
        raise HTTPException(status_code=503, detail=f"input was not delivered: {error}") from error
    await asyncio.sleep(max(0, request.wait_frames) / 60)
    after = await _map_response(knowledge.current(reader, include_raw=True))
    before_player = before["live_player"]
    after_player = after["live_player"]
    moved = (
        before_player.get("verified") and after_player.get("verified")
        and (before_player.get("x"), before_player.get("y"))
        != (after_player.get("x"), after_player.get("y"))
    )
    changed_map = (
        before_player.get("map_section_id") is not None
        and before_player.get("map_section_id") != after_player.get("map_section_id")
    )
    return {
        "input": {"button": request.button, "result": input_result},
        "movement_result": "moved" if moved else "not_observed",
        "map_section_changed": changed_map,
        "evidence": (
            "位置镜像在输入后变化。"
            if moved else "输入已送达，但未观察到位置变化；这不能单独证明碰撞。"
        ),
        "collision_semantics": "unknown; raw permission bytes remain undecoded",
        "before": before,
        "after": after,
    }


async def _retry_live_build(build: Any) -> dict[str, Any]:
    last_error: NativeMapError | None = None
    for _ in range(8):
        try:
            return await build()
        except NativeMapError as error:
            last_error = error
            if not any(word in str(error) for word in ("loaded", "BTX0", "coordinate")):
                break
            await asyncio.sleep(0.1)
    raise HTTPException(status_code=503, detail=str(last_error or "Native map is unavailable"))


async def _map_response(awaitable: Any) -> dict[str, Any]:
    try:
        return await awaitable
    except (NativeMapError, ConnectionError, TimeoutError, OSError, ValueError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
