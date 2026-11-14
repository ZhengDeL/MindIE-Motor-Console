"""Pinned Motor compatibility boundaries and non-mutating admission checks.

These checks preserve arbitrary user JSON. They do not patch native routing,
clear its SHM ledger, test hardware, or turn configured dependencies into ready
services. Complex native exception windows remain explicit upstream boundaries.
"""
from __future__ import annotations

import json

from .version import BASELINE_MOTOR_COMMIT

_TLS_NAMES = {"infer_tls_config", "mgmt_tls_config", "observability_tls_config",
              "north_tls_config", "etcd_tls_config", "grpc_tls_config"}
_BOUNDARIES = (
    ("allocation_post_commit", "static-risk", "Coordinator CAS分配后同步异常补偿仍需隔离验证"),
    ("release_ack_cleanup", "static-risk", "Coordinator释放ACK后的本地残留与重复扣减需隔离验证"),
    ("last_instance_delete", "static-risk", "最后实例DEL失败与空SET补偿边界需隔离验证"),
    ("tls_context_fallback", "static-risk", "原生TLS配置失败存在HTTP回退路径，必须验证实际协议"),
    ("agent_status_auth", "static-risk", "原生agent-status的hardened鉴权链尚未完成隔离验证"),
    ("conductor_pool_recovery", "documented-limitation", "Conductor单独重启不会由当前周期分支恢复集中pool订阅"),
    ("token_reinference_placeholder", "unsupported", "固定源码TokenReinference仍为占位循环"),
)


def native_contract_status(commit: str = BASELINE_MOTOR_COMMIT) -> dict:
    baseline = commit == BASELINE_MOTOR_COMMIT
    return {"commit": commit, "adapter_modifies_upstream": False,
            "source_integrity": "checked_at_preflight",
            "status": "known_boundaries" if baseline else "separate_profile_required",
            "runtime_acceptance": "not_claimed",
            "boundaries": [{"code": code, "status": status, "detail": detail} for code, status, detail in _BOUNDARIES] if baseline else []}


