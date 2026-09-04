"""Prefer versioned exported v5 static world JSON when present.

The exported files are immutable derived ROM facts and can be loaded without
re-parsing map archives at runtime.  Missing zones fall back to
OriginalWorldService, preserving complete coverage.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any

from .original_world import OriginalWorldService


class ExportedWorldStore:
    def __init__(self, fallback: OriginalWorldService, project_root: str | Path | None = None) -> None:
        self.fallback = fallback
        self.root = Path(project_root) if project_root else Path(__file__).resolve().parents[3]
        self.zone_dir = self.root / "reverse_engineering" / "derived" / "v5" / "zones"

    @lru_cache(maxsize=512)
    def zone(self, zone_id: int) -> dict[str, Any]:
        path = self.zone_dir / f"zone_{int(zone_id):04d}.json"
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and int(data.get("zone_id", -1)) == int(zone_id):
                    data = dict(data)
                    data.setdefault("static_load", {})
                    data["static_load"] = {
                        "source": "exported_json",
                        "path": str(path.relative_to(self.root)),
                        "runtime_rom_reparse": False,
                    }
                    return data
            except (OSError, ValueError, TypeError):
                pass
        data = self.fallback.zone(int(zone_id))
        data = dict(data)
        data["static_load"] = {
            "source": "rom_fallback",
            "runtime_rom_reparse": True,
            "reason": "no valid exported zone JSON exists for this zone",
        }
        return data

    def availability(self, zone_id: int) -> dict[str, Any]:
        path = self.zone_dir / f"zone_{int(zone_id):04d}.json"
        return {"zone_id": int(zone_id), "exported": path.is_file(), "path": str(path)}
