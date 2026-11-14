"""Audited, single-worker SSH installation of new execution Agents.

Native Motor deployments remain the responsibility of the existing adapters.
This module accepts no shell commands, local paths, or persisted SSH secrets.
An interrupted external operation is reconciled, never automatically replayed.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .agent_metadata_projection import AGENT_META_BYTES, project_agent_metadata
from .agent_rollout_artifacts import ArtifactError, ArtifactInventory
from .agent_rollout_models import validate_install_root, ExecuteInput, HostKeyInput, PlanInput, RecoverInput, SSHConnection, StrictInput
from .agent_rollout_ssh import RolloutError, SSHMaterial, SSHSession, discover_host_key
from .agent_trust import validate_agent_ca
from .observability import context_fields, request_context
from .store import now, uid
from .node_configuration import invalidate_observation, nic_configuration
from .version import ADAPTER_DRIVER, AGENT_PROTOCOL_VERSION, BASELINE_MOTOR_COMMIT, BASELINE_PROFILE_ID, PRODUCT_ID


PLANS = "agent_rollout_plans"
JOBS = "agent_rollout_jobs"
ACTIVE = {"queued", "running"}
STEPS = (("preflight", "复核目标与安装计划"), ("stage", "创建专属安装目录"),
         ("image", "校验并准备固定版本镜像"), ("prepare", "生成原生 Agent 配置与证书"),
         ("start", "启动 Agent 容器"), ("connect", "校验 TLS、协议与接入节点"))
_ID = re.compile(r"(?:plan|rollout)_[0-9a-f]{32}\Z")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _text(value, maximum=512):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise RolloutError("installer_response_invalid")
    return value


def _remote_projection(value):
    """Persist only the documented public probe contract, never remote output."""
    identity = value.get("identity")
    if not isinstance(identity, dict):
        raise RolloutError("installer_response_invalid")
    projected = {key: _text(identity.get(key), bound) for key, bound in
                 (("boot_id_sha256", 64), ("docker_id", 200), ("architecture", 16), ("docker_root", 512))}
    if not re.fullmatch(r"[0-9a-f]{64}", projected["boot_id_sha256"]) or projected["architecture"] not in {"arm64", "amd64"}:
        raise RolloutError("installer_response_invalid")
    existing = value.get("existing_installation")
    if not isinstance(existing, list) or len(existing) > 512:
        raise RolloutError("installer_response_invalid")
    entries = []
    for item in existing:
        if not isinstance(item, dict) or type(item.get("running")) is not bool:
            raise RolloutError("installer_response_invalid")
        entries.append({"container_id": _text(item.get("id"), 64), "name": _text(item.get("name"), 100),
                        "image_id": _text(item.get("image_id"), 80), "state": "running" if item["running"] else "stopped",
                        "runtime": _text(item.get("runtime", ""), 16), "project": _text(item.get("project", ""), 100)})
    result = {"identity": projected, "architecture": projected["architecture"], "existing_installation": entries}
    for key in ("installation_path_exists", "host_reservation_exists", "agent_port_available", "can_install"):
        if type(value.get(key)) is not bool:
            raise RolloutError("installer_response_invalid")
        result[key] = value[key]
    for key in ("docker_version", "compose_version", "installation_dir"):
        result[key] = _text(value.get(key), 512)
    return result


class AgentRolloutManager:
    def __init__(self, store, fleet, mutation_lock, *, mode="control"):
        self.store, self.fleet, self.mutation_lock, self.mode = store, fleet, mutation_lock, mode
        self.artifacts = ArtifactInventory(store.directory / "rollout-artifacts")
        # Deployment-owned configuration; clients cannot choose filesystem roots.
        self.install_root = os.environ.get("MOTOR_AGENT_ROLLOUT_ROOT", "/var/lib/motor-console-agents")
        self.install_root = validate_install_root(self.install_root)
        root = Path(__file__).resolve().parents[1]
        self.program = (root / "scripts/agent_rollout_remote.py").read_text(encoding="utf-8")
        assets = root / "rollout-assets" if (root / "rollout-assets").is_dir() else root
        self.materials = {name: (assets / name).read_text(encoding="utf-8") for name in
                          ("compose.docker-agent.yaml", "compose.kubernetes-agent.yaml")}
        self.materials_digest = _digest({"program": self.program, "compose": self.materials, "root": self.install_root})
        self._guard = threading.RLock()
        self._preflights = threading.BoundedSemaphore(2)
        self._materials, self._events, self._futures, self._sessions = {}, {}, {}, {}
        self._closed = False
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-rollout")
        self._recover_interrupted()

    def _available(self):
        if self.mode != "control":
            raise RolloutError("rollout_requires_control_mode")
        if self._closed:
            raise RolloutError("rollout_manager_stopping")
        if (self.store.directory / "maintenance.json").exists():
            raise RolloutError("console_in_maintenance")

    def _recover_interrupted(self):
        # No credentials survive a restart, and an SSH command may have outlived
        # its connection. Preserve uncertainty and require explicit observation.
        for original in self.store.list(JOBS):
            if original.get("status") not in ACTIVE:
                continue
            value = {**original, "status": "interrupted", "updated_at": now(), "error_code": "control_restarted",
                     "resources_may_exist": original.get("resources_may_exist", False) or original.get("stage") != "preflight"}
            for step in value.get("steps", []):
                if step.get("status") == "running":
                    step.update(status="failed", message="控制面已重启，远端操作结果需要核验", finished_at=now())
            self.store.mutate_audited(JOBS, value["id"], value, action="恢复 Agent 安装任务", target=value["id"],
                                      status="interrupted", detail="未重放 SSH 命令；保留已有资源供管理员核验")

    def public_job(self, value):
        allowed = {"id", "plan_id", "name", "runtime", "address", "status", "stage", "steps", "created_at", "updated_at",
                   "error_code", "resources_may_exist", "agent_url", "node_id", "connection_verified", "rollback"}
        result = {key: item for key, item in value.items() if key in allowed}
        result["can_connect"] = bool(value.get("_agent_token") and value.get("_start_attempted") and
                                     value.get("status") not in ACTIVE and value.get("status") != "completed" and
                                     (value.get("_agent_ca_pem") or value.get("_spec", {}).get("tls_mode") == "existing_https"))
        result["can_cancel"] = value.get("status") in ACTIVE and not value.get("_cancel_requested", False)
        result["can_recover"] = bool(value.get("resources_may_exist") and value.get("status") not in ACTIVE | {"completed"}
                                     and value["id"] not in self._events)
        result["connection"] = value.get("_connection", {})
        return result

    @staticmethod
    def public_plan(value):
        return {key: item for key, item in value.items() if not key.startswith("_")}

    def get_job(self, identifier):
        if not _ID.fullmatch(identifier):
            raise RolloutError("rollout_not_found")
        value = self.store.get(JOBS, identifier)
        if value is None:
            raise RolloutError("rollout_not_found")
        return value

    def list_jobs(self, limit=50):
        return {"items": [self.public_job(value) for value in self.store.list(JOBS)[:limit]]}

    def _payload(self, value, action):
        return {"action": action, "spec": value["_spec"], "operation_id": value["id"], "install_root": self.install_root,
                "expected_identity": value["_remote_identity"]}

    def create_plan(self, data: PlanInput):
        self._available()
        if not self._preflights.acquire(blocking=False):
            raise RolloutError("preflight_busy")
        material = SSHMaterial(data.credentials)
        try:
            spec, connection = data.spec.model_dump(), data.connection.model_dump()
            if connection["privilege"] == "root" and connection["username"] != "root":
                raise RolloutError("root_login_requires_root_user")
            artifact = self.artifacts.metadata(spec["artifact_id"]) if spec["image_source"] == "artifact" else None
            operation = uid("rollout_")
            with SSHSession(data.connection, material) as session:
                remote = _remote_projection(session.rpc(self.program, {"action": "probe", "spec": spec,
                    "operation_id": operation, "install_root": self.install_root}, timeout=90))
            matches = [n for n in self.store.list("nodes") if n.get("address") == spec["address"] or n.get("agent_url") == spec["agent_url"]]
            exact = matches[0] if len(matches) == 1 and matches[0].get("address") == spec["address"] and matches[0].get("agent_url") == spec["agent_url"] else None
            conflict = bool(matches) and exact is None
            checks = []
            def check(identifier, title, passed, message, *, warning=False):
                checks.append({"id": identifier, "title": title, "status": "passed" if passed else "warning" if warning else "failed", "message": message})
            check("ssh", "SSH 身份与固定授权", True, "已先核对主机指纹，再验证登录身份及实际安装权限")
            check("runtime", "容器引擎与 Compose", True, "已有 Docker 与 Compose；不会安装引擎或修改 daemon 配置")
            check("existing", "既有 Agent 保护", not remote["existing_installation"], "仅允许全新安装，既有 Agent 不会被覆盖、重启或升级")
            check("directory", "专属目录与并发保护", not remote["installation_path_exists"] and not remote["host_reservation_exists"], "目录必须不存在，且该主机没有未核验的安装保留锁")
            check("port", "监听地址与端口", remote["agent_port_available"], "已只读核验指定地址可绑定，执行前将再次核验")
            check("registration", "控制台节点标识", not conflict, "同一地址与入口的既有节点保留 ID；冲突地址不自动替换")
            if artifact:
                check("artifact", "离线镜像架构", artifact["architecture"] == remote["architecture"], "执行时将流式校验完整 SHA256 与镜像 ID；元数据不是发布签名")
            else:
                check("image", "固定摘要镜像", True, "按指定 digest 拉取；启动前在隔离容器内验证版本、协议与原生 Motor 完整性")
            check("tls", "接入前安全校验", False, "安装后仍须通过真实 TLS、Token、协议与 Motor 配置绑定校验", warning=True)
            stamp = now()
            plan = {"id": uid("plan_"), "created_at": stamp, "expires_at": (datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(),
                    "spec": spec, "connection": connection, "checks": checks, "remote": remote,
                    "steps": [{"id": identifier, "title": title, "status": "pending"} for identifier, title in STEPS],
                    "existing_installation": remote["existing_installation"],
                    "can_execute": remote["can_install"] and all(c["status"] != "failed" for c in checks),
                    "_owner": context_fields()["actor"], "_operation_id": operation, "_artifact": artifact,
                    "_registered_node": exact, "_materials_digest": self.materials_digest, "_consumed": False}
            plan["digest"] = _digest(plan)
            # Bound durable preflight history. No secrets were added to the plan.
            stale = [(PLANS, p["id"]) for p in self.store.list(PLANS)[99:]]
            self.store.mutate_many_audited([(PLANS, plan)], deletes=stale, action="预检 Agent 推送", target=plan["id"],
                                            metadata={"runtime": spec["runtime"], "can_execute": plan["can_execute"]})
            return self.public_plan(plan)
        finally:
            material.clear()
            self._preflights.release()

    def execute(self, identifier, data: ExecuteInput):
        self._available()
        with self._guard, self.mutation_lock:
            self._available()
            plan = self.store.get(PLANS, identifier) if _ID.fullmatch(identifier) else None
            if not plan:
                raise RolloutError("plan_not_found")
            if plan.get("_owner") != context_fields()["actor"]:
                raise RolloutError("plan_actor_mismatch")
            if datetime.fromisoformat(plan["expires_at"]) <= datetime.now(timezone.utc):
                raise RolloutError("plan_expired")
            if (not hmac.compare_digest(data.plan_digest, plan["digest"]) or plan.get("_materials_digest") != self.materials_digest
                    or plan.get("_consumed") or not plan.get("can_execute") or data.confirmation_name != plan["spec"]["name"]):
                raise RolloutError("plan_changed_or_consumed")
            if _digest({key: value for key, value in plan.items() if key != "digest"}) != plan["digest"]:
                raise RolloutError("plan_changed_or_consumed")
            active = [job for job in self.store.list(JOBS) if job.get("status") in ACTIVE]
            if len(active) >= 2:
                raise RolloutError("rollout_queue_full")
            if any(j.get("address") == plan["spec"]["address"] or j.get("_connection", {}).get("host") == plan["connection"]["host"] for j in active):
                raise RolloutError("host_installation_busy")
            if plan["_artifact"] and self.artifacts.metadata(plan["_artifact"]["id"]) != plan["_artifact"]:
                raise RolloutError("artifact_changed_after_plan")
            spec = plan["spec"]
            job = {"id": plan["_operation_id"], "plan_id": plan["id"], "name": spec["name"], "runtime": spec["runtime"],
                   "address": spec["address"], "agent_url": spec["agent_url"], "created_at": now(), "updated_at": now(),
                   "status": "queued", "stage": "preflight", "steps": plan["steps"], "resources_may_exist": False,
                   "connection_verified": False, "rollback": {"automatic": False, "existing_resources_changed": False,
                       "policy": "仅创建专属目录与新 Agent；异常时保留现场，不自动删除目录、镜像或容器"},
                   "_owner": plan["_owner"], "_request_id": context_fields()["request_id"], "_spec": spec,
                   "_connection": plan["connection"], "_remote_identity": plan["remote"]["identity"],
                   "_artifact": plan["_artifact"], "_registered_node": plan["_registered_node"],
                   "_agent_token": self.fleet.secrets.encrypt(secrets.token_urlsafe(48)), "_cancel_requested": False}
            material = SSHMaterial(data.credentials)
            try:
                # Compare-and-consume inside the same database unit of work.
                with self.store.unit_of_work(write=True) as db:
                    row = db.resources.get(PLANS,identifier)
                    if row != plan:
                        raise RolloutError("plan_changed_or_consumed")
                    db.resources.put(PLANS, {**plan, "_consumed": True})
                    db.resources.put(JOBS, job)
                    self.store.audit("执行 Agent 推送", job["id"], "accepted", "凭据仅在当前进程内存；重启不自动重放", connection=db)
                self._materials[job["id"]] = material
                self._events[job["id"]] = threading.Event()
                self._futures[job["id"]] = self._pool.submit(self._run, job["id"])
            except Exception:
                material.clear()
                raise
            return {"job_id": job["id"]}

    def _save(self, value, *, action="Agent 安装阶段", audit_status="success"):
        with self._guard:
            current = self.store.get(JOBS, value["id"])
            if current and current.get("_cancel_requested"):
                value["_cancel_requested"] = True
            value["updated_at"] = now()
            self.store.mutate_audited(JOBS, value["id"], value, action=action, target=value["id"], status=audit_status,
                                      metadata={"stage": value["stage"], "status": value["status"], "resources_may_exist": value["resources_may_exist"]})

    def _step(self, job, identifier, operation):
        if self._events[job["id"]].is_set():
            raise RolloutError("cancel_requested")
        job["stage"] = identifier
        entry = next(s for s in job["steps"] if s["id"] == identifier)
        entry.update(status="running", started_at=now())
        self._save(job)
        result = operation()
        if job.get("status") == "completed":
            return result  # Registration and completed job were one transaction.
        entry.update(status="completed", finished_at=now())
        self._save(job)
        return result

    def _run(self, identifier):
        with self._guard:
            material = self._materials.get(identifier)
        if material is None:
            return
        try:
            job = self.store.get(JOBS, identifier)
            if job is None:
                raise RolloutError("rollout_not_found")
        except Exception:
            material.clear()
            with self._guard:
                self._materials.pop(identifier, None)
                self._events.pop(identifier, None)
                self._futures.pop(identifier, None)
            return
        with request_context(actor=job["_owner"], request_id=job["_request_id"]):
            session = None
            try:
                job["status"] = "running"
                self._save(job)
                with SSHSession(SSHConnection.model_validate(job["_connection"]), material) as session:
                    with self._guard:
                        self._sessions[identifier] = session
                    def preflight():
                        remote = _remote_projection(session.rpc(self.program, self._payload(job, "probe")))
                        if remote["identity"] != job["_remote_identity"]:
                            raise RolloutError("remote_identity_changed")
                        if not remote["can_install"]:
                            raise RolloutError("existing_installation" if remote["existing_installation"] else "remote_installation_conflict")
                        return remote
                    self._step(job, "preflight", preflight)
                    def stage():
                        job["resources_may_exist"] = True
                        self._save(job)
                        return session.rpc(self.program, {**self._payload(job, "stage"), "materials": self.materials})
                    self._step(job, "stage", stage)
                    def image():
                        if job["_artifact"]:
                            with self.artifacts.open_verified(job["_artifact"]["id"]) as (metadata, stream):
                                if metadata != job["_artifact"]:
                                    raise RolloutError("artifact_changed_after_plan")
                                payload = {**self._payload(job, "load"), "artifact": metadata}
                                session.upload_artifact(self.program, payload, stream)
                                result = session.rpc(self.program, payload, timeout=660)
                        else:
                            result = session.rpc(self.program, {**self._payload(job, "pull"), "image": job["_spec"]["image"]}, timeout=660)
                        image_id = result.get("image_id")
                        if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                            raise RolloutError("loaded_image_identity_mismatch")
                        job["_image_id"] = image_id
                        return result
                    self._step(job, "image", image)
                    def prepare():
                        token = self.fleet.secrets.decrypt(job["_agent_token"])
                        try:
                            result = session.rpc(self.program, {**self._payload(job, "prepare"), "image": job["_image_id"], "agent_token": token}, timeout=260)
                        finally:
                            token = None
                        if result.get("prepared") is not True:
                            raise RolloutError("agent_preparation_failed")
                        if job["_spec"]["tls_mode"] == "managed_tls":
                            tls = result.get("tls", {})
                            try:
                                ca = validate_agent_ca(tls.get("ca_pem"))
                            except (TypeError, ValueError):
                                raise RolloutError("agent_tls_preparation_failed") from None
                            if tls.get("agent_url") != job["agent_url"]:
                                raise RolloutError("agent_tls_preparation_failed")
                            job["_agent_ca_pem"] = ca
                        return {"prepared": True}
                    self._step(job, "prepare", prepare)
                    def start():
                        with self.mutation_lock:
                            self._available()
                            if self._events[identifier].is_set():
                                raise RolloutError("cancel_requested")
                        job["_start_attempted"] = True
                        self._save(job)  # The command may outlive a lost SSH connection.
                        result = session.rpc(self.program, {**self._payload(job, "start"), "image": job["_image_id"]}, timeout=160)
                        if result.get("started") is not True or result.get("running") is not True or result.get("image_id") != job["_image_id"]:
                            raise RolloutError("started_agent_identity_unverified")
                        job["_container_id"] = _text(result.get("container_id"), 64)
                        job["rollback"].update(container_id=job["_container_id"], image_id=job["_image_id"])
                        return {"started": True}
                    self._step(job, "start", start)
                    # Release the root-owned staging reservation only after the
                    # mutating command has returned. It is retained on disconnect.
                    session.rpc(self.program, self._payload(job, "finish"), timeout=30)
                session = None
                self._step(job, "connect", lambda: self._verify_and_register(job, retries=8))
            except Exception as error:
                code = error.code if isinstance(error, (RolloutError, ArtifactError)) else "rollout_internal_failure"
                self._finish_error(job, code)
            finally:
                material.clear()
                with self._guard:
                    self._materials.pop(identifier, None)
                    self._events.pop(identifier, None)
                    self._futures.pop(identifier, None)
                    self._sessions.pop(identifier, None)

    def _finish_error(self, job, code):
        # Never include exception strings or stderr: either can contain secrets.
        if code == "cancel_requested" and not job.get("_start_attempted"):
            status = "cancelled"
        elif job.get("_start_attempted"):
            status = "installed_unconnected" if job.get("_container_id") else "interrupted"
        else:
            status = "interrupted" if code in {"ssh_stage_connection_lost", "installer_stage_timeout", "remote_command_timeout", "helper_cleanup_unverified"} and job.get("resources_may_exist") else "failed"
        job.update(status=status, error_code=code, connection_verified=False)
        job.pop("node_id", None)
        entry = next((s for s in job["steps"] if s["id"] == job["stage"]), None)
        if entry and (entry["status"] == "running" or job["stage"] == "connect"):
            entry.update(status="failed", finished_at=now(), message=code)
        for step in job["steps"]:
            if step["status"] == "pending":
                step["status"] = "skipped"
        try:
            self._save(job, action="Agent 安装未完成", audit_status=status)
        except Exception:
            # If the audit store itself fails, do not produce completed success.
            # The last durable running stage is recovered as interrupted at boot.
            pass

    def _verified_metadata(self, node, runtime):
        metadata = project_agent_metadata(self.fleet.call(node, "GET", "/agent/meta", timeout=10, max_response_bytes=AGENT_META_BYTES))
        profile = metadata.get("motor_profile", {})
        if (metadata["product_id"] != PRODUCT_ID or metadata["agent_protocol"] != AGENT_PROTOCOL_VERSION
                or metadata.get("operation_protocol") != 1
                or not all((metadata.get("execution_capabilities") or {}).get(name) is True
                           for name in ("resource_admission", "resource_ledger", "operation_receipts"))
                or AGENT_PROTOCOL_VERSION not in metadata.get("supported_control_protocols", [])
                or runtime not in metadata.get("supported_runtimes", []) or profile.get("profile_id") != BASELINE_PROFILE_ID
                or profile.get("commit") != BASELINE_MOTOR_COMMIT or profile.get("driver") != ADAPTER_DRIVER):
            raise RolloutError("agent_compatibility_failed")
        return metadata

    def _verify_and_register(self, job, *, retries=1):
        spec = job["_spec"]
        candidate = {"name": spec["name"], "address": spec["address"], "agent_url": spec["agent_url"],
                     "_token": job["_agent_token"], "_agent_ca_pem": job.get("_agent_ca_pem", "")}
        if spec["tls_mode"] == "managed_tls" and not candidate["_agent_ca_pem"]:
            raise RolloutError("agent_tls_trust_unavailable")
        metadata = None
        for attempt in range(retries):
            try:
                metadata = self._verified_metadata(candidate, spec["runtime"])
                break
            except RolloutError:
                raise
            except Exception:
                if attempt == retries-1:
                    raise RolloutError("agent_connection_unverified") from None
                # Short bounded startup grace; cancellation stays responsive.
                event = self._events.get(job["id"])
                if event and event.wait(1):
                    raise RolloutError("cancel_requested")
                if event is None:
                    time.sleep(1)
        event = self._events.get(job["id"])
        if event and event.is_set():
            raise RolloutError("cancel_requested")
        with self._guard, self.mutation_lock, self.store.unit_of_work(write=True) as db:
            current = db.resources.get(JOBS,job["id"])
            if current != job:
                raise RolloutError("rollout_record_changed")
            nodes = db.resources.list("nodes")
            expected_node = job.get("_registered_node")
            matches = [n for n in nodes if n.get("address") == spec["address"] or n.get("agent_url") == spec["agent_url"]]
            if expected_node is None and matches:
                raise RolloutError("node_registration_changed")
            old = None
            if expected_node is not None:
                # Inventory collection and display edits may progress while an
                # install is verified. Only an unchanged connection can receive
                # this result; preserve the administrator's latest metadata.
                connection_fields = ("id", "address", "agent_url", "_token", "_agent_ca_pem", "credential_revision")
                if len(matches) != 1 or any(matches[0].get(key) != expected_node.get(key) for key in connection_fields):
                    raise RolloutError("node_registration_changed")
                old = matches[0]
            node = {**(old or {}), **{key: candidate[key] for key in ("agent_url", "_token", "_agent_ca_pem")},
                    "id": old["id"] if old else uid("node_"),
                    "architecture": job["_remote_identity"]["architecture"], "agent_metadata": metadata,
                    "agent_metadata_checked_at": now(),
                    "credential_revision": (old.get("credential_revision", 0)+1) if old else 0,
                    "resource_scope": spec["runtime"], "credentials_updated_at": now()}
            if old:
                configured, reported, effective = nic_configuration(old)
                node.update(name=old["name"], address=old["address"], labels=old.get("labels", []),
                            configured_nic_name=configured, reported_nic_name=reported, nic_name=effective,
                            configuration_revision=old.get("configuration_revision", 0))
            else:
                node.update(name=spec["name"], address=spec["address"], labels=spec["labels"],
                            configured_nic_name=spec["nic_name"], reported_nic_name="", nic_name=spec["nic_name"],
                            configuration_revision=0)
            node = invalidate_observation(node, "Agent 连接已验证，等待重新采集资源观测")
            node.update(cpu_percent=None, memory_percent=None, npu_total=None, npu_used=None)
            job.update(status="completed", stage="connect", node_id=node["id"], connection_verified=True, updated_at=now())
            job.pop("error_code", None)
            step = next(s for s in job["steps"] if s["id"] == "connect")
            step.update(status="completed", finished_at=now())
            db.resources.put("nodes", node)
            db.resources.put(JOBS, job)
            self.store.audit("接入已安装 Agent", node["id"], "success", "已校验真实 TLS、访问凭据、协议与固定 Motor 配置；保留既有节点 ID",
                             metadata={"job_id": job["id"], "node_id": node["id"], "runtime": spec["runtime"]}, connection=db)
        return {"verified": True, "node_id": job["node_id"]}

    def connect(self, identifier):
        self._available()
        with self._guard:
            job = self.get_job(identifier)
            if not self.public_job(job)["can_connect"]:
                raise RolloutError("rollout_not_connectable")
            # Duplicate requests cannot race another background job on this ID.
            if identifier in self._events:
                raise RolloutError("rollout_busy")
            self._events[identifier] = threading.Event()
        try:
            with request_context(actor=context_fields()["actor"]):
                self._verify_and_register(job)
            return self.public_job(job)
        finally:
            with self._guard:
                self._events.pop(identifier, None)

    def cancel(self, identifier):
        with self._guard:
            job = self.get_job(identifier)
            if job.get("status") not in ACTIVE or identifier not in self._events:
                raise RolloutError("rollout_not_cancellable")
            value = {**job, "_cancel_requested": True}
            self._save(value, action="请求取消 Agent 安装", audit_status="accepted")
            self._events[identifier].set()
            future = self._futures.get(identifier)
            if future and future.cancel():
                value.update(status="cancelled", error_code="cancel_requested")
                self._save(value, action="取消排队 Agent 安装", audit_status="cancelled")
                self._materials.pop(identifier).clear()
                self._events.pop(identifier, None)
                self._futures.pop(identifier, None)
            return self.public_job(value)

    def recover(self, identifier, data: RecoverInput):
        """Resupply SSH credentials for observation only; never retry installation."""
        self._available()
        with self._guard:
            job = self.get_job(identifier)
            if job.get("status") in ACTIVE or job.get("status") == "completed" or identifier in self._events:
                raise RolloutError("rollout_not_recoverable")
            self._events[identifier] = threading.Event()
        material = SSHMaterial(data.credentials)
        try:
            with SSHSession(SSHConnection.model_validate(job["_connection"]), material) as session:
                result = session.rpc(self.program, self._payload(job, "status"), timeout=60)
            if type(result.get("helper_containers_present")) is not bool or result["helper_containers_present"]:
                raise RolloutError("remote_operations_need_review")
            tls = result.get("tls")
            if job["_spec"]["tls_mode"] == "managed_tls" and tls:
                try:
                    job["_agent_ca_pem"] = validate_agent_ca(tls.get("ca_pem"))
                except (ValueError, TypeError):
                    raise RolloutError("agent_tls_preparation_failed") from None
                if tls.get("agent_url") != job["agent_url"]:
                    raise RolloutError("agent_tls_preparation_failed")
            agent = result.get("owned_agent")
            if agent is not None:
                if (not isinstance(agent, dict) or not job.get("_image_id") or agent.get("image_id") != job["_image_id"]
                        or agent.get("operation_id") != job["id"] or type(agent.get("running")) is not bool):
                    raise RolloutError("started_agent_identity_unverified")
                job["_container_id"] = _text(agent.get("id"), 64)
                job["_start_attempted"] = True
                job["status"] = "installed_unconnected"
            job["resources_may_exist"] = True
            job["_reconciled_at"] = now()
            self._save(job, action="只读核验 Agent 安装现场", audit_status="observed")
        finally:
            material.clear()
            with self._guard:
                self._events.pop(identifier, None)
        return self.public_job(job)

    def close(self):
        failure = None
        with self._guard:
            self._closed = True
            for identifier, event in list(self._events.items()):
                event.set()
                future = self._futures.get(identifier)
                if future and future.cancel():
                    job = self.store.get(JOBS, identifier)
                    if job:
                        job.update(status="cancelled", error_code="control_stopping")
                        try:
                            self._save(job, action="停止排队 Agent 安装", audit_status="cancelled")
                        except Exception as error:
                            failure = error
                    material = self._materials.pop(identifier, None)
                    if material:
                        material.clear()
            sessions = list(self._sessions.values())
        # Closing the transport interrupts bounded SSH/SFTP operations; a remote
        # command may continue, so its durable stage remains marked uncertain.
        for session in sessions:
            session.__exit__()
        # Workers need _guard to finish, so never wait while holding it.
        self._pool.shutdown(wait=True, cancel_futures=True)
        if failure is not None:
            raise RolloutError("rollout_shutdown_audit_failed") from None


def create_agent_rollout_router(manager, role_for_request):
    def require_admin(request: Request):
        if role_for_request(request) != "admin":
            raise HTTPException(403, "只有系统管理员可以管理 Agent 推送")

    router = APIRouter(prefix="/api/agent-rollouts", tags=["agent-rollouts"], dependencies=[Depends(require_admin)])

    def safe(operation):
        try:
            return operation()
        except (RolloutError, ArtifactError) as error:
            code = error.code
            status = 404 if code.endswith("not_found") else 502 if code.startswith("ssh_") else 409
            raise HTTPException(status, code) from None

    @router.post("/host-key")
    def host_key(data: HostKeyInput):
        def operation():
            manager._available()
            return discover_host_key(data.host, data.port)
        return safe(operation)

    @router.get("/artifacts")
    def artifacts():
        return safe(manager.artifacts.list)

    @router.post("/plans")
    def plan(data: PlanInput):
        return safe(lambda: manager.create_plan(data))

    @router.post("/plans/{identifier}/execute", status_code=202)
    def execute(identifier: str, data: ExecuteInput):
        return safe(lambda: manager.execute(identifier, data))

    @router.get("/jobs")
    def jobs(limit: int = Query(default=50, ge=1, le=100)):
        return manager.list_jobs(limit)

    @router.get("/jobs/{identifier}")
    def job(identifier: str):
        return safe(lambda: manager.public_job(manager.get_job(identifier)))

    @router.post("/jobs/{identifier}/cancel")
    def cancel(identifier: str, data: StrictInput):
        return safe(lambda: manager.cancel(identifier))

    @router.post("/jobs/{identifier}/connect")
    def connect(identifier: str, data: StrictInput):
        return safe(lambda: manager.connect(identifier))

    @router.post("/jobs/{identifier}/recover")
    def recover(identifier: str, data: RecoverInput):
        return safe(lambda: manager.recover(identifier, data))

    return router
