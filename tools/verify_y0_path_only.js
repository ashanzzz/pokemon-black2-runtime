const fs = require('fs');

let js = fs.readFileSync('frontend/v2.js', 'utf8');

// Replace top-level `let` with `global.` for testing
js = js.replace(/let activePlannedPathMap/g, 'global.activePlannedPathMap');
js = js.replace(/let activePlannedMovementMode/g, 'global.activePlannedMovementMode');
js = js.replace(/let activeCellDataMap/g, 'global.activeCellDataMap');
js = js.replace(/let currentSelectedCell/g, 'global.currentSelectedCell');
js = js.replace(/let activeSlicesData/g, 'global.activeSlicesData');
js = js.replace(/let sliceFilterMode/g, 'global.sliceFilterMode');
js = js.replace(/let livePlayerGrid/g, 'global.livePlayerGrid');

global.window = {
  location: { hash: '#radar' },
  addEventListener: () => {}
};
global.history = { replaceState: () => {} };

const elements = {};
function getOrCreateEl(id) {
  if (!elements[id]) {
    elements[id] = {
      id,
      innerText: '',
      innerHTML: '',
      style: {},
      className: '',
      dataset: {},
      classList: {
        add: () => {},
        remove: () => {},
        contains: () => false
      },
      querySelectorAll: () => [],
      getAttribute: () => null,
      scrollIntoView: () => {}
    };
  }
  return elements[id];
}

global.document = {
  getElementById: (id) => getOrCreateEl(id),
  querySelectorAll: () => [],
  addEventListener: () => {},
  readyState: 'complete'
};

eval(js);

const sampleData = {
  format: "black2-radar-slices/v1",
  zone_id: 457,
  zone_name: "Virbank Complex",
  player_floor_y: 0,
  total_active_layers: 2,
  active_layers: [0, 2],
  slices: [
    {
      floor_y: 0,
      grid: [
        [{ x: 13, z: 48, y: 0, symbol: 'P', is_player: true, walkable: true, kind: '主角所在位置' }],
        [{ x: 13, z: 49, y: 0, symbol: '.', is_player: false, walkable: true, kind: '平坦道路' }]
      ]
    },
    {
      floor_y: 2,
      grid: [
        [{ x: 13, z: 48, y: 2, symbol: '╫', is_player: false, walkable: true, kind: '独木桥/窄桥' }],
        [{ x: 13, z: 49, y: 2, symbol: '.', is_player: false, walkable: true, kind: '高台路面' }]
      ]
    }
  ]
};

// Test renderMultiLayerGrid
renderMultiLayerGrid(sampleData);
const container = getOrCreateEl('mapSliceContainer');
console.log('[TEST 1] Grid rendered, HTML length:', container.innerHTML.length);

// Test auto inspection
const pKey = '13,48,0';
const pCell = sampleData.slices[0].grid[0][0];
renderTileInspection(pKey, 13, 0, 48, 'P', true, '主角所在位置');
console.log('[TEST 2] inspectTileCoord:', getOrCreateEl('inspectTileCoord').innerText);
console.log('[TEST 3] matrixWalk:', getOrCreateEl('matrixWalk').innerText);
console.log('[TEST 4] matrixBike:', getOrCreateEl('matrixBike').innerText);

// Test simulated path
activePlannedPathMap.set('13,49,0', { step: 1, total: 1, isGoal: true });
activePlannedMovementMode = 'run';
renderMultiLayerGrid(sampleData);

// Check if slice-floor-0 has path and slice-floor-2 does NOT have path
const html = container.innerHTML;
const s0Idx = html.indexOf('id="slice-floor-0"');
const s2Idx = html.indexOf('id="slice-floor-2"');
const s0Html = html.slice(s0Idx, s2Idx);
const s2Html = html.slice(s2Idx);

const s0HasPath = s0Html.includes('cell-path-');
const s2HasPath = s2Html.includes('cell-path-');
console.log('[TEST 5] Y=0 has path indication:', s0HasPath);
console.log('[TEST 6] Y=2 has path indication (must be false):', s2HasPath);

if (s0HasPath && !s2HasPath) {
  console.log('ALL TESTS PASSED PERFECTLY!');
} else {
  console.error('TEST FAILED!');
}
process.exit(0);
