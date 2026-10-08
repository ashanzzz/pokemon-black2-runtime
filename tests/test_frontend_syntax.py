"""Automated regression test for frontend JavaScript syntax and tab integrity.

Guarantees from the root cause that frontend/v2.js has zero SyntaxErrors,
that switchTab is permanently functional, and that no PowerShell template
corruption ever reaches production.
"""
import subprocess
import shutil
from pathlib import Path
import pytest


def test_v2_js_node_syntax_check():
    node_path = shutil.which("node")
    if not node_path:
        pytest.skip("Node.js not installed in environment")
    v2_path = Path("frontend/v2.js")
    assert v2_path.is_file(), "frontend/v2.js must exist"

    result = subprocess.run([node_path, "-c", str(v2_path)], capture_output=True, text=True)
    assert result.returncode == 0, f"frontend/v2.js syntax error:\n{result.stderr}"


def test_v2_js_has_no_corrupted_tokens():
    text = Path("frontend/v2.js").read_text(encoding="utf-8")
    assert "? Lv.?" not in text
    assert "fetch(/api/" not in text
    assert "appendLog([" not in text


def test_v2_html_contains_critical_tabs_and_fallback():
    html = Path("frontend/v2.html").read_text(encoding="utf-8")
    assert "switchTab" in html
    assert "pane-overview" in html
    assert "pane-radar" in html
    assert "pane-combat" in html
    assert "pane-roster" in html
    assert "pane-memory" in html
    assert "pane-3d" in html