with open("backend/black2/api/battle_routes.py", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

old_code = """    identity = await _battle_identity(evidence)
    battle_kind = (identity.get("battle_kind") or {}).get("value")
    if battle_kind == "trainer":
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-flee/v1",
            "ok": False,
            "status": "rejected",
            "message": "Cannot flee from a trainer battle (面对训练家无法逃跑！)",
        })"""

new_code = """    identity = await _battle_identity(evidence)
    battle_kind = (identity.get("battle_kind") or {}).get("value")
    has_trainer_id = bool(
        identity.get("trainer_id") is not None
        or ((identity.get("trainer") or {}).get("trainer_id") is not None)
    )
    if battle_kind == "trainer" and has_trainer_id:
        return JSONResponse(status_code=409, content={
            "format": "black2-battle-flee/v1",
            "ok": False,
            "status": "rejected",
            "message": "Cannot flee from a trainer battle (面对训练家无法逃跑！)",
        })"""

if old_code in text:
    text = text.replace(old_code, new_code)
    with open("backend/black2/api/battle_routes.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Updated battle_routes.py flee_wild_battle successfully!")
else:
    print("old_code not found in battle_routes.py")

with open("backend/black2/battle/battle_action_service.py", "r", encoding="utf-8") as f:
    text2 = f.read().replace("\r\n", "\n")

old_run = """        if snapshot.get("battle_kind") == "trainer":
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {"code": "CANNOT_RUN_FROM_TRAINER", "message": "Cannot flee from trainer battle."},
                "request_id": request_id,
            }"""

new_run = """        has_trainer_id = bool(
            snapshot.get("trainer_id") is not None
            or ((snapshot.get("trainer") or {}).get("trainer_id") is not None)
        )
        if snapshot.get("battle_kind") == "trainer" and has_trainer_id:
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {"code": "CANNOT_RUN_FROM_TRAINER", "message": "Cannot flee from trainer battle."},
                "request_id": request_id,
            }"""

if old_run in text2:
    text2 = text2.replace(old_run, new_run)
    with open("backend/black2/battle/battle_action_service.py", "w", encoding="utf-8") as f:
        f.write(text2)
    print("Updated battle_action_service.py _execute_run successfully!")
else:
    print("old_run not found in battle_action_service.py")