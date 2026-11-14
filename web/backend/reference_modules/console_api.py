"""Selected reference HTTP modules; business calls go through public ports."""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from .. import demo
from ..fleet import FleetError
from ..integrations import image_catalog, MODEL_RESPONSE_BYTES
from ..observability import redact
from ..version import PRODUCT_NAME
from .services_http import append_router
from .services_models import ImageInspectionInput


def selected_routes(ctx, router):
    """Retain native route ownership and optional-feature enablement."""
    from ..provider_host.catalogue import route_contract
    registry = ctx.require("application_state").plugins
    router.routes[:] = [route for route in router.routes
        if route_contract(getattr(route, "path", "")) == ctx.contract
        and (not (owner := registry.owner_for_path(getattr(route, "path", ""))) or registry.enabled(owner))]
    return router


def factory(ctx):
    inputs = ctx.require("api_inputs")
    workflow_inputs = inputs.workflow_inputs
    selected_mode, mutation_lock = workflow_inputs.mode, workflow_inputs.mutation_lock
    secured, role_for_request = list(inputs.secured), inputs.role_for_request
    # Preserve reviewed additive package contributions in this exact namespace.
    router = APIRouter(routes=list(ctx.require("reference_routes")))
    if ctx.contract not in {"api.frontend", "api.auth", "api.accounts"}:
        from ..provider_host.defaults import AuditedStore
        store = AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink"))

    if ctx.contract in {"api.auth", "api.accounts"}:
        if selected_mode == "demo":
            if ctx.contract == "api.auth":
                @router.get("/api/auth/status")
                def demo_identity():
                    return {"setup_required": False, "authenticated": True, "user": None, "csrf_token": None,
                            "expires_at": None, "permissions": [], "sso": {"enabled": False}, "demo": True}
        else:
            from ..auth import create_auth_router
            from ..oidc import create_oidc_router
            auth = ctx.consume("security.identity")
            append_router(router, create_auth_router(auth))
            if ctx.contract == "api.auth": append_router(router, create_oidc_router(auth))
        return selected_routes(ctx, router)

    if ctx.contract == "api.nodes":
        state = ctx.require("application_state")
        if state.plugins.enabled("nodes"):
            from ..agent_rollout import AgentRolloutManager, create_agent_rollout_router
            from ..node_management import create_node_router
            fleet = ctx.consume("agent.connection")
            sampler = ctx.consume("telemetry.inventory", mutation_lock=mutation_lock)
            manager = AgentRolloutManager(store, fleet, mutation_lock, mode=selected_mode)
            ctx.on_shutdown(manager.close)
            state.agent_rollouts = manager
            state.legacy_publications.publish(ctx, "nodes.manager", manager)
            append_router(router, create_agent_rollout_router(manager, role_for_request), dependencies=secured)
            append_router(router, create_node_router(store, fleet, mutation_lock, mode=selected_mode,
                                                   inventory_sampler=sampler), dependencies=secured)
        return selected_routes(ctx, router)

    if ctx.contract == "api.templates":
        if ctx.require("application_state").plugins.enabled("templates"):
            from ..template_library import create_template_router
            template_router = create_template_router(store, ctx.consume("templates.catalog"), mutation_lock,
                role_for_request, profile_provider=ctx.consume("motor.native"))
            append_router(router, template_router, dependencies=secured)
            ctx.require("application_state").legacy_publications.publish(ctx, "templates.routes", (template_router,))
        return selected_routes(ctx, router)

    if ctx.contract == "api.system":
        from ..system import create_system_router
        state = ctx.require("application_state")
        manager = ctx.consume("maintenance.manager")
        state.system = manager
        append_router(router, create_system_router(manager))
        if state.plugins.enabled("branding"):
            from ..branding import create_branding_router
            append_router(router, create_branding_router(ctx.consume("branding.manager", mutation_lock=mutation_lock)))
        return selected_routes(ctx, router)

    if ctx.contract == "api.extensions":
        from ..extensions import create_extension_router
        registry = ctx.require("application_state").plugins
        fleet = ctx.consume("agent.connection")
        grafana = ctx.consume("integration.grafana", mutation_lock=mutation_lock) if registry.enabled("grafana") else None
        append_router(router, create_extension_router(store, fleet.secrets, mutation_lock, workflow_inputs.upstream,
            enabled=registry.enabled, grafana_provider=grafana), dependencies=secured)
        return selected_routes(ctx, router)

    if ctx.contract == "api.plugins":
        from ..plugin_host.community import create_package_router, sdk_description
        registry = ctx.require("application_state").plugins
        community = ctx.consume("plugins.packages")
        append_router(router, create_package_router(community, registry, store, mutation_lock, role_for_request),
                              dependencies=secured)

        @router.get("/api/plugins", dependencies=secured)
        def plugin_catalog():
            return {**community.catalog(registry), "assembly": ctx.describe()}

        @router.get("/api/plugin-sdk", dependencies=secured)
        def plugin_sdk():
            return {**sdk_description(), "provider_sdk": ctx.sdk_description()}

        return selected_routes(ctx, router)

    if ctx.contract == 'api.overview':
        inventory_view = ctx.consume("resources.inventory")
        enabled_runtimes = ctx.require("application_state").plugins.enabled_runtimes

        @router.get("/api/overview", dependencies=secured)
        def overview(request: Request):
            services, nodes = store.list("services"), store.list("nodes")
            current = [inventory_view(n) for n in nodes if n.get("docker_available") is True]
            known = [n for n in current if (n.get("resource_freshness") == "fresh" or selected_mode == "demo" and n.get("demo"))
                     and n.get("npu_total") is not None and not n.get("identity_conflict")
                     and not any(other["id"] != n["id"] and (
                         n.get("host_identity") and n["host_identity"] == other.get("host_identity")
                         or n.get("daemon_id") and n["daemon_id"] == other.get("daemon_id")
                         or n.get("address") == other.get("address")) for other in current)]
            npu_value = sum(n["npu_total"] for n in known) if known else "—"
            counts = {"running": sum(s["status"] == "running" for s in services), "stopped": sum(s["status"] == "stopped" for s in services), "error": sum(s["status"] == "error" for s in services), "total": len(services)}
            return {"stats": [{"key": "services", "label": "推理服务", "value": len(services), "unit": "个", "detail": f"{counts['running']} 个业务就绪"},
                              {"key": "nodes", "label": "接入节点", "value": len(nodes), "unit": "台", "detail": f"{sum(n['status'] == 'online' for n in nodes)} 台探测在线"},
                              {"key": "npu", "label": "Docker NPU 已采集", "value": npu_value, "unit": "个逻辑设备", "detail": f"{len(known)}/{len(current)} 个 Agent 有有效观测；声明容量不代表空闲"},
                              {"key": "runtimes", "label": "运行时", "value": len(enabled_runtimes()), "unit": "种", "detail": " / ".join(enabled_runtimes())}],
                    "series": demo.series() if selected_mode == "demo" else [], "activities": [{"id": a["id"], "title": a["action"], "detail": a["target"] + " · " + a["detail"], "time": a["time"], "status": a["status"]} for a in store.audit_items(10)] if role_for_request(request) in {"admin", "demo"} else [], "service_counts": counts, "demo": selected_mode == "demo"}

        return router

    if ctx.contract == 'api.images':
        fleet = ctx.consume("agent.connection")
        native = ctx.consume("motor.native")
        public_profile = native.public_profile
        from ..image_control import create_image_router
        image_router = create_image_router(store, fleet, mutation_lock)
        append_router(router, image_router, dependencies=secured)
        state = ctx.require("application_state")
        if state.plugins.enabled("images"):
            state.legacy_publications.publish(ctx, "images.routes", (image_router,))
        def node_or_404(identifier):
            value = store.get("nodes", identifier)
            if value is None: raise HTTPException(404, "节点不存在")
            return value

        @router.get("/api/images", dependencies=secured)
        def images():
            if selected_mode == "demo": return {"items": [{"id": "demo-image-1", "node_id": "demo-node-1", "node_name": "ascend-a3-01", "tags": ["mindie-motor:demo-a3"], "size": 18500000000, "architecture": "arm64", "demo": True}], "errors": [], "unsupported_nodes": []}
            items, errors, unsupported_nodes = [], [], []
            for node in store.list("nodes"):
                if node.get("docker_available") is False:
                    unsupported_nodes.append({"node_id": node["id"], "node_name": node["name"],
                        "reason": "此节点不提供 Docker 镜像库存；Kubernetes 镜像通过集群或镜像仓库管理。"})
                    continue
                try:
                    response = fleet.call(node, "GET", "/agent/images", timeout=30)
                    for item in (response if isinstance(response, list) else response.get("items", [])):
                        items.append({**item, "node_id": node["id"], "node_name": node["name"]})
                except FleetError as error:
                    detail = str(error)
                    prefix = node["name"] + ": "
                    errors.append(redact(detail if detail.startswith(prefix) else prefix + detail))
            return {"items": items, "errors": errors, "unsupported_nodes": unsupported_nodes}

        @router.get("/api/integrations/images/catalog", dependencies=secured)
        def integration_images():
            return image_catalog()

        @router.post("/api/integrations/images/inspect", dependencies=secured)
        def inspect_integration_image(data: ImageInspectionInput):
            from ..image_contract import sanitize_inspection_result
            node = node_or_404(data.node_id)
            if selected_mode == "demo":
                raise HTTPException(409, "演示环境不执行真实镜像检查，请接入授权执行 Agent")
            if node.get("docker_available") is False:
                raise HTTPException(422, "此执行 Agent 未接入 Docker；K8S 镜像不能用宿主 Docker 库存代替检查")
            try:
                profile = public_profile()
                payload = {"image": data.image, "probe_runtime": data.probe_runtime,
                           "motor_profile": profile["profile_id"]}
                result = fleet.call(node, "POST", "/agent/images/inspect", payload,
                                    timeout=150 if data.probe_runtime else 45, max_response_bytes=MODEL_RESPONSE_BYTES)
                result = sanitize_inspection_result(result, expected_image=data.image, expected_profile=profile)
            except (FleetError, ValueError) as exc:
                raise HTTPException(502, redact(str(exc))) from exc
            store.audit("检查推理镜像", node["name"], "success", detail="已读取镜像检查报告，未部署或加载模型",
                        metadata={"node_id": data.node_id, "image_id": result["image_id"],
                                  "probe_requested": data.probe_runtime, "compatibility": "unverified"})
            return result

        return router

    if ctx.contract == 'api.audit':

        @router.get("/api/audit", dependencies=secured)
        def audit_log(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0, le=100000),
                      actor: str | None = None, request_id: str | None = None, action: str | None = None,
                      status: str | None = None, since: str | None = None, until: str | None = None,
                      actor_username: str | None = Query(None, max_length=128)):
            try: return store.query_audit(limit=limit, offset=offset, actor=actor, actor_username=actor_username, request_id=request_id, action=action, status=status, since=since, until=until)
            except ValueError as exc: raise HTTPException(422, str(exc)) from exc

        return router

    if ctx.contract == 'api.frontend':
        configuration = ctx.settings["configuration"]
        dist = Path(os.getenv("MOTOR_STATIC_DIR", Path(__file__).resolve().parents[2] / "frontend/dist"))
        if configuration.static_enabled and (dist / "assets").is_dir():
            router.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @router.get("/{path:path}")
        def frontend(path: str):
            if not configuration.static_enabled:
                raise HTTPException(404, "静态 Web 由独立前端服务提供")
            if path.startswith(("api/", "agent/")): raise HTTPException(404, "接口不存在")
            asset = (dist / path).resolve()
            if asset.is_relative_to(dist.resolve()) and asset.is_file(): return FileResponse(asset)
            index = dist / "index.html"
            if index.is_file(): return FileResponse(index)
            return JSONResponse({"name": PRODUCT_NAME, "detail": "前端尚未构建；开发时运行 npm run dev，或先构建 frontend/dist", "mode": selected_mode})

        return router

    raise ValueError("Unsupported reference console HTTP contract: " + ctx.contract)
