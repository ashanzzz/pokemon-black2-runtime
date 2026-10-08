with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

old_pk = """                        fly_pokemon = {
                            "slot": slot.get("slot"),
                            "species_name": slot.get("species_name_zh") or slot.get("species_name"),
                            "level": slot.get("level"),
                        }"""

new_pk = """                        s_name = slot.get("species_name_zh") or slot.get("species_name")
                        if not s_name:
                            try:
                                from ..dex.store import DexStore
                                dex = DexStore()
                                pk_info = dex.get("pokemon", slot.get("species", 0)) or {}
                                s_name = (pk_info.get("names") or {}).get("zh-Hans") or pk_info.get("name")
                            except Exception:
                                pass
                        fly_pokemon = {
                            "slot": slot.get("slot"),
                            "species_name": s_name or f"Pokemon #{slot.get('species')}",
                            "level": slot.get("level"),
                        }"""

assert old_pk in text
text = text.replace(old_pk, new_pk)
with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated fly_pokemon name enrichment!")