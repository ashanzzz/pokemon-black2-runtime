"""Player-runtime endpoints: structure-grounded movement/orientation inspection."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..memory.reader import MemoryReader
from ..world.runtime_player_state import player_runtime_service


import asyncio
from ..bizhawk.bridge_client import BridgeClient
from ..actions.input_engine import ActionEngine

router = APIRouter(prefix="/api/v1/player", tags=["player-runtime"])
_reader: MemoryReader | None = None
_client: BridgeClient | None = None
_action_engine: ActionEngine | None = None


def configure_player_routes(
    reader: MemoryReader,
    client: BridgeClient | None = None,
    action_engine: ActionEngine | None = None,
) -> None:
    global _reader, _client, _action_engine
    _reader = reader
    _client = client
    _action_engine = action_engine


def _player_reader() -> MemoryReader:
    if _reader is None:
        raise RuntimeError("player routes are not configured")
    return _reader


class GaitCalibrationRequest(BaseModel):
    label: str


@router.get("/runtime")
async def runtime_player(reader: MemoryReader = Depends(_player_reader)) -> dict[str, Any]:
    try:
        # This route is an explicit operator request.  It may perform one
        # bounded full-RAM discovery pass when no cached Field chain exists;
        # the background runtime hub never does.
        return await player_runtime_service.sample(reader, allow_discovery=True)
    except (ConnectionError, TimeoutError, OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/calibration")
async def gait_calibration() -> dict[str, Any]:
    return player_runtime_service.calibration_profile()


@router.post("/calibration")
async def record_gait_calibration(
    request: GaitCalibrationRequest,
    reader: MemoryReader = Depends(_player_reader),
) -> dict[str, Any]:
    # Refresh immediately so the labelled sample is as close as possible to the
    # operator-observed walk/run state.
    await player_runtime_service.sample(reader, allow_discovery=False)
    result = player_runtime_service.record_gait_sample(request.label)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result)
    return result


@router.delete("/calibration")
async def clear_gait_calibration() -> dict[str, Any]:
    return {"ok": True, "profile": player_runtime_service.reset_calibration()}


@router.get("/capabilities")
async def player_capabilities(reader: MemoryReader = Depends(_player_reader)) -> dict[str, Any]:
    """Return real-time player capability and action legality matrix."""
    from ..world.player_capabilities import evaluate_capabilities
    from .navigation_routes import navigation_static_provider
    from ..decoders.party_runtime import PlayerPartyDecoder
    from ..decoders.inventory_runtime import PlayerInventoryDecoder

    try:
        sample = await player_runtime_service.sample(reader, allow_discovery=False)
    except Exception:
        sample = {}

    # PlayerRuntime/v3 exposes the canonical state at the top level.  Older
    # callers used a nested `player` projection, so accept both shapes but
    # never silently fall back to OnFoot when the live transport is Cycling or
    # Surf.  The previous default made /player/capabilities disagree with the
    # authoritative /player/runtime response.
    player_data = sample.get("player") if isinstance(sample.get("player"), dict) else sample
    locomotion = sample.get("locomotion") if isinstance(sample.get("locomotion"), dict) else {}
    transport_raw = locomotion.get("transport_mode_raw")
    if transport_raw is None:
        transport_name = str(locomotion.get("transport_mode") or "").lower()
        transport_raw = {"onfoot": 0, "cycling": 1, "surf": 2, "diving": 3}.get(transport_name, 0)
    try:
        ex_state_raw = int(player_data.get("ex_state_raw", transport_raw))
    except (TypeError, ValueError):
        ex_state_raw = int(transport_raw) if isinstance(transport_raw, (int, float, str)) else 0
    zone_id = sample.get("zone_id")
    if zone_id is None:
        zone_id = player_data.get("zone_id")
    if zone_id is None:
        try:
            from ..world.map_truth import MapTruthService
            truth = await MapTruthService().current(reader)
            if truth:
                zone_id = truth.get("identity", {}).get("map_header", {}).get("value") or truth.get("zone_id")
        except Exception:
            pass

    zone_rules: dict[str, Any] = {}
    if zone_id is not None:
        try:
            prov = navigation_static_provider()
            z = prov.rom.zone(int(zone_id))
            zone_rules = {
                "enable_cycling": z.enable_cycling,
                "enable_running": z.enable_running,
                "enable_escape_rope": z.enable_escape_rope,
                "enable_fly_from": z.enable_fly_from,
            }
        except Exception:
            pass

    party_move_ids: set[int] = set()
    key_item_ids: set[int] = set()
    bag_item_ids: set[int] = set()

    try:
        party_data = await PlayerPartyDecoder(reader).sample()
        for s in party_data.get("slots", []):
            for m in s.get("moves", []):
                if isinstance(m, dict) and m.get("move_id"):
                    party_move_ids.add(int(m["move_id"]))
    except Exception:
        pass

    try:
        inv_dec = PlayerInventoryDecoder(reader)
        inv_dec.configure(reader)
        bag_data = await inv_dec.sample()
        for it in bag_data.get("items", []):
            iid = it.get("item_id")
            if iid:
                bag_item_ids.add(int(iid))
                if it.get("pocket") == "key_items" or it.get("pocket_id") == "key_items" or it.get("key_item") is True:
                    key_item_ids.add(int(iid))
    except Exception:
        pass

    adjacent_features: set[str] = set()
    position = sample.get("position") if isinstance(sample.get("position"), dict) else player_data.get("position")
    if zone_id is not None and isinstance(position, dict):
        gpos = position.get("grid") or {}
        px = gpos.get("x")
        pz = gpos.get("z")
        if px is not None and pz is not None:
            try:
                prov = navigation_static_provider()
                surfs = prov._decode_zone_surfaces(int(zone_id))
                by_c = {(s["x"], s["z"]): s for s in surfs}
                for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    adj = by_c.get((px + dx, pz + dz))
                    if adj:
                        surf_data = adj.get("surface", {})
                        mat_kind = surf_data.get("material", {}).get("kind")
                        req_moves = surf_data.get("collision", {}).get("requires") or []
                        if mat_kind in ("water", "water_edge") or "surf" in req_moves:
                            adjacent_features.add("water")
                            adjacent_features.add("shore")
            except Exception:
                pass

    caps = evaluate_capabilities(
        ex_state_raw=ex_state_raw,
        zone_rules=zone_rules,
        party_move_ids=party_move_ids,
        key_item_ids=key_item_ids,
        bag_item_ids=bag_item_ids,
        adjacent_features=adjacent_features,
    )
    result = caps.as_dict()
    result["zone_id"] = zone_id
    result["zone_rules"] = zone_rules
    return result


async def _inspect_bicycle_state(reader: MemoryReader) -> dict[str, Any]:
    """Helper to inspect live bicycle possession, Zone legality and transport mode."""
    sample = await player_runtime_service.sample(reader, allow_discovery=False)
    locomotion = sample.get("locomotion") or {}
    transport = str(locomotion.get("transport_mode") or "OnFoot")
    is_cycling = (transport == "Cycling")

    from ..decoders.inventory_runtime import PlayerInventoryDecoder
    from .navigation_routes import navigation_static_provider

    zone_id = sample.get("zone_id")
    enable_cycling = False
    if zone_id is not None:
        try:
            prov = navigation_static_provider()
            z = prov.rom.zone(int(zone_id))
            enable_cycling = bool(getattr(z, "enable_cycling", False))
        except Exception:
            pass

    has_bicycle = False
    bike_slot_index = None
    try:
        inv_dec = PlayerInventoryDecoder(reader)
        inv_dec.configure(reader)
        bag_data = await inv_dec.sample()
        key_items = [it for it in bag_data.get("items", []) if it.get("pocket") == "key_items" or it.get("pocket_id") == "key_items" or it.get("key_item") is True]
        for idx, it in enumerate(key_items):
            iid = it.get("item_id")
            if iid in (450, 427):
                has_bicycle = True
                bike_slot_index = idx
                break
        if not has_bicycle:
            # Fallback scan all items
            for it in bag_data.get("items", []):
                if it.get("item_id") in (450, 427):
                    has_bicycle = True
                    bike_slot_index = max(0, int(it.get("slot", 1)) - 1)
                    break
    except Exception:
        pass

    can_cycle = has_bicycle and enable_cycling and (transport in ("OnFoot", "Cycling"))
    reason = "Ready" if can_cycle else (
        "Bicycle is not owned in key items pocket" if not has_bicycle else
        "Cycling is disabled in this Zone (indoor/cave)" if not enable_cycling else
        f"Cannot ride bicycle while in transport {transport}"
    )

    return {
        "is_cycling": is_cycling,
        "transport_mode": transport,
        "has_bicycle": has_bicycle,
        "enable_cycling": enable_cycling,
        "can_cycle": can_cycle,
        "zone_id": zone_id,
        "reason": reason,
        "bike_slot_index": bike_slot_index,
    }



async def _resolve_shortcut_address(reader: MemoryReader) -> int | None:
    """Dynamically traverse GameData -> SaveControl -> SaveData -> Block 32 to get ShortcutSave[0] physical address."""
    try:
        # GameData at 0x0223B570
        gd_bytes = bytes(await reader.read_bytes(0x0223B570, 0x10))
        save_control = int.from_bytes(gd_bytes[0:4], "little")
        if not (0x02000000 <= save_control < 0x02400000):
            return None
        sc_bytes = bytes(await reader.read_bytes(save_control, 0x20))
        save_data = int.from_bytes(sc_bytes[0x10:0x14], "little")
        if not (0x02000000 <= save_data < 0x02400000):
            return None
        sd_bytes = bytes(await reader.read_bytes(save_data, 0x40))
        r5 = int.from_bytes(sd_bytes[0x2C:0x30], "little")
        base_buf = int.from_bytes(sd_bytes[0x34:0x38], "little")
        if not (0x02000000 <= r5 < 0x02400000) or not (0x02000000 <= base_buf < 0x02400000):
            return None
        r5_bytes = bytes(await reader.read_bytes(r5, 0x20))
        table_ptr = int.from_bytes(r5_bytes[0x14:0x18], "little")
        if not (0x02000000 <= table_ptr < 0x02400000):
            return None
        # Block 32 descriptor at table_ptr + 32 * 12
        desc_bytes = bytes(await reader.read_bytes(table_ptr + 32 * 12, 12))
        rel_offset = int.from_bytes(desc_bytes[8:12], "little")
        # ShortcutSave array starts at Block 32 + 0x5C
        return base_buf + rel_offset + 0x5C
    except Exception:
        return None


@router.get("/bicycle")
async def get_bicycle_status(reader: MemoryReader = Depends(_player_reader)) -> dict[str, Any]:
    """Inspect live bicycle capability and current transport state."""
    return await _inspect_bicycle_state(reader)


@router.post("/bicycle/mount")
async def mount_bicycle(reader: MemoryReader = Depends(_player_reader)) -> dict[str, Any]:
    """Mount bicycle via lightweight shortcut pipeline with live RAM state verification."""
    state = await _inspect_bicycle_state(reader)
    if state["is_cycling"]:
        return {
            "ok": True,
            "status": "already_cycling",
            "mounted": True,
            "transport_mode": "Cycling",
            "frames_per_tile": 4,
            "message": "Player is already riding bicycle.",
        }

    if not state["can_cycle"]:
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "status": "rejected",
                "reason": state["reason"],
                "details": state,
            },
        )

    # 自动内存快捷登记 (Auto Memory Shortcut Registration):
    # 黑2官方 SaveBlock 32 + 0x5C (ShortcutSave) 中，0 固定为自行车全局枚举 (0=自行车, 1=地图, 2=对战记录器, 3=朋友手册, 4=超级钓竿, 5=寻宝机器)
    if _client is not None:
        sh_addr = await _resolve_shortcut_address(reader)
        if sh_addr is not None:
            try:
                # 第0槽绑定 0 (自行车)，后续清空为 0xFF，直通上车/下车，绝不弹轮盘
                bytes_payload = [0] + [0xFF] * 15
                await _client.write_bytes(sh_addr, bytes_payload, domain="Main RAM")
            except Exception:
                pass

    # 发送轻量 4 帧 Y 键触发上车
    if _client is not None:
        await _client.press_buttons(["Y"], frames=4)
    elif _action_engine is not None:
        await _action_engine.press_button("Y", hold_frames=4)
    else:
        raise HTTPException(status_code=503, detail="Emulator input client is not configured.")

    # 闭环等待内存状态跃迁为 Cycling (最多 1.5 秒)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.5
    while loop.time() < deadline:
        await asyncio.sleep(0.1)
        cur_sample = await player_runtime_service.sample(reader, allow_discovery=False)
        cur_trans = str((cur_sample.get("locomotion") or {}).get("transport_mode") or "")
        if cur_trans == "Cycling":
            return {
                "ok": True,
                "status": "succeeded",
                "mounted": True,
                "transport_mode": "Cycling",
                "frames_per_tile": 4,
                "frame": cur_sample.get("frame"),
                "message": "Successfully mounted bicycle via lightweight shortcut pipeline.",
            }

    # 超时仍未上车 (说明 Y 键未登记自行车)
    raise HTTPException(
        status_code=409,
        detail={
            "ok": False,
            "status": "shortcut_not_registered",
            "mounted": False,
            "transport_mode": "OnFoot",
            "reason": "Bicycle is owned and permitted, but pressing shortcut key Y did not activate Cycling state. In the game Bag, please register Bicycle to the Y shortcut key.",
        },
    )


@router.post("/bicycle/dismount")
async def dismount_bicycle(reader: MemoryReader = Depends(_player_reader)) -> dict[str, Any]:
    """Dismount bicycle back to OnFoot via lightweight shortcut pipeline."""
    state = await _inspect_bicycle_state(reader)
    if not state["is_cycling"]:
        return {
            "ok": True,
            "status": "already_on_foot",
            "mounted": False,
            "transport_mode": state["transport_mode"],
            "message": "Player is already on foot.",
        }

    # 确保快捷槽直通绑定自行车 (避免下车时误弹轮盘)
    if _client is not None:
        sh_addr = await _resolve_shortcut_address(reader)
        if sh_addr is not None:
            try:
                bytes_payload = [0] + [0xFF] * 15
                await _client.write_bytes(sh_addr, bytes_payload, domain="Main RAM")
            except Exception:
                pass

    # 发送轻量 4 帧 Y 键下车
    if _client is not None:
        await _client.press_buttons(["Y"], frames=4)
    elif _action_engine is not None:
        await _action_engine.press_button("Y", hold_frames=4)
    else:
        raise HTTPException(status_code=503, detail="Emulator input client is not configured.")

    # 闭环等待内存状态跃迁回 OnFoot (最多 1.5 秒)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.5
    while loop.time() < deadline:
        await asyncio.sleep(0.1)
        cur_sample = await player_runtime_service.sample(reader, allow_discovery=False)
        cur_trans = str((cur_sample.get("locomotion") or {}).get("transport_mode") or "")
        if cur_trans in ("OnFoot", "None", ""):
            return {
                "ok": True,
                "status": "succeeded",
                "dismounted": True,
                "transport_mode": "OnFoot",
                "frame": cur_sample.get("frame"),
                "message": "Successfully dismounted bicycle.",
            }

    raise HTTPException(
        status_code=409,
        detail={
            "ok": False,
            "status": "dismount_timeout",
            "transport_mode": "Cycling",
            "reason": "Dismount input timed out waiting for OnFoot state.",
        },
    )


@router.post("/bicycle/toggle")
async def toggle_bicycle(reader: MemoryReader = Depends(_player_reader)) -> dict[str, Any]:
    """Toggle bicycle between mounted and dismounted states."""
    state = await _inspect_bicycle_state(reader)
    if state["is_cycling"]:
        return await dismount_bicycle(reader)
    else:
        return await mount_bicycle(reader)
