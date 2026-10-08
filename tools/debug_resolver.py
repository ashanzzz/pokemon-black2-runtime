import sys, os
sys.path.insert(0, os.path.abspath("."))
import asyncio
from backend.black2.world.runtime_field_resolver import runtime_field_resolver
from backend.black2.api.app import memory_reader

async def run():
    res = await runtime_field_resolver.resolve(memory_reader, force_rediscover=True)
    print("Resolve result:", res)

asyncio.run(run())
