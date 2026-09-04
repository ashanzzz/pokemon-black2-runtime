#!/usr/bin/env python3
"""Pokémon Black 2 Semantic Runtime v4 - canonical launcher."""
from __future__ import annotations

import argparse
import os
import uvicorn

from backend.black2.bizhawk.process_probe import probe_bizhawk_process


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def print_banner() -> None:
    print("=" * 72)
    print("   Pokémon Black 2 - AI Semantic Runtime v4.0.0")
    print("   HTTP UI/API and BizHawk TCP bridge have separate canonical roles")
    print("=" * 72)


def check_status(http_host: str, http_port: int, bridge_host: str, bridge_port: int) -> None:
    proc = probe_bizhawk_process()
    print("\n[Layer 0 · EmuHawk Process]")
    if proc.running:
        print(f"  [PASS] EmuHawk running · PID={proc.pid}")
        print(f"         {proc.exe_path}")
    else:
        print("  [WARN] EmuHawk process not detected. Start BizHawk first.")

    lua_path = os.path.abspath("bridge/bizhawk/black2_bridge.lua")
    print("\n[Layer 1 · BizHawk TCP Bridge]")
    print(f"  TCP listener: {bridge_host}:{bridge_port}")
    print("  BizHawk → Tools → Lua Console → Open script:")
    print(f"  {lua_path}")

    print("\n[Layer 2 · Browser / FastAPI]")
    print(f"  UI:  http://{http_host}:{http_port}/")
    print(f"  API: http://{http_host}:{http_port}/docs")
    print("  Browser pages use same-origin relative API paths; no page owns a port.\n")


def start_server(http_host: str, http_port: int, bridge_host: str, bridge_port: int) -> None:
    # app.py imports runtime_config only after uvicorn resolves the app string;
    # populate the canonical environment first so its public schema matches the
    # actual listener even when the user overrides CLI ports.
    os.environ["BLACK2_HTTP_HOST"] = http_host
    os.environ["BLACK2_HTTP_PORT"] = str(http_port)
    os.environ["BLACK2_BRIDGE_HOST"] = bridge_host
    os.environ["BLACK2_BRIDGE_PORT"] = str(bridge_port)
    print_banner()
    check_status(http_host, http_port, bridge_host, bridge_port)
    print(f"Starting FastAPI on http://{http_host}:{http_port} ...\n")
    uvicorn.run(
        "backend.black2.api.app:app",
        host=http_host,
        port=http_port,
        log_level="info",
        reload=False,
    )


if __name__ == "__main__":
    default_http_host = os.getenv("BLACK2_HTTP_HOST", "127.0.0.1")
    default_http_port = _env_int("BLACK2_HTTP_PORT", 8765)
    default_bridge_host = os.getenv("BLACK2_BRIDGE_HOST", "127.0.0.1")
    default_bridge_port = _env_int("BLACK2_BRIDGE_PORT", 8766)

    parser = argparse.ArgumentParser(description="Pokémon Black 2 Semantic Runtime Launcher")
    parser.add_argument("--host", default=default_http_host, help="HTTP server host")
    parser.add_argument("--port", type=int, default=default_http_port, help="HTTP server port")
    parser.add_argument("--bridge-host", default=default_bridge_host, help="BizHawk TCP bridge host")
    parser.add_argument("--bridge-port", type=int, default=default_bridge_port, help="BizHawk TCP bridge port")
    parser.add_argument("--check", action="store_true", help="Only show process and port roles")
    args = parser.parse_args()

    if args.check:
        print_banner()
        check_status(args.host, args.port, args.bridge_host, args.bridge_port)
    else:
        start_server(args.host, args.port, args.bridge_host, args.bridge_port)
