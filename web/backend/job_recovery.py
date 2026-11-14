"""Bounded, read-only resource reconciliation for interrupted execution jobs."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import re
import threading
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request

from .fleet import FleetError
from .observability import redact
from .store import now


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


def _project_state(result: dict, runtime: str, expected_node_id: str | None = None) -> dict:
    """Keep resource identities and state; never include arbitrary Agent output."""
    if not isinstance(result, dict) or not isinstance(result.get("items"), list):
        raise FleetError("Agent resource status is malformed")
    if len(result["items"]) > 256:
        raise FleetError("Agent resource status exceeds the response limit")
    allowed = ({"id", "name", "role", "node_id", "status", "health", "revision"}
               if runtime == "docker" else {"name", "uid", "phase", "ready", "role"})
    items = []
    for item in result["items"]:
        if not isinstance(item, dict):
            raise FleetError("Agent resource status contains invalid items")
        projected = {k: v for k, v in item.items() if k in allowed}
        required = ("id", "name", "node_id", "status") if runtime == "docker" else ("name", "uid")
        if any(not isinstance(projected.get(k), str) or not projected[k] for k in required):
            raise FleetError("Agent resource status omits a required identity")
        if runtime == "docker" and expected_node_id is not None and projected["node_id"] != expected_node_id:
            raise FleetError("Agent resource status belongs to another node")
        if any(not isinstance(v, (str, bool, type(None))) or isinstance(v, str) and len(v) > 1024
               for v in projected.values()):
            raise FleetError("Agent resource status contains invalid fields")
        items.append(projected)
    projection = {"items": items, "functional_acceptance": "not_tested"}
    if runtime == "kubernetes":
        resources = result.get("resources")
        if not isinstance(resources, list) or len(resources) > 512:
            raise FleetError("Agent resource identity list is malformed")
        projected_resources = []
        for resource in resources:
            if not isinstance(resource, dict):
                raise FleetError("Agent resource identity is malformed")
            value = {k: resource[k] for k in ("kind", "name", "namespace", "uid") if k in resource}
            if (any(not isinstance(value.get(k), str) or not value[k] for k in ("kind", "name", "uid"))
                    or any(not isinstance(v, str) or len(v) > 1024 for v in value.values())):
                raise FleetError("Agent resource identity fields are malformed")
            projected_resources.append(value)
        status, namespace = result.get("status", "unknown"), result.get("namespace", "")
        if (not isinstance(status, str) or len(status) > 100
                or not isinstance(namespace, str) or len(namespace) > 253):
            raise FleetError("Agent resource status or namespace is malformed")
        projection.update(resources=projected_resources, status=status, namespace=namespace,
                          pod_readiness=result.get("ready") is True)
    return projection


def create_recovery_router(store, fleet, mutation_lock, *, mode: str) -> APIRouter:
    router = APIRouter(prefix="/api/jobs", tags=["job-recovery"])
    capacity = threading.BoundedSemaphore(2)

    @router.post("/{identifier}/receipts")
    def receipts(identifier: str, request: Request, offset: int = Query(0, ge=0, le=511)):
        if set(request.query_params) - {"offset"} or request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers:
            raise HTTPException(422, "回执查询不接受请求体或执行参数")
        if mode == "demo":
            raise HTTPException(409, "演示环境不模拟 Agent 持久执行回执")
        if not capacity.acquire(blocking=False):
            raise HTTPException(429, "回执查询正在执行，请稍后重试", headers={"Retry-After": "5"})
        try:
            with mutation_lock:
                job = store.get("jobs", identifier)
                if job is None: raise HTTPException(404, "任务不存在")
                if job.get("execution_runtime") != "docker":
                    raise HTTPException(409, "此任务没有 Docker Agent 持久回执")
                targets = [step for step in [*job.get("cleanup_steps", []), *job.get("steps", [])] if step.get("operation_id")]
                if not targets or len(targets) > 512:
                    raise HTTPException(409, "此任务没有可核对的持久操作标识")
                count = len(targets)
                if offset >= count: raise HTTPException(422, "回执分页超出范围")
                targets = targets[offset:offset + 16]
                nodes = {step["node_id"]: store.get("nodes", step["node_id"]) for step in targets}

            def observe(step):
                operation_id = step["operation_id"]
                item = {"node_id": step["node_id"], "operation_id": operation_id, "operation_status": "unknown"}
                if not isinstance(operation_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", operation_id):
                    return {**item, "error": "已保存的操作标识无效"}
                node = nodes.get(step["node_id"])
                if not node: return {**item, "error": "节点接入记录已不存在"}
                try:
                    receipt = fleet.call(node, "GET", "/agent/operations/" + operation_id,
                                         timeout=10, max_response_bytes=256 * 1024)
                    state = receipt.get("operation_status") if isinstance(receipt, dict) else None
                    if (not isinstance(receipt, dict) or receipt.get("operation_id") != operation_id
                            or receipt.get("service_id") != job.get("service_id")
                            or receipt.get("node_id") != step["node_id"]
                            or receipt.get("action") != operation_id.rsplit(":", 1)[-1]
                            or state not in {"accepted", "running", "completed", "failed", "interrupted"}):
                        raise FleetError("Agent 返回的操作回执无效")
                    return {**item, "operation_status": state}
                except (FleetError, AttributeError) as error:
                    return {**item, "error": redact(str(error))[:2000]}

            with ThreadPoolExecutor(max_workers=min(3, len(targets)), thread_name_prefix="agent-receipt") as pool:
                observations = list(pool.map(observe, targets))
            report = {"observed_at": now(), "read_only": True, "items": observations,
                      "offset": offset, "target_count": count,
                      "next_offset": offset + len(targets) if offset + len(targets) < count else None,
                      "note": "仅核对原操作回执，不重放操作；节点完成不等于推理业务验收通过"}
            value = {**job, "receipt_observations": report}
            guards = [("nodes", key, node) for key, node in nodes.items() if node is not None]
            with mutation_lock:
                if not store.compare_and_mutate_audited("jobs", identifier, job, value,
                        action="Agent 回执查询", target=identifier, detail="只读核对原操作；任务结果与服务状态保留", guards=guards):
                    raise HTTPException(409, "任务或节点已变化，请重新查询回执")
            return report
        finally:
            capacity.release()

    @router.post("/{identifier}/reconcile")
    def reconcile(identifier: str, request: Request,
                  offset: int = Query(0, ge=0, le=511), limit: int = Query(16, ge=1, le=16)):
        if (set(request.query_params) - {"offset", "limit"}
                or request.headers.get("content-length", "0") != "0" or "transfer-encoding" in request.headers):
            raise HTTPException(422, "资源对账只接受 offset/limit，不接受请求体或执行参数")
        if mode == "demo":
            raise HTTPException(409, "演示环境不模拟节点资源对账")
        if not capacity.acquire(blocking=False):
            raise HTTPException(429, "资源对账正在执行，请稍后重试", headers={"Retry-After": "5"})
        try:
            with mutation_lock:
                job = store.get("jobs", identifier)
                if job is None:
                    raise HTTPException(404, "任务不存在")
                if job.get("status") not in {"interrupted", "failed"}:
                    raise HTTPException(409, "仅中断或失败任务需要资源对账")
                service = store.get("services", job.get("service_id", ""))
                if (not service or service.get("last_job_id") != identifier
                        or any(j.get("service_id") == service["id"] and j.get("status") in {"queued", "running"}
                               for j in store.list("jobs"))):
                    raise HTTPException(409, "服务已有后续任务或已删除，请查看当前服务资源")
                runtime = job.get("execution_runtime", service.get("runtime"))
                if runtime not in {"docker", "kubernetes"}:
                    raise HTTPException(409, "任务缺少可识别的执行运行时")
                raw_targets = [*(job.get("execution_placements") or job.get("steps", [])), *job.get("cleanup_steps", [])]
                targets = list(dict.fromkeys(p.get("node_id") for p in raw_targets if p.get("node_id")))
                if runtime == "kubernetes":
                    targets = targets[:1]
                if not targets or len(targets) > 512 or offset >= len(targets):
                    raise HTTPException(422, "没有可对账的执行节点或分页超出范围")
                page = targets[offset:offset + limit]
                nodes = {node_id: store.get("nodes", node_id) for node_id in page}
                fingerprint = _fingerprint(service)

            def observe(node_id):
                node = nodes[node_id]
                item = {"node_id": node_id, "node_name": node.get("name", "") if node else "",
                        "runtime": runtime, "observed_at": now()}
                if node is None:
                    return {**item, "result": "unknown", "error": "节点接入记录不存在"}
                try:
                    path = ("/agent/kubernetes/status?" + urlencode({"service_id": service["id"]})
                            if runtime == "kubernetes" else "/agent/status?" + urlencode({"service_id": service["id"], "node_id": node_id}))
                    state = _project_state(fleet.call(node, "GET", path, timeout=20,
                                                      max_response_bytes=2 * 1024 * 1024), runtime, node_id)
                    exists = bool(state["items"] or state.get("resources"))
                    return {**item, "result": "present" if exists else "absent", "state": state}
                except FleetError as error:
                    return {**item, "result": "unknown", "error": redact(str(error))[:2000]}

            with ThreadPoolExecutor(max_workers=min(4, len(page)), thread_name_prefix="motor-console-reconcile") as pool:
                observations = list(pool.map(observe, page))
            previous = job.get("reconciliation") or {}
            if offset and (previous.get("service_fingerprint") != fingerprint or previous.get("next_offset") != offset):
                raise HTTPException(409, "对账分页或服务状态已变化，请从第一页重新检查")
            items = [*previous.get("nodes", []), *observations] if offset else observations
            next_offset = offset + len(page) if offset + len(page) < len(targets) else None
            report = {"observed_at": now(), "read_only": True, "target_count": len(targets),
                      "checked_count": len(items), "next_offset": next_offset, "nodes": items,
                      "service_fingerprint": fingerprint, "functional_acceptance": "not_tested",
                      "note": "只核对当前受管资源；不会重放任务、改变服务状态或判定推理验收通过"}
            value = {**job, "reconciliation": report}
            guards = [("services", service["id"], service),
                      *[("nodes", node_id, node) for node_id, node in nodes.items() if node is not None]]
            with mutation_lock:
                if not store.compare_and_mutate_audited("jobs", identifier, job, value,
                        action="任务资源对账", target=service.get("name", service["id"]),
                        detail="只读检查受管资源；原任务状态保留", guards=guards,
                        metadata={"job_id": identifier, "checked_count": len(items),
                                  "target_count": len(targets), "unknown_count": sum(i["result"] == "unknown" for i in items)}):
                    raise HTTPException(409, "任务、节点或服务状态已变化，本次观测未保存，请重新检查")
            return report
        finally:
            capacity.release()

    return router
