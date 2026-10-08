import sys; sys.path.insert(0, '.')
import asyncio
from backend.black2.api.pc_routes import get_pc_summary, configure_pc_routes
from backend.black2.bizhawk.bridge_client import BridgeClient

async def main():
    client = BridgeClient()
    await client.connect()
    configure_pc_routes(client)
    try:
        res = await get_pc_summary()
        print('Result:', res)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
