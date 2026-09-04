#!/usr/bin/env python3
"""Capture v6 3D World Evidence Bundle.

Combines:
1. Native screen capture (PNG)
2. verify_v6_runtime.py execution log
3. /api/v1/map/v6/scene/current JSON
4. /api/v1/map/v6/player/live JSON
5. /api/v1/map/v6/player/asset/meta JSON
6. Auto-packs into a timestamped ZIP archive for easy sharing and statistics.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import zipfile

try:
    import requests
except ImportError:
    requests = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_BASE = PROJECT_ROOT / "reverse_engineering" / "v6_evidence"


def capture_desktop_screenshot(out_path: Path) -> bool:
    """Capture full desktop via PowerShell Windows.Forms GDI."""
    normalized_path = out_path.as_posix()
    ps_cmd = f"""
    Add-Type -AssemblyName System.Drawing
    Add-Type -AssemblyName System.Windows.Forms
    $screen = [System.Windows.Forms.Screen]::PrimaryScreen
    $bitmap = New-Object System.Drawing.Bitmap($screen.Bounds.Width, $screen.Bounds.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.CopyFromScreen($screen.Bounds.Location, [System.Drawing.Point]::Empty, $screen.Bounds.Size)
    $bitmap.Save('{normalized_path}', [System.Drawing.Imaging.ImageFormat]::Png)
    $graphics.Dispose()
    $bitmap.Dispose()
    """
    try:
        res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True, timeout=15)
        return out_path.is_file() and out_path.stat().st_size > 0
    except Exception as exc:
        print(f"[WARN] Desktop screenshot failed: {exc}", file=sys.stderr)
        return False


def fetch_json(url: str, timeout: int = 40) -> dict:
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc), "url": url}


def capture_v6_evidence(label: str = "general", base_url: str = "http://127.0.0.1:8765") -> dict:
    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_label = re.sub(r"[^\w\-]", "_", label.strip() or "general")
    folder_name = f"evidence_{timestamp}_{clean_label}"
    target_dir = OUTPUT_BASE / folder_name
    target_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== [v6 Evidence Capture] Label: '{label}' ===")
    print(f"Directory: {target_dir}")

    # 1. Desktop Screenshot
    screen_png = target_dir / "screen.png"
    print("1. Capturing desktop screenshot...")
    shot_ok = capture_desktop_screenshot(screen_png)
    print(f"   Screenshot: {'OK' if shot_ok else 'FAILED'} ({screen_png.name})")

    # 2. Run verify_v6_runtime.py
    print("2. Running tools/verify_v6_runtime.py...")
    verify_script = PROJECT_ROOT / "tools" / "verify_v6_runtime.py"
    verify_log = target_dir / "verify_output.txt"
    try:
        cmd = [sys.executable, str(verify_script), base_url]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=50)
        verify_content = res.stdout + (f"\nSTDERR:\n{res.stderr}" if res.stderr else "")
        verify_log.write_text(verify_content, encoding="utf-8")
        print("   Verification:\n" + "\n".join("     " + l for l in res.stdout.strip().splitlines()))
    except Exception as exc:
        verify_log.write_text(f"verify_v6_runtime error: {exc}\n", encoding="utf-8")
        print(f"   Verification error: {exc}")

    # 3. /api/v1/map/v6/scene/current
    print("3. Fetching /api/v1/map/v6/scene/current...")
    scene_json = fetch_json(f"{base_url}/api/v1/map/v6/scene/current")
    (target_dir / "scene_current.json").write_text(json.dumps(scene_json, ensure_ascii=False, indent=2), encoding="utf-8")
    scene_key = scene_json.get("scene_key", "unknown")
    env = scene_json.get("environment", "unknown")
    origin = scene_json.get("scene_origin", {})
    print(f"   Scene: key={scene_key}, env={env}, origin={origin}")

    # 4. /api/v1/map/v6/player/live
    print("4. Fetching /api/v1/map/v6/player/live...")
    player_json = fetch_json(f"{base_url}/api/v1/map/v6/player/live")
    (target_dir / "player_live.json").write_text(json.dumps(player_json, ensure_ascii=False, indent=2), encoding="utf-8")
    zone_id = player_json.get("zone_id")
    world_pos = player_json.get("world")
    facing = (player_json.get("orientation") or {}).get("facing")
    print(f"   Player: zone={zone_id}, world={world_pos}, facing={facing}")

    # 5. /api/v1/map/v6/player/asset/meta
    print("5. Fetching /api/v1/map/v6/player/asset/meta...")
    meta_json = fetch_json(f"{base_url}/api/v1/map/v6/player/asset/meta")
    (target_dir / "player_asset_meta.json").write_text(json.dumps(meta_json, ensure_ascii=False, indent=2), encoding="utf-8")
    asset_mode = meta_json.get("asset_mode", meta_json.get("status", "unknown"))
    print(f"   Player Asset Meta: mode={asset_mode}")

    # 6. Metadata manifest
    manifest = {
        "timestamp": timestamp,
        "label": label,
        "folder": folder_name,
        "scene_key": scene_key,
        "environment": env,
        "zone_id": zone_id,
        "player_world": world_pos,
        "player_facing": facing,
        "player_asset_mode": asset_mode,
        "screenshot_saved": shot_ok,
        "files": [p.name for p in target_dir.iterdir() if p.is_file()],
    }
    (target_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 7. Zip package
    zip_path = OUTPUT_BASE / f"{folder_name}.zip"
    print(f"6. Packing into {zip_path.name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in target_dir.iterdir():
            if f.is_file():
                z.write(f, arcname=f"{folder_name}/{f.name}")
    print(f"   Package created: {zip_path.stat().st_size / 1024:.1f} KB")

    return {
        "ok": True,
        "label": label,
        "folder": str(target_dir),
        "zip_path": str(zip_path),
        "zip_name": zip_path.name,
        "download_url": f"/api/v1/map/v6/evidence/download/{zip_path.name}",
        "manifest": manifest,
    }

    print("\n" + "=" * 50)
    print("[SUCCESS] EVIDENCE CAPTURE COMPLETE")
    print(f"Bundle ZIP: {zip_path}")
    print("=" * 50)

    return {
        "ok": True,
        "label": label,
        "folder": str(target_dir),
        "zip_path": str(zip_path),
        "zip_name": zip_path.name,
        "manifest": manifest,
    }


def main():
    ap = argparse.ArgumentParser(description="Capture v6 3D World Evidence Bundle")
    ap.add_argument("--label", default="evidence", help="Descriptive label (e.g., outdoor, indoor, enter_door)")
    ap.add_argument("--url", default="http://127.0.0.1:8765", help="Backend API base URL")
    args = ap.parse_args()
    res = capture_v6_evidence(label=args.label, base_url=args.url)
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
