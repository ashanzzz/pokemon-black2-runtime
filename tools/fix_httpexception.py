with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace(
    "from fastapi import APIRouter, Query, Request",
    "from fastapi import APIRouter, Query, Request, HTTPException"
)
with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Added HTTPException import to navigation_routes.py!")