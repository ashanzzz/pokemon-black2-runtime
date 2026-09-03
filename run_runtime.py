#!/usr/bin/env python3
"""Pokémon Black 2 Semantic Runtime - CLI & Server Launcher."""

import argparse
import asyncio
import os
import sys
import uvicorn

from backend.black2.bizhawk.process_probe import probe_bizhawk_process


def print_banner():
    print("=" * 65)
    print("   Pokémon Black 2 - AI Semantic Runtime v2.0.0")
    print("   BizHawk Greenfield Architecture Engine")
    print("=" * 65)


def check_status():
    proc = probe_bizhawk_process()
    print("\n[Layer 0: Process Probe]")
    if proc.running:
        print(f"  [PASS] EmuHawk is running!")
        print(f"         PID:  {proc.pid}")
        print(f"         Path: {proc.exe_path}")
    else:
        print("  [WARN] EmuHawk process not detected. Please start BizHawk.")

    print("\n[Layer 1: Bridge Instructions]")
    lua_path = os.path.abspath("bridge/bizhawk/black2_bridge.lua")
    print("  To attach your currently open BizHawk to this runtime:")
    print("  1. In BizHawk menu, click: Tools -> Lua Console")
    print(f"  2. Click 'Open script' and select:")
    print(f"     {lua_path}")
    print("  3. The bridge will instantly connect to this server at http://127.0.0.1:8765\n")


def start_server(host: str = "127.0.0.1", port: int = 8765):
    print_banner()
    check_status()
    print(f"Starting Semantic Runtime API server on http://{host}:{port} ...\n")
    uvicorn.run("backend.black2.api.app:app", host=host, port=port, log_level="info", reload=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pokémon Black 2 Semantic Runtime Launcher")
    parser.add_argument("--host", default="127.0.0.1", help="Server host")
    parser.add_argument("--port", type=int, default=8765, help="Server port")
    parser.add_argument("--check", action="store_true", help="Only check process status")
    args = parser.parse_args()

    if args.check:
        print_banner()
        check_status()
    else:
        start_server(args.host, args.port)
