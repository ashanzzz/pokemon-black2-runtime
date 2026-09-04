(function () {
  'use strict';
  const DEFAULT_INTERVAL = 350;
  const REQUEST_TIMEOUT = 1800;
  const listeners = new Set();
  let timer = null, running = false, inFlight = false;
  let last = { health: null, snapshot: null, httpOnline: true, error: null, updatedAt: 0 };

  async function jsonFetch(path, options = {}, timeout = REQUEST_TIMEOUT) {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), timeout);
    try {
      const res = await fetch(path, { ...options, signal: ctrl.signal, cache: 'no-store' });
      const text = await res.text();
      let body = null;
      try { body = text ? JSON.parse(text) : null; } catch (_) { body = { raw: text }; }
      if (!res.ok) {
        const err = new Error(`${res.status} ${res.statusText}`);
        err.status = res.status; err.body = body; throw err;
      }
      return body;
    } finally { clearTimeout(t); }
  }

  async function poll() {
    if (inFlight) return last;
    inFlight = true;
    try {
      // Health is authoritative for transport. Snapshot failure never means offline.
      const health = await jsonFetch('/api/v1/runtime/health', {}, 1000);
      let snapshot = last.snapshot;
      let snapshotError = null;
      try { snapshot = await jsonFetch('/api/v1/runtime/snapshot', {}, 1500); }
      catch (e) { snapshotError = String(e.message || e); }
      last = { health, snapshot, httpOnline: true, error: snapshotError, updatedAt: Date.now() };
    } catch (e) {
      last = { ...last, httpOnline: false, error: String(e.message || e), updatedAt: Date.now() };
    } finally {
      inFlight = false;
      listeners.forEach(fn => { try { fn(last); } catch (e) { console.error(e); } });
    }
    return last;
  }

  function start(interval = DEFAULT_INTERVAL) {
    if (running) return;
    running = true; poll(); timer = setInterval(poll, Math.max(150, interval));
  }
  function stop(){ running=false; if(timer) clearInterval(timer); timer=null; }
  function subscribe(fn){ listeners.add(fn); if(last.updatedAt) fn(last); return () => listeners.delete(fn); }
  function state(){ return last; }
  function safe(v, fallback='--'){ return v === null || v === undefined || v === '' ? fallback : v; }
  function esc(v){ return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c])); }
  function pretty(v){ return JSON.stringify(v, null, 2); }

  function transportClass(health, httpOnline){
    if(!httpOnline) return 'bad';
    if(health && health.bridge_connected) return 'good';
    return 'warn';
  }
  function runtimeClass(status){ return status === 'ready' ? 'good' : status === 'degraded' ? 'warn' : 'candidate'; }
  function updateShell(model){
    const h=model.health||{};
    const http=document.querySelector('[data-status="http"]');
    const bridge=document.querySelector('[data-status="bridge"]');
    const semantic=document.querySelector('[data-status="semantic"]');
    const frame=document.querySelector('[data-status="frame"]');
    if(http){ http.className=`status-pill ${model.httpOnline?'good':'bad'}`; http.querySelector('.value').textContent=model.httpOnline?'HTTP online':'HTTP offline'; }
    if(bridge){ bridge.className=`status-pill ${transportClass(h,model.httpOnline)}`; bridge.querySelector('.value').textContent=h.bridge_connected?'Bridge connected':'Bridge waiting'; }
    if(semantic){ const st=h.semantic_status||'unresolved'; semantic.className=`status-pill ${runtimeClass(st)}`; semantic.querySelector('.value').textContent=`Semantic ${st}`; }
    if(frame) frame.textContent=`F ${safe(h.frame,0)}`;
  }
  function mountShell(active='runtime'){
    const host=document.querySelector('[data-runtime-shell]'); if(!host) return;
    const nav=[
      ['runtime','/','Runtime'],['player','/frontend/player-state.html','Player'],['dialogue','/frontend/dialogue-inspector.html','Dialogue'],
      ['world','/frontend/map-runtime.html','World'],['trainer','/frontend/trainer-state.html','Trainer'],['tools','/frontend/controller.html','Tools']
    ];
    host.innerHTML=`<header class="topbar"><a class="brand" href="/"><div class="brand-mark">B2</div><div><div class="brand-title">Pokémon Black 2 Runtime</div><div class="brand-sub">RAM-grounded observer · IREJ</div></div></a><nav class="nav">${nav.map(([id,href,label])=>`<a href="${href}" class="${id===active?'active':''}">${label}</a>`).join('')}</nav><div class="status-strip"><span class="status-pill" data-status="http"><span class="dot"></span><span class="value">HTTP ...</span></span><span class="status-pill" data-status="bridge"><span class="dot"></span><span class="value">Bridge ...</span></span><span class="status-pill" data-status="semantic"><span class="dot"></span><span class="value">Semantic ...</span></span><span class="status-pill"><span data-status="frame">F 0</span></span></div></header>`;
    subscribe(updateShell); start();
  }

  window.Black2Runtime = { jsonFetch, poll, start, stop, subscribe, state, safe, esc, pretty, mountShell };
})();
