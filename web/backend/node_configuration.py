"""Registered Agent settings, separate from observations and native workloads."""
from __future__ import annotations

import re

from .resource_inventory import inventory_snapshot
from .image_receipts import image_operation_unresolved
from .store import now

_ACTIVE_SERVICES = {"running", "deploying", "starting", "stopping", "partial", "restarting", "scaling", "removing"}
_ACTIVE_JOBS = {"queued", "running"}


def nic_configuration(node: dict) -> tuple[str, str, str]:
    # Old records did not distinguish manual input from discovery. Preserve
    # their choice until an administrator explicitly requests automatic mode.
    configured = node.get("configured_nic_name", node.get("nic_name", ""))
    reported = node.get("reported_nic_name", "")
    configured = configured if isinstance(configured, str) else ""
    reported = reported if isinstance(reported, str) else ""
    return configured, reported, configured or reported


def _configured_nic_value(node: dict):
    return node.get("configured_nic_name", node.get("nic_name", ""))


def _invalid_configured_nic(node: dict) -> bool:
    value = _configured_nic_value(node)
    return value is not None and value != "" and not isinstance(value, str)


def execution_node(node: dict) -> dict:
    """Project the same effective NIC used by the node configuration view.

    Do not persist this projection or promote discovery to a manual setting.
    Missing NICs remain empty so unrelated read operations can still use nodes.
    """
    effective = "" if _invalid_configured_nic(node) else nic_configuration(node)[2]
    # Preserve legacy manual/automatic intent across repeated projections,
    # including invalid legacy values that must not fall back to discovery.
    return {**node, "configured_nic_name": _configured_nic_value(node), "nic_name": effective}


def valid_nic_name(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", value) is not None


def nic_configuration_error(node: dict) -> str:
    name = str(node.get("name") or node.get("id") or node.get("address") or "执行节点")[:100]
    detail = ("通信网卡配置类型无效" if _invalid_configured_nic(node) else
              "通信网卡名称无效" if nic_configuration(node)[2] else "未识别到通信网卡")
    return (f"节点「{name}」{detail}；请在“节点管理 → 查看配置”中指定网卡，"
            "或重新探测以使用 Agent 上报值。")


def _targets(placements) -> set[str]:
    return {item["node_id"] for item in placements or []
            if isinstance(item, dict) and isinstance(item.get("node_id"), str)}


def _job_unresolved(job: dict) -> bool:
    if job.get("status") in _ACTIVE_JOBS or job.get("outcome_unknown") is True or job.get("resources_may_exist") is True:
        return True
    steps, cleanup = job.get("steps", []), job.get("cleanup_steps", [])
    if not isinstance(steps, list) or not isinstance(cleanup, list):
        return True
    return any(isinstance(step, dict) and (
        step.get("status") in {"interrupted", "unknown"}
        or step.get("operation_status") in {"accepted", "running", "interrupted", "unknown"}
        or step.get("resources_may_exist") is True
    ) for step in [*steps, *cleanup])


def node_references(store, node: dict) -> dict:
    identifier = node["id"]
    services, protected_services, service_targets, kubernetes_resources = [], [], {}, []
    for service in store.list("services"):
        planned = _targets(service.get("placements"))
        deployed = _targets(service.get("deployed_placements"))
        service_targets[service["id"]] = planned | deployed
        if identifier not in planned | deployed:
            continue
        resources = (service.get("resources_may_exist") is True or bool(deployed)
                     or service.get("status") in _ACTIVE_SERVICES)
        protected = resources and identifier in (deployed or planned)
        summary = {"id": service["id"], "name": str(service.get("name", service["id"]))[:100],
                   "status": str(service.get("status", "unknown"))[:64],
                   "runtime": service.get("deployed_runtime") or service.get("runtime", ""),
                   "resources_may_exist": bool(service.get("resources_may_exist")),
                   "blocks_network_edit": bool(protected)}
        services.append(summary)
        if protected:
            protected_services.append(service["id"])
            if summary["runtime"] == "kubernetes":
                kubernetes_resources.append(service["id"])
    jobs = []
    for job in store.list("jobs"):
        if not _job_unresolved(job):
            continue
        targets = (_targets(job.get("execution_placements")) | _targets(job.get("steps"))
                   | _targets(job.get("cleanup_steps")))
        if isinstance(job.get("node_id"), str):
            targets.add(job["node_id"])
        if not targets:
            targets = service_targets.get(job.get("service_id"), set())
        if identifier in targets:
            jobs.append({"id": job["id"], "action": str(job.get("action", ""))[:64],
                         "status": job["status"], "service_id": str(job.get("service_id", ""))[:128], "kind": "service"})
    for job in store.list("agent_rollout_jobs"):
        if not _job_unresolved(job):
            continue
        registered = job.get("_registered_node")
        registered_id = registered.get("id") if isinstance(registered, dict) else None
        if (job.get("node_id") == identifier or registered_id == identifier
                or job.get("address") == node.get("address")
                or job.get("agent_url") == node.get("agent_url")):
            jobs.append({"id": job["id"], "action": "Agent 接入", "status": job["status"],
                         "service_id": "", "kind": "agent_rollout"})
    for operation in store.list("image_operations"):
        state = operation.get("operation_status", operation.get("status"))
        unresolved = image_operation_unresolved(operation)
        if operation.get("node_id") == identifier and unresolved:
            jobs.append({"id": operation["id"], "action": "镜像操作", "status": str(state),
                         "service_id": "", "kind": "image_operation"})
    # An orphaned/unknown allocation remains a resource reference even if its
    # service record was lost. A stale claim cannot prove that resources freed.
    history = node.get("last_good_inventory")
    history = history if isinstance(history, dict) else {}
    ledger = node.get("resource_ledger") or history.get("resource_ledger") or {}
    ledger_incomplete = isinstance(ledger, dict) and ledger.get("complete") is False
    items = ledger.get("items", []) if isinstance(ledger, dict) else []
    claims = [entry for entry in items if isinstance(entry, dict)
              and entry.get("state") in {"pending", "active", "uncertain"}
              and entry.get("node_id") == identifier] if isinstance(items, list) else []
    unresolved_claims = [entry for entry in claims if entry["state"] in {"pending", "uncertain"}]
    endpoint_reason = ""
    if kubernetes_resources:
        endpoint_reason = "此节点仍关联 Kubernetes 部署或未决资源；当前协议尚未绑定集群及执行状态身份，不能仅凭同宿主切换管理 URL。原 URL 的令牌和 CA 轮换仍可正常进行，不会触碰推理服务。"
    elif ledger_incomplete:
        endpoint_reason = "节点仍有未完整返回的资源账本；请先核对全部未释放声明，不能根据部分列表移除或切换执行入口。"
    elif jobs or unresolved_claims:
        endpoint_reason = "节点仍有执行中任务或待对账的资源分配，请先完成任务或核对未决资源后切换管理 URL。原 URL 的令牌和 CA 轮换不受影响。"
    return {"services": services[:128], "jobs": jobs[:64],
            "service_count": len(services), "job_count": len(jobs), "resource_claim_count": len(claims),
            "unresolved_resource_claim_count": len(unresolved_claims),
            "resource_ledger_incomplete": ledger_incomplete,
            "endpoint_protected": bool(endpoint_reason), "endpoint_protection_reason": endpoint_reason,
            "network_protected": bool(protected_services or jobs or claims or ledger_incomplete)}


def edit_policy(references: dict) -> dict:
    protected = references["network_protected"]
    blocked = "仍有关联的受管资源或执行中任务；部署 IP 和通信网卡暂不可修改，以保护后续部署、管理与访问。此限制不会中断当前推理流量。"
    return {
        "name": {"allowed": True, "reason": "仅修改显示名称，节点 ID 和已部署容器名称保持不变。"},
        "labels": {"allowed": True, "reason": "修改登记标签，不改变当前模型服务。"},
        "address": {"allowed": not protected, "reason": blocked if protected else "用于后续 Motor 部署和服务访问；修改登记值不会调整宿主网络或重启模型。"},
        "nic_name": {"allowed": not protected, "reason": blocked if protected else "用于后续 Motor 通信配置；留空使用 Agent 上报网卡，人工指定优先。"},
        "credentials": {"allowed": True, "reason": "验证新凭据后更新管理连接，不重启模型服务。"},
        "agent_url": {"allowed": not references.get("endpoint_protected"),
                      "reason": references.get("endpoint_protection_reason") or "切换管理入口需验证原执行宿主身份；Docker 还需验证同一 daemon，不改变模型访问地址。"},
    }


def invalidate_observation(node: dict, reason: str) -> dict:
    history = inventory_snapshot(node)
    if not (history.get("host_identity") or history.get("daemon_id")):
        history = inventory_snapshot(node.get("last_good_inventory") or {}) or history
    return {**node, "status": "unknown", "inventory_status": "partial", "daemon_id": "", "host_identity": "",
            "last_seen": "", "error": "", "last_sample_error": reason, "last_good_inventory": history,
            "collection_errors": [{"source": "configuration", "code": "observation_invalidated", "message": reason}],
            "observation_invalidated_at": now()}


def execution_identity(node: dict) -> dict:
    history = node.get("last_good_inventory") or {}
    history = history if isinstance(history, dict) else {}
    return {field: node.get(field) or history.get(field) for field in ("host_identity", "daemon_id")}
