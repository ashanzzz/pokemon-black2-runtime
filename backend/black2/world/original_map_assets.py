"""Original B2/W2 terrain/building asset extraction and optional Apicula GLB cache."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import shutil
import subprocess
from typing import Any

from .gen5_rom_map import Gen5RomMap


class OriginalMapAssetError(RuntimeError):
    pass


class OriginalMapAssetService:
    def __init__(self, rom_path: str | None = None, project_root: str | Path | None = None) -> None:
        self.rom = Gen5RomMap(rom_path)
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        self.cache = root / "runtime" / "original_map_v5"
        self.apicula = root / "runtime" / "tools" / "apicula" / "apicula.exe"
        self._lock = asyncio.Lock()

    @staticmethod
    def _hash(*parts: bytes) -> str:
        h = hashlib.sha256()
        for part in parts:
            h.update(part)
        return h.hexdigest()[:20]

    async def _convert(self, model: bytes, texture: bytes, namespace: str) -> Path:
        key = self._hash(model, texture)
        out = self.cache / namespace / key
        out.mkdir(parents=True, exist_ok=True)
        existing = sorted(out.glob("*.glb"))
        if len(existing) == 1 and existing[0].stat().st_size > 100:
            return existing[0]
        if not self.apicula.is_file():
            raise OriginalMapAssetError(
                f"Apicula missing: {self.apicula}. Raw model/texture endpoints still remain available."
            )
        model_path = out / "model.bmd"
        texture_path = out / "texture.btx"
        model_path.write_bytes(model)
        texture_path.write_bytes(texture)
        result = await asyncio.to_thread(
            subprocess.run,
            [
                str(self.apicula), "convert", "-f", "glb", "--overwrite",
                str(model_path), str(texture_path), "-o", str(out),
            ],
            cwd=self.cache.parent,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        generated = sorted(out.glob("*.glb"))
        if result.returncode != 0 or not generated:
            raise OriginalMapAssetError(
                f"Apicula failed ({result.returncode}): {(result.stderr or result.stdout)[-1200:]}"
            )
        if len(generated) > 1:
            # Deterministic selection: largest generated scene.  Keep all files
            # for forensic review rather than deleting converter output.
            generated.sort(key=lambda p: (-p.stat().st_size, p.name))
        return generated[0]

    def raw_terrain(self, zone_id: int, x: int, y: int) -> tuple[bytes, bytes, dict[str, Any]]:
        zone = self.rom.zone(zone_id)
        matrix = self.rom.matrix(zone.matrix_id)
        cell = matrix.cell(x, y)
        chunk_id = int(cell["chunk_id"])
        chunk = self.rom.chunk(chunk_id)
        texture = self.rom.terrain_texture(zone.area_id)
        return chunk.terrain_model, texture, {
            "zone_id": zone_id,
            "matrix_id": zone.matrix_id,
            "chunk_id": chunk_id,
            "cell": {"x": x, "y": y},
            "texture_id": self.rom.area(zone.area_id).textures_id,
        }

    async def terrain_glb(self, zone_id: int, x: int, y: int) -> tuple[Path, dict[str, Any]]:
        model, texture, meta = self.raw_terrain(zone_id, x, y)
        async with self._lock:
            path = await self._convert(model, texture, "terrain")
        return path, meta

    def raw_building(self, zone_id: int, uid: int) -> tuple[bytes, bytes, dict[str, Any]]:
        zone = self.rom.zone(zone_id)
        area = self.rom.area(zone.area_id)
        bundle = self.rom.building_bundle(zone.area_id)
        resource = bundle.by_uid(uid)
        if resource is None:
            raise OriginalMapAssetError(f"building UID {uid} is not in area {zone.area_id} bundle")
        texture = self.rom.building_texture(zone.area_id)
        return resource.model, texture, {
            "zone_id": zone_id,
            "area_id": zone.area_id,
            "buildings_id": area.buildings_id,
            "is_exterior": area.is_exterior,
            "uid": resource.uid,
            "resource_index": resource.resource_index,
            "door_uid": resource.door_uid,
        }

    async def building_glb(self, zone_id: int, uid: int) -> tuple[Path, dict[str, Any]]:
        model, texture, meta = self.raw_building(zone_id, uid)
        async with self._lock:
            path = await self._convert(model, texture, "buildings")
        return path, meta

    def cache_status(self) -> dict[str, Any]:
        return {
            "state": "ready",
            "cache": str(self.cache),
            "apicula": str(self.apicula),
            "apicula_available": self.apicula.is_file(),
            "policy": "raw original assets only; no procedural replacement geometry",
        }
