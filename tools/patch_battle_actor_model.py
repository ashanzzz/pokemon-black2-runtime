with open("backend/black2/decoders/battle_identity.py", "r", encoding="utf-8") as f:
    text = f.read()

old_project = """    def _project_group(self, group: dict[str, Any]) -> dict[str, Any]:
        return {
            "species_id": group.get("species_id"),
            "species": group.get("species"),
            "current_hp": group.get("current_hp"),
            "max_hp": group.get("max_hp"),
            "level": group.get("level"),
            "level_status": group.get("level_status", "unresolved"),
            "gender": group.get("gender"),
            "ability": group.get("ability"),
            "stats": group.get("stats"),
            "stat_stages": group.get("stat_stages"),
            "moves": group.get("moves", []),
            "level_or_exp_raw_values": group.get("level_or_exp_raw_values", []),
            "object_count": group.get("occurrences", 0),
            "objects": group.get("objects", []),
            "confidence": "candidate",
        }"""

new_project = """    def _project_group(
        self,
        group: dict[str, Any],
        actor_id: str = "player:0",
        side: str = "player",
        party_slot: int | None = None,
        party_slot_status: str = "unresolved",
    ) -> dict[str, Any]:
        cur_hp = group.get("current_hp") or 0
        max_hp = group.get("max_hp") or 1
        pct = round(cur_hp / max_hp, 3) if max_hp > 0 else 0.0
        obj_addr = group.get("objects", [{}])[0].get("payload_address")

        sp = group.get("species") or {}
        sp_name = sp.get("names", {}).get("zh-Hans") or sp.get("name") if isinstance(sp, dict) else None

        stats = group.get("stats") or {}
        stat_stages = group.get("stat_stages") or {}

        return {
            # Unified BattleActor schema (Design Doc Section 3)
            "actor_id": actor_id,
            "side": side,
            "position": "center",
            "battle_object_id": obj_addr,
            "party_slot": party_slot,
            "party_slot_status": party_slot_status,
            "species_id": group.get("species_id"),
            "species_name": sp_name,
            "level": group.get("level"),
            "hp": {
                "current": cur_hp,
                "max": max_hp,
                "percent": pct,
            },
            "status": {
                "major": None,
                "sleep_turns": None,
            },
            "ability": group.get("ability"),
            "held_item": {"id": None},
            "stats": stats,
            "stat_stages": stat_stages,
            "stages": stat_stages,
            "moves": group.get("moves", []),

            # Backwards-compatible legacy fields
            "species": group.get("species"),
            "current_hp": cur_hp,
            "max_hp": max_hp,
            "level_status": group.get("level_status", "unresolved"),
            "gender": group.get("gender"),
            "level_or_exp_raw_values": group.get("level_or_exp_raw_values", []),
            "object_count": group.get("occurrences", 0),
            "objects": group.get("objects", []),
            "confidence": "candidate",
        }"""

assert old_project in text
text = text.replace(old_project, new_project)

old_match = """        if opponent_groups:
            # Clean separation: species not in player's party are opponents
            opponent_party = [self._project_group(g) for g in opponent_groups]
            opponent_active = opponent_party[0]
            player_active_group = next((g for g in player_groups if g.get("species_id") == player_lead_species), None) or (player_groups[0] if player_groups else None)
            player_active = self._project_group(player_active_group) if player_active_group else None
            side_status = "candidate\""""

new_match = """        if opponent_groups:
            opponent_party = [self._project_group(g, actor_id=f"opponent:{i}", side="opponent", party_slot=None, party_slot_status="not_applicable") for i, g in enumerate(opponent_groups)]
            opponent_active = opponent_party[0]
            player_active_group = next((g for g in player_groups if g.get("species_id") == player_lead_species), None) or (player_groups[0] if player_groups else None)
            party_slot_matched = None
            party_slot_status = "unverified"
            if player_active_group and player_party and player_party.get("slots"):
                for s in player_party["slots"]:
                    if s.get("species") == player_active_group.get("species_id") and s.get("level") == player_active_group.get("level"):
                        party_slot_matched = s.get("slot")
                        party_slot_status = "verified"
                        break
            player_active = self._project_group(
                player_active_group,
                actor_id="player:0",
                side="player",
                party_slot=party_slot_matched or 1,
                party_slot_status=party_slot_status if party_slot_matched else "candidate",
            ) if player_active_group else None
            side_status = "candidate\""""

assert old_match in text
text = text.replace(old_match, new_match)

with open("backend/black2/decoders/battle_identity.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated battle_identity.py with unified BattleActor model!")