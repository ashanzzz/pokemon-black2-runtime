with open("backend/black2/world/navigation_tasks.py", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Add expected to _settle_after_segment_clear signature
old_sig = """    async def _settle_after_segment_clear(
        self,
        record: dict[str, Any],
        *,
        allowed_nodes: list[NavNode],
        allow_moving_pipeline: bool = False,
    ) -> tuple[NavNode | None, dict[str, Any], dict[str, Any] | None, bool]:"""

new_sig = """    async def _settle_after_segment_clear(
        self,
        record: dict[str, Any],
        *,
        allowed_nodes: list[NavNode],
        allow_moving_pipeline: bool = False,
        expected: NavNode | None = None,
    ) -> tuple[NavNode | None, dict[str, Any], dict[str, Any] | None, bool]:"""

text = text.replace(old_sig, new_sig)

# 2. Pass expected=expected in _wait_for_landing
old_call = """            settled_node, settled_player, settle_error, settled = await self._settle_after_segment_clear(
                record, allowed_nodes=allowed, allow_moving_pipeline=continue_pipeline,
            )"""

new_call = """            settled_node, settled_player, settle_error, settled = await self._settle_after_segment_clear(
                record, allowed_nodes=allowed, allow_moving_pipeline=continue_pipeline, expected=expected,
            )"""

text = text.replace(old_call, new_call)

with open("backend/black2/world/navigation_tasks.py", "w", encoding="utf-8") as f:
    f.write(text)

print("navigation_tasks.py patched: expected parameter added to _settle_after_segment_clear!")
