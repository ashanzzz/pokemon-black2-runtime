with open("backend/black2/decoders/party_runtime.py", "r", encoding="utf-8") as f:
    text = f.read()

# Remove misplaced helper at the top
old_top = """_shared_dex = None

def _get_dex():
    global _shared_dex
    if _shared_dex is None:
        try:
            from ..dex.store import DexStore
            _shared_dex = DexStore()
        except Exception:
            _shared_dex = None
    return _shared_dex

\"\"\"Checksum-gated Gen V player party decoder for Pokemon Black 2 IREJ rev.1."""

new_top = """\"\"\"Checksum-gated Gen V player party decoder for Pokemon Black 2 IREJ rev.1.\"\"\"
from __future__ import annotations"""

# Let's inspect the top of party_runtime.py
lines = text.splitlines()
# Find where from __future__ import annotations is
future_idx = next(i for i, l in enumerate(lines) if "from __future__ import annotations" in l)
helper_lines = [l for l in lines[:future_idx] if not l.startswith('"""') and not l.startswith("This") and not l.startswith("does") and not l.startswith("current")]

cleaned = "\n".join(lines[future_idx+1:])

helper_block = """_shared_dex = None

def _get_dex():
    global _shared_dex
    if _shared_dex is None:
        try:
            from ..dex.store import DexStore
            _shared_dex = DexStore()
        except Exception:
            _shared_dex = None
    return _shared_dex"""

final_text = '"""Checksum-gated Gen V player party decoder for Pokemon Black 2 IREJ rev.1."""\nfrom __future__ import annotations\n\n' + helper_block + "\n\n" + cleaned

with open("backend/black2/decoders/party_runtime.py", "w", encoding="utf-8") as f:
    f.write(final_text)
print("Fixed from __future__ placement in party_runtime.py!")