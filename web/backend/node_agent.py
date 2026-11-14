"""Authenticated node-local HTTP boundary; never silently substitutes demo data."""
from __future__ import annotations

import os
from ipaddress import IPv4Address
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from .docker_runtime import ContainerConflict, DockerRuntime, DockerUnavailable
from .docker_agent_state import AgentStateConflict
from .motor_adapter import MotorConfigError
from .compatibility import CompatibilityError, agent_metadata, public_profile
from .version import PRODUCT_ID, OBSERVATION_NONCE_HEADER
from .image_contract import ImageContractInspector, ImageInspectionInput, ImageInspectionBusy, ImageNotLocal
from .agent_credentials import agent_credential_metadata, authorize_agent as authorize
from .observability import redact
from .observation_cache import ObservationCache
from .execution_policy import AgentReadOnlyError, execution_metadata, require_mutations
from .image_operations import ImageOperations, ImagePullInput, ImageRemoveInput


def create_agent_router(runtime: DockerRuntime | None = None, *, docker_enabled: bool = True,
                        kubernetes_enabled: bool = True, kubernetes_runtime=None,
                        host_telemetry=None, agent_policy=None, operation_tracker=None, native_adapter=None) -> APIRouter:
    if type(docker_enabled) is not bool or type(kubernetes_enabled) is not bool:
        raise ValueError("Agent runtime selections must be booleans")
    def selected_authorize(authorization: str | None = Header(default=None)):
        return agent_policy.authorize(authorization)
    auth_dependency = selected_authorize if agent_policy is not None else authorize
    router = APIRouter(prefix="/agent", tags=["docker-node-agent"])
    # A disabled provider must not acquire its daemon, workspace or operation
    # database merely to expose the shared Agent health and telemetry routes.
    docker_runtime = (runtime if runtime is not None else DockerRuntime()) if docker_enabled else None
    host_root = getattr(docker_runtime, "host_root", None) or Path(os.getenv("MOTOR_HOST_ROOT", "/"))
    inventory_cache = getattr(docker_runtime, "_inventory_cache", None) or ObservationCache()
    if docker_runtime is not None:
        image_inspector = ImageContractInspector(docker_runtime, profile_provider=native_adapter)
        image_operations = ImageOperations(docker_runtime)
        router.add_event_handler("shutdown", image_operations.close)

    def call(function, *args):
        def detail(error):
            return str(redact(str(error)))[:2000]
        try:
            return function(*args)
        except HTTPException:
            raise
        except AgentReadOnlyError as exc:
            raise HTTPException(403, detail(exc)) from exc
        except DockerUnavailable as exc:
            raise HTTPException(503, detail(exc)) from exc
        except ImageNotLocal as exc:
            raise HTTPException(404, detail(exc)) from exc
        except ImageInspectionBusy as exc:
            raise HTTPException(429, detail(exc), headers={"Retry-After": "2"}) from exc
        except CompatibilityError as exc:
            raise HTTPException(409, detail(exc)) from exc
        except (ContainerConflict, AgentStateConflict) as exc:
            raise HTTPException(409, detail(exc)) from exc
        except (MotorConfigError, ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, detail(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, "Node Agent operation failed: " + detail(exc)) from exc

    def tracked(action, payload, result):
        return operation_tracker("result", "docker", action, payload, result) if operation_tracker is not None else result

    @router.get("/health")
    def health():
        profile = call(native_adapter.public_profile if native_adapter is not None else public_profile)
        return {"status": "ok", "service": "motor-docker-node-agent" if docker_enabled else "motor-node-agent", "product_id": PRODUCT_ID,
                "motor_commit": profile["commit"], "motor_profile_id": profile["profile_id"]}

    @router.get("/meta", dependencies=[Depends(auth_dependency)])
    def meta():
        mode = execution_metadata(getattr(docker_runtime, "read_only", None))
        metadata = native_adapter.agent_metadata() if native_adapter is not None else agent_metadata()
        metadata.update(mode)
        metadata["supported_runtimes"] = (["docker"] if docker_enabled else []) + (["kubernetes"] if kubernetes_enabled else [])
        metadata["execution_capabilities"]["mutations"] = not mode["read_only"]
        for name in ("operation_receipts", "resource_ledger", "resource_admission", "image_operations"):
            metadata["execution_capabilities"][name] = docker_enabled
        return {**metadata, "credential_policy": agent_policy.metadata() if agent_policy is not None else agent_credential_metadata()}

    def host_inventory(*, docker_unavailable: bool = False):
        from .resource_inventory import observation_times
        from .resource_telemetry import collect_resource_telemetry
        import platform
        version = {}
        errors = []
        if kubernetes_enabled and os.getenv("KUBECONFIG"):
            # This path also supports a Kubernetes-only Agent. It must not
            # instantiate or probe Docker before observing the configured API.
            if kubernetes_runtime is None:
                from .kubernetes_runtime import KubernetesRuntime
                version = KubernetesRuntime().observation_version()
            else:
                version = kubernetes_runtime.observation_version()
            if not version.get("gitVersion"):
                raise HTTPException(503, "Kubernetes API version could not be verified")
        elif kubernetes_enabled:
            errors.append({"source": "kubernetes", "code": "not_configured",
                           "message": "Kubernetes connection is not configured; only Agent host telemetry was collected"})
        if docker_unavailable:
            errors.append({"source": "docker", "code": "docker_unavailable",
                           "message": "Docker inventory is unavailable; Kubernetes API was verified separately"})
        return {"status": "online", "inventory_schema": 2, **observation_times(),
                "inventory_status": "partial", "collection_errors": errors,
                **(host_telemetry or collect_resource_telemetry)(host_root), "architecture": platform.machine(),
                "docker_version": "", "docker_available": False,
                "kubernetes_available": bool(version.get("gitVersion")),
                "kubernetes_version": version.get("gitVersion", ""),
                "npu_total": None, "npu_used": None, "devices": [], "devices_in_use": [],
                "resource_scope": "Hardware telemetry describes only this execution Agent host, not the full cluster. "
                                  "Runtime inventory and cluster allocations are separate observations."}

    def collect_inventory():
        if docker_runtime is None:
            return host_inventory()
        try:
            collector = getattr(docker_runtime, "_collect_inventory", None) or docker_runtime.inventory
            return {**call(collector), "kubernetes_available": kubernetes_enabled and bool(os.getenv("KUBECONFIG"))}
        except HTTPException as exc:
            if exc.status_code != 503 or not kubernetes_enabled or not os.getenv("KUBECONFIG"):
                raise
            return host_inventory(docker_unavailable=True)

    @router.get("/inventory", dependencies=[Depends(auth_dependency)])
    def inventory(response: Response, observation_nonce: str | None = Header(default=None, alias=OBSERVATION_NONCE_HEADER),
                  node_address: IPv4Address | None = Query(default=None)):
        # Cache the complete result, including the Kubernetes-only fallback.
        # The real Runtime shares this cache and invalidates it around writes.
        from .resource_freshness import inventory_response
        response.headers["Cache-Control"] = "no-store"
        if node_address is not None and (node_address.is_unspecified or node_address.is_multicast or node_address.is_link_local):
            raise HTTPException(422, "请选择节点实际 IPv4 地址")

        def observed():
            snapshot = inventory_cache.read(collect_inventory)
            if node_address is not None and docker_runtime is not None:
                snapshot = docker_runtime.inventory_network_view(snapshot, str(node_address))
            return inventory_response(snapshot, observation_nonce)

        return call(observed)

    if docker_runtime is None:
        return router

    @router.get("/operations/{operation_id}", dependencies=[Depends(auth_dependency)])
    def operation(operation_id: str):
        receipt = call(docker_runtime.operation, operation_id)
        if receipt is None:
            raise HTTPException(404, "No operation receipt exists for this identifier")
        return tracked(receipt.get("action", "receipt"), {"operation_id": operation_id, "service_id": receipt.get("service_id")}, receipt)

    @router.get("/images", dependencies=[Depends(auth_dependency)])
    def images():
        return {"items": call(docker_runtime.images)}

    @router.get("/status", dependencies=[Depends(auth_dependency)])
    def status(service_id: str, node_id: str):
        return call(docker_runtime.status, service_id, node_id)

    @router.post("/images/inspect", dependencies=[Depends(auth_dependency)])
    def inspect_image(payload: ImageInspectionInput):
        # Contract inspection may create a short-lived CPU-only container.
        call(require_mutations, getattr(docker_runtime, "read_only", None))
        return call(image_inspector.inspect, payload)

    @router.post("/images/pull", dependencies=[Depends(auth_dependency)], status_code=202)
    def pull_image(payload: ImagePullInput):
        return tracked("image_pull", payload.model_dump(), call(image_operations.submit, "image_pull", payload))

    @router.post("/images/remove", dependencies=[Depends(auth_dependency)], status_code=202)
    def remove_image(payload: ImageRemoveInput):
        return tracked("image_remove", payload.model_dump(), call(image_operations.submit, "image_remove", payload))

    @router.get("/images/operations/{operation_id}", dependencies=[Depends(auth_dependency)])
    def image_operation(operation_id: str):
        receipt = call(image_operations.receipt, operation_id)
        if receipt is None:
            raise HTTPException(404, "No image operation exists for this identifier")
        return tracked("image_receipt", {"operation_id": operation_id}, receipt)

    @router.post("/preflight", dependencies=[Depends(auth_dependency)])
    def preflight(payload: dict):
        return call(docker_runtime.preflight, payload)

    @router.post("/deploy", dependencies=[Depends(auth_dependency)])
    def deploy(payload: dict):
        return tracked("deploy", payload, call(docker_runtime.deploy, payload))

    def lifecycle(action: str, payload: dict):
        node = payload.get("node") or {}
        if not isinstance(node, dict):
            raise HTTPException(400, "node must be an object")
        node_id = payload.get("node_id") or node.get("id")
        if not node_id:
            raise HTTPException(400, "node_id is required for an Agent lifecycle operation")
        if any(value != node_id for value in (payload.get("node_id", node_id), node.get("id", node_id))):
            raise HTTPException(400, "node_id and node.id must identify the same target")
        return tracked(action, payload, call(docker_runtime.action, action, payload.get("service_id"), node_id, payload.get("operation_id")))

    @router.post("/stop", dependencies=[Depends(auth_dependency)])
    def stop(payload: dict):
        return lifecycle("stop", payload)

    @router.post("/restart", dependencies=[Depends(auth_dependency)])
    def restart(payload: dict):
        return lifecycle("restart", payload)

    @router.post("/remove", dependencies=[Depends(auth_dependency)])
    def remove(payload: dict):
        return lifecycle("remove", payload)

    @router.get("/logs", dependencies=[Depends(auth_dependency)])
    def logs(service_id: str, tail: int = Query(200, ge=1, le=2000), node_id: str | None = None,
             role: str | None = Query(None, min_length=1, max_length=48, pattern=r"^[a-z][a-z0-9_-]*$")):
        return call(docker_runtime.logs, service_id, tail, node_id, role)

    return router
