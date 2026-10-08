with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

old_mount = """                    if mid == 19:
                        fly_mount = {
                            "slot": slot.get("slot"),
                            "species_name": slot.get("species_name_zh") or slot.get("species_name"),
                            "species_id": slot.get("species"),
                            "level": slot.get("level"),
                            "move_id": 19,
                            "move_name": m.get("name_zh") or m.get("name") or "飞翔",
                        }"""

new_mount = """                    if mid == 19:
                        s_name = slot.get("species_name_zh") or slot.get("species_name")
                        if not s_name:
                            try:
                                from ..dex.store import DexStore
                                dex = DexStore()
                                pk_info = dex.get("pokemon", slot.get("species", 0)) or {}
                                s_name = (pk_info.get("names") or {}).get("zh-Hans") or pk_info.get("name")
                            except Exception:
                                pass
                        fly_mount = {
                            "slot": slot.get("slot"),
                            "species_name": s_name or f"Pokemon #{slot.get('species')}",
                            "species_id": slot.get("species"),
                            "level": slot.get("level"),
                            "move_id": 19,
                            "move_name": m.get("name_zh") or m.get("name") or "飞翔",
                        }"""

assert old_mount in text
text = text.replace(old_mount, new_mount)
with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated fly_mount species_name enrichment in navigation_routes.py!")