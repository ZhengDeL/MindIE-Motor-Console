"""Reference Motor service lifecycle and plans, behind services.workflow v1.

This module owns service state transitions and preserves the deployed Motor JSON.
It never creates a scheduler, observer or background worker.
"""
from __future__ import annotations
import copy
import hashlib
import json
import sqlite3
from fastapi import HTTPException
from ..catalog import summarize
from ..deployment_view import configuration_digest
from ..fleet import FleetError, endpoint
from ..node_configuration import execution_node
from ..ports.services import ServiceDeletionV1, ServiceWorkflowInputsV1
from ..store import now, uid
from ..version import BASELINE_PROFILE_ID

def extra_manifest_digest(service: dict) -> str | None:
    documents = service.get("extra_manifests") or []
    return hashlib.sha256(json.dumps(documents, sort_keys=True).encode()).hexdigest() if documents else None

class ReferenceServiceWorkflow:
    def __init__(self, inputs: ServiceWorkflowInputsV1, *, store, fleet, native, logs):
        self.inputs, self.store, self.fleet, self.native, self.logs = inputs, store, fleet, native, logs

    def list(self):
        return self.store.list("services")

    def get(self, identifier: str) -> dict:
        result = self.store.get("services", identifier)
        if result is None: raise HTTPException(404, "服务不存在")
        return result

    def node(self, identifier: str) -> dict:
        result = self.store.get("nodes", identifier)
        if result is None: raise HTTPException(404, "节点不存在")
        return result

    def node_map(self): return {value["id"]: execution_node(value) for value in self.store.list("nodes")}

    def reconcile(self, service: dict) -> dict:
        if (self.inputs.mode != "demo" and service["runtime"] == "docker" and service["status"] == "removing"
                and service.get("deletion_requested_at") and service.get("deletion_operation_id")):
            placements = self.execution_placements(service, "remove")
            if not placements: return service
            seen = set()
            try:
                for placement in placements:
                    identifier = placement["node_id"]
                    if identifier in seen: continue
                    seen.add(identifier)
                    node = self.store.get("nodes", identifier)
                    if node is None: return service
                    operation = service["deletion_operation_id"] + "-" + identifier
                    receipt = self.fleet.call(execution_node(node), "GET", "/agent/operations/" + operation, timeout=5)
                    if (receipt.get("operation_id") != operation or receipt.get("action") != "remove"
                            or receipt.get("service_id") != service["id"] or receipt.get("node_id") != identifier
                            or receipt.get("operation_status") != "completed" or receipt.get("status") != "removed"
                            or receipt.get("resources_may_exist") is not False):
                        return service
            except FleetError:
                return service
            with self.inputs.mutation_lock:
                latest = self.store.get("services", service["id"])
                if (latest and latest.get("status") == "removing"
                        and latest.get("deletion_operation_id") == service["deletion_operation_id"]
                        and latest.get("updated_at") == service.get("updated_at")):
                    tombstone = {**latest, "deleted_at": now(), "resources_may_exist": False, "removal_converged": True}
                    self.store.mutate_many_audited([("deleted_services", tombstone)], deletes=[("services", latest["id"])],
                        action="删除服务完成", target=latest["name"], detail="所有执行节点已返回匹配的删除完成回执；未重放删除操作")
                    return {**latest, "deleted": True, "status": "removed"}
            return service
        if self.inputs.mode == "demo" or service["runtime"] != "kubernetes" or service["status"] not in {"stopping", "removing"}: return service
        candidates = self.execution_placements(service, "stop")
        if not candidates: return service
        node = self.store.get("nodes", candidates[0]["node_id"])
        if not node: return service
        try:
            state = self.fleet.call(node, "GET", "/agent/kubernetes/status?service_id=" + service["id"], timeout=5)
            removed = (service["status"] == "removing" and state.get("status") == "removed"
                       and state.get("removal_converged") is True and not state.get("pending_deletions"))
            if service["status"] == "stopping" and state.get("status") == "stopped" or removed:
                with self.inputs.mutation_lock:
                    latest = self.store.get("services", service["id"])
                    if latest and latest["status"] == service["status"] and latest["updated_at"] == service["updated_at"]:
                        if removed and latest.get("deletion_requested_at"):
                            self.store.mutate_many_audited([("deleted_services", {**latest, "deleted_at": now(),
                                                        "removal_converged": True})],
                                deletes=[("services", latest["id"])], action="删除服务完成", target=latest["name"],
                                detail="Agent 已确认受管 UID、资源和 Pod 全部删除，归档服务记录")
                            return {**latest, "deleted": True, "status": "removed"}
                        latest.update(status="draft" if removed else "stopped", updated_at=now())
                        if removed: latest.update(resources_may_exist=False, deployed_placements=[], needs_rebuild=False, has_unapplied_changes=False, deployed_digest=None)
                        self.store.put("services", latest)
                        return latest
        except FleetError: pass
        return service

    def execution_placements(self, service: dict, action: str):
        placements = service.get("deployed_placements", service["placements"]) if action in {"stop", "restart", "remove", "undeploy"} else service["placements"]
        return placements[:1] if service["runtime"] == "kubernetes" else placements

    def check_not_active(self, service: dict):
        if service["status"] in {"running", "deploying", "starting", "stopping", "partial", "restarting", "scaling", "removing"}:
            raise HTTPException(409, "请先停止服务，避免修改正在使用的配置或删除运行记录")
        if service["status"] == "error" and service.get("resources_may_exist"):
            raise HTTPException(409, "失败任务可能已创建部分资源，请先停止并核对资源")

    def render(self, data: dict, previous: dict | None = None):
        self.inputs.require_runtime(data["runtime"])
        values = copy.deepcopy(data)
        values.pop("expected_revision", None)
        if values["runtime"] != "kubernetes" and values["extra_manifests"]:
            raise HTTPException(422, "附加 Kubernetes 清单只能用于 Kubernetes 服务")
        config = values["user_config"]
        if not isinstance(config.get("motor_deploy_config"), dict): raise HTTPException(422, "配置缺少 motor_deploy_config 对象")
        try: summary = summarize(config)
        except (ValueError, TypeError) as error: raise HTTPException(422, "实例数量格式无效") from error
        nodes = self.node_map()
        profiles = {nodes[p["node_id"]].get("agent_metadata", {}).get("motor_profile", {}).get("profile_id") for p in values["placements"] if p.get("node_id") in nodes}
        profiles.discard(None)
        if len(profiles) > 1: raise HTTPException(422, "同一服务的执行节点必须使用一致的 Motor 适配配置")
        values["motor_profile"] = values["motor_profile"] or (next(iter(profiles)) if profiles else self.native.public_profile()["profile_id"])
        if profiles and values["motor_profile"] not in profiles: raise HTTPException(409, "服务绑定的 Motor 适配配置与所选执行节点不一致")
        if values["runtime"] == "kubernetes" and len(values["placements"]) != 1:
            raise HTTPException(422, "Kubernetes 服务必须选择且仅选择一个执行 Agent；Pod 布局由原生 Motor 调度配置决定")
        for placement in values["placements"]:
            if placement.get("node_id") not in nodes: raise HTTPException(422, "部署布局引用了不存在的节点")
            if not isinstance(placement.get("devices", []), list): raise HTTPException(422, "设备编号必须为列表")
        stamp = now()
        requested_devices = sum(len(p.get("devices", [])) for p in values["placements"])
        if values["runtime"] == "kubernetes":
            deploy = config["motor_deploy_config"]
            try:
                requested_devices = sum(int(deploy.get(f"{r}_instances_num", 0) or 0) * int(deploy.get(f"single_{r}_instance_pod_num", 1) or 1) * int(deploy.get(f"{r}_pod_npu_num", 0) or 0) for r in ("p", "d", "e", "hybrid"))
            except (ValueError, TypeError) as error:
                raise HTTPException(422, "Kubernetes 配置中的实例数或申请卡数无效") from error
        service = {"id": previous["id"] if previous else uid("svc_"), **values, **summary,
                   "status": "draft", "created_at": previous["created_at"] if previous else stamp,
                   "updated_at": stamp, "last_error": "", "node_names": list(dict.fromkeys(nodes[p["node_id"]]["name"] for p in values["placements"])),
                   "device_count": requested_devices, "endpoint": "", "demo": self.inputs.mode == "demo"}
        if previous:
            for key in ("deployed_placements", "deployed_runtime", "deployed_digest", "deployed_motor_profile",
                        "deployed_user_config", "deployed_env", "config_revision", "resources_may_exist", "last_job_id"):
                if key in previous: service[key] = previous[key]
        service["has_unapplied_changes"] = bool(service.get("deployed_digest") and service["deployed_digest"] != configuration_digest(service))
        service["needs_rebuild"] = bool(service.get("resources_may_exist") and service["runtime"] == "docker")
        try: service["endpoint"] = endpoint(service, nodes)
        except (ValueError, FleetError): pass
        return service

    def payload(self, service: dict, placement: dict, nodes: dict[str, dict]):
        node = nodes[placement["node_id"]]
        management = next((nodes[p["node_id"]] for p in service["placements"] if p["role"] in {"coordinator_controller", "coordinator", "coordinator,controller", "single_container"}), node)
        return {"service_id": service["id"], "service_name": service["name"], "revision_id": service.get("revision_id", service["updated_at"]),
                "user_config": copy.deepcopy(service["user_config"]), "env": copy.deepcopy(service["env"]), "placement": copy.deepcopy(placement),
                "extra_manifests": copy.deepcopy(service.get("extra_manifests", [])),
                "motor_profile": service.get("motor_profile") or BASELINE_PROFILE_ID,
                "full_placements": copy.deepcopy(service["placements"]),
                "placement_nodes": {identifier: {key: value.get(key, "") for key in ("id", "address", "nic_name")} for identifier, value in nodes.items() if identifier in {p["node_id"] for p in service["placements"]}},
                "node": {"id": node["id"], "name": node["name"], "address": node["address"], "nic_name": node.get("nic_name", "")},
                "management": {"address": management["address"]}}

    def plan(self, service: dict, *, replacing: bool = False):
        checks, commands, warnings, manifests = [], [], [], []
        nodes = self.node_map()
        placements = service["placements"]
        if not placements: checks.append({"name": "部署布局", "status": "fail", "detail": "请至少选择一个执行节点并填写角色布局"})
        if service["runtime"] == "docker" and (service.get("motor_profile") or BASELINE_PROFILE_ID) == self.native.public_profile()["profile_id"]:
            try:
                checks.extend(self.native.validate_layout(service["user_config"], placements, env=service["env"], nodes=nodes))
            except ImportError:
                checks.append({"name": "Motor 适配器", "status": "fail", "detail": "当前构建缺少 Docker 适配器"})
            except (ValueError, TypeError, KeyError) as error:
                checks.append({"name": "Motor 配置", "status": "fail", "detail": str(error)})
        target_placements = placements[:1] if service["runtime"] == "kubernetes" else placements
        for placement in target_placements:
            if placement.get("node_id") not in nodes:
                checks.append({"name": "目标节点", "status": "fail", "detail": "目标节点不存在"}); continue
            node = nodes[placement["node_id"]]
            if self.inputs.mode == "demo":
                checks.append({"name": node["name"] + " · 模拟预检", "status": "pass", "detail": "仅演示流程，未检查实际镜像、设备或集群"})
                commands.append({"node_id": node["id"], "role": placement.get("role", ""), "command": "# 演示环境：真实环境将由固定 Motor 适配器生成计划"})
                if service["runtime"] == "kubernetes":
                    manifests.append({"node_id": node["id"], "namespace": "演示命名空间", "resources": [],
                                      "manifest_yaml": "# 演示预览：以下仅为附加对象；未生成真实集群的原生清单\n" + json.dumps(service.get("extra_manifests", []), ensure_ascii=False, indent=2),
                                      "extra_manifest_sha256": extra_manifest_digest(service)})
                continue
            path = "/agent/kubernetes/plan" if service["runtime"] == "kubernetes" else "/agent/preflight"
            try:
                request_payload = self.payload(service, placement, nodes)
                if replacing: request_payload["allow_owned_stopped"] = True
                result = self.fleet.call(node, "POST", path, request_payload, timeout=120)
                checks.extend({**item, "name": node["name"] + " · " + str(item.get("name", "预检"))} for item in result.get("checks", []))
                if not result.get("valid") and not any(item.get("status") == "fail" for item in result.get("checks", [])):
                    checks.append({"name": node["name"], "status": "fail", "detail": "节点预检未通过"})
                warnings.extend(result.get("warnings", []))
                commands.append({"node_id": node["id"], "role": placement.get("role", ""), "command": result.get("command", "")})
                if service["runtime"] == "kubernetes":
                    manifests.append({"node_id": node["id"], **{key: result.get(key) for key in ("namespace", "resources", "manifest_yaml", "extra_manifest_sha256")}})
                    if extra_manifest_digest(service) != result.get("extra_manifest_sha256"):
                        checks.append({"name": node["name"] + " · 附加清单摘要", "status": "fail", "detail": "执行 Agent 返回的附加清单摘要与当前配置不一致"})
            except FleetError as error:
                checks.append({"name": node["name"] + " · Agent", "status": "fail", "detail": str(error)})
        if self.inputs.mode == "demo": warnings.append("当前为演示环境；通过仅表示模拟流程，不构成部署验收。")
        return {"valid": bool(placements) and not any(item.get("status") == "fail" for item in checks), "checks": checks, "commands": commands, "warnings": warnings,
                "manifests": manifests, "extra_manifest_sha256": extra_manifest_digest(service), "configuration_digest": configuration_digest(service)}

    def require_scalable(self, service: dict):
        if service["runtime"] != "kubernetes": raise HTTPException(409, "在线扩缩容仅支持 Kubernetes 原生实例组")
        if service["status"] not in {"running", "starting", "partial"} or not service.get("resources_may_exist"):
            raise HTTPException(409, "请先部署 Kubernetes 服务，且不能同时执行其他操作")

    def plan_scale(self, service: dict, counts: dict):
        candidate = copy.deepcopy(service["user_config"])
        candidate["motor_deploy_config"].update(counts)
        if self.inputs.mode == "demo":
            return {"valid": True, "demo": True, "counts": counts, "user_config": candidate,
                    "message": "演示扩缩容计划；真实环境仅支持原生受管 InferServiceSet，不能与自动扩缩容竞争"}
        placements = self.execution_placements(service, "restart")
        if len(placements) != 1: raise FleetError("缺少唯一 Kubernetes 执行 Agent")
        node = self.node(placements[0]["node_id"])
        result = self.fleet.call(node, "POST", "/agent/kubernetes/scale", {"service_id": service["id"], "counts": counts, "dry_run": True,
                            "motor_profile": service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID}, timeout=120)
        if result.get("user_config") != candidate: raise FleetError("执行 Agent 的配置基线与控制面不一致，未执行扩缩容")
        return result

    def create(self, data: dict):
        with self.inputs.mutation_lock:
            if any(s["name"] == data["name"] for s in self.store.list("services")): raise HTTPException(409, "服务名称已存在")
            result = self.render(data)
            self.store.mutate_audited("services", result["id"], result, revision=True,
                                 action="创建服务", target=result["name"], detail="保存草稿，尚未执行部署")
            return result

    def update(self, identifier: str, data: dict):
        with self.inputs.mutation_lock:
            if data.get("expected_revision") is None:
                raise HTTPException(422, "保存配置必须提供 expected_revision，请先读取当前服务修订")
            previous = self.get(identifier); self.check_not_active(previous)
            if previous.get("resources_may_exist") and previous["runtime"] != data["runtime"]: raise HTTPException(409, "切换运行时前请先移除原运行时资源，再创建新服务")
            if any(s["name"] == data["name"] and s["id"] != identifier for s in self.store.list("services")): raise HTTPException(409, "服务名称已存在")
            result = self.render(data, previous)
            self.store.mutate_audited("services", result["id"], result, revision=True, expected_revision=data.get("expected_revision"),
                                 action="更新配置", target=result["name"], detail="已保存新修订，尚未应用")
            return result

    def delete(self, identifier: str):
        with self.inputs.mutation_lock:
            service = self.get(identifier)
            if not (service["status"] == "removing" and service.get("deletion_requested_at")):
                self.check_not_active(service)
            if self.inputs.mode != "demo" and service.get("resources_may_exist"):
                if not self.execution_placements(service, "remove"):
                    raise HTTPException(409, "服务可能仍有资源，但缺少可验证的执行布局；保留记录，请先核对原任务与节点")
                service.update(status="removing", updated_at=now(),
                               deletion_requested_at=service.get("deletion_requested_at") or now(),
                               deletion_operation_id=service.get("deletion_operation_id") or uid("delete_"))
                self.inputs.checkpoint(extra_pending=[service["deletion_operation_id"]])
                self.store.mutate_audited("services", identifier, service, action="删除服务受理", target=service["name"],
                                     status="accepted", detail="先持久记录删除意图，受管资源收敛前保留服务与节点引用")
                seen = set()
                pending = False
                try:
                    for placement in self.execution_placements(service, "remove"):
                        if placement["node_id"] in seen: continue
                        seen.add(placement["node_id"])
                        node = self.node(placement["node_id"])
                        path = "/agent/kubernetes/remove" if service["runtime"] == "kubernetes" else "/agent/remove"
                        result = self.fleet.call(node, "POST", path, {"service_id": identifier, "node_id": node["id"],
                            "operation_id": service["deletion_operation_id"] + "-" + node["id"],
                            "motor_profile": service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID})
                        if service["runtime"] == "kubernetes":
                            pending = pending or result.get("removal_converged") is not True
                        else:
                            pending = pending or bool(result.get("resources_may_exist")) or result.get("status") != "removed"
                except Exception:
                    try: self.logs.write("service.delete_external_failed", severity="ERROR", service_id=identifier, attempted_node_ids=sorted(seen))
                    except (OSError, RuntimeError, sqlite3.Error): self.inputs.mark_unhealthy()
                    raise
                if pending:
                    return ServiceDeletionV1({"ok": False, "status": "removing", "resources_may_exist": True,
                                         "detail": "删除已受理，等待受管资源与 Pod 收敛；服务记录和引用继续保留"}, status_code=202)
            try:
                self.store.mutate_many_audited([("deleted_services", {**service, "deleted_at": now()})],
                    deletes=[("services", identifier)], action="删除服务", target=service["name"],
                    detail="删除受管资源请求已提交；修订和审计保留，不删除权重")
            except Exception:
                try: self.logs.write("service.delete_uncommitted", severity="ERROR", service_id=identifier,
                                external_effects_possible=bool(service.get("resources_may_exist")))
                except (OSError, RuntimeError, sqlite3.Error): self.inputs.mark_unhealthy()
                raise
            return ServiceDeletionV1({"ok": True})

    def export(self, identifier: str):
        service = self.get(identifier)
        return {**{key: service[key] for key in ("name", "runtime", "user_config", "env", "placements")},
                "extra_manifests": service.get("extra_manifests", []), "motor_profile": service.get("motor_profile") or BASELINE_PROFILE_ID}

    def revisions(self, identifier: str):
        self.get(identifier); return self.store.revisions(identifier)


def factory(ctx):
    from ..provider_host.defaults import AuditedStore
    return ReferenceServiceWorkflow(ctx.require("workflow_inputs"),
        store=AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink")),
        fleet=ctx.consume("agent.connection"), native=ctx.consume("motor.native"),
        logs=ctx.consume("logs.platform"))
