with open("tests/test_p0_architecture.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace('patch.object(app_mod.client, "touch_screen"', 'patch.object(app_mod.client, "touch"')
with open("tests/test_p0_architecture.py", "w", encoding="utf-8") as f:
    f.write(text)
print("Updated test_p0_architecture.py to mock client.touch!")