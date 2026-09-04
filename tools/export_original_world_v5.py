#!/usr/bin/env python3
"""Export all static original-world map descriptors once from the ROM.

This tool never connects to BizHawk and never reads RAM.  It is intended for
one-time/precomputed map metadata.  3D GLB conversion stays lazy/on-demand so a
full export does not spend hours converting unused assets.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.black2.world.original_world import OriginalWorldService  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rom", default=os.getenv("BLACK2_ROM_PATH"))
    ap.add_argument("--out", type=Path, default=PROJECT_ROOT / "runtime" / "original_world_v5")
    ap.add_argument("--zone", type=int, action="append", help="export only selected ZoneID; repeatable")
    args = ap.parse_args()
    if not args.rom:
        ap.error("--rom or BLACK2_ROM_PATH is required")

    service = OriginalWorldService(args.rom)
    args.out.mkdir(parents=True, exist_ok=True)
    zone_ids = args.zone if args.zone else list(range(service.rom.zone_count))
    summary = []
    for zone_id in zone_ids:
        try:
            world = service.zone(zone_id)
            (args.out / f"zone_{zone_id:04d}.json").write_text(
                json.dumps(world, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            summary.append({
                "zone_id": zone_id,
                "status": "ok",
                "matrix_id": world.get("matrix", {}).get("matrix_id"),
                "cell_count": len(world.get("cells", [])),
                "building_count": len(world.get("buildings", [])),
                "entities_id": world.get("zone", {}).get("entities_id"),
            })
        except Exception as exc:
            summary.append({"zone_id": zone_id, "status": "error", "reason": f"{type(exc).__name__}: {exc}"})
        print(f"[v5-static] zone {zone_id}: {summary[-1]['status']}")
    report = {
        "format": "black2-original-world-export/v5",
        "policy": "ROM only; no BizHawk/RAM access",
        "rom": service.rom.static_identity(),
        "zones": summary,
    }
    (args.out / "index.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[v5-static] wrote {args.out / 'index.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
