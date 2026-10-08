from __future__ import annotations

import json

from backend.black2.runtime.json_safety import sanitize_json


def test_sanitize_json_replaces_lone_surrogates_without_touching_normal_text():
    value = {"text": "前\udfed后", "nested": ["\ud800", "ok"], "n": 3}
    cleaned = sanitize_json(value)
    assert cleaned == {"text": "前�后", "nested": ["�", "ok"], "n": 3}
    # This is the exact failure mode seen at the live /api/state boundary.
    json.dumps(cleaned, ensure_ascii=False).encode("utf-8")


def test_sanitize_json_preserves_valid_non_bmp_characters():
    assert sanitize_json("宝可梦😀") == "宝可梦😀"

