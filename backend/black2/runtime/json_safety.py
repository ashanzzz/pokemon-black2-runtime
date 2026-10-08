"""JSON boundary helpers for hostile/unknown game text.

Gen V text buffers are UTF-16-like code units and an unverified decoder can
occasionally surface a lone surrogate (for example ``U+DFED``).  Python can
hold that value in memory, but Starlette/Pydantic correctly refuses to emit it
as UTF-8 JSON, turning an otherwise healthy runtime endpoint into HTTP 500.
Keep the raw value available inside the reverse-engineering process while
sanitising only the public JSON snapshot boundary.
"""
from __future__ import annotations

from typing import Any


def sanitize_json(value: Any) -> Any:
    """Return a JSON-safe copy, replacing lone UTF-16 surrogate code points.

    The helper intentionally preserves mapping keys, numbers and booleans and
    recursively handles lists/tuples.  Unknown custom objects are returned as
    is; public endpoints should already expose plain model dictionaries.
    """
    if isinstance(value, str):
        if not any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            return value
        return "".join("\uFFFD" if 0xD800 <= ord(char) <= 0xDFFF else char for char in value)
    if isinstance(value, dict):
        return {sanitize_json(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_json(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_json(item) for item in value]
    return value

