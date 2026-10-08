with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

# Replace the broken line:
broken_line = "const hasPathOnFloor = (pathNodesOnThisSlice > 0);"
fixed_line = "const hasPathOnFloor = (isGroundSlice && activePlannedPathMap.size > 0);"
if broken_line in text:
    text = text.replace(broken_line, fixed_line)
    with open("frontend/v2.js", "w", encoding="utf-8") as f:
        f.write(text)
    print("Fixed undeclared pathNodesOnThisSlice reference error!")
else:
    print("broken_line not found or already fixed")
