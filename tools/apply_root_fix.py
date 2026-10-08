# 1. Update frontend/v2.html
with open("frontend/v2.html", "r", encoding="utf-8") as f:
    html = f.read()

inline_head_code = """  <link rel="stylesheet" href="/frontend/v2.css?v=20261006_04">
  <script>
    // 【根基保障】极简零依赖的 Tab 切换引擎，内联解析，保证任何网络或外部脚本问题下 100% 永远可点击！
    function switchTab(paneId) {
      try {
        document.querySelectorAll('.ds-rail-btn').forEach(function(b) {
          b.classList.remove('active');
        });
        document.querySelectorAll('.ds-pane').forEach(function(p) {
          p.classList.remove('active');
        });
        var targetBtn = Array.from(document.querySelectorAll('.ds-rail-btn')).find(function(b) {
          return (b.getAttribute('onclick') || '').indexOf(paneId) !== -1;
        });
        if (targetBtn) targetBtn.classList.add('active');
        var targetPane = document.getElementById(paneId);
        if (targetPane) targetPane.classList.add('active');
        var hashKey = paneId.replace('pane-', '');
        if (window.location.hash !== '#' + hashKey) {
          history.replaceState(null, '', '#' + hashKey);
        }
        if (typeof onTabSwitched === 'function') {
          onTabSwitched(paneId);
        }
      } catch (err) {
        console.error('switchTab error:', err);
      }
    }

    // 全局未捕获异常透明可视横幅，杜绝静默假死
    window.onerror = function(msg, url, line) {
      if (document.body) {
        var errBox = document.getElementById('globalJsErrorBanner');
        if (!errBox) {
          errBox = document.createElement('div');
          errBox.id = 'globalJsErrorBanner';
          errBox.style.cssText = 'position:fixed;top:0;left:0;right:0;background:#dc2626;color:#fff;padding:8px 16px;font-size:12px;font-family:monospace;z-index:999999;display:flex;justify-content:space-between;align-items:center;box-shadow:0 2px 10px rgba(0,0,0,0.5);';
          document.body.appendChild(errBox);
        }
        errBox.innerHTML = '<span>⚠️ 脚本执行警告: ' + msg + ' (第 ' + line + ' 行)</span><button onclick="this.parentElement.remove()" style="background:transparent;border:1px solid #fff;color:#fff;border-radius:3px;padding:2px 8px;cursor:pointer;">关闭</button>';
      }
    };
  </script>"""

old_head = '  <link rel="stylesheet" href="/frontend/v2.css?v=20261006_04">'
if "【根基保障】" not in html:
    assert old_head in html
    html = html.replace(old_head, inline_head_code)
    html = html.replace("v2.js?v=20261008_06", "v2.js?v=20261008_rootfix")
    with open("frontend/v2.html", "w", encoding="utf-8") as f:
        f.write(html)
    print("1. Added inline bulletproof switchTab to frontend/v2.html head!")
else:
    print("1. Inline switchTab already present.")

# 2. Update frontend/v2.js switchTab
with open("frontend/v2.js", "r", encoding="utf-8") as f:
    js = f.read()

old_switch_tab = """function switchTab(paneId) {
  document.querySelectorAll('.ds-rail-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.ds-pane').forEach(p => p.classList.remove('active'));
  
  const targetBtn = Array.from(document.querySelectorAll('.ds-rail-btn')).find(b => b.getAttribute('onclick')?.includes(paneId));
  if (targetBtn) targetBtn.classList.add('active');
  
  const targetPane = document.getElementById(paneId);
  if (targetPane) targetPane.classList.add('active');

  // 同步 URL hash
  const hashKey = paneId.replace('pane-', '');
  if (window.location.hash !== "#" + hashKey) {
    history.replaceState(null, '', '#' + hashKey);
  }

  // 如果离开 3D 页面，自动挂起休眠释放 GPU
  if (paneId !== 'pane-3d' && is3DActive) {
    pause3DView();
  }
  // 切换到空间地图时，立即重绘或拉取最新雷达切片
  if (paneId === 'pane-radar') {
    if (activeSlicesData) {
      renderMultiLayerGrid(activeSlicesData);
    }
    pollRadar();
  }
}"""

new_switch_tab = """function onTabSwitched(paneId) {
  // 离开 3D 页面自动挂起释放 GPU
  if (paneId !== 'pane-3d' && typeof is3DActive !== 'undefined' && is3DActive) {
    if (typeof pause3DView === 'function') pause3DView();
  }
  // 切换到空间地图时，立即重绘最新切片并轮询
  if (paneId === 'pane-radar') {
    if (typeof activeSlicesData !== 'undefined' && activeSlicesData && typeof renderMultiLayerGrid === 'function') {
      renderMultiLayerGrid(activeSlicesData);
    }
    if (typeof pollRadar === 'function') pollRadar();
  }
}

function switchTab(paneId) {
  try {
    document.querySelectorAll('.ds-rail-btn').forEach(b => b.classList.remove('active'));
    document.querySelectorAll('.ds-pane').forEach(p => p.classList.remove('active'));
    
    const targetBtn = Array.from(document.querySelectorAll('.ds-rail-btn')).find(b => b.getAttribute('onclick')?.includes(paneId));
    if (targetBtn) targetBtn.classList.add('active');
    
    const targetPane = document.getElementById(paneId);
    if (targetPane) targetPane.classList.add('active');

    const hashKey = paneId.replace('pane-', '');
    if (window.location.hash !== "#" + hashKey) {
      history.replaceState(null, '', '#' + hashKey);
    }

    onTabSwitched(paneId);
  } catch (e) {
    console.error('switchTab error:', e);
  }
}"""

if "function onTabSwitched" not in js:
    assert old_switch_tab in js
    js = js.replace(old_switch_tab, new_switch_tab)
    with open("frontend/v2.js", "w", encoding="utf-8") as f:
        f.write(js)
    print("2. Updated switchTab and added onTabSwitched in frontend/v2.js!")
else:
    print("2. onTabSwitched already present.")

# 3. Add no-cache middleware in backend/black2/api/app.py
with open("backend/black2/api/app.py", "r", encoding="utf-8") as f:
    app_text = f.read()

middleware_code = """@app.middleware("http")
async def add_no_cache_header(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/frontend/") or request.url.path in ("/v2", "/v1", "/workbench"):
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

"""

if "add_no_cache_header" not in app_text:
    target_pos = 'if os.path.exists(FRONTEND_DIR):'
    assert target_pos in app_text
    app_text = app_text.replace(target_pos, middleware_code + target_pos)
    with open("backend/black2/api/app.py", "w", encoding="utf-8") as f:
        f.write(app_text)
    print("3. Added add_no_cache_header middleware in app.py!")
else:
    print("3. add_no_cache_header middleware already present.")