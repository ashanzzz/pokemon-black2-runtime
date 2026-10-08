with open("backend/black2/api/battle_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

import re
old_pattern = re.compile(r"@router\.get\(\"/request\"\)\s+async def battle_request\(\) -> dict\[str, Any\]:.*?return \{.*?\"evidence\": evidence,\s+\}", re.DOTALL)
m = old_pattern.search(text)
assert m is not None

new_func = """@router.get("/request")
async def battle_request() -> dict[str, Any]:
    \"\"\"Authoritative battle decision request interface for autonomous AI agents.\"\"\"
    evidence = await _evidence()
    active = bool(evidence.get("active"))
    identity = await _battle_identity(evidence)

    if not active:
        return {
            **_read_only_contract(),
            "format": "black2-battle-request/v1",
            "status": "not_in_battle",
            "active": False,
            "battle_id": None,
            "request_id": None,
            "phase": "none",
            "waiting_for_player": False,
            "battle_kind": None,
            "battle_format": None,
            "turn": None,
            "player_actor": None,
            "opponent_actor": None,
            "cursor": None,
            "legal_actions": [],
            "message": "Game is currently not in battle.",
        }

    frame = int(evidence.get("frame") or 0)
    battle_id = f"battle_{frame}"
    battle_kind = (identity.get("battle_kind") or {}).get("value") or "wild"

    ui_samp = await _ui_sample()
    cursor_data = await _ui_cursor_sample(ui_samp)
    raw_phase = cursor_data.get("phase")
    phase_str = "command_selection" if raw_phase == "command_menu" else ("move_selection" if raw_phase == "move_menu" else "action_processing")
    waiting_for_player = raw_phase in ("command_menu", "move_menu")

    player_act = identity.get("player", {}).get("active") or {}
    opp_act = identity.get("opponent", {}).get("active") or {}

    legal_actions = []
    # Moves
    for m in player_act.get("moves", []):
        cpp = m.get("current_pp", 0)
        usable = bool(cpp is not None and cpp > 0)
        legal_actions.append({
            "type": "use_move",
            "move_slot": m.get("slot"),
            "move_id": m.get("move_id"),
            "move_name": m.get("name"),
            "type": m.get("type"),
            "power": m.get("power"),
            "current_pp": cpp,
            "max_pp": m.get("max_pp"),
            "legal": usable,
            "reason": None if usable else "PP is exhausted",
        })

    # Switch
    try:
        party_data = await _party_decoder.sample()
        for s in (party_data or {}).get("slots", []):
            slot_num = s.get("slot")
            if slot_num != player_act.get("party_slot"):
                hp = s.get("current_hp", 0)
                can_switch = hp > 0
                legal_actions.append({
                    "type": "switch",
                    "party_slot": slot_num,
                    "species_id": s.get("species"),
                    "species_name": s.get("species_name_zh") or s.get("species_name"),
                    "level": s.get("level"),
                    "hp": hp,
                    "legal": can_switch,
                    "reason": None if can_switch else "Pokemon has fainted",
                })
    except Exception:
        pass

    if battle_kind == "wild":
        legal_actions.append({"type": "throw_ball", "legal": True})
        legal_actions.append({"type": "run", "legal": True})
    else:
        legal_actions.append({"type": "throw_ball", "legal": False, "reason": "Cannot catch trainer's Pokemon"})
        legal_actions.append({"type": "run", "legal": False, "reason": "Cannot flee from trainer battle"})

    return {
        **_read_only_contract(),
        "format": "black2-battle-request/v1",
        "status": "ready" if waiting_for_player else "waiting_settle",
        "active": True,
        "battle_id": battle_id,
        "request_id": frame,
        "phase": phase_str,
        "turn": 1,
        "waiting_for_player": waiting_for_player,
        "battle_kind": battle_kind,
        "battle_format": "single",
        "player_actor": player_act,
        "opponent_actor": opp_act,
        "cursor": cursor_data,
        "legal_actions": legal_actions,
        "identity": identity,
        "actors": [{
            "actor": "player:0",
            "pokemon": player_act,
            "legal_actions": legal_actions,
            "legal_actions_known": True,
        }],
    }"""

text = text[:m.start()] + new_func + text[m.end():]

# Alias capture-context
if "@router.get(\"/capture-context\")" not in text:
    old_cap = '@router.get("/capture-eval")'
    new_cap = '@router.get("/capture-context")\n@router.get("/capture-eval")'
    text = text.replace(old_cap, new_cap, 1)

with open("backend/black2/api/battle_routes.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated battle_request and capture-context in battle_routes.py!")