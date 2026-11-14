"""Reference interrupted-task reconciliation; never replays external effects."""
from __future__ import annotations

import json

from ..store import now


def reconcile_restart(store):
    for old in store.list("jobs"):
        if old["status"] not in {"queued", "running"}: continue
        old.update(status="interrupted", error="控制面进程重启；节点任务及容器需重新探测，未自动重复执行")
        store.put("jobs", old)
        service = store.get("services", old.get("service_id", ""))
        if service:
            service.update(status="error", last_error=old["error"], updated_at=now())
            if old.get("host_actions_attempted"):
                service["resources_may_exist"] = True
                previous = service.get("deployed_placements", [])
                service["deployed_placements"] = list({json.dumps(p, sort_keys=True): p for p in [*previous, *service["placements"]]}.values())
            store.put("services", service)
            store.audit("任务中断", service["name"], "interrupted", "保留可能存在的资源归属；不会自动重复部署或停止")
