"""Selected reference HTTP modules; business calls go through public ports."""
from __future__ import annotations
import time
from dataclasses import replace
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from ..deployment_view import configuration_digest
from ..fleet import FleetError, endpoint, motor_request, observations, ready_value
from ..inference_results import inference_output
from ..integrations import discover_models, project_models, MODEL_RESPONSE_BYTES
from ..log_records import fair_tail, role_matches, source_key, validate_log_role
from ..node_configuration import execution_node
from ..observability import redact
from ..ports.services import ServiceCommandV1
from ..service_metrics import metrics_revision
from ..store import now
from ..version import BASELINE_PROFILE_ID
from .services_http import append_router
from .services_models import ServiceInput, InferenceInput, DeploymentApproval, ScaleInput, UndeployInput


def project_service(value: dict, role: str | None, *, nodes: dict | None = None):
    lookup = nodes or {}
    identifiers = list(dict.fromkeys(p["node_id"] for p in value.get("placements", []) if p.get("node_id")))
    value = {**value, "config_revision": value.get("config_revision", 1),
             "node_names": [lookup.get(identifier, {}).get("name") or identifier for identifier in identifiers]}
    # Public addresses are projections, not trusted persisted observations.
    # Existing Kubernetes records may contain AgentIP:PodPort from an older
    # release; recompute against the immutable binding instead of echoing it.
    try: value["endpoint"] = endpoint(value, lookup, deployed=bool(value.get("resources_may_exist")))
    except (FleetError, ValueError, KeyError, TypeError): value["endpoint"] = ""
    if role != "viewer": return value
    return {**value, "user_config": {}, "env": {}, "deployed_user_config": {}, "deployed_env": {},
            "extra_manifests": [], "config_redacted": True,
            "last_error": redact(value.get("last_error", ""))}


def factory(ctx):
    inputs = ctx.require("api_inputs")
    workflow_inputs = inputs.workflow_inputs
    selected_mode, upstream, mutation_lock = workflow_inputs.mode, workflow_inputs.upstream, workflow_inputs.mutation_lock
    secured, role_for_request = list(inputs.secured), inputs.role_for_request
    workflow = ctx.consume("services.workflow", workflow_inputs=workflow_inputs) if ctx.contract != "api.tasks" else None
    from ..provider_host.defaults import AuditedStore
    store = AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink"))
    fleet = ctx.consume("agent.connection")
    # These are optional additive package contributions, not prebuilt defaults.
    # The selected factory owns all reference business routes below.
    router = APIRouter(routes=list(ctx.require("reference_routes")))

    def node_map():
        return {value["id"]: execution_node(value) for value in store.list("nodes")}

    def node_or_404(identifier):
        value = store.get("nodes", identifier)
        if value is None: raise HTTPException(404, "节点不存在")
        return value

    def service_view(value, request, *, nodes=None):
        return project_service(value, role_for_request(request), nodes=node_map() if nodes is None else nodes)

    if workflow is not None:
        service_or_404, reconcile_stopping = workflow.get, workflow.reconcile
        execution_placements, plan_service = workflow.execution_placements, workflow.plan
        plan_scale, require_scalable = workflow.plan_scale, workflow.require_scalable

    if ctx.contract == 'api.services':
        coordinator = ctx.consume("tasks.coordinator", coordinator_inputs=replace(inputs.coordinator_inputs, execution_enabled=True))
        def submit_job(identifier, action, approval=None, scale=None, undeploy=None, request=None):
            return coordinator.submit(identifier, action, ServiceCommandV1(
                approval=approval.model_dump() if approval else None,
                counts=scale.counts if scale else None,
                confirmation_name=undeploy.confirmation_name if undeploy else None,
                idempotency_key=request.headers.get("Idempotency-Key", "") if request else ""))
        from ..endpoint_control import create_endpoint_router
        from ..placement_recommendations import create_placement_router
        placement_router = create_placement_router(store, upstream_dir=upstream, recommender=ctx.consume("resources.placement"))
        endpoint_router = create_endpoint_router(store, fleet, mode=selected_mode)
        append_router(router, placement_router, dependencies=secured)
        append_router(router, endpoint_router, dependencies=secured)
        state = ctx.require("application_state")
        if state.plugins.enabled("services"):
            state.legacy_publications.publish(ctx, "services.routes", (placement_router, endpoint_router))

        @router.get("/api/services", dependencies=secured)
        def get_services(request: Request):
            nodes = node_map()
            current = [reconcile_stopping(service) for service in workflow.list()]
            return {"items": [service_view(service, request, nodes=nodes) for service in current if not service.get("deleted")]}

        @router.post("/api/services", dependencies=secured, status_code=201)
        def create_service(data: ServiceInput):
            return workflow.create(data.model_dump())

        @router.get("/api/services/{identifier}", dependencies=secured)
        def get_service(identifier: str, request: Request):
            service = reconcile_stopping(service_or_404(identifier))
            if service.get("deleted"): raise HTTPException(404, "服务已完成删除")
            return service_view(service, request)

        @router.put("/api/services/{identifier}", dependencies=secured)
        def edit_service(identifier: str, data: ServiceInput):
            return workflow.update(identifier, data.model_dump())

        @router.post("/api/services/{identifier}/plan", dependencies=secured)
        def service_plan(identifier: str):
            service = service_or_404(identifier)
            return plan_service(service, replacing=bool(service.get("needs_rebuild")))

        @router.post("/api/services/{identifier}/deploy", dependencies=secured)
        def deploy_service(identifier: str, request: Request, approval: DeploymentApproval | None = None): return submit_job(identifier, "deploy", approval, request=request)

        @router.post("/api/services/{identifier}/stop", dependencies=secured)
        def stop_service(identifier: str, request: Request): return submit_job(identifier, "stop", request=request)

        @router.post("/api/services/{identifier}/restart", dependencies=secured)
        def restart_service(identifier: str, request: Request): return submit_job(identifier, "restart", request=request)

        @router.post("/api/services/{identifier}/rebuild", dependencies=secured)
        def rebuild_service(identifier: str, request: Request, approval: DeploymentApproval | None = None): return submit_job(identifier, "rebuild", approval, request=request)

        @router.post("/api/services/{identifier}/scale/plan", dependencies=secured)
        def scale_service_plan(identifier: str, data: ScaleInput):
            service = service_or_404(identifier)
            require_scalable(service)
            return plan_scale(service, data.counts)

        @router.post("/api/services/{identifier}/scale", dependencies=secured)
        def scale_service(identifier: str, request: Request, data: ScaleInput): return submit_job(identifier, "scale", scale=data, request=request)

        @router.post("/api/services/{identifier}/undeploy", dependencies=secured)
        def undeploy_service(identifier: str, request: Request, data: UndeployInput): return submit_job(identifier, "undeploy", undeploy=data, request=request)

        @router.delete("/api/services/{identifier}", dependencies=secured)
        def delete_service(identifier: str):
            result = workflow.delete(identifier)
            return JSONResponse(result.body, status_code=result.status_code)

        @router.get("/api/services/{identifier}/export", dependencies=secured)
        def export_service(identifier: str):
            return workflow.export(identifier)

        @router.get("/api/services/{identifier}/revisions", dependencies=secured)
        def service_revisions(identifier: str):
            return {"items": workflow.revisions(identifier)}

        @router.post("/api/services/{identifier}/inference", dependencies=secured)
        def inference(identifier: str, data: InferenceInput):
            service = service_or_404(identifier)
            if service["status"] not in {"running", "starting", "partial"} or (selected_mode != "demo" and not service.get("resources_may_exist")):
                raise HTTPException(409, "只能向已部署且正在运行的服务发起推理，草稿或已停止服务不会连接其他同地址业务")
            if selected_mode == "demo": return {"text": "这是平台的演示响应。真实环境会通过所选服务的 Coordinator 执行推理，并返回实际结果。", "latency_ms": 186, "usage": {"prompt_tokens": 12, "completion_tokens": 32}, "endpoint": service["endpoint"], "demo": True}
            if service["runtime"] == "kubernetes":
                if not service["placements"]: raise HTTPException(422, "缺少 K8S 执行节点")
                node = node_or_404(service["placements"][0]["node_id"])
                result = fleet.call(node, "POST", "/agent/kubernetes/inference", {"service_id": identifier, "motor_profile": service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID, **data.model_dump()}, timeout=190)
                store.audit("推理验证", service["name"], "success", "经 K8S Agent 收到推理结果")
                return result
            begin = time.monotonic()
            result = motor_request(service, node_map(), "infer", "/v1/chat/completions", method="POST", body={"model": service["model"], "messages": [{"role": "user", "content": data.prompt}], "max_tokens": data.max_tokens, "stream": False}, timeout=180)
            try:
                output = inference_output(result)
            except ValueError as exc:
                store.audit("推理验证", service["name"], "failed", str(exc))
                raise FleetError(str(exc)) from exc
            state = output["output_status"]
            detail = {"complete": "收到非流式文本响应", "truncated": "达到原生生成上限；文本未完整结束",
                      "incomplete": f"原生响应以 {output['finish_reason']} 结束，未确认正常完成",
                      "reasoning_only": "收到思考内容，尚无最终文本回答", "empty": "原生响应没有文本或思考内容，输出验收未通过"}[state]
            store.audit("推理验证", service["name"], "success" if state == "complete" else "failed" if state == "empty" else "partial", detail,
                        metadata={key: output[key] for key in ("response_id", "model", "finish_reason", "output_status", "truncated") if key in output})
            return {**output, "latency_ms": round((time.monotonic() - begin) * 1000, 1),
                    "usage": result.get("usage", {}), "endpoint": service["endpoint"]}

        return router

    if ctx.contract == 'api.metrics':
        service_observer = ctx.consume("telemetry.service-health")
        service_metrics_sampler = ctx.consume("telemetry.service-metrics")
        ctx.require("application_state").service_metrics_sampler = service_metrics_sampler

        @router.get("/api/services/{identifier}/metrics", dependencies=secured)
        def service_metrics(identifier: str):
            with mutation_lock:
                service = service_or_404(identifier)
                nodes = node_map()
                current_digest = configuration_digest(service)
                revision = metrics_revision(service, nodes, current_digest)
                if service.get("deployed_digest") and service["deployed_digest"] != current_digest:
                    service = {**service, "has_unapplied_changes": True}

            def is_current():
                with mutation_lock:
                    latest = store.get("services", identifier)
                    return latest is not None and metrics_revision(
                        latest, node_map(), configuration_digest(latest)) == revision

            return service_metrics_sampler.sample(service, nodes, revision=revision,
                                                  is_current=is_current, fleet=fleet, demo=selected_mode == "demo")

        @router.get("/api/services/{identifier}/observations", dependencies=secured)
        def service_observations(identifier: str):
            service = service_or_404(identifier)
            observation_started = now()
            original_digest = configuration_digest(service)
            original_updated = service["updated_at"]
            if selected_mode == "demo":
                return {"demo": True, "instances": {"instances": [{"id": 1, "role": "union", "healthy": service["status"] == "running", "status": "ACTIVE"}]}, "readiness": {"ready": service["status"] == "running"}, "metrics_text": "# DEMO: simulated metrics\nmotor:request_rate 56.8\nmotor:request_latency 185.4\n", "observed_at": now(), "errors": []}
            if service["status"] in {"draft", "stopped"} or not service.get("resources_may_exist"):
                return {"instances": None, "readiness": None, "metrics_text": "", "errors": [{"source": "lifecycle", "detail": "服务尚未部署或已停止，未连接该地址上的业务"}], "observed_at": now()}
            if service["runtime"] == "kubernetes":
                if not service["placements"]: raise HTTPException(422, "缺少 K8S 执行节点")
                node = node_or_404(service["placements"][0]["node_id"])
                deadline = time.monotonic() + 40
                result = fleet.call(node, "GET", "/agent/kubernetes/observations?service_id=" + identifier,
                                    timeout=32, total_timeout=32)
                status = None
                try:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0: raise FleetError("Kubernetes capacity observation deadline elapsed")
                    status = fleet.call(node, "GET", "/agent/kubernetes/status?service_id=" + identifier,
                                        timeout=min(8, remaining), total_timeout=remaining)
                except Exception:
                    result = {**result, "errors": [*result.get("errors", []),
                              {"source": "workload", "detail": "受管 Kubernetes UID 与角色目标未在本次总预算内核实。"}]}
                from ..kubernetes_health import kubernetes_observation_gates
                result = kubernetes_observation_gates(service, result, status)
            else:
                result = service_observer.observe(service, node_map(), observations)
            with mutation_lock:
                latest = store.get("services", identifier)
                eligible = latest and latest["updated_at"] == original_updated and configuration_digest(latest) == original_digest
                eligible = eligible and latest["status"] in {"running", "starting", "partial"}
                eligible = eligible and latest.get("last_observation_started", "") <= observation_started
                if eligible:
                    gate = result.get("readiness_gate")
                    latest["status"] = ({"ready": "running", "starting": "starting", "degraded": "partial", "unknown": "partial"}[gate["state"]]
                                        if gate else "running" if ready_value(result["readiness"]) else "starting" if not result["errors"] else "partial")
                    latest["last_observation_started"] = observation_started
                    latest["last_observed_at"] = result.get("observed_at", now())
                    store.put("services", latest)
                else:
                    result.update(stale=True, readiness=None, instances=None)
                    for field in ("runtime", "registration_gate", "readiness_gate"):
                        if field in result:
                            result[field] = None
                    result.setdefault("errors", []).append({"source": "revision", "detail": "服务修订或运行状态已变化，本次旧观测未应用"})
            result["errors"] = [redact(item) for item in result.get("errors", [])]
            for field in ("runtime", "registration_gate", "readiness_gate"):
                if isinstance(result.get(field), dict):
                    result[field]["issues"] = [redact(item) for item in result[field].get("issues", [])]
            return result

        return router

    if ctx.contract == 'api.runtime-logs':
        service_log_reader = ctx.consume("logs.runtime")
        ctx.require("application_state").service_log_reader = service_log_reader

        @router.get("/api/services/{identifier}/logs", dependencies=secured)
        def service_logs(identifier: str, tail: int = Query(200, ge=1, le=2000),
                         node_id: str | None = Query(None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"),
                         role: str | None = Query(None, min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_-]*$"),
                         limit: int | None = Query(None, ge=1, le=2000), offset: int = Query(0, ge=0, le=1999)):
            from urllib.parse import urlencode
            service = service_or_404(identifier)
            try:
                role = validate_log_role(role)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            page_size = limit or tail
            window_size = max(tail, offset + page_size)
            if window_size > 2000:
                raise HTTPException(422, "日志分页窗口最多2000条，请缩小页码范围或按节点、角色筛选")
            errors = []
            if selected_mode == "demo":
                items = [{"time": now(), "level": "INFO", "level_source": "platform", "role": "coordinator", "node": "ascend-a3-01", "source": "demo", "message": "[演示] Coordinator started; waiting for native instance registration"},
                         {"time": now(), "level": "INFO", "level_source": "platform", "role": "union", "node": "ascend-a3-01", "source": "demo", "message": "[演示] 模型实例已注册，演示请求已完成；此日志不来自真实推理引擎"}]
            else:
                targets, seen = [], set()
                log_service = {**service, "runtime": service.get("deployed_runtime") or service["runtime"]}
                for placement in execution_placements(log_service, "stop"):
                    target = placement["node_id"]
                    if target in seen:
                        continue
                    seen.add(target)
                    if node_id is None or target == node_id:
                        targets.append(node_or_404(target))
                if node_id is not None and node_id not in seen:
                    raise HTTPException(422, "该执行节点不在当前服务的运行布局中")
                def fetch(node, budget):
                    prefix = "/agent/kubernetes" if log_service["runtime"] == "kubernetes" else "/agent"
                    query = {"service_id": identifier, "tail": window_size}
                    if prefix == "/agent":
                        query["node_id"] = node["id"]
                    if role is not None:
                        query["role"] = role
                    response = fleet.call(node, "GET", prefix + "/logs?" + urlencode(query), timeout=budget, total_timeout=budget)
                    if role is not None and (response.get("filters") or {}).get("role") != role:
                        response = {**response, "partial": True}
                    return response
                items, errors = service_log_reader.read(targets, fetch, seconds=20)
                messages = {"log_total_deadline": "日志读取超时，本次只显示已返回的来源", "log_reader_busy": "日志读取繁忙，请稍后重试",
                            "source_log_window_partial": "该来源仅返回了部分日志片段", "source_log_error": "该来源的部分容器日志暂不可读取"}
                for error in errors:
                    items.append({"time": "", "observed_at": now(), "level": "WARN" if error["code"] == "source_log_window_partial" else "ERROR",
                                  "level_source": "platform", "role": "agent", "node": error["node_id"], "agent_node_id": error["node_id"],
                                  "node_name": error["node_name"], "message": messages.get(error["code"], "无法读取该节点日志，请检查Agent连接"), "source": "agent_error"})
            items = [item for item in items if item.get("source") == "agent_error" or role_matches(item.get("role"), role)]
            sources = {source_key(item) for item in items}
            window = fair_tail(items, window_size)
            page = window[offset:offset + page_size]
            return {"items": [redact(item) for item in page], "errors": errors, "partial": bool(errors),
                    "total": len(window), "limit": page_size, "offset": offset, "has_more": offset + len(page) < len(window),
                    "window": "bounded_recent_tail", "consistent_snapshot": False,
                    "sources_total": len(sources), "sources_included": len({source_key(item) for item in page}),
                    "filters": {"node_id": node_id, "role": role}}

        return router

    if ctx.contract == 'api.gateways':
        from ..gateway_registry import create_gateway_router
        gateway_router = create_gateway_router(store, fleet.secrets, mutation_lock, role_for_request)
        append_router(router, gateway_router, dependencies=secured)
        state = ctx.require("application_state")
        if state.plugins.enabled("gateways"):
            state.legacy_publications.publish(ctx, "gateways.routes", (gateway_router,))

        @router.get("/api/services/{identifier}/aigw-models", dependencies=secured)
        def service_aigw_models(identifier: str):
            service = service_or_404(identifier)
            original_digest = configuration_digest(service)
            empty = {"source": "motor_coordinator", "items": [], "collected_at": now()}
            if selected_mode == "demo":
                return {**empty, "status": "demo", "error": "演示环境不模拟真实 AIGW 模型发现"}
            if service["status"] in {"draft", "stopped", "stopping", "removing"} or not service.get("resources_may_exist"):
                return {**empty, "status": "not_deployed", "error": "服务尚未运行，未连接该地址上的业务"}
            try:
                if service["runtime"] == "kubernetes":
                    if not service["placements"]:
                        raise FleetError("缺少 Kubernetes 执行入口")
                    node = node_or_404(service["placements"][0]["node_id"])
                    result = fleet.call(node, "GET", "/agent/kubernetes/aigw-models?service_id=" + identifier,
                                        timeout=40, max_response_bytes=MODEL_RESPONSE_BYTES)
                    if not isinstance(result, dict) or result.get("status") != "available":
                        raise FleetError("执行 Agent 返回了无效的模型发现结果")
                    result = {**empty, "status": "available", "items": project_models({"data": result.get("items")})}
                else:
                    result = discover_models(service, node_map())
                latest = store.get("services", identifier)
                if (not latest or configuration_digest(latest) != original_digest
                        or latest["status"] in {"draft", "stopped", "stopping", "removing"}
                        or not latest.get("resources_may_exist")):
                    return {**empty, "status": "unavailable", "error": "服务配置或运行状态已变化，请重新读取模型列表"}
                return {**result, "items": [redact(item) for item in result["items"]]}
            except FleetError as exc:
                return {**empty, "status": "unavailable", "error": redact(str(exc))}

        return router

    if ctx.contract == 'api.tasks':
        coordinator = ctx.consume("tasks.coordinator", coordinator_inputs=inputs.coordinator_inputs)
        from ..job_recovery import create_recovery_router
        recovery_router = create_recovery_router(store, fleet, mutation_lock, mode=selected_mode)
        append_router(router, recovery_router, dependencies=secured)
        state = ctx.require("application_state")
        if state.plugins.enabled("services"):
            state.legacy_publications.publish(ctx, "services.routes", (recovery_router,))

        @router.get("/api/jobs/{identifier}", dependencies=secured)
        def job_status(identifier: str, request: Request):
            result = coordinator.get(identifier)
            return redact({key: value for key, value in result.items() if key not in {"plan", "scale_plan"}}) if role_for_request(request) == "viewer" else result

        @router.get("/api/jobs", dependencies=secured)
        def job_history(request: Request, service_id: str | None = None):
            items = coordinator.list(service_id)
            if role_for_request(request) == "viewer": items = [redact({key: value for key, value in job.items() if key not in {"plan", "scale_plan"}}) for job in items]
            return {"items": items}

        return router

    raise ValueError("Unsupported reference service HTTP contract: " + ctx.contract)
