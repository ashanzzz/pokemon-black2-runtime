import sys, os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from backend.black2.world.runtime_player_state import player_runtime_service
from backend.black2.api.app import memory_reader

async def run():
    res = await player_runtime_service.sample(memory_reader)
    print("Sample result:", res.get("status"), res.get("reason"))

asyncio.run(run())