def native_contract_checks(user_config: dict, env: dict | None = None, *, runtime: str,
                           commit: str = BASELINE_MOTOR_COMMIT) -> list[dict]:
    checks = []
    def check(name, status, detail):
        checks.append({"name": name, "status": status, "detail": detail})
    if commit != BASELINE_MOTOR_COMMIT:
        check("Native compatibility profile", "warn", "Custom native versions require their own tested capability and security contract")
        return checks
    if not isinstance(user_config, dict) or not isinstance(env or {}, dict):
        check("Native configuration", "fail", "user_config and env must be JSON objects")
        return checks
    deploy = user_config.get("motor_deploy_config") or {}
    controller = user_config.get("motor_controller_config") or {}
    coordinator = user_config.get("motor_coordinator_config") or {}
    if any(not isinstance(value, dict) for value in (deploy, controller, coordinator)):
        check("Native configuration", "fail", "Motor deployment/controller/coordinator sections must be objects")
        return checks
    tls_root = deploy.get("tls_config", {})
    if not isinstance(tls_root, dict):
        check("Native TLS", "fail", "motor_deploy_config.tls_config must be an object")
        tls_root = {}
    tls_blocks = [("motor_deploy_config.tls_config." + key, value) for key, value in tls_root.items() if key in _TLS_NAMES]
    for section_name, section in (("motor_controller_config", controller), ("motor_coordinator_config", coordinator)):
        tls_blocks.extend((section_name + "." + key, value) for key, value in section.items()
                          if key in _TLS_NAMES and key not in tls_root)
    for location, tls in tls_blocks:
        if not isinstance(tls, dict) or type(tls.get("enable_tls", False)) is not bool:
            check("Native TLS · " + location, "fail", "TLS configuration must be an object with a boolean enable_tls")
            continue
        if tls.get("enable_tls") is not True:
            continue
        missing = [key for key in ("ca_file", "cert_file", "key_file")
                   if not isinstance(tls.get(key), str) or not tls[key].strip() or any(c in tls[key] for c in "\x00\r\n")]
        if missing:
            check("Native TLS · " + location, "fail", "TLS enabled but required certificate paths are missing or invalid: " + ", ".join(missing))
        else:
            check("Native TLS · " + location, "warn", "Explicit certificate paths are configured; file contents, role mounts and actual HTTPS/mTLS remain to be verified. Native fallback is not accepted as secure readiness.")
    ft = controller.get("fault_tolerance_config") or {}
    if not isinstance(ft, dict):
        check("Native fault tolerance", "fail", "fault_tolerance_config must be an object")
    elif any(key in ft and type(ft[key]) is not bool for key in ("enable_fault_tolerance", "enable_token_reinference")):
        check("Native fault tolerance", "fail", "Fault tolerance enable flags must be JSON booleans")
    elif ft.get("enable_fault_tolerance", True) is not False and ft.get("enable_token_reinference") is True:
        check("Native TokenReinference", "fail", "The pinned strategy is an unimplemented busy-loop placeholder; explicit enablement is unsupported. Review this field without silently rewriting the original configuration.")
    elif "enable_token_reinference" not in ft and ft.get("enable_fault_tolerance", True) is not False:
        check("Native TokenReinference", "warn", "Pinned native defaults enable a placeholder token strategy; this is not a verified recovery capability. Review the explicit fault-tolerance profile before enabling hardware fault control.")
    scheduler = coordinator.get("scheduler_config") or {}
    if not isinstance(scheduler, dict):
        check("Native scheduler", "fail", "scheduler_config must be an object")
        return checks
    prefill = scheduler.get("prefill_scheduler_type", scheduler.get("scheduler_type", "load_balance"))
    decode = scheduler.get("decode_scheduler_type", scheduler.get("scheduler_type", "load_balance"))
    if prefill == "dynamic_bucket":
        check("Native scheduler", "fail", "dynamic_bucket is a Decode-only native POC; Prefill cannot use this policy")
    if decode == "kv_cache_affinity":
        check("Native scheduler", "fail", "Decode KV affinity is unsupported by the pinned policy; native fallback must not be advertised as affinity readiness")
    if decode == "dynamic_bucket":
        check("Native dynamic bucket", "warn", "This native POC requires separate workload and fallback acceptance")
    agent_status = coordinator.get("agent_status_config") or {}
    if isinstance(agent_status, dict) and agent_status.get("enabled") is True and agent_status.get("require_infer_auth") is True:
        check("Native agent-status authentication", "fail", "The pinned hardened side-channel auth contract is unverified; do not enable this route as authenticated without a separately tested protected ingress/profile")
    if prefill == "kv_cache_affinity":
        conductor = {}
        for section in (coordinator.get("prefill_kv_event_config"), user_config.get("kv_conductor_config"), scheduler.get("kv_conductor_config")):
            if isinstance(section, dict):
                conductor.update({key: value for key, value in section.items() if value not in (None, "")})
        env_host = next((section.get("KV_CONDUCTOR_SERVICE") for section in (env or {}).values()
                         if isinstance(section, dict) and section.get("KV_CONDUCTOR_SERVICE")), "")
        host = conductor.get("conductor_service") or env_host
        internal_k8s = runtime == "kubernetes" and isinstance(user_config.get("kv_conductor_config"), dict) \
            and type(user_config["kv_conductor_config"].get("http_server_port")) is int \
            and user_config["kv_conductor_config"]["http_server_port"] > 0
        if not (isinstance(host, str) and host.strip()) and not internal_k8s:
            check("Native KV Conductor", "fail", "KV affinity requires an explicit external Conductor address/environment or a rendered Kubernetes Conductor role; Docker does not start it automatically")
        role = "union" if "hybrid_instances_num" in deploy else "prefill"
        engine = user_config.get("motor_engine_" + role + "_config") or {}
        engine_config = (engine.get("engine_config") or {}) if isinstance(engine, dict) else {}
        events = engine_config.get("kv-events-config", engine_config.get("kv_events_config", {})) if isinstance(engine_config, dict) else {}
        if isinstance(events, str):
            try:
                events = json.loads(events)
            except ValueError:
                events = {}
        engine_type = str(engine.get("engine_type", "vllm")).lower() if isinstance(engine, dict) else ""
        if engine_type in {"vllm", "vllm_ascend", "vllm-ascend"} and (not isinstance(events, dict) or events.get("enable_kv_cache_events") is not True):
            check("Native KV events", "fail", "vLLM Prefill/Union requires explicit enabled KV events; saving an affinity policy alone does not create an index")
        check("Native KV readiness", "warn", "Conductor process, tokenizer/block size, actual HBM/offload/Store events, scheduled policy and restart recovery require separate acceptance; configuration is not ready evidence")
    if user_config.get("north_config"):
        check("Native CCAE control", "warn", "The Reporter may accept instance-termination commands; registry/Kafka/control permissions and real external receipts require separate acceptance")
    return checks
