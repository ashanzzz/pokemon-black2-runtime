import os
import sys
import asyncio

# Ensure project root is in sys.path when launched from any directory
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backend.black2.mcp.server import main

if __name__ == "__main__":
    asyncio.run(main())
