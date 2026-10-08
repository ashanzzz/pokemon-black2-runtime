# 1. Update agent_action_routes.py with /observation
with open("backend/black2/api/agent_action_routes.py", "r", encoding="utf-8") as f:
    text = f.read()

observation_code = """

@router.get("/observation")
async def get_agent_observation() -> dict[str, Any]:
    \"\"\"Unified single-point observation API for autonomous AI agents (Design Doc Section 9).\"\"\"
    import time
    from ..world.runtime_player_state import player_runtime_service
    from ..world.player_coordinates import canonical_grid_player
    from .battle_routes import _evidence, _battle_identity, _ui_sample, _ui_cursor_sample, _party_decoder, _inventory_decoder

    p_raw = player_runtime_service.latest or {}
    p_grid = canonical_grid_player(p_raw, require_resolved=False) or {}
    frame = p_raw.get("frame") or 0

    # Battle check
    b_ev = await _evidence()
    in_battle = bool(b_ev.get("active"))
    battle_obs = None
    if in_battle:
        b_ident = await _battle_identity(b_ev)
        ui_samp = await _ui_sample()
        cursor = await _ui_cursor_sample(ui_samp)
        raw_phase = cursor.get("phase")
        phase_str = "command_selection" if raw_phase == "command_menu" else ("move_selection" if raw_phase == "move_menu" else "action_processing")
        waiting_for_player = raw_phase in ("command_menu", "move_menu")
        player_act = b_ident.get("player", {}).get("active") or {}
        opp_act = b_ident.get("opponent", {}).get("active") or {}

        # Legal actions
        legal_acts = []
        for m in player_act.get("moves", []):
            cpp = m.get("current_pp", 0)
            legal_acts.append({
                "type": "use_move",
                "move_slot": m.get("slot"),
                "move_name": m.get("name"),
                "pp": cpp,
                "legal": bool(cpp is not None and cpp > 0),
            })
        if b_ident.get("battle_kind", {}).get("value") == "wild":
            legal_acts.append({"type": "throw_ball", "legal": True})
            legal_acts.append({"type": "run", "legal": True})
        else:
            legal_acts.append({"type": "throw_ball", "legal": False})
            legal_acts.append({"type": "run", "legal": False})

        battle_obs = {
            "active": True,
            "battle_id": f"battle_{frame}",
            "battle_kind": (b_ident.get("battle_kind") or {}).get("value") or "wild",
            "phase": phase_str,
            "waiting_for_player": waiting_for_player,
            "player_actor": player_act,
            "opponent_actor": opp_act,
            "cursor": cursor,
            "legal_actions": legal_acts,
        }

    # Party
    party_slots = []
    try:
        p_data = await _party_decoder.sample()
        for s in (p_data or {}).get("slots", []):
            party_slots.append({
                "slot": s.get("slot"),
                "species_id": s.get("species"),
                "species_name": s.get("species_name_zh") or s.get("species_name"),
                "level": s.get("level"),
                "hp": {"current": s.get("current_hp"), "max": s.get("max_hp")},
                "status": s.get("status_name", "HEALTHY"),
            })
    except Exception:
        pass

    # Inventory summary
    inv_summary = {"total_items": 0, "pokeballs": 0, "medicines": 0}
    try:
        inv_data = await _inventory_decoder.sample()
        items = (inv_data or {}).get("items", [])
        inv_summary["total_items"] = len(items)
        inv_summary["pokeballs"] = sum(item.get("quantity", 0) for item in items if "球" in str(item.get("name", "")))
        inv_summary["medicines"] = sum(item.get("quantity", 0) for item in items if item.get("pocket") == "medicine")
    except Exception:
        pass

    # Mode detection
    mode = "BATTLE" if in_battle else "OVERWORLD"

    # Compile available action verbs
    if in_battle:
        available_actions = ["battle:use_move", "battle:switch", "battle:throw_ball", "battle:run", "battle:item", "input:press", "input:touch"]
    else:
        available_actions = ["navigation:move_to", "interaction:interact", "player:fly", "player:bicycle", "game:party:swap", "input:press", "input:touch"]

    return {
        "format": "black2-agent-observation/v1",
        "status": "ready",
        "frame": frame,
        "timestamp_utc": time.time(),
        "mode": mode,
        "player": {
            "zone_id": p_grid.get("zone_id") or p_raw.get("zone_id"),
            "grid_position": p_grid.get("position", {}).get("grid") or p_raw.get("position", {}).get("grid"),
            "facing": (p_raw.get("orientation") or {}).get("facing", "South"),
            "locomotion_phase": (p_raw.get("locomotion") or {}).get("phase", "Idle"),
            "transport_mode": (p_raw.get("locomotion") or {}).get("transport_mode", "OnFoot"),
        },
        "ui": {
            "screen_type": mode,
            "phase": battle_obs.get("phase") if battle_obs else "field_idle",
            "waiting_for_input": battle_obs.get("waiting_for_player") if battle_obs else True,
            "can_move_player": not in_battle,
        },
        "dialogue": {
            "is_active": False,
            "text": None,
            "waiting_for_a": False,
            "choices": [],
        },
        "battle": battle_obs,
        "party_summary": party_slots,
        "inventory_summary": inv_summary,
        "available_actions": available_actions,
    }
"""

