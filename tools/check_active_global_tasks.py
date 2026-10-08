import sys, os; sys.path.insert(0, os.path.abspath('.'))
import urllib.request, json
# Let's inspect active global tasks from python
from backend.black2.api.navigation_routes import _get_global_task_service
gts = _get_global_task_service()
for tid, t in gts._active_global_tasks.items():
    print(f"Task {tid}: status={t.get('status')}, step={t.get('current_step_index')}, error={t.get('error')}, micro={t.get('current_micro_task_id')}")
