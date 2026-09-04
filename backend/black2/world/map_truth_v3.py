"""Join verified runtime Field state to static Gen-5 ROM map resources.

This is the v5 boundary between dynamic RAM truth and static original-map data.
The ROM is never used to guess which map is current; it is joined only after
runtime mapper/zone evidence produces a matching identity.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from typing import Any

from ..memory.reader import MemoryReader
from .gen5_rom_map import MATRIX_NONE, Gen5RomMap
from .original_world import OriginalWorldService
from .runtime_field_resolver import resolve_runtime_field, resolve_runtime_field_from_ram


class MapTruthV3:
    def __init__(self, rom_path: str | None = None) -> None:
        self.rom = Gen5RomMap(rom_path)
        self.original = OriginalWorldService(str(self.rom.rom.path))
        self._matrix_index: dict[tuple[int, int, tuple[int, ...]], list[int]] | None = None

    def _build_matrix_index(self) -> dict[tuple[int, int, tuple[int, ...]], list[int]]:
        if self._matrix_index is not None:
            return self._matrix_index
        index: dict[tuple[int, int, tuple[int, ...]], list[int]] = {}
        for matrix_id in range(len(self.rom.archive("a/0/0/9").files)):
            try:
                matrix = self.rom.matrix(matrix_id)
            except Exception:
                continue
            key = (matrix.width, matrix.height, matrix.chunk_ids)
            index.setdefault(key, []).append(matrix_id)
        self._matrix_index = index
        return index

    @staticmethod
    def _runtime_zone_votes(runtime: dict[str, Any]) -> dict[str, Any]:
        votes: list[tuple[str, int, str]] = []
        player_zone = (runtime.get("player") or {}).get("zone_id")
        if isinstance(player_zone, int):
            votes.append(("PlayerState.ZoneID", player_zone, "probable"))
        candidate = ((runtime.get("map_identity") or {}).get("zone_id_candidate") or {})
        if isinstance(candidate.get("value"), int):
            votes.append(("FieldActorSystem.zone_consensus", int(candidate["value"]), str(candidate.get("confidence") or "candidate")))
        actors = runtime.get("actors") or {}
        consensus = actors.get("zone_consensus") or {}
        if isinstance(consensus.get("value"), int):
            votes.append(("Actors.zone_consensus", int(consensus["value"]), str(consensus.get("confidence") or "candidate")))
        counts = Counter(value for _source, value, _confidence in votes)
        winner = counts.most_common(1)[0][0] if counts else None
        return {
            "value": winner,
            "votes": [
                {"source": source, "value": value, "confidence": confidence}
                for source, value, confidence in votes
            ],
            "agreement": counts.get(winner, 0) if winner is not None else 0,
            "confidence": (
                "probable" if winner is not None and counts[winner] >= 2
                else "candidate" if winner is not None else "unresolved"
            ),
        }

    def from_runtime(self, runtime: dict[str, Any], *, include_world: bool = True) -> dict[str, Any]:
        mapper = runtime.get("mapper") or {}
        width = mapper.get("matrix_width")
        height = mapper.get("matrix_height")
        chunk_ids = mapper.get("chunk_dat_ids") or []
        if not (
            runtime.get("status") in {"resolved", "candidate"}
            and isinstance(width, int) and isinstance(height, int)
            and isinstance(chunk_ids, list)
            and len(chunk_ids) == width * height
        ):
            return {
                "format": "black2-map-truth/v3",
                "status": "unresolved",
                "confidence": "unresolved",
                "reason": "runtime FieldG3DMapper does not expose a coherent full matrix chunk table",
                "runtime": runtime,
            }

        normalized_chunks = tuple(int(value) & 0xFFFFFFFF for value in chunk_ids)
        key = (width, height, normalized_chunks)
        matrix_candidates = list(self._build_matrix_index().get(key, []))
        zone_votes = self._runtime_zone_votes(runtime)
        runtime_zone = zone_votes.get("value")

        # Use the runtime ZoneID only to disambiguate exact matrix-table matches;
        # never use a ROM ZoneHeader by itself to manufacture current identity.
        zone_matrix = None
        if isinstance(runtime_zone, int) and 0 <= runtime_zone < self.rom.zone_count:
            try:
                zone_matrix = self.rom.zone(runtime_zone).matrix_id
            except Exception:
                zone_matrix = None

        selected_matrix = None
        matrix_reason = "no exact ROM matrix table matches runtime mapper"
        if len(matrix_candidates) == 1:
            selected_matrix = matrix_candidates[0]
            matrix_reason = "unique exact width/height/chunkDatIDs match"
        elif zone_matrix in matrix_candidates:
            selected_matrix = zone_matrix
            matrix_reason = "multiple exact matrices; runtime ZoneID selects matching ZoneHeader.matrixID"

        player_chunk = mapper.get("player_chunk") or {}
        matrix_zone_at_player = None
        if selected_matrix is not None:
            matrix = self.rom.matrix(selected_matrix)
            index = player_chunk.get("index")
            if isinstance(index, int) and 0 <= index < matrix.cell_count and matrix.zone_ids is not None:
                matrix_zone_at_player = matrix.zone_ids[index]

        consistency = {
            "runtime_zone": runtime_zone,
            "zone_header_matrix": zone_matrix,
            "matrix_zone_at_player": matrix_zone_at_player,
            "selected_matrix": selected_matrix,
            "matrix_candidates": matrix_candidates,
            "chunk_table_exact_match": bool(matrix_candidates),
            "zone_header_matches_matrix": (
                isinstance(zone_matrix, int) and selected_matrix == zone_matrix
            ),
            "matrix_zone_matches_runtime_zone": (
                matrix_zone_at_player is None
                or runtime_zone is None
                or matrix_zone_at_player == runtime_zone
            ),
        }

        status = "candidate"
        confidence = "candidate"
        verified_reasons: list[str] = []
        if selected_matrix is not None:
            confidence = "probable"
            status = "resolved"
            verified_reasons.append("runtime mapper chunk table exactly matches ROM matrix")
        if (
            selected_matrix is not None
            and isinstance(runtime_zone, int)
            and zone_matrix == selected_matrix
            and consistency["matrix_zone_matches_runtime_zone"]
        ):
            confidence = "verified"
            status = "resolved"
            verified_reasons.extend([
                "Player/Actor runtime ZoneID maps to selected ROM ZoneHeader",
                "ZoneHeader.matrixID agrees with runtime-matched matrix",
                "matrix zone table agrees at player chunk when present",
            ])

        world = None
        if include_world and isinstance(runtime_zone, int) and 0 <= runtime_zone < self.rom.zone_count:
            try:
                world = self.original.zone(runtime_zone)
                live_span = mapper.get("chunk_span_world")
                if isinstance(live_span, (int, float)) and live_span > 0:
                    world = self._apply_live_chunk_span(world, float(live_span))
            except Exception as error:
                world = {"zone_id": runtime_zone, "decode_error": str(error)}

        return {
            "format": "black2-map-truth/v3",
            "status": status,
            "confidence": confidence,
            "verification": verified_reasons,
            "matrix_match": {
                "selected_matrix_id": selected_matrix,
                "candidate_matrix_ids": matrix_candidates,
                "reason": matrix_reason,
            },
            "zone_identity": zone_votes,
            "consistency": consistency,
            "runtime": runtime,
            "original_world": world,
        }

    @staticmethod
    def _apply_live_chunk_span(world: dict[str, Any], span: float) -> dict[str, Any]:
        # Shallow/deep copy only the pieces changed, keeping the cached static
        # source immutable.
        import copy
        result = copy.deepcopy(world)
        result["render_coordinate_system"] = {
            "chunk_span_world": span,
            "confidence": "runtime_verified",
            "reason": "FieldG3DMapper.chunkSpan",
        }
        for item in result.get("buildings", []):
            cell = item.get("chunk_cell") or {}
            local = item.get("local_position") or {}
            if all(isinstance(cell.get(k), int) for k in ("x", "y")):
                item["world_position"] = {
                    "x": (cell["x"] + 0.5) * span + float(local.get("x") or 0.0),
                    "y": float(local.get("y") or 0.0),
                    "z": (cell["y"] + 0.5) * span - float(local.get("z") or 0.0),
                }
                item["world_position_confidence"] = "runtime_verified_span"
        return result

    def from_ram(self, ram: bytes, *, frame: int | None = None, include_world: bool = True) -> dict[str, Any]:
        runtime = resolve_runtime_field_from_ram(ram, frame=frame)
        return self.from_runtime(runtime, include_world=include_world)

    async def current(self, reader: MemoryReader, *, include_world: bool = True) -> dict[str, Any]:
        runtime = await resolve_runtime_field(reader)
        return self.from_runtime(runtime, include_world=include_world)