if "@router.get(\"/observation\")" not in text:
    text += observation_code
    with open("backend/black2/api/agent_action_routes.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Added /api/v1/agent/observation to agent_action_routes.py!")
else:
    print("/observation already in agent_action_routes.py")

# 2. Update app.py with /api/v1/input/* and /api/v1/ui/state
with open("backend/black2/api/app.py", "r", encoding="utf-8") as f:
    app_text = f.read()

input_code = """
class GenericInputPressRequest(BaseModel):
    button: str = Field(..., description="Button name (A, B, X, Y, Up, Down, Left, Right, Start, Select, L, R)")
    frames: int = Field(4, ge=1, le=120, description="Hold frames")

class GenericInputTouchRequest(BaseModel):
    x: int = Field(..., ge=0, le=255, description="Touch screen X coordinate (0..255)")
    y: int = Field(..., ge=0, le=191, description="Touch screen Y coordinate (0..191)")
    frames: int = Field(6, ge=1, le=120, description="Hold frames")

@app.post("/api/v1/input/press")
async def post_v1_input_press(req: GenericInputPressRequest):
    \"\"\"Standard low-level button press actuator for external AI fallback.\"\"\"
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    return await client.press_buttons([req.button], frames=req.frames)

@app.post("/api/v1/input/hold")
async def post_v1_input_hold(req: GenericInputPressRequest):
    \"\"\"Standard low-level button hold actuator for external AI fallback.\"\"\"
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    return await client.press_buttons([req.button], frames=max(req.frames, 16))

@app.post("/api/v1/input/touch")
async def post_v1_input_touch(req: GenericInputTouchRequest):
    \"\"\"Standard low-level touch screen actuator for external AI fallback.\"\"\"
    if not client.is_connected:
        raise HTTPException(status_code=503, detail="BizHawk bridge is not connected")
    return await client.touch_screen(req.x, req.y, frames=req.frames)

@app.get("/api/v1/ui/state")
async def get_v1_ui_state():
    \"\"\"Standard UI screen state and waiting status query (Design Doc Section 11).\"\"\"
    from .battle_routes import _evidence, _ui_sample, _ui_cursor_sample
    ev = await _evidence()
    in_battle = bool(ev.get("active"))
    if in_battle:
        ui_samp = await _ui_sample()
        cursor = await _ui_cursor_sample(ui_samp)
        raw_phase = cursor.get("phase")
        return {
            "format": "black2-ui-state/v1",
            "screen": "BATTLE",
            "phase": raw_phase or "command_menu",
            "waiting_for_input": raw_phase in ("command_menu", "move_menu"),
            "can_move_player": False,
            "cursor": cursor,
        }
    return {
        "format": "black2-ui-state/v1",
        "screen": "OVERWORLD",
        "phase": "field_idle",
        "waiting_for_input": True,
        "can_move_player": True,
        "cursor": None,
    }
"""

if "@app.post(\"/api/v1/input/press\")" not in app_text:
    target_pos = 'configure_prepared_action_routes(prepared_action_service)'
    assert target_pos in app_text
    app_text = app_text.replace(target_pos, input_code + "\n" + target_pos)
    with open("backend/black2/api/app.py", "w", encoding="utf-8") as f:
        f.write(app_text)
    print("Added /api/v1/input/* and /api/v1/ui/state to app.py!")
else:
    print("/api/v1/input/* already present in app.py")