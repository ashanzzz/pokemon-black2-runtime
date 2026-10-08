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
        [{ x: 14, z: 46, y: 0, symbol: 'P', is_player: true, walkable: true, kind: '主角所在位置' }],
        [{ x: 12, z: 44, y: 0, symbol: '.', is_player: false, walkable: true, kind: '平坦道路' }]
      ]
    },
    {
      floor_y: 2,
      grid: [
        [{ x: 14, z: 46, y: 2, symbol: '.', is_player: false, walkable: true, kind: '高台路面' }],
        [{ x: 12, z: 44, y: 2, symbol: '╫', is_player: false, walkable: true, kind: '独木桥/窄桥' }]
      ]
    }
  ]
};

// Add simulated multi-layer path: step 1 on Y=0, step 2 (goal) on Y=2 at (12, 44)
activePlannedPathMap.set('14,46,0', { step: 1, total: 2, isGoal: false, y: 0, isRealFloor: true });
activePlannedPathMap.set('14,46', { step: 1, total: 2, isGoal: false, y: 0, isProjection: true });

activePlannedPathMap.set('12,44,2', { step: 2, total: 2, isGoal: true, y: 2, isRealFloor: true });
activePlannedPathMap.set('12,44', { step: 2, total: 2, isGoal: true, y: 2, isProjection: true });
activePlannedMovementMode = 'bike';

renderMultiLayerGrid(sampleData);
const container = getOrCreateEl('mapSliceContainer');
const html = container.innerHTML;

const s0Idx = html.indexOf('id="slice-floor-0"');
const s2Idx = html.indexOf('id="slice-floor-2"');
const s0Html = html.slice(s0Idx, s2Idx);
const s2Html = html.slice(s2Idx);

console.log('[TEST 1] Y=0 has projected goal (12, 44):', s0Html.includes('cell-path-projected'));
console.log('[TEST 2] Y=2 has solid goal (12, 44):', s2Html.includes('cell-path-goal'));

if (s0Html.includes('cell-path-projected') && s2Html.includes('cell-path-goal')) {
  console.log('SUCCESS: Dual-layer physical path + projected glowing outline verified!');
} else {
  console.error('FAILED verification!');
}
process.exit(0);
