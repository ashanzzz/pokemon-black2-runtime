with open("tests/test_battle_action_service.py", "r", encoding="utf-8") as f:
    text = f.read().replace("\r\n", "\n")

text = text.replace(
    "mock_sample = AsyncMock(side_effect=[sample_before, sample_after, sample_after, sample_after])",
    "mock_sample = AsyncMock(side_effect=[sample_before, sample_before, sample_after, sample_after, sample_after])"
)

text = text.replace(
    "mock_sample = AsyncMock(side_effect=[sample_active, sample_escaped, sample_escaped])",
    "mock_sample = AsyncMock(side_effect=[sample_active, sample_active, sample_escaped, sample_escaped])"
)

with open("tests/test_battle_action_service.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated test_battle_action_service.py side_effects successfully!")