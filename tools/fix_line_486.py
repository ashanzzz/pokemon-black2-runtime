with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace(
    "const hasPathOnFloor = (isGroundSlice && activePlannedPathMap.size > 0);",
    "const hasPathOnFloor = (realSteps.length > 0 || projSteps.length > 0);"
)

with open("frontend/v2.js", "w", encoding="utf-8") as f:
    f.write(text)

print("Fixed line 486 with (realSteps.length > 0 || projSteps.length > 0)")
