"""Explainable Docker placement proposals; Motor remains the execution authority.

This is a conservative greedy recommendation, never an automatic deployment or
an assertion of network-topology or model-memory fit. Unknown observations are
ineligible. Final admission always happens again on the destination Agent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field
from typing import Any, Literal

from .motor_adapter import (_kv_enabled, official_helpers, preserve_json,
                            validate_config, validate_layout)
from .node_configuration import execution_node, nic_configuration_error, valid_nic_name
from .resource_inventory import inventory_view
from .version import BASELINE_PROFILE_ID, BASELINE_MOTOR_COMMIT, ADAPTER_DRIVER, PRODUCT_ID


class PlacementPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    user_config: dict[str, Any]
    env: dict[str, Any] = Field(default_factory=dict)
    strategy: Literal["pack", "spread"] = "pack"
    min_free_hbm_bytes: int = Field(default=0, ge=0, le=2**60)
    motor_profile: str = Field(default="", max_length=96)


def _fresh(value, captured):
    try:
        observed = datetime.fromisoformat(value["collected_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
        return observed <= captured and expires > captured
    except (ValueError, KeyError, TypeError):
        return False


def _ledger_deadline(value):
    observed = datetime.fromisoformat(value["collected_at"].replace("Z", "+00:00"))
    legacy = observed + timedelta(seconds=45)
    if not value.get("expires_at"):
        return legacy
    return min(legacy, datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00")))


def recommend_placements(data: PlacementPlanInput, records: list[dict], *, upstream_dir=None,
                         captured: datetime | None = None) -> dict:
    stamp = captured or datetime.now(timezone.utc)
    report = {"valid": False, "placements": [], "errors": [], "warnings": [
        "建议采用保守启发式排序；尚未验证跨机 HCCS/RoCE 拓扑、链路性能及模型完整内存需求。",
        "建议不会预留设备。应用后仍须执行部署预检与 Agent 最终资源准入。"],
        "exclusions": [], "evidence": {"generated_at": stamp.isoformat(),
            "expires_at": (stamp + timedelta(seconds=30)).isoformat(), "strategy": data.strategy,
            "basis": "新鲜宿主身份、Docker 声明、持久资源账本、已映射硬件健康与进程观测",
            "topology_verified": False, "model_memory_fit_verified": False,
            "candidate_nodes": 0, "candidate_devices": 0}}
    if data.motor_profile and data.motor_profile != BASELINE_PROFILE_ID:
        report["errors"].append("当前资源建议仅支持已固定并验证的 Motor 适配配置；其他版本请手工审查布局。")
        return report
    try:
        info = validate_config(data.user_config, data.env, upstream_dir)
    except (ValueError, KeyError, TypeError) as error:
        report["errors"].append(str(error)[:2000])
        return report
    views = [inventory_view(execution_node(record), at=stamp) for record in records]
    hosts, candidates = [], []
    for node in views:
        reason = ""
        meta = node.get("agent_metadata") or {}
        caps = meta.get("execution_capabilities") or {}
        identity = (node.get("host_identity"), node.get("daemon_id"))
        duplicate = any(other["id"] != node["id"] and (
            identity[0] and other.get("host_identity") == identity[0]
            or identity[1] and other.get("daemon_id") == identity[1]
            or other.get("address") == node.get("address")) for other in views)
        if node.get("demo"):
            reason = "演示节点不能参与真实资源建议"
        elif node.get("docker_available") is not True:
            reason = "节点没有可用 Docker 运行时"
        elif node.get("resource_freshness") != "fresh" or node.get("npu_total") is None:
            reason = "当前资源观测缺失、离线或已过期"
        elif not all(identity) or duplicate or node.get("identity_conflict"):
            reason = "宿主/daemon 身份缺失或重复接入"
        elif meta.get("product_id") != PRODUCT_ID:
            reason = "Agent 产品身份与当前平台不一致"
        elif ((meta.get("motor_profile") or {}).get("profile_id") != BASELINE_PROFILE_ID
              or (meta.get("motor_profile") or {}).get("commit") != BASELINE_MOTOR_COMMIT
              or (meta.get("motor_profile") or {}).get("driver") != ADAPTER_DRIVER):
            reason = "Motor 版本绑定与建议适配器不一致"
        elif meta.get("operation_protocol") != 1 or not all(caps.get(name) is True for name in ("resource_admission", "resource_ledger", "operation_receipts")):
            reason = "Agent 尚未具备完整资源准入、账本与回执能力，请升级 Agent"
        elif not valid_nic_name(node.get("nic_name")):
            reason = nic_configuration_error(node)
        if reason:
            report["exclusions"].append({"node_id": node["id"], "reason": reason})
            continue
        hosts.append(node)
        telemetry, ledger = node.get("npu_telemetry") or {}, node.get("resource_ledger") or {}
        if not _fresh(telemetry, stamp) or telemetry.get("status") not in {"ready", "partial"}:
            reason = "缺少有效硬件观测，仅可作为零设备管理组件候选"
        elif (ledger.get("complete") is not True or ledger.get("daemon_id") != node["daemon_id"]
              or ledger.get("scope") != "single-agent-docker-daemon"):
            reason = "持久资源账本不完整或 daemon 身份不一致"
        else:
            try:
                ledger_time = datetime.fromisoformat(ledger["collected_at"].replace("Z", "+00:00"))
                if not ledger_time <= stamp < _ledger_deadline(ledger):
                    reason = "持久资源账本观测已过期或时钟不一致"
            except (KeyError, ValueError, TypeError):
                reason = "持久资源账本缺少可信采集时间"
        if reason:
            report["exclusions"].append({"node_id": node["id"], "reason": reason})
            continue
        occupied = set(node.get("devices_in_use", []))
        for entry in ledger.get("items", []):
            if entry.get("state") in {"pending", "active", "uncertain"}:
                occupied.update(entry.get("devices", []))
        free, mapped = [], set()
        for device in telemetry.get("devices", []):
            index = device.get("device_index")
            if index is not None and index in mapped:
                # Two physical observations must never map onto the same card.
                free = []
                reason = "硬件映射重复，节点不参与设备建议"
                break
            if index is not None:
                mapped.add(index)
            if (device.get("mapping_status") != "verified" or device.get("physical_device_id") != index
                    or type(index) is not int or index not in node.get("devices", [])
                    or index in occupied or device.get("health") != "healthy"
                    or device.get("processes_status") != "ready"
                    or device.get("process_pid_scope") != "host"
                    or device.get("processes") or device.get("utilization_percent") is None
                    or device["utilization_percent"] != 0):
                continue
            total, used = device.get("memory_total_bytes"), device.get("memory_used_bytes")
            if total is None or total <= 0 or used is None or total - used < data.min_free_hbm_bytes:
                continue
            free.append((index, total - used))
        if free:
            candidates.append({"node": node, "free": sorted(free, key=lambda v: (-v[1], v[0])), "assigned": 0})
        else:
            report["exclusions"].append({"node_id": node["id"], "reason": reason or
                "没有同时满足映射、健康、进程无占用、利用率与 HBM 条件的空闲设备"})
    report["evidence"].update(candidate_nodes=len(candidates), candidate_devices=sum(len(c["free"]) for c in candidates))
    if not hosts:
        report["errors"].append("没有满足部署条件的执行节点；请查看排除原因，核对节点配置后重新探测。")
        return report
    placements = []
    deploy = data.user_config["motor_deploy_config"]

    def allocate(role, name, count, excluded):
        choices = [c for c in candidates if len(c["free"]) >= count and c["node"]["address"] not in excluded]
        if data.strategy == "pack":
            choices.sort(key=lambda c: (len(c["free"]) - count, -c["assigned"], c["node"]["id"]))
        else:
            choices.sort(key=lambda c: (c["assigned"], -len(c["free"]), c["node"]["id"]))
        if not choices:
            raise ValueError(f"{role}/{name} 需要 {count} 张已核实空闲 NPU 和不同实例主机，当前可信资源不足。")
        choice = choices[0]
        selected, choice["free"] = choice["free"][:count], choice["free"][count:]
        choice["assigned"] += count
        index = len(placements)
        ports = {"node_manager": 4026 + index * 100, "rpc": 9000 + index * 100,
                 "base": 10000 + index * 1000}
        section = data.user_config.get(f"motor_engine_{role}_config") or {}
        if (section.get("engine_config") or {}).get("kv_transfer_config"):
            ports["kv"] = 21000 + index * 1000
        placements.append({"node_id": choice["node"]["id"], "role": role, "instance_name": name,
            "devices": sorted(v[0] for v in selected), "ports": ports if role != "single_container" else {}})
        excluded.add(choice["node"]["address"])

    try:
        if deploy["deploy_mode"] == "single_container":
            with official_helpers(upstream_dir) as helpers:
                count = helpers.docker.required_visible_device_count(preserve_json(data.user_config))
            allocate("single_container", "single", count, set())
        else:
            manager = sorted(hosts, key=lambda node: node["id"])[0]
            placements.append({"node_id": manager["id"], "role": "coordinator_controller", "instance_name": "ctrl", "devices": [], "ports": {}})
            if _kv_enabled(data.user_config):
                placements.append({"node_id": manager["id"], "role": "kv_store", "instance_name": "kv", "devices": [], "ports": {}})
            for role in ("union", "prefill", "decode"):
                prefix = "hybrid" if role == "union" else role[0]
                for instance in range(info["replicas"].get(role, 0)):
                    excluded = set()
                    for _ in range(int(deploy.get(f"single_{prefix}_instance_pod_num", 1))):
                        allocate(role, f"{role[0]}{instance}", int(deploy[f"{prefix}_pod_npu_num"]), excluded)
        checks = validate_layout(data.user_config, placements, env=data.env, nodes=views, upstream_dir=upstream_dir)
        report["errors"].extend(c["detail"] for c in checks if c["status"] == "fail")
    except (ValueError, TypeError, KeyError) as error:
        report["errors"].append(str(error)[:2000])
    if not report["errors"]:
        report.update(valid=True, placements=placements)
        expiries = [datetime.fromisoformat(n["expires_at"].replace("Z", "+00:00")) for n in hosts]
        for candidate in candidates:
            expiries.append(datetime.fromisoformat(candidate["node"]["npu_telemetry"]["expires_at"].replace("Z", "+00:00")))
            expiries.append(_ledger_deadline(candidate["node"]["resource_ledger"]))
        report["evidence"]["expires_at"] = min(stamp + timedelta(seconds=30), *expiries).isoformat()
    return report


def create_placement_router(store, *, upstream_dir=None) -> APIRouter:
    router = APIRouter(prefix="/api/resources", tags=["resources"])

    @router.post("/placement-plan")
    def placement_plan(data: PlacementPlanInput):
        return recommend_placements(data, store.list("nodes"), upstream_dir=upstream_dir)

    return router
