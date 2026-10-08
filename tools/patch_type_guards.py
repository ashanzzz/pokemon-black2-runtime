with open("backend/black2/world/fast_travel.py", "r", encoding="utf-8") as f:
    text = f.read()

old_filters = """        if status:
            s_norm = status.lower().strip()
            if s_norm in ("flyable", "available", "unlocked"):
                rows = [r for r in rows if r.get("status") == "unlocked"]
            elif s_norm in ("locked", "blocked", "unavailable"):
                rows = [r for r in rows if r.get("status") == "locked"]

        if flyable is not None:
            rows = [r for r in rows if r.get("flyable") is flyable]

        if category:
            cat_norm = category.lower().rstrip("s")
            cat_alias = {
                "gym_citie": "gym_city", "gym_city": "gym_city", "gym": "gym_city",
                "citie": "major_city", "city": "major_city", "major_city": "major_city",
                "town": "town",
                "landmark": "landmark",
                "route": "route",
            }
            target_cat = cat_alias.get(cat_norm, cat_norm)
            if target_cat != "all":
                rows = [r for r in rows if r.get("category") == target_cat]

        if search:
            q = search.lower().strip()
            rows = [
                r for r in rows
                if q in str(r.get("zone_id"))
                or q in str(r.get("name_zh", "")).lower()
                or q in str(r.get("name_en", "")).lower()
            ]

        if has_pokemon_center is not None:
            rows = [r for r in rows if r.get("has_pokemon_center") is has_pokemon_center]

        if has_gym is not None:
            rows = [r for r in rows if r.get("has_gym") is has_gym]"""

new_filters = """        if status and isinstance(status, str):
            s_norm = status.lower().strip()
            if s_norm in ("flyable", "available", "unlocked"):
                rows = [r for r in rows if r.get("status") == "unlocked"]
            elif s_norm in ("locked", "blocked", "unavailable"):
                rows = [r for r in rows if r.get("status") == "locked"]

        if flyable is not None and isinstance(flyable, bool):
            rows = [r for r in rows if r.get("flyable") is flyable]

        if category and isinstance(category, str):
            cat_norm = category.lower().rstrip("s")
            cat_alias = {
                "gym_citie": "gym_city", "gym_city": "gym_city", "gym": "gym_city",
                "citie": "major_city", "city": "major_city", "major_city": "major_city",
                "town": "town",
                "landmark": "landmark",
                "route": "route",
            }
            target_cat = cat_alias.get(cat_norm, cat_norm)
            if target_cat != "all":
                rows = [r for r in rows if r.get("category") == target_cat]

        if search and isinstance(search, str):
            q = search.lower().strip()
            rows = [
                r for r in rows
                if q in str(r.get("zone_id"))
                or q in str(r.get("name_zh", "")).lower()
                or q in str(r.get("name_en", "")).lower()
            ]

        if has_pokemon_center is not None and isinstance(has_pokemon_center, bool):
            rows = [r for r in rows if r.get("has_pokemon_center") is has_pokemon_center]

        if has_gym is not None and isinstance(has_gym, bool):
            rows = [r for r in rows if r.get("has_gym") is has_gym]"""

assert old_filters in text
text = text.replace(old_filters, new_filters)
with open("backend/black2/world/fast_travel.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated fast_travel.py filter type guards!")