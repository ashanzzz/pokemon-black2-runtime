import sys

# Test importing progression_state_service
from backend.black2.progression.state import progression_state_service
prog = progression_state_service.latest or {}
b_count = int(prog.get("badges", {}).get("count", 0) or 0)
print("Current badges count:", b_count)