"""Explicit demonstration state; never presented as actual host inventory."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import math

from .catalog import summarize
from .store import Store, now
from .version import PRODUCT_NAME


def seed(store: Store, template_list: list[dict]):
    if store.list("nodes") or store.list("services"): return
    for index in range(3):
        store.put("nodes", {"id": f"demo-node-{index + 1}", "name": f"ascend-a3-{index + 1:02d}", "address": f"192.0.2.{21 + index}", "agent_url": "", "labels": ["Atlas 800I A3", "演示节点"], "status": "online", "architecture": "aarch64", "os": "openEuler 22.03 LTS", "docker_version": "26.1.3", "npu_total": 16, "npu_used": [8, 8, 0][index], "cpu_percent": [34, 48, 12][index], "memory_percent": [42, 57, 18][index], "nic_name": "enp189s0f0", "last_seen": now(), "devices": list(range(16)), "demo": True})
    preferred = next((item for item in template_list if item["id"] == "vllm/pd_hybrid"), template_list[0] if template_list else None)
    user = copy.deepcopy(preferred["user_config"]) if preferred else {"motor_deploy_config": {"hardware_type": "800I_A3", "hybrid_instances_num": 1}, "motor_engine_union_config": {"engine_type": "vllm", "engine_config": {"served_model_name": "Qwen3-8B"}}}
    env = copy.deepcopy(preferred["env"]) if preferred else {"motor_common_env": {}}
    for index, (name, model, status, runtime) in enumerate([
        ("qwen3-production", "Qwen3-8B", "running", "docker"),
        ("deepseek-inference", "DeepSeek-V4-Flash", "running", "kubernetes"),
        ("glm-evaluation", "GLM-5.3", "draft", "docker"),
    ]):
        data = copy.deepcopy(user)
        data["motor_engine_union_config"]["engine_config"]["served_model_name"] = model
        data.setdefault("motor_deploy_config", {})["image_name"] = "mindie-motor:demo-a3"
        placements = [
            {"node_id": "demo-node-1", "role": "coordinator_controller", "instance_name": "ctrl", "devices": [], "ports": {}},
            {"node_id": f"demo-node-{1 + index % 2}", "role": "union", "instance_name": "u0", "devices": list(range(8)), "ports": {"node_manager": 4026, "rpc": 9000, "base": 10000}},
        ]
        if runtime == "kubernetes": placements = placements[:1]
        stamp = (datetime.now(timezone.utc) - timedelta(hours=8 - index * 2)).isoformat()
        store.put("services", {"id": f"demo-service-{index + 1}", "name": name, "runtime": runtime, **summarize(data), "status": status,
                               "placements": placements, "node_names": list(dict.fromkeys("ascend-a3-" + p["node_id"].rsplit("-", 1)[1].zfill(2) for p in placements)), "device_count": 8, "endpoint": "http://192.0.2.21:1025", "created_at": stamp, "updated_at": stamp, "user_config": data, "env": env, "last_error": "", "demo": True}, revision=True)
        if status == "running":
            seeded = store.get("services", f"demo-service-{index + 1}")
            seeded.update(resources_may_exist=True, deployed_placements=copy.deepcopy(placements[:1] if runtime == "kubernetes" else placements))
            store.put("services", seeded)
    store.audit("演示环境就绪", PRODUCT_NAME, "success", "所有节点、服务和指标均为演示数据，未连接真实集群")


def series() -> list[dict]:
    base = datetime.now(timezone.utc)
    return [{"time": (base - timedelta(minutes=5 * (23 - i))).isoformat(), "requests": round(46 + math.sin(i / 3) * 12 + (i % 5) * 3, 1), "latency": round(185 + math.cos(i / 4) * 24 + (i % 3) * 7, 1)} for i in range(24)]
