const fs = require("fs");
const code = fs.readFileSync("frontend/v2.js", "utf8");

const globalDom = {
  document: {
    getElementById: (id) => ({ id, innerText: "", innerHTML: "", style: {}, classList: { add: () => {}, remove: () => {} }, querySelectorAll: () => [], dataset: {} }),
    querySelectorAll: () => [],
    addEventListener: () => {},
    readyState: "complete"
  },
  window: {
    location: { hash: "#radar" },
    addEventListener: () => {},
    history: { replaceState: () => {} }
  },
  fetch: (url, opts) => {
    if (url.includes("/api/v1/navigation/plans")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          status: "ready",
          movement: { selected: "bike" },
          cost: { steps: 2 },
          route_detail: {
            nodes: [
              { x: 157, z: 649, y: 2 },
              { x: 157, z: 648, y: 2 },
              { x: 157, z: 647, y: 2 }
            ]
          }
        })
      });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  },
  setInterval: () => {},
  setTimeout: () => {}
};

const vm = require("vm");
const ctx = vm.createContext(globalDom);
vm.runInContext(code + "; globalThis.runTest = async () => { await handlePlanNav(); return { size: activePlannedPathMap.size, mode: activePlannedMovementMode, node1: activePlannedPathMap.get(\x27157,648,2\x27), goal: activePlannedPathMap.get(\x27157,647,2\x27) }; }", ctx);

ctx.runTest().then(res => console.log("Test result:", JSON.stringify(res, null, 2)));