"""Reference admission policy on explicit observation and ownership capabilities."""
from __future__ import annotations
from datetime import datetime,timezone
import hashlib,json,math,os,re
from ..docker_runtime import ContainerConflict
from ..motor_adapter import SERVICE_LABEL,NODE_LABEL


def check_physical_usage(context, requested, owned):
    if not requested:
        return
    mode = os.getenv("MOTOR_NPU_ADMISSION_MODE", "observed")
    if mode not in {"observed", "declarations"}:
        raise ContainerConflict("MOTOR_NPU_ADMISSION_MODE must be observed or declarations")
    if mode == "declarations":
        return
    from ..resource_telemetry import collect_resource_telemetry
    telemetry = context.telemetry(context.host_root).get("npu_telemetry") or {}
    try:
        captured = datetime.now(timezone.utc)
        observed_at = datetime.fromisoformat(telemetry.get("collected_at", "").replace("Z", "+00:00"))
        expires = datetime.fromisoformat(telemetry.get("expires_at", "").replace("Z", "+00:00"))
        if (observed_at.tzinfo is None or expires.tzinfo is None or observed_at > captured
                or expires <= captured or not 0 < (expires - observed_at).total_seconds() <= 300):
            raise ValueError("Expired observation")
    except (ValueError, TypeError, AttributeError) as error:
        raise ContainerConflict("Fresh NPU hardware observations are required before resource admission") from error
    observed = telemetry.get("devices") or []
    owned_pids = None
    for device in requested:
        matches = [item for item in observed if item.get("device_index") == device]
        if len(matches) != 1:
            raise ContainerConflict(f"davinci{device} has no unique hardware observation")
        observation = matches[0]
        if observation.get("mapping_status") != "verified":
            raise ContainerConflict(f"davinci{device} hardware identity mapping has not been verified")
        if observation.get("process_pid_scope") != "host" or observation.get("physical_device_id") != device:
            raise ContainerConflict(f"davinci{device} has no verified physical device and host PID mapping")
        if observation.get("health") != "healthy" or observation.get("processes_status") != "ready":
            raise ContainerConflict(f"davinci{device} health or process observation is unavailable")
        total, used = observation.get("memory_total_bytes"), observation.get("memory_used_bytes")
        if type(total) is not int or type(used) is not int or not 0 <= used <= total or total == 0:
            raise ContainerConflict(f"davinci{device} HBM observation is unavailable or inconsistent")
        processes = observation.get("processes") or []
        if processes:
            if owned_pids is None:
                owned_pids = context.container_pids(owned)
            if any(item.get("pid") not in owned_pids for item in processes):
                raise ContainerConflict(f"davinci{device} has processes outside the restart targets")
        else:
            utilization = observation.get("utilization_percent")
            if (type(utilization) not in {int, float} or not math.isfinite(utilization)
                    or utilization < 0 or utilization > 100 or utilization > 0):
                raise ContainerConflict(f"davinci{device} idle hardware utilization is not verified")


def restart(context, daemon, owned, containers, operation_id):
    targets = {container.id for container in owned}
    devices, ports, claims = set(), set(), {}
    discovered = set(context.devices())
    for container in owned:
        labels = context.labels(container)
        requested = context.declared_devices(container)
        if "motor-console.resource-devices" in labels:
            try:
                requested |= context.resource_numbers(json.loads(labels["motor-console.resource-devices"]), "device")
            except (ValueError, TypeError) as error:
                raise ContainerConflict("Owned container device claims are malformed") from error
        devices.update(requested)
        planned_ports = context.container_ports(container, required=True)
        ports.update(planned_ports)
        allocation_id = labels.get("motor-console.allocation-id") or "allocation_" + hashlib.sha256((daemon + ":" + container.id).encode()).hexdigest()[:32]
        if not re.fullmatch(r"allocation_[a-f0-9]{32}", allocation_id):
            raise ContainerConflict("Owned container allocation identity is malformed")
        claim = claims.setdefault(allocation_id, {"allocation_id": allocation_id, "daemon_id": daemon,
            "service_id": labels[SERVICE_LABEL], "node_id": labels[NODE_LABEL], "operation_id": operation_id,
            "devices": [], "ports": [], "container_ids": [], "container_names": []})
        claim["devices"] = sorted(set(claim["devices"]) | requested)
        claim["ports"] = sorted(set(claim["ports"]) | planned_ports)
        claim["container_ids"].append(container.id); claim["container_names"].append(container.name)
    if not devices <= discovered:
        raise ContainerConflict("Restart target devices are missing from the current host inventory")
    # Full-group checks precede the first restart, including containers of the same service not in this target.
    for container in containers:
        if container.id in targets or getattr(container, "status", "") not in {"running", "restarting", "paused"}:
            continue
        if devices & context.declared_devices(container):
            raise ContainerConflict("Restart devices are held by another running container: " + container.name)
        if ports and ports & context.container_ports(container):
            raise ContainerConflict("Restart ports are claimed by another running container: " + container.name)
    busy_ports = {port for port in ports if context.port_in_use(port)}
    if busy_ports and not busy_ports <= context.owned_listener_ports(owned):
        raise ContainerConflict("A restart port is occupied outside the verified target processes")
    context.physical_check(devices, owned)
    return list(claims.values())
