"""Original Gen-5 field actor resource lookup for the v6 3D world viewer.

CTRMapV documents B2/W2 FIELD_MMODEL_INDEX as archive 47 (a/0/4/7) and
FIELD_MMODEL_RES as archive 48 (a/0/4/8).  Player default OBJCODEs are 231
(male) and 240 (female).  The registry entry is 28 bytes after a u32 count.

This service stays ROM-only and cached.  It never reads emulator RAM.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, asdict
import hashlib
from pathlib import Path
import shutil
import struct
import subprocess
from typing import Any

from .gen5_rom_map import Gen5RomMap

MMODEL_INDEX_PATH = "a/0/4/7"
MMODEL_RES_PATH = "a/0/4/8"
ENTRY_SIZE = 28
PLAYER_OBJCODE_MALE = 231
PLAYER_OBJCODE_FEMALE = 240


class ActorAssetError(RuntimeError):
    pass


def normalize_bw2_objcode(obj_code: int) -> int:
    if obj_code >= 377:
        if obj_code < 4096 or obj_code > 4716:
            if obj_code < 8192 or obj_code > 8203:
                return 10
            return obj_code - 7195
        return obj_code - 3719
    return obj_code


@dataclass(frozen=True)
class ActorRegistryEntry:
    index: int
    uid: int
    entity_type: int
    scene_node_type: int
    shadow: bool
    footprints: int
    reflections: bool
    billboard_size: int
    sprite_atlas_size: int
    sprite_controller_type: int
    gender: int
    width: int
    height: int
    wpos_offset_x: int
    wpos_offset_y: int
    wpos_offset_z: int
    resource_indices: tuple[int, int, int, int, int]


class OriginalActorAssetService:
    def __init__(self, rom_path: str | None = None, project_root: str | Path | None = None) -> None:
        self.rom = Gen5RomMap(rom_path)
        root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        self.cache = root / "runtime" / "original_actor_v6"
        self.apicula = root / "runtime" / "tools" / "apicula" / "apicula.exe"
        self._lock = asyncio.Lock()

    def _registry_blob(self) -> bytes:
        files = self.rom.archive(MMODEL_INDEX_PATH).files
        if not files:
            raise ActorAssetError(f"{MMODEL_INDEX_PATH} has no files")
        return files[0]

    def registry_entry(self, obj_code: int) -> ActorRegistryEntry:
        index = normalize_bw2_objcode(int(obj_code))
        raw = self._registry_blob()
        if len(raw) < 4:
            raise ActorAssetError("MModel registry is truncated")
        count = struct.unpack_from("<I", raw, 0)[0]
        if not 0 <= index < count:
            raise ActorAssetError(f"OBJCODE {obj_code} normalized to {index}, registry count={count}")
        off = 4 + index * ENTRY_SIZE
        if off + ENTRY_SIZE > len(raw):
            raise ActorAssetError("MModel registry entry is truncated")
        r = raw[off:off + ENTRY_SIZE]
        resources = struct.unpack_from("<5H", r, 16)
        def s8(v: int) -> int:
            return v - 256 if v >= 128 else v
        return ActorRegistryEntry(
            index=index,
            uid=struct.unpack_from("<H", r, 0)[0],
            entity_type=r[2],
            scene_node_type=r[3],
            shadow=bool(r[4]),
            footprints=r[5],
            reflections=bool(r[6]),
            billboard_size=r[7],
            sprite_atlas_size=r[8],
            sprite_controller_type=r[9],
            gender=r[10],
            width=r[11],
            height=r[12],
            wpos_offset_x=s8(r[13]),
            wpos_offset_y=s8(r[14]),
            wpos_offset_z=s8(r[15]),
            resource_indices=tuple(resources),
        )

    def descriptor(self, obj_code: int) -> dict[str, Any]:
        entry = self.registry_entry(obj_code)
        res_id = entry.resource_indices[0]
        files = self.rom.archive(MMODEL_RES_PATH).files
        if not 0 <= res_id < len(files):
            raise ActorAssetError(f"actor resource {res_id} outside {MMODEL_RES_PATH}")
        payload = files[res_id]
        kind = "nsbmd_3d" if res_id < 7 else "nsbtx_billboard"
        return {
            "format": "black2-original-actor/v6",
            "obj_code": obj_code,
            "normalized_obj_code": entry.index,
            "registry": asdict(entry),
            "resource_id": res_id,
            "resource_kind": kind,
            "resource_magic": payload[:4].decode("ascii", "replace"),
            "source": {
                "registry": f"rom:/{MMODEL_INDEX_PATH}[0]",
                "resource": f"rom:/{MMODEL_RES_PATH}[{res_id}]",
            },
            "renderer": {
                "preferred": "original_glb" if kind == "nsbmd_3d" else "original_billboard_texture",
                "fallback": "pixel_marker",
                "note": "fallback is presentation-only and never treated as ROM geometry",
            },
        }

    def raw_resource(self, obj_code: int) -> tuple[bytes, dict[str, Any]]:
        meta = self.descriptor(obj_code)
        payload = self.rom.archive(MMODEL_RES_PATH).files[int(meta["resource_id"])]
        return payload, meta

    async def actor_glb(self, obj_code: int) -> tuple[Path, dict[str, Any]]:
        payload, meta = self.raw_resource(obj_code)
        if meta["resource_kind"] != "nsbmd_3d":
            raise ActorAssetError("this actor is a billboard texture resource, not an NSBMD model")
        if not self.apicula.is_file():
            raise ActorAssetError(f"Apicula missing: {self.apicula}")
        key = hashlib.sha256(payload).hexdigest()[:20]
        out = self.cache / "models" / key
        out.mkdir(parents=True, exist_ok=True)
        ready = sorted(out.glob("*.glb"))
        if ready:
            return ready[0], meta
        source = out / "actor.bmd"
        source.write_bytes(payload)
        async with self._lock:
            result = await asyncio.to_thread(
                subprocess.run,
                [str(self.apicula), "convert", "-f", "glb", "--overwrite", str(source), "-o", str(out)],
                cwd=self.cache,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        ready = sorted(out.glob("*.glb"))
        if result.returncode != 0 or not ready:
            raise ActorAssetError(f"Apicula actor conversion failed: {(result.stderr or result.stdout)[-1000:]}")
        return ready[0], meta

    async def billboard_pngs(self, obj_code: int) -> tuple[list[Path], dict[str, Any]]:
        """Best-effort exact texture extraction; no synthetic texture is returned here."""
        payload, meta = self.raw_resource(obj_code)
        if meta["resource_kind"] != "nsbtx_billboard":
            raise ActorAssetError("this actor is not a billboard texture resource")
        if not self.apicula.is_file():
            raise ActorAssetError(f"Apicula missing: {self.apicula}")
        key = hashlib.sha256(payload).hexdigest()[:20]
        out = self.cache / "sprites" / key
        out.mkdir(parents=True, exist_ok=True)
        ready = sorted(out.glob("*.png"))
        if ready:
            return ready, meta
        source = out / "actor.btx"
        source.write_bytes(payload)
        async with self._lock:
            result = await asyncio.to_thread(
                subprocess.run,
                [str(self.apicula), "extract", str(source), "-o", str(out / "extract")],
                cwd=self.cache,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        ready = sorted((out / "extract").rglob("*.png"))
        if result.returncode != 0 or not ready:
            raise ActorAssetError(
                "original BTX0 is available, but the installed Apicula build did not emit PNG textures; "
                "the viewer will use a clearly-labelled pixel marker fallback"
            )
        return ready, meta

    def cache_status(self) -> dict[str, Any]:
        return {
            "cache": str(self.cache),
            "apicula": str(self.apicula),
            "apicula_available": self.apicula.is_file(),
            "sources": {"registry": MMODEL_INDEX_PATH, "resources": MMODEL_RES_PATH},
        }
