"""Durable reference task coordination, consuming the selected service workflow.

CAS, idempotency, operation IDs, receipt handling and unknown outcomes retain
their native execution ordering. Read-only task APIs need no executor instance.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
import contextvars
import copy
import hashlib
import json
import re
import sqlite3
import sys
import time
from fastapi import HTTPException
from ..deployment_view import configuration_digest
from ..fleet import FleetError
from ..observability import context_fields, redact, request_context
from ..node_configuration import execution_node
from ..ports.services import ServiceCommandV1, TaskCoordinatorInputsV1
from ..store import now, uid
from ..version import BASELINE_PROFILE_ID
from .services_workflow import extra_manifest_digest


class ReferenceTaskCoordinator:
    def __init__(self, inputs: TaskCoordinatorInputsV1, *, store, fleet, logs, workflow=None, scheduler=None):
        self.inputs, self.store, self.fleet, self.logs = inputs, store, fleet, logs
        self.workflow, self.scheduler = workflow, scheduler

    def get(self, identifier):
        result = self.store.get("jobs", identifier)
        if result is None: raise HTTPException(404, "任务不存在")
        return result

    def list(self, service_id=None):
        return [job for job in self.store.list("jobs") if service_id is None or job.get("service_id") == service_id]

    def run(self, job_id: str):
        if self.workflow is None or self.scheduler is None:
            raise HTTPException(409, "此任务协调器未启用服务执行")
        job = self.store.get("jobs", job_id)
        if job is None: return
        with request_context(actor=job.get("actor", "system"), request_id=job.get("request_id")):
            try:
                self.logs.write("job.started", job_id=job_id, action=job["action"], service_id=job["service_id"])
                self.execute(job_id)
                result = self.store.get("jobs", job_id)
                self.logs.write("job.finished", severity="ERROR" if result["status"] == "failed" else "INFO", job_id=job_id,
                           action=result["action"], status=result["status"], error=result.get("error", ""))
            except Exception:
                self.inputs.mark_unhealthy()
                with self.inputs.mutation_lock:
                    current = self.store.get("jobs", job_id)
                    if current and current["status"] in {"queued", "running"}:
                        current.update(status="failed", finished_at=now(), error="平台任务日志不可用；任务已终止，请核对执行证据后重试")
                        service = self.store.get("services", current["service_id"])
                        if service:
                            service.update(status="error" if current.get("host_actions_attempted") else current.get("previous_status", "error"),
                                           last_error=current["error"], updated_at=now())
                            self.store.put("services", service)
                        self.store.put("jobs", current)
                print(json.dumps({"event": "job_log_unavailable", "job_id": job_id}), file=sys.stderr)
            finally:
                try: self.inputs.checkpoint()
                except Exception: self.inputs.mark_unhealthy()

    def execute(self, job_id: str):
        job = self.store.get("jobs", job_id)
        if job is None: return
        service = self.store.get("services", job["service_id"])
        if service is None: return
        action, nodes = job["action"], {value["id"]: execution_node(value) for value in self.store.list("nodes")}
        scaled_config = None
        try:
            job["status"] = "running"; self.store.put("jobs", job)
            if action in {"deploy", "rebuild"}:
                plan = self.workflow.plan(service, replacing=action == "rebuild")
                job["plan"] = plan; self.store.put("jobs", job)
                if not plan["valid"]: raise FleetError("预检未通过：" + "; ".join(item["detail"] for item in plan["checks"] if item["status"] == "fail")[:2000])
                if plan["extra_manifest_sha256"] and plan["extra_manifest_sha256"] != job.get("extra_manifests_approved_sha256"):
                    raise FleetError("附加清单已变化，请重新审查计划并确认摘要")
                if plan["extra_manifest_sha256"] and plan["configuration_digest"] != job.get("reviewed_configuration_digest"):
                    raise FleetError("服务配置已变化，请重新审查部署计划")
            if action == "scale":
                scale_plan = self.workflow.plan_scale(service, job["scale_counts"])
                job["scale_plan"] = scale_plan; self.store.put("jobs", job)
                scaled_config = scale_plan["user_config"]
            if self.inputs.mode == "demo":
                for step in job["steps"]:
                    step.update(status="running", message="演示任务执行中"); self.store.put("jobs", job)
                    time.sleep(0.2)
                    step.update(status="completed", message="演示操作已完成，未操作真实节点"); self.store.put("jobs", job)
                service["status"] = "stopped" if action == "stop" else "running"
            else:
                all_placements = self.workflow.execution_placements(service, action)
                placements = all_placements[:1] if service["runtime"] == "kubernetes" else all_placements
                if action not in {"deploy", "rebuild"}:
                    seen = set()
                    placements = [p for p in placements if not (p["node_id"] in seen or seen.add(p["node_id"]))]

                if action == "rebuild" and service["runtime"] == "docker":
                    # Explicitly authorized rebuild: all new checks pass before removing stopped resources.
                    for identifier in dict.fromkeys(p["node_id"] for p in service.get("deployed_placements", [])):
                        operation_id = f"{job_id}:cleanup:{identifier}:remove"
                        cleanup = {"node_id": identifier, "role": "cleanup", "status": "running", "operation_id": operation_id}
                        job.setdefault("cleanup_steps", []).append(cleanup)
                        job["host_actions_attempted"] = True
                        self.store.put("jobs", job)
                        try:
                            receipt = self.fleet.call(nodes[identifier], "POST", "/agent/remove", {"service_id": service["id"],
                                "motor_profile": service.get("deployed_motor_profile") or BASELINE_PROFILE_ID,
                                "node_id": identifier,
                                "operation_id": operation_id})
                            cleanup.update(status="completed", operation_status="completed", receipt=receipt)
                        except FleetError as error:
                            cleanup.update(status="interrupted" if error.outcome_unknown else "failed",
                                           operation_status=(error.receipt or {}).get("operation_status", "unknown"), message=str(error))
                            job["outcome_unknown"] = error.outcome_unknown
                            raise
                        finally:
                            self.store.put("jobs", job)

                def execute(index: int, placement: dict):
                    node = nodes[placement["node_id"]]
                    with self.inputs.mutation_lock:
                        job["steps"][index].update(status="running", message="已提交节点执行")
                        self.store.put("jobs", job)
                    prefix = "/agent/kubernetes" if service["runtime"] == "kubernetes" else "/agent"
                    operation = "deploy" if action == "rebuild" else "remove" if action == "undeploy" else action
                    with self.inputs.mutation_lock:
                        job["host_actions_attempted"] = True
                        self.store.put("jobs", job)
                    request_payload = self.workflow.payload(service, placement, nodes)
                    request_payload["console_provider_generation"] = job.get("provider_generation")
                    request_payload["console_provider_binding"] = job.get("provider_binding")
                    if action == "scale": request_payload = {"service_id": service["id"], "counts": job["scale_counts"], "motor_profile": service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID}
                    elif action not in {"deploy", "rebuild"}: request_payload["motor_profile"] = service.get("deployed_motor_profile") or service.get("motor_profile") or BASELINE_PROFILE_ID
                    if action in {"deploy", "rebuild"}:
                        request_payload["extra_manifests_approved_sha256"] = job.get("extra_manifests_approved_sha256", "")
                    if service["runtime"] == "docker":
                        request_payload["operation_id"] = f"{job_id}:{index}:{operation}"
                        with self.inputs.mutation_lock:
                            job["steps"][index].update(operation_id=request_payload["operation_id"], operation_status="submitting")
                            self.store.put("jobs", job)
                    try:
                        result = self.fleet.call(node, "POST", prefix + "/" + operation, request_payload, timeout=240)
                    except FleetError as error:
                        with self.inputs.mutation_lock:
                            if error.outcome_unknown: job["outcome_unknown"] = True
                            job["steps"][index].update(status="interrupted" if error.outcome_unknown else "failed",
                                operation_status=(error.receipt or {}).get("operation_status", "unknown" if error.outcome_unknown else "failed"),
                                message=redact(str(error))[:2000])
                            self.store.put("jobs", job)
                        raise
                    if result.get("status") in {"error", "failed"}: raise FleetError(result.get("message", "节点执行失败"))
                    if action == "scale" and result.get("user_config") != scaled_config:
                        raise FleetError("执行 Agent 返回的扩缩容配置与已校验计划不一致，请核对受管资源")
                    with self.inputs.mutation_lock:
                        job["steps"][index].update(status="completed", message=result.get("message", "节点执行完成"), resource_id=result.get("container_id", ""), runtime_status=result.get("status", "unknown"),
                                                  **({"operation_status": "completed"} if service["runtime"] == "docker" else {}),
                                                  agent_provider_generation=result.get("provider_generation"),
                                                  agent_provider_binding=result.get("provider_binding"))
                        self.store.put("jobs", job)

                management_roles = {"coordinator_controller", "coordinator", "controller", "coordinator,controller", "kv_store"}
                # Preserve dependencies and the Agent's non-queuing execution capacity.
                first = [i for i, p in enumerate(placements) if action in {"deploy", "rebuild"} and p.get("role") in management_roles]
                for index in first: execute(index, placements[index])
                remaining = [i for i in range(len(placements)) if i not in first]
                agent_groups = {}
                for index in remaining:
                    agent_groups.setdefault(placements[index]["node_id"], []).append(index)

                def execute_group(indices):
                    for offset, index in enumerate(indices):
                        try:
                            execute(index, placements[index])
                        except Exception as error:
                            with self.inputs.mutation_lock:
                                if job["steps"][index]["status"] != "interrupted":
                                    job["steps"][index].update(status="failed", message=redact(str(error))[:2000])
                                for pending_index in indices[offset + 1:]:
                                    if job["steps"][pending_index]["status"] == "pending":
                                        job["steps"][pending_index].update(status="cancelled",
                                            message="同一 Agent 的前序步骤失败或结果未确认；后续步骤未提交，请核对资源与回执")
                                self.store.put("jobs", job)
                            raise

                failures = []
                with ThreadPoolExecutor(max_workers=min(8, max(1, len(agent_groups)))) as node_pool:
                    futures = [node_pool.submit(contextvars.copy_context().run, execute_group, indices)
                               for indices in agent_groups.values()]
                    for future in as_completed(futures):
                        try: future.result()
                        except Exception as error:
                            failures.append(str(error))
                if failures: raise FleetError("; ".join(failures)[:3000])
                if action == "undeploy": service["status"] = "removing"
                elif action == "stop":
                    service["status"] = "stopping" if service["runtime"] == "kubernetes" and any(step.get("runtime_status") != "stopped" for step in job["steps"]) else "stopped"
                else: service["status"] = "starting"
            if action == "scale":
                completed_status = service["status"]
                fields = {key: service[key] for key in ("name", "runtime", "env", "placements")}
                fields["motor_profile"] = service.get("motor_profile", BASELINE_PROFILE_ID)
                service = self.workflow.render({**fields, "user_config": scaled_config, "extra_manifests": service.get("extra_manifests", [])}, service)
                service["status"] = completed_status
            if action in {"deploy", "rebuild", "scale"}:
                actual = service["placements"][:1] if service["runtime"] == "kubernetes" else service["placements"]
                service.update(deployed_placements=copy.deepcopy(actual), deployed_runtime=service["runtime"], deployed_digest=configuration_digest(service),
                               deployed_motor_profile=service.get("motor_profile") or BASELINE_PROFILE_ID,
                               deployed_user_config=copy.deepcopy(service["user_config"]), deployed_env=copy.deepcopy(service["env"]))
            service.update(updated_at=now(), last_error="", resources_may_exist=True,
                           needs_rebuild=action == "stop" and service["runtime"] == "docker",
                           has_unapplied_changes=bool(service.get("deployed_digest") and service["deployed_digest"] != configuration_digest(service)))
            if action == "undeploy" and self.inputs.mode == "demo":
                service.update(status="draft", resources_may_exist=False, deployed_placements=[], deployed_digest=None, has_unapplied_changes=False)
            job.update(status="completed", finished_at=now(), error="")
            self.store.mutate_many_audited([("services", service), ("jobs", job)],
                revision_keys={("services", service["id"])} if action == "scale" else set(),
                action=action, target=service["name"],
                detail="节点操作完成；业务就绪需独立探测" if self.inputs.mode != "demo" else "演示操作")
        except Exception as error:
            message = "平台状态或审计存储不可用，请核对任务执行证据" if isinstance(error, sqlite3.Error) else redact(str(error))[:4000]
            interrupted = bool(job.get("outcome_unknown") or getattr(error, "outcome_unknown", False))
            job.update(status="interrupted" if interrupted else "failed", error=message, finished_at=now())
            may_exist = service.get("resources_may_exist", False) or bool(job.get("host_actions_attempted"))
            failed_status = job.get("previous_status", "error") if action == "scale" and not job.get("host_actions_attempted") else "error"
            service.update(status=failed_status, last_error=message, updated_at=now(), resources_may_exist=may_exist)
            if job.get("host_actions_attempted"):
                previous_placements = service.get("deployed_placements", [])
                service["deployed_placements"] = list({json.dumps(p, sort_keys=True): p for p in [*previous_placements, *service["placements"]]}.values())
            for step in job["steps"]:
                if step["status"] == "running": step.update(status="interrupted" if interrupted else "failed", message=message[:2000])
            try:
                self.store.mutate_many_audited([("services", service), ("jobs", job)], action=action,
                                         target=service["name"], status="failed", detail=message[:2000])
            except Exception:
                # Accepted host effects cannot be rolled back by an audit fault.
                self.inputs.mark_unhealthy()
                job["audit_recorded"] = False
                self.store.put_many([("services", service), ("jobs", job)])
                try: self.logs.write("job.audit_unavailable", severity="ERROR", job_id=job["id"], error_type=type(error).__name__)
                except (OSError, RuntimeError, sqlite3.Error): pass

    def submit(self, identifier: str, action: str, command: ServiceCommandV1):
        if action not in {"deploy", "rebuild", "stop", "restart", "scale", "undeploy"}:
            raise HTTPException(422, "不支持的服务操作")
        if self.workflow is None or self.scheduler is None:
            raise HTTPException(409, "此任务协调器未启用服务执行")
        approval, scale, undeploy = command.approval, command.counts, command.confirmation_name
        with self.inputs.mutation_lock:
            service = self.workflow.get(identifier)
            key = command.idempotency_key
            command_hash = None
            fingerprint = None
            if key:
                if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", key): raise HTTPException(422, "Idempotency-Key 格式无效")
                command_hash = hashlib.sha256((context_fields()["actor"] + ":" + key).encode()).hexdigest()
                intent = {"service_id": identifier, "action": action,
                          "approval": approval if approval else None, "counts": scale if scale else None,
                          "confirmation": undeploy if undeploy else None}
                fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True).encode()).hexdigest()
                previous = self.store.get("idempotency", command_hash)
                if previous:
                    if previous["fingerprint"] != fingerprint: raise HTTPException(409, "此幂等键已用于不同操作，请核对原任务")
                    return {"job_id": previous["job_id"], "replayed": True}
            if service["status"] == "removing" and action != "undeploy":
                raise HTTPException(409, "受管资源正在卸载，请等待删除收敛后操作")
            if action == "undeploy":
                if service["runtime"] != "kubernetes" or not service.get("resources_may_exist"):
                    raise HTTPException(409, "仅能卸载已部署的 Kubernetes 受管资源")
                if undeploy is None or undeploy != service["name"]:
                    raise HTTPException(409, "请准确输入服务名称，确认卸载该服务的受管部署")
            if action == "scale":
                self.workflow.require_scalable(service)
                if scale is None: raise HTTPException(422, "缺少目标实例数")
            expected_hash = extra_manifest_digest(service) if action in {"deploy", "rebuild"} else None
            approved_hash = approval.get("extra_manifests_approved_sha256", "") if approval else ""
            if expected_hash and approved_hash != expected_hash:
                raise HTTPException(409, "请先查看 Kubernetes 部署计划，明确确认附加清单摘要")
            if expected_hash and (not approval or approval.get("reviewed_configuration_digest", "") != configuration_digest(service)):
                raise HTTPException(409, "已审查的服务配置不一致，请重新生成计划并确认")
            if action in {"restart", "stop"} and (not service.get("resources_may_exist") or not self.workflow.execution_placements(service, action)):
                raise HTTPException(409, "服务尚未部署，没有可操作的受管资源")
            if action == "stop" and not self.workflow.execution_placements(service, action):
                raise HTTPException(409, "没有可停止的执行目标")
            active_jobs = [job for job in self.store.list("jobs") if job["status"] in {"queued", "running"}]
            if any(job["service_id"] == identifier for job in active_jobs):
                raise HTTPException(409, "该服务已有执行中的任务")
            if len(active_jobs) >= 64:
                raise HTTPException(429, "平台部署任务队列已满，请等待现有任务完成", headers={"Retry-After": "10"})
            if action == "deploy" and service["status"] not in {"draft", "stopped", "error"}: raise HTTPException(409, "请先停止当前服务再部署")
            if action == "deploy" and service.get("resources_may_exist") and service["runtime"] == "docker": raise HTTPException(409, "已有受管容器，请使用明确的重建操作应用当前配置")
            if action == "rebuild" and service["status"] not in {"draft", "stopped"}: raise HTTPException(409, "重建前必须停止服务")
            if action == "restart" and service.get("deployed_digest") and configuration_digest(service) != service["deployed_digest"]: raise HTTPException(409, "有未应用配置；重启只使用旧容器修订，请选择重建并应用")
            if action == "restart" and service["runtime"] == "docker" and (service.get("needs_rebuild") or service["status"] == "stopped"):
                raise HTTPException(409, "Docker 服务已停止或需要重建；请重新预检资源并执行重建，不能跳过准入直接重启旧容器")
            source_placements = self.workflow.execution_placements(service, action)
            targets = source_placements[:1] if service["runtime"] == "kubernetes" else source_placements
            if action not in {"deploy", "rebuild"}:
                seen = set(); targets = [p for p in targets if not (p["node_id"] in seen or seen.add(p["node_id"]))]
            job = {"id": uid("job_"), "service_id": identifier, "action": action, "status": "queued", "created_at": now(), "error": "",
                   "execution_runtime": service["runtime"], "execution_placements": copy.deepcopy(targets),
                   "provider_generation": self.inputs.generation,
                   "provider_binding": self.inputs.runtime_binding(service["runtime"]),
                   **context_fields(),
                   "extra_manifests_approved_sha256": approved_hash,
                   "reviewed_configuration_digest": approval.get("reviewed_configuration_digest", "") if approval else "",
                   "scale_counts": scale if scale else None, "previous_status": service["status"],
                   "steps": [{"node_id": p["node_id"], "role": p.get("role", ""), "status": "pending", "message": "等待执行"} for p in targets]}
            service.update(status={"deploy": "deploying", "rebuild": "deploying", "stop": "stopping", "restart": "restarting", "scale": "scaling", "undeploy": "removing"}[action], updated_at=now(), last_job_id=job["id"])
            records = [("jobs", job), ("services", service)]
            if command_hash: records.append(("idempotency", {"id": command_hash, "fingerprint": fingerprint, "job_id": job["id"], "created_at": now()}))
            self.inputs.checkpoint(extra_pending=[job["id"]])
            self.store.mutate_many_audited(records, action="任务受理", target=service["name"], status="accepted",
                                     metadata={"job_id": job["id"], "action": action})
            try:
                self.scheduler.submit(self.run, job["id"])
            except Exception as error:
                # Return the durable accepted ID even if scheduling then fails.
                job.update(status="failed", error="任务调度不可用，未执行节点操作", finished_at=now())
                service.update(status=job["previous_status"], last_error=job["error"], updated_at=now())
                try:
                    self.store.mutate_many_audited([("jobs", job), ("services", service)], action="任务调度失败",
                                             target=service["name"], status="failed", detail=job["error"])
                except Exception:
                    self.inputs.mark_unhealthy()
                    job["audit_recorded"] = False
                    self.store.put_many([("jobs", job), ("services", service)])
                try: self.logs.write("job.schedule_failed", severity="ERROR", job_id=job["id"], error_type=type(error).__name__)
                except (OSError, RuntimeError, sqlite3.Error): self.inputs.mark_unhealthy()
            return {"job_id": job["id"]}


def factory(ctx):
    from ..provider_host.defaults import AuditedStore
    inputs = ctx.require("coordinator_inputs")
    store = AuditedStore(ctx.consume("storage.store"), ctx.consume("audit.sink"))
    workflow = ctx.consume("services.workflow", workflow_inputs=inputs.workflow_inputs) if inputs.execution_enabled else None
    scheduler = ctx.consume("tasks.scheduler") if inputs.execution_enabled else None
    ctx.consume("tasks.recovery")(store)
    return ReferenceTaskCoordinator(inputs,
        store=store,
        fleet=ctx.consume("agent.connection"), logs=ctx.consume("logs.platform"),
        workflow=workflow, scheduler=scheduler)
