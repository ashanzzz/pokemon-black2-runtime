"""ROM-complete map knowledge for human and AI consumers.

The catalog is deliberately lossless where the ROM is understood and explicit
where semantics are not proven. It indexes every map model, matrix, ZoneData
header, and overworld event record once, then exposes smaller current-window
views for live play.
"""
from __future__ import annotations

from collections import Counter
import struct
from datetime import datetime, timezone
from typing import Any

from ..memory.reader import MemoryReader
from .native_map import NativeMapError, inspect_loaded_visual_map, read_live_map_state
from .rom_maps import NativeMapEngine, NarcArchive


ZONE_RECORD_SIZE = 0x30
OVERWORLD_EVENTS_PATH = "a/1/2/6"
_HEADER_SIZE = 8
_FURNITURE_SIZE = 0x14
_NPC_SIZE = 0x24
_WARP_SIZE = 0x14
_TRIGGER_SIZE = 0x16


def _s16(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<h", raw, offset)[0]


def _decode_matrix(raw: bytes, matrix_id: int) -> dict[str, Any]:
    if len(raw) < 8:
        raise ValueError(f"Matrix {matrix_id} is truncated")
    width, height = struct.unpack_from("<HH", raw, 4)
    count = width * height
    if not width or not height or len(raw) < 8 + count * 4:
        raise ValueError(f"Matrix {matrix_id} has invalid dimensions")
    model_ids = struct.unpack_from(f"<{count}I", raw, 8)
    has_zones = struct.unpack_from("<I", raw, 0)[0] == 1
    definitions = (  # deprecated v4 name; contains ZoneID values
        struct.unpack_from(f"<{count}I", raw, 8 + count * 4)
        if has_zones and len(raw) >= 8 + count * 8 else None
    )
    model_counts = Counter(model_ids)
    definition_counts = Counter(definitions) if definitions is not None else Counter()
    return {
        "matrix_id": matrix_id,
        "width": width,
        "height": height,
        "cell_count": count,
        "model_ids": sorted(model_counts),
        "model_counts": {str(key): value for key, value in sorted(model_counts.items())},
        "has_definition_table": definitions is not None,
        "definition_ids": sorted(definition_counts),
        "definition_counts": {str(key): value for key, value in sorted(definition_counts.items())},
        "source": f"rom:/a/0/0/9[{matrix_id}]",
        "_model_table": model_ids,
        "_definition_table": definitions,
    }


def _decode_events(raw: bytes, event_archive_id: int) -> dict[str, Any]:
    if len(raw) < _HEADER_SIZE:
        raise ValueError(f"Overworld record {event_archive_id} is truncated")
    declared_length = struct.unpack_from("<I", raw, 0)[0]
    furniture_count, npc_count, warp_count, trigger_count = raw[4:8]
    required = (
        _HEADER_SIZE
        + furniture_count * _FURNITURE_SIZE
        + npc_count * _NPC_SIZE
        + warp_count * _WARP_SIZE
        + trigger_count * _TRIGGER_SIZE
    )
    if required > len(raw):
        raise ValueError(f"Overworld record {event_archive_id} declares {required} bytes but contains {len(raw)}")

    cursor = _HEADER_SIZE
    furniture = []
    for index in range(furniture_count):
        record = raw[cursor:cursor + _FURNITURE_SIZE]
        cursor += _FURNITURE_SIZE
        furniture.append({
            "id": index,
            "script_id": struct.unpack_from("<H", record, 0)[0],
            "x": struct.unpack_from("<i", record, 8)[0],
            "y": struct.unpack_from("<i", record, 12)[0],
            "z": struct.unpack_from("<i", record, 16)[0],
            "tile_x": struct.unpack_from("<i", record, 8)[0],
            "tile_y": struct.unpack_from("<i", record, 12)[0],
            "coordinate_units": "map_local_tiles",
        })

    npcs = []
    for index in range(npc_count):
        record = raw[cursor:cursor + _NPC_SIZE]
        cursor += _NPC_SIZE
        npcs.append({
            "id": struct.unpack_from("<H", record, 0)[0],
            "sprite_id": struct.unpack_from("<H", record, 2)[0],
            "movement_id": struct.unpack_from("<H", record, 4)[0],
            "flag_id": struct.unpack_from("<H", record, 8)[0],
            "script_id": struct.unpack_from("<H", record, 10)[0],
            "facing_id": struct.unpack_from("<H", record, 12)[0],
            "x": _s16(record, 28),
            "y": _s16(record, 30),
            "z": _s16(record, 34),
            "tile_x": _s16(record, 28),
            "tile_y": _s16(record, 30),
            "coordinate_units": "map_local_tiles",
        })

    warps = []
    for index in range(warp_count):
        record = raw[cursor:cursor + _WARP_SIZE]
        cursor += _WARP_SIZE
        x_world, y_world = _s16(record, 8), _s16(record, 12)
        warps.append({
            "id": index,
            "target_map_id": struct.unpack_from("<H", record, 0)[0],
            "target_warp_id": struct.unpack_from("<H", record, 2)[0],
            "kind": struct.unpack_from("<H", record, 4)[0],
            "x_world": x_world,
            "y_world": y_world,
            "z": _s16(record, 18),
            "tile_x": x_world / 16.0,
            "tile_y": y_world / 16.0,
            "width": max(1, struct.unpack_from("<H", record, 14)[0]),
            "height": max(1, struct.unpack_from("<H", record, 16)[0]),
            "coordinate_units": "map_world_units_16_per_tile",
        })

    triggers = []
    for index in range(trigger_count):
        record = raw[cursor:cursor + _TRIGGER_SIZE]
        cursor += _TRIGGER_SIZE
        triggers.append({
            "id": index,
            "entity_id": struct.unpack_from("<H", record, 0)[0],
            "constant": struct.unpack_from("<H", record, 2)[0],
            "reference": struct.unpack_from("<H", record, 4)[0],
            "x": _s16(record, 10),
            "y": _s16(record, 12),
            "z": _s16(record, 14),
            "tile_x": _s16(record, 10),
            "tile_y": _s16(record, 12),
            "coordinate_units": "map_local_tiles",
        })

    return {
        "event_archive_id": event_archive_id,
        "source": f"rom:/{OVERWORLD_EVENTS_PATH}[{event_archive_id}]",
        "coordinate_space": "map_definition_local_map_plane",
        "declared_length": declared_length,
        "furniture": furniture,
        "npcs": npcs,
        "warps": warps,
        "triggers": triggers,
        "counts": {
            "furniture": furniture_count,
            "npcs": npc_count,
            "warps": warp_count,
            "triggers": trigger_count,
        },
        "semantic_status": {
            "warp_targets": "raw target_map_id/target_warp_id; destination mapping still needs live transition evidence",
            "object_types": "raw furniture/NPC/trigger records; item and field-move semantics are not inferred",
        },
    }


def _visual_definition_ids(visual: dict[str, Any], engine: NativeMapEngine) -> set[int]:
    """Read definitions only from cells resident in the live visual window."""
    definition_ids: set[int] = set()
    scenes = visual.get("candidate_scenes", [])
    if visual.get("matrix_id") is not None:
        scenes = [
            {
                "matrix_id": visual["matrix_id"],
                "resident_cells": visual.get("active_cells", []),
            },
            *scenes,
        ]
    for scene in scenes:
        try:
            matrix_id = int(scene["matrix_id"])
            matrix = _decode_matrix(engine.matrix_narc.files[matrix_id], matrix_id)
        except (KeyError, IndexError, TypeError, ValueError, struct.error):
            continue
        definitions = matrix["_definition_table"]
        if definitions is None:
            continue
        width, height = matrix["width"], matrix["height"]
        for cell in scene.get("resident_cells", []):
            try:
                x, y = int(cell["x"]), int(cell["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if 0 <= x < width and 0 <= y < height:
                definition = definitions[y * width + x]
                if definition != 0xFFFFFFFF:
                    definition_ids.add(definition)
    direct_definition = visual.get("map_definition_id")
    if direct_definition is not None and direct_definition != 0xFFFFFFFF:
        definition_ids.add(int(direct_definition))
    return definition_ids


def _zone_headers(engine: NativeMapEngine) -> list[dict[str, Any]]:
    count, trailing = divmod(len(engine.zone_data), ZONE_RECORD_SIZE)
    headers = []
    for header_id in range(count):
        offset = header_id * ZONE_RECORD_SIZE
        raw = engine.zone_data[offset:offset + ZONE_RECORD_SIZE]
        headers.append({
            "map_header_id": header_id,
            "area_id": struct.unpack_from("<H", raw, 0x02)[0],
            "matrix_id": struct.unpack_from("<H", raw, 0x04)[0],
            "entities_id": struct.unpack_from("<H", raw, 0x16)[0],
            # Deprecated v4 aliases. There are not two matrix IDs here.
            "primary_matrix_id": struct.unpack_from("<H", raw, 0x04)[0],
            "shared_matrix_id": struct.unpack_from("<H", raw, 0x04)[0],
            "event_archive_id": struct.unpack_from("<H", raw, 0x16)[0],
            "raw_hex": raw.hex(),
            "source": f"rom:/a/0/1/2[{header_id}]",
        })
    if trailing:
        for header in headers:
            header["zone_data_trailing_bytes"] = trailing
    return headers


def _model_catalog(engine: NativeMapEngine) -> list[dict[str, Any]]:
    models = []
    for model_id, model in sorted(engine.models.items()):
        plane_counts = [
            {f"0x{code:02X}": count for code, count in sorted(Counter(plane).items())}
            for plane in model.planes
        ]
        models.append({
            "model_id": model_id,
            "signature": f"0x{model.signature:08X}",
            "width": model.width,
            "height": model.height,
            "plane_count": model.plane_count,
            "permission_encoding": "raw_contiguous_byte_planes",
            "plane_value_counts": plane_counts,
            "semantic_status": "raw permission bytes only; passability, terrain, height and field-move meaning are not inferred",
            "source": f"rom:/a/0/0/8[{model_id}]",
        })
    return models


class MapKnowledgeService:
    """Build a ROM-wide catalog once and provide live, smaller projections."""

    def __init__(self) -> None:
        self._catalog: dict[str, Any] | None = None
        self._event_cache: dict[int, dict[str, Any]] = {}
        self._last_live: dict[str, Any] | None = None
        self._observations: list[dict[str, Any]] = []

    @property
    def engine(self) -> NativeMapEngine:
        return NativeMapEngine.get_instance()

    def _events(self, event_archive_id: int) -> dict[str, Any]:
        if event_archive_id not in self._event_cache:
            archive = NarcArchive(self.engine.rom.read_file(OVERWORLD_EVENTS_PATH))
            if not 0 <= event_archive_id < len(archive.files):
                raise ValueError(f"event archive {event_archive_id} is outside {OVERWORLD_EVENTS_PATH}")
            self._event_cache[event_archive_id] = _decode_events(archive.files[event_archive_id], event_archive_id)
        return self._event_cache[event_archive_id]

    def catalog(self) -> dict[str, Any]:
        if self._catalog is not None:
            return self._catalog
        engine = self.engine
        matrices = []
        for matrix_id, raw in enumerate(engine.matrix_narc.files):
            try:
                matrix = _decode_matrix(raw, matrix_id)
            except (ValueError, struct.error) as error:
                matrices.append({"matrix_id": matrix_id, "decode_error": str(error), "source": f"rom:/a/0/0/9[{matrix_id}]"})
                continue
            matrices.append({key: value for key, value in matrix.items() if not key.startswith("_")})

        headers = _zone_headers(engine)
        events = []
        for event_archive_id in range(len(NarcArchive(engine.rom.read_file(OVERWORLD_EVENTS_PATH)).files)):
            try:
                events.append(self._events(event_archive_id))
            except (ValueError, struct.error) as error:
                events.append({"event_archive_id": event_archive_id, "decode_error": str(error), "source": f"rom:/{OVERWORLD_EVENTS_PATH}[{event_archive_id}]"})

        self._catalog = {
            "format": "black2-rom-map-knowledge/v1",
            "rom_name": self.engine.rom.path.name,
            "sources": {
                "models": "rom:/a/0/0/8",
                "matrices": "rom:/a/0/0/9",
                "zone_data": "rom:/a/0/1/2",
                "overworld_events": f"rom:/{OVERWORLD_EVENTS_PATH}",
                "read_only": True,
            },
            "semantic_policy": {
                "collision": "raw permission planes are preserved; semantic labels require movement evidence",
                "height": "raw plane values and model elevation fields are preserved; height meaning is unverified",
                "warps": "raw source and target IDs are preserved; target landing is upgraded only after live transition evidence",
                "items_and_field_moves": "script/entity references are preserved; Strength/Cut/etc. are not assigned from bytes",
            },
            "summary": {
                "model_count": len(self.engine.model_narc.files),
                "decodable_model_count": len(self.engine.models),
                "matrix_count": len(matrices),
                "zone_header_count": len(headers),
                "event_archive_count": len(events),
                "decoded_event_archive_count": sum("counts" in item for item in events),
                "malformed_event_archive_count": sum("decode_error" in item for item in events),
                "event_record_totals": {
                    key: sum(item.get("counts", {}).get(key, 0) for item in events)
                    for key in ("furniture", "npcs", "warps", "triggers")
                },
            },
            "models": _model_catalog(self.engine),
            "matrices": matrices,
            "map_headers": headers,
            "events": events,
        }
        return self._catalog

    def map_detail(self, map_header_id: int, include_raw: bool = False) -> dict[str, Any]:
        catalog = self.catalog()
        headers = [item for item in catalog["map_headers"] if item["map_header_id"] == map_header_id]
        if not headers:
            raise ValueError(f"Map Header {map_header_id} is outside ZoneData")
        header = headers[0]
        matrix_ids = sorted({header["primary_matrix_id"], header["shared_matrix_id"]})
        matrices = []
        model_ids: set[int] = set()
        for matrix_id in matrix_ids:
            if matrix_id >= len(self.engine.matrix_narc.files):
                matrices.append({"matrix_id": matrix_id, "decode_error": "matrix reference is outside a/0/0/9"})
                continue
            matrix = _decode_matrix(self.engine.matrix_narc.files[matrix_id], matrix_id)
            definitions = matrix["_definition_table"]
            cells = []
            if definitions is not None:
                cells = [
                    {"x": index % matrix["width"], "y": index // matrix["width"], "model_id": matrix["_model_table"][index]}
                    for index, definition in enumerate(definitions)
                    if definition == map_header_id
                ]
            else:
                cells = [
                    {"x": index % matrix["width"], "y": index // matrix["width"], "model_id": model_id}
                    for index, model_id in enumerate(matrix["_model_table"])
                ]
            model_ids.update(cell["model_id"] for cell in cells)
            matrices.append({
                key: value for key, value in matrix.items()
                if not key.startswith("_")
            } | {"selected_cells": cells, "association_verified": bool(cells)})

        model_map = {item["model_id"]: item for item in catalog["models"]}
        models = []
        for model_id in sorted(model_ids):
            model = dict(model_map.get(model_id, {"model_id": model_id, "semantic_status": "model not decoded"}))
            if include_raw and model_id in self.engine.models:
                model["permission_planes"] = {
                    str(plane): self.engine.models[model_id].plane_rows(plane)
                    for plane in range(self.engine.models[model_id].plane_count)
                }
            models.append(model)
        event_archive_id = header["event_archive_id"]
        try:
            events = self._events(event_archive_id)
        except (ValueError, struct.error) as error:
            events = {"event_archive_id": event_archive_id, "decode_error": str(error)}
        return {
            "format": "black2-map-detail/v1",
            "map_header": header,
            "matrices": matrices,
            "models": models,
            "events": events,
            "verification": {
                "matrix_association": "exact ZoneData fields and definition-table cells; empty selected_cells means association is not proven",
                "collision_semantics": "raw only",
                "height_semantics": "raw only",
                "warp_destination": "raw target IDs only until a live map transition confirms the landing",
            },
        }

    def _record_live_observation(self, snapshot: dict[str, Any]) -> dict[str, Any] | None:
        previous = self._last_live
        if previous is None:
            observation = {
                "observation_id": 1,
                "kind": "initial_sample",
                "verified": False,
                "evidence": "首次读取 ARM9 位置镜像；没有前一时刻可比较。",
                "before": None,
                "after": snapshot["live_player"],
                "semantic_upgrade": False,
            }
        else:
            before = previous["live_player"]
            after = snapshot["live_player"]
            position_changed = (
                before.get("verified") and after.get("verified")
                and (before.get("x"), before.get("y"), before.get("elevation"))
                != (after.get("x"), after.get("y"), after.get("elevation"))
            )
            map_changed = (
                before.get("map_section_id") is not None
                and after.get("map_section_id") is not None
                and before.get("map_section_id") != after.get("map_section_id")
            )
            if not position_changed and not map_changed:
                return None
            kind = "map_section_change" if map_changed else "position_change"
            evidence = (
                "多份 ARM9 位置镜像和地图字段在采样前后发生一致变化；这是实时移动/切图证据，"
                "不是碰撞语义结论。"
                if position_changed or map_changed else ""
            )
            observation = {
                "observation_id": len(self._observations) + 1,
                "kind": kind,
                "verified": True,
                "evidence": evidence,
                "before": before,
                "after": after,
                "semantic_upgrade": False,
            }
        observation["observed_at"] = datetime.now(timezone.utc).isoformat()
        self._observations.append(observation)
        self._observations = self._observations[-100:]
        return observation

    def observations(self) -> dict[str, Any]:
        return {
            "format": "black2-live-map-observations/v1",
            "count": len(self._observations),
            "observations": list(self._observations),
            "semantic_status": (
                "位置变化和地图字段变化可作为实时证据；碰撞、目标落点和 Strength/Cut 等能力仍需"
                "在明确输入已送达且游戏状态稳定时逐项验证。"
            ),
        }

    async def current(self, reader: MemoryReader, include_raw: bool = False) -> dict[str, Any]:
        live = await read_live_map_state(reader)
        visual = None
        visual_error = None
        if live.x is not None and live.y is not None:
            try:
                visual = await inspect_loaded_visual_map(reader, live.x, live.y, self.engine)
            except (NativeMapError, ConnectionError, TimeoutError, OSError) as error:
                visual_error = str(error)

        candidate_headers: set[int] = set()
        candidate_matrix_ids = []
        if visual:
            candidate_matrix_ids = sorted({
                int(item["matrix_id"])
                for item in visual.get("candidate_scenes", [])
                if item.get("matrix_id") is not None
            } | ({int(visual["matrix_id"])} if visual.get("matrix_id") is not None else set()))
            candidate_headers.update(_visual_definition_ids(visual, self.engine))

        details = []
        for map_header_id in sorted(candidate_headers):
            try:
                details.append(self.map_detail(map_header_id, include_raw=include_raw))
            except (ValueError, struct.error):
                continue
        snapshot = {
            "format": "black2-live-map-knowledge/v1",
            "live_player": {
                "x": live.x,
                "y": live.y,
                "elevation": live.elevation,
                "map_section_id": live.map_id,
                "verified": live.verified,
            },
            "native_visual": visual,
            "native_visual_error": visual_error,
            "candidate_matrix_ids": candidate_matrix_ids,
            "candidate_map_header_count": len(details),
            "candidate_map_headers": details,
            "movement_observation": None,
            "observation_count": len(self._observations),
            "semantic_status": "current model/matrix residency is live evidence; collision and ability meanings remain unverified until movement/trigger observations are recorded",
        }
        snapshot["movement_observation"] = self._record_live_observation(snapshot)
        snapshot["observation_count"] = len(self._observations)
        self._last_live = snapshot
        return snapshot


def format_current_text(snapshot: dict[str, Any]) -> str:
    live = snapshot["live_player"]
    lines = [
        "POKEMON BLACK 2 · LIVE MAP KNOWLEDGE",
        "====================================",
        f"玩家坐标: X={live['x']} Y={live['y']} 高程原始值={live['elevation']} 验证={live['verified']}",
        f"游戏地图字段: {live['map_section_id']}（仅作实时字段，不代替 Map Header）",
        f"当前矩阵候选: {snapshot['candidate_matrix_ids'] or '未确认'}",
        f"移动观测记录数: {snapshot.get('observation_count', 0)}",
        "",
        "语义边界:",
        "- 碰撞: 输出 ROM 原始 permission byte 平面；可走/阻挡/单向坡等含义必须由实际移动结果确认。",
        "- 高度: 输出模型的原始 plane 数据和 ARM9 高程字段；未把任何 plane 猜成高度层。",
        "- 出入口: 输出 source/target map 与 warp ID；目标落点要以实际切图后的 Map Header 验证。",
        "- 道具/能力: 输出 furniture、script、entity、trigger 原始记录；未把对象猜成怪力石或可砍树。",
        "",
        f"候选 Map Header 数量: {snapshot['candidate_map_header_count']}",
    ]
    for detail in snapshot["candidate_map_headers"]:
        header = detail["map_header"]
        event = detail["events"]
        lines.append("")
        lines.append(f"Map Header #{header['map_header_id']} · event archive #{header['event_archive_id']}")
        lines.append(f"矩阵关联: primary #{header['primary_matrix_id']} / shared #{header['shared_matrix_id']}")
        counts = event.get("counts", {})
        lines.append(f"事件记录: warps={counts.get('warps', 0)} npcs={counts.get('npcs', 0)} furniture={counts.get('furniture', 0)} triggers={counts.get('triggers', 0)}")
        for warp in event.get("warps", []):
            lines.append(f"  出入口 #{warp['id']}: ({warp['tile_x']},{warp['tile_y']}) -> target_map={warp['target_map_id']} target_warp={warp['target_warp_id']}（待实际切图验证）")
        for model in detail.get("models", []):
            lines.append(f"  碰撞模型 #{model['model_id']}: {model.get('width', '?')}x{model.get('height', '?')} planes={model.get('plane_count', '?')} · 原始字节")
            if "permission_planes" in model:
                for plane_id, rows in model["permission_planes"].items():
                    lines.append(f"    permission plane {plane_id}（原始 hex，每行一个模型行）:")
                    lines.extend(f"      {' '.join(f'{value:02X}' for value in row)}" for row in rows)
    observation = snapshot.get("movement_observation")
    if observation:
        lines.extend([
            "",
            f"本次实时观测: {observation['kind']} · verified={observation['verified']}",
            f"  证据: {observation['evidence']}",
        ])
    if snapshot.get("native_visual_error"):
        lines.append("")
        lines.append(f"原生视觉窗口诊断: {snapshot['native_visual_error']}")
    return "\n".join(lines) + "\n"


def format_catalog_text(catalog: dict[str, Any]) -> str:
    summary = catalog["summary"]
    lines = [
        "POKEMON BLACK 2 · ROM MAP KNOWLEDGE CATALOG",
        "===========================================",
        f"ROM: {catalog['rom_name']}",
        f"模型: {summary['decodable_model_count']}/{summary['model_count']} 可解码",
        f"矩阵: {summary['matrix_count']} · Map Header: {summary['zone_header_count']} · 事件档案: {summary['event_archive_count']}",
        f"事件档案解码: {summary['decoded_event_archive_count']} 成功 · {summary['malformed_event_archive_count']} 条保留为错误",
        f"事件总量: {summary['event_record_totals']}",
        "",
        "注意: 碰撞/高度/能力均保留原始数据，不把未经游戏操作验证的字节翻译成语义。",
        "",
        "Map Header / 入口索引:",
    ]
    for header in catalog["map_headers"]:
        lines.append(
            f"  #{header['map_header_id']}: primary_matrix={header['primary_matrix_id']} shared_matrix={header['shared_matrix_id']} event_archive={header['event_archive_id']}"
        )
    lines.append("")
    lines.append("模型碰撞索引:")
    for model in catalog["models"]:
        lines.append(
            f"  #{model['model_id']}: {model['width']}x{model['height']} planes={model['plane_count']} signature={model['signature']} raw_permission"
        )
    lines.append("")
    lines.append("事件档案索引:")
    for event in catalog["events"]:
        if "counts" in event:
            lines.append(f"  #{event['event_archive_id']}: {event['counts']}")
        else:
            lines.append(f"  #{event['event_archive_id']}: {event.get('decode_error', 'decode error')}")
    return "\n".join(lines) + "\n"
