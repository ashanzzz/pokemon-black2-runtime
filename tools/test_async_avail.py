import sys, os, asyncio
sys.path.insert(0, os.path.abspath("."))
from backend.black2.api.navigation_routes import navigation_fast_travel_available, navigation_fast_travel_destinations

async def run():
    r1 = await navigation_fast_travel_destinations(status="flyable", format="flat")
    print("Direct call length:", len(r1))
    r2 = await navigation_fast_travel_available()
    print("Available call length:", len(r2))

asyncio.run(run())