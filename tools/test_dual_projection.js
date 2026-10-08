const sampleNodes = [
  { x: 14, z: 46, y: 0 },
  { x: 13, z: 46, y: 0 },
  { x: 12, z: 46, y: 1 },
  { x: 11, z: 46, y: 1 },
  { x: 10, z: 46, y: 2 },
  { x: 10, z: 45, y: 2 },
  { x: 10, z: 44, y: 2 },
  { x: 11, z: 44, y: 2 },
  { x: 12, z: 44, y: 2 },
];

const pathMap = new Map();
sampleNodes.forEach((n, idx) => {
  const isGoal = (idx === sampleNodes.length - 1);
  pathMap.set(`${n.x},${n.z},${n.y}`, { step: idx + 1, total: sampleNodes.length, isGoal, y: n.y });
  pathMap.set(`${n.x},${n.z}`, { step: idx + 1, total: sampleNodes.length, isGoal, y: n.y });
});

// Test cell at (12, 44) on Slice Y=0 vs Slice Y=2
function checkCell(x, z, fy) {
  const cellKey = `${x},${z},${fy}`;
  const exact = pathMap.get(cellKey);
  const proj = pathMap.get(`${x},${z}`);
  if (exact) {
    return { type: 'solid', step: exact.step, isGoal: exact.isGoal };
  } else if (proj) {
    return { type: 'projected', step: proj.step, isGoal: proj.isGoal };
  }
  return null;
}

console.log('On Y=0, step at (12, 44):', checkCell(12, 44, 0)); // Expect: projected (step 9, isGoal: true)
console.log('On Y=2, step at (12, 44):', checkCell(12, 44, 2)); // Expect: solid (step 9, isGoal: true)
console.log('On Y=0, step at (13, 46):', checkCell(13, 46, 0)); // Expect: solid (step 2)
console.log('On Y=2, step at (13, 46):', checkCell(13, 46, 2)); // Expect: projected (step 2)
