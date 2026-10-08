with open("backend/black2/decoders/battle_identity.py", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

old_kind = """            battle_kind = {
                "status": "candidate",
                "value": "trainer",
                "confidence": 0.90 if has_dialogue else 0.78,"""

new_kind = """            battle_kind = {
                "status": "candidate",
                "value": "trainer" if has_dialogue else "wild",
                "confidence": 0.90 if has_dialogue else 0.78,"""

if old_kind in text:
    text = text.replace(old_kind, new_kind)
    with open("backend/black2/decoders/battle_identity.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Updated battle_identity.py battle_kind logic successfully!")
else:
    print("old_kind not found in battle_identity.py")

with open("backend/black2/battle/battle_action_service.py", "r", encoding="utf-8") as f:
    text2 = f.read().replace("\r\n", "\n")

old_run = """        has_trainer_id = bool(
            snapshot.get("trainer_id") is not None
            or ((snapshot.get("trainer") or {}).get("trainer_id") is not None)
        )
        if snapshot.get("battle_kind") == "trainer" and has_trainer_id:"""

new_run = """        if snapshot.get("battle_kind") == "trainer":"""

if old_run in text2:
    text2 = text2.replace(old_run, new_run)
    with open("backend/black2/battle/battle_action_service.py", "w", encoding="utf-8") as f:
        f.write(text2)
    print("Restored battle_action_service.py _execute_run trainer check!")