"""Canonical local port/config roles for the Black 2 runtime."""
from __future__ import annotations

from dataclasses import dataclass
import os


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RuntimeConfig:
    http_host: str = os.getenv("BLACK2_HTTP_HOST", "127.0.0.1")
    http_port: int = _env_int("BLACK2_HTTP_PORT", 8765)
    bridge_host: str = os.getenv("BLACK2_BRIDGE_HOST", "127.0.0.1")
    bridge_port: int = _env_int("BLACK2_BRIDGE_PORT", 8766)
    semantic_sample_interval: float = float(os.getenv("BLACK2_SAMPLE_INTERVAL", "0.20"))

    def public_schema(self) -> dict:
        return {
            "http": {
                "host": self.http_host,
                "port": self.http_port,
                "role": "Web UI + FastAPI only",
            },
            "bridge": {
                "host": self.bridge_host,
                "port": self.bridge_port,
                "role": "BizHawk Lua TCP bridge only",
            },
            "policy": "Browser pages use same-origin relative URLs; no frontend hard-coded port.",
        }


runtime_config = RuntimeConfig()
