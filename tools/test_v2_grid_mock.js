const fs = require('fs');

// Create mock DOM environment
const js = fs.readFileSync('frontend/v2.js', 'utf8');

// Mock browser objects
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

// Evaluate the script in our mock environment
eval(js);

console.log('Script evaluated successfully! Testing renderMultiLayerGrid...');

// Sample slice data matching the API response
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
        [{ x: 13, z: 48, y: 0, symbol: 'P', is_player: true, walkable: true, kind: 'Player' }],
        [{ x: 13, z: 49, y: 0, symbol: '.', is_player: false, walkable: true, kind: 'Road' }]
      ]
    },
    {
      floor_y: 2,
      grid: [
        [{ x: 13, z: 48, y: 2, symbol: '╫', is_player: false, walkable: true, kind: 'Catwalk' }],
        [{ x: 13, z: 49, y: 2, symbol: '.', is_player: false, walkable: true, kind: 'Air' }]
      ]
    }
  ]
};

try {
  renderMultiLayerGrid(sampleData);
  const container = getOrCreateEl('mapSliceContainer');
  console.log('Success! container.innerHTML length:', container.innerHTML.length);
  if (container.innerHTML.includes('slice-floor-0') && container.innerHTML.includes('slice-floor-2')) {
    console.log('Both slice-floor-0 and slice-floor-2 are present in innerHTML!');
  } else {
    console.error('Missing slices in innerHTML! Content snippet:', container.innerHTML.slice(0, 300));
  }
} catch (err) {
  console.error('ERROR in renderMultiLayerGrid:', err);
}
