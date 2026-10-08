with open("frontend/v2.js", "r", encoding="utf-8") as f:
    text = f.read()

print("pollRadar def:", "async function pollRadar()" in text)
print("renderMultiLayerGrid def:", "function renderMultiLayerGrid(data)" in text)
print("handleCellClick def:", "function handleCellClick(el)" in text)
print("handlePlanNav def:", "async function handlePlanNav()" in text)
