with open("backend/black2/battle/battle_action_service.py", "r", encoding="utf-8") as f:
    text = f.read()

target = "        before_pp = target_move.get(\"current_pp\", 0) if isinstance(target_move, dict) else 0"
guard = """        before_pp = target_move.get("current_pp", 0) if isinstance(target_move, dict) else 0
        if before_pp <= 0:
            move_name = target_move.get("name") if isinstance(target_move, dict) else f"Move #{move_slot}"
            max_pp_val = target_move.get("max_pp", 0) if isinstance(target_move, dict) else 0
            return {
                "format": "black2-battle-action-execution/v1",
                "status": "rejected",
                "executed": False,
                "reason": {
                    "code": "MOVE_PP_EXHAUSTED",
                    "message": f"Move slot {move_slot}「{move_name}」PP is exhausted (0/{max_pp_val}). Action aborted to prevent a wasted turn.",
                },
                "move_slot": move_slot,
                "current_pp": 0,
                "max_pp": max_pp_val,
                "request_id": request_id,
            }"""

if "MOVE_PP_EXHAUSTED" not in text:
    assert target in text
    text = text.replace(target, guard)
    with open("backend/black2/battle/battle_action_service.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Added MOVE_PP_EXHAUSTED guard to battle_action_service.py!")
else:
    print("MOVE_PP_EXHAUSTED already present.")