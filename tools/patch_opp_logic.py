with open("backend/black2/decoders/battle_identity.py", "r", encoding="utf-8") as f:
    text = f.read()

old_logic = """        player_groups = [group for group in groups if group.get("species_id") in player_ids]
        opponent_groups = [group for group in groups if group.get("species_id") not in player_ids]
        if len(player_groups) == 1 and len(opponent_groups) >= 1:
            player_active = self._project_group(player_groups[0])
            opponent_party = [self._project_group(group) for group in opponent_groups]
            opponent_active = opponent_party[0] if opponent_party else None
            side_status = "candidate"
        elif player_lead_species is not None and len(groups) >= 2:
            # When opponent species is already present in player's party (e.g. duplicate species)
            player_lead_group = next((g for g in groups if g.get("species_id") == player_lead_species), None)
            other_groups = [g for g in groups if g is not player_lead_group]
            if player_lead_group is not None and other_groups:
                player_active = self._project_group(player_lead_group)
                opponent_party = [self._project_group(g) for g in other_groups]
                opponent_active = opponent_party[0] if opponent_party else None
                side_status = "candidate"
            else:
                player_active = None
                opponent_party = []
                opponent_active = None
                side_status = "unresolved"
        else:
            player_active = None
            opponent_party = []
            opponent_active = None
            side_status = "unresolved" """

new_logic = """        player_groups = [group for group in groups if group.get("species_id") in player_ids]
        opponent_groups = [group for group in groups if group.get("species_id") not in player_ids]

        if opponent_groups:
            # Clean separation: species not in player's party are opponents
            opponent_party = [self._project_group(g) for g in opponent_groups]
            opponent_active = opponent_party[0]
            player_active_group = next((g for g in player_groups if g.get("species_id") == player_lead_species), None) or (player_groups[0] if player_groups else None)
            player_active = self._project_group(player_active_group) if player_active_group else None
            side_status = "candidate"
        elif player_lead_species is not None and len(groups) >= 2:
            # Fallback when opponent species happens to be in player's party
            player_lead_group = next((g for g in groups if g.get("species_id") == player_lead_species), None)
            other_groups = [g for g in groups if g is not player_lead_group]
            if player_lead_group is not None and other_groups:
                player_active = self._project_group(player_lead_group)
                opponent_party = [self._project_group(g) for g in other_groups]
                opponent_active = opponent_party[0] if opponent_party else None
                side_status = "candidate"
            else:
                player_active = None
                opponent_party = []
                opponent_active = None
                side_status = "unresolved"
        else:
            player_active = None
            opponent_party = []
            opponent_active = None
            side_status = "unresolved" """

# Normalize whitespace and replace
import re
pattern = re.compile(r"player_groups = \[group for group in groups if group\.get\(\"species_id\"\) in player_ids\].*?side_status = \"unresolved\"", re.DOTALL)
match = pattern.search(text)
assert match is not None
text = text[:match.start()] + new_logic.strip() + text[match.end():]

with open("backend/black2/decoders/battle_identity.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated opponent identification logic in battle_identity.py!")