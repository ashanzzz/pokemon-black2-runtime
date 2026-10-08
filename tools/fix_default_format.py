with open("backend/black2/api/navigation_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

old_code = 'format: str = Query("detailed", description="\'detailed\' (returns envelope with flight status), \'flat\' (array)"),'
new_code = 'format: str = Query("flat", description="\'flat\' (returns list of destinations), \'detailed\' (returns envelope with flight status)"),'

assert old_code in text
text = text.replace(old_code, new_code)
with open("backend/black2/api/navigation_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated navigation_fast_travel_destinations default format to 'flat'!")