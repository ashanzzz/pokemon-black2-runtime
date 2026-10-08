import sys, os; sys.path.insert(0, os.path.abspath('.'))
from backend.black2.api.navigation_routes import navigation_static_provider
p = navigation_static_provider()
print("x=194, z=650 (doorstep):", p.surface_at(448, 194, 650, 0))
print("x=193, z=650 (door):", p.surface_at(448, 193, 650, 0))
print("x=192, z=650 (behind door / gate wall):", p.surface_at(448, 192, 650, 0))
