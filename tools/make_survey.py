with open("tools/test_live_swap_and_battle.py", "r", encoding="utf-8") as f:
    lines = f.read().splitlines()

prepend = [
    "# Swap slot 1 with slot 4 (Cobalion 638)",
    "post_json(\"http://127.0.0.1:8765/api/v1/game/party/swap\", {\"slot_a\": 1, \"slot_b\": 4})",
    "time.sleep(0.5)",
]

lines = prepend + lines
for i, l in enumerate(lines):
    if "530" in l:
        lines[i] = l.replace("530", "638").replace("Excadrill", "Cobalion")

with open("tools/run_full_autonomous_encounter_survey.py", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
print("Created tools/run_full_autonomous_encounter_survey.py successfully!")