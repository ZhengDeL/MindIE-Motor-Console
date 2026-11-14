"""Node-local Docker SDK lifecycle with explicit ownership and real failures."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import errno
import ipaddress
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import threading
import time
from typing import Any
from types import SimpleNamespace
import uuid

from .motor_adapter import (INSTANCE_LABEL, MANAGED_LABEL, NODE_LABEL, ROLE_LABEL, SERVICE_LABEL,
                            MotorConfigError, build_container_spec, official_helpers, safe_name, upstream_path, validate_layout)
from .agent_capacity import native_capacity, native_operation, native_service_lock
from .docker_agent_state import DockerAgentState, AgentStateConflict, operation_identifier, intent_hash
from .observation_cache import ObservationCache
from .execution_policy import execution_metadata, require_mutations


class DockerUnavailable(RuntimeError):
    pass


class ContainerConflict(MotorConfigError):
    pass


class InventoryBudgetExceeded(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DockerRuntime:
    def __init__(self, client=None, upstream_dir: str | Path | None = None,
                 workspace_dir: str | Path | None = None, host_root: str | Path | None = None,
                 state_dir: str | Path | None = None, read_only: bool | None = None,
                 native_adapter=None, ledger_factory=None, host_telemetry=None, admission=None,
                 provider_generation=None, provider_binding=None):
        if native_adapter is None:
            from .provider_host.defaults import ReferenceNativeAdapter
            native_adapter = ReferenceNativeAdapter()
        self.native_adapter = native_adapter
        self.ledger_factory = ledger_factory or DockerAgentState
        self.host_telemetry = host_telemetry
        if admission is None:
            from .provider_host.defaults import ResourceAdmission
            admission = ResourceAdmission()
        self.admission = admission
        self.provider_generation, self.provider_binding = provider_generation, provider_binding
        self._client = client
        self._inventory_client = client
        self._inventory_cache = ObservationCache()
        self.upstream_dir = self.native_adapter.upstream_path(upstream_dir)
        self.workspace_dir = str(workspace_dir or os.environ.get("MOTOR_HOST_WORKSPACE_DIR", "/opt/motor-console/workspace"))
        self.host_root = Path(host_root or os.environ.get("MOTOR_HOST_ROOT", "/"))
        self._lock = threading.RLock()
        self._state_dir = Path(state_dir or os.getenv("MOTOR_DATA_DIR") or Path(self.workspace_dir) / ".agent-state")
        self._journal = None
        self._journal_guard = threading.Lock()
        self.read_only = execution_metadata(read_only)["read_only"]

    def _state(self, *, read_timeout=None):
        acquired = self._journal_guard.acquire() if read_timeout is None else self._journal_guard.acquire(timeout=read_timeout)
        if not acquired:
            raise DockerUnavailable("Agent resource journal is busy")
        try:
            if self._journal is None:
                self._journal = self.ledger_factory(self._state_dir, initialization_timeout=10 if read_timeout is None else read_timeout)
            return self._journal
        finally:
            self._journal_guard.release()

    @staticmethod
    def _daemon_id(info):
        identity = info.get("ID")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9:._-]{1,256}", identity):
            return ""
        return "docker:" + hashlib.sha256(identity.encode()).hexdigest()

    def _snapshot(self, client):
        containers = client.containers.list(all=True)
        if len(containers) > 4096:
            raise ContainerConflict("Docker resource inventory exceeds its supported bounds")
        return containers

    def _reconcile(self, client):
        daemon = self._daemon_id(client.info())
        if not daemon:
            raise DockerUnavailable("Docker daemon identity cannot be verified for resource coordination")
        containers = self._snapshot(client)
        self._state().reconcile(daemon, containers, (MANAGED_LABEL, SERVICE_LABEL, NODE_LABEL))
        return daemon, containers

    def _execute(self, action, payload, function):
        require_mutations(self.read_only)
        safe_name(payload.get("service_id"), "service_id")
        node = payload.get("node") or {}
        if not isinstance(node, dict):
            raise MotorConfigError("node must be an object")
        node_id = payload.get("node_id") or node.get("id")
        identities = [value for value in (payload.get("node_id"), node.get("id")) if value is not None]
        if action == "deploy":
            placement = payload.get("placement") or {}
            if not isinstance(placement, dict):
                raise MotorConfigError("placement must be an object")
            if placement.get("node_id") is not None:
                identities.append(placement["node_id"])
        if any(value != node_id for value in identities):
            raise MotorConfigError("node_id, node.id and placement.node_id must identify the same target")
        if node_id is not None:
            safe_name(node_id, "node_id")
        identifier = operation_identifier(payload.get("operation_id"))
        state = self._state()
        fingerprint = intent_hash(action, payload)
        replay = state.replay(identifier, fingerprint)
        if replay is not None:
            return replay
        # Busy requests are rejected before claiming an idempotency identity.
        # Nested native calls reuse both the capacity slot and this RLock.
        with native_capacity().slot(), native_service_lock(self._lock):
            replay = state.begin(identifier, fingerprint, action, str(payload["service_id"]), str(node_id or ""))
            if replay is not None:
                return replay
            return self._execute_recorded(action, {**payload, "operation_id": identifier}, function)

    def _execute_recorded(self, action, payload, function):
        identifier = payload["operation_id"]
        state = self._state()
        state.running(identifier)
        self._inventory_cache.invalidate()
        try:
            result = function(payload)
        except Exception as error:
            state.uncertain(identifier)
            state.finish(identifier, error=error, resources_may_exist=True)
            raise
        finally:
            self._inventory_cache.invalidate()
        receipt = state.finish(identifier, result=result, resources_may_exist=action not in {"stop", "remove"})
        try:
            self._reconcile(self._docker())
        except Exception:
            # A lost observation cannot erase a confirmed operation receipt or free claims.
            pass
        return receipt

    def operation(self, identifier):
        return self._state().receipt(identifier)

    def engine_client(self):
        """Public Docker engine capability used by the selected image package."""
        return self._docker()

    def resource_ledger(self):
        """Public operation/allocation port; never exposes a SQL connection."""
        return self._state()

    def _docker(self):
        try:
            if self._client is None:
                import docker
                self._client = docker.from_env(timeout=15)
            self._client.ping()
            return self._client
        except Exception as exc:
            raise DockerUnavailable(f"Docker daemon is unavailable on this node: {exc}") from exc

    def _host_path(self, value: str) -> Path:
        path = PurePosixPath(value)
        if not path.is_absolute() or ".." in path.parts or "\x00" in value:
            raise MotorConfigError(f"Invalid host path: {value}")
        # Absolute symlink targets belong to the Docker host namespace. Letting
        # pathlib follow /host/usr/bin/tool -> /usr/local/tool would instead
        # inspect the Agent's /usr/local and falsely reject a valid host bind.
        pending = list(path.parts[1:])
        resolved: list[str] = []
        links = 0
        while pending:
            part = pending.pop(0)
            if part in {"", "."}:
                continue
            if part == "..":
                if resolved:
                    resolved.pop()
                continue
            candidate = self.host_root.joinpath(*resolved, part)
            if candidate.is_symlink():
                links += 1
                if links > 40:
                    raise OSError(errno.ELOOP, "Host path has too many symbolic links")
                target = PurePosixPath(os.readlink(candidate))
                if target.is_absolute():
                    resolved.clear()
                    pending = list(target.parts[1:]) + pending
                else:
                    pending = list(target.parts) + pending
            else:
                resolved.append(part)
        return self.host_root.joinpath(*resolved)

    @staticmethod
    def _labels(container) -> dict:
        return (container.attrs.get("Config") or {}).get("Labels") or getattr(container, "labels", {}) or {}

    def _owned(self, service_id: str, node_id: str | None = None) -> list:
        safe_name(service_id, "service_id")
        if node_id is not None:
            safe_name(node_id, "node_id")
        client = self._docker()
        filters = {"label": [f"{MANAGED_LABEL}=true", f"{SERVICE_LABEL}={service_id}"]}
        containers = client.containers.list(all=True, filters=filters)
        result = []
        for container in containers:
            labels = self._labels(container)
            if labels.get(MANAGED_LABEL) != "true" or labels.get(SERVICE_LABEL) != service_id or not labels.get(NODE_LABEL):
                continue
            if node_id and labels.get(NODE_LABEL) != node_id:
                continue
            result.append(container)
        return result

    @staticmethod
    def _declared_devices(container) -> set[int]:
        values = (container.attrs.get("HostConfig") or {}).get("Devices") or []
        result = set()
        for value in values:
            path = value.get("PathOnHost", "") if isinstance(value, dict) else str(value).split(":")[0]
            match = re.fullmatch(r"/dev/davinci(\d+)", path)
            if match:
                result.add(int(match[1]))
        return result

    def _devices(self) -> list[int]:
        directory = self._host_path("/dev")
        try:
            return sorted(int(m[1]) for item in directory.iterdir()
                          if (m := re.fullmatch(r"davinci(\d+)", item.name)))
        except OSError as error:
            raise DockerUnavailable("Host NPU device discovery failed; device count is unknown") from error

    def status(self, service_id: str, node_id: str) -> dict:
        """Read only the narrow state of containers with verified ownership."""
        items = []
        for container in self._owned(service_id, node_id):
            # Refresh before reporting; never interpret a daemon error as absent.
            container.reload()
            labels = self._labels(container)
            if (labels.get(MANAGED_LABEL) != "true" or labels.get(SERVICE_LABEL) != service_id
                    or labels.get(NODE_LABEL) != node_id):
                raise ContainerConflict("Container ownership changed during status collection")
            state = container.attrs.get("State") or {}
            items.append({"id": container.id, "name": container.name,
                          "role": labels.get(ROLE_LABEL, ""), "node_id": node_id,
                          "status": state.get("Status", "unknown"),
                          "health": (state.get("Health") or {}).get("Status"),
                          "revision": labels.get("motor-console.job", "")})
            if len(items) > 256:
                raise ContainerConflict("Owned container status exceeds the response limit")
        return {"items": items, "observed_at": _now(), "functional_acceptance": "not_tested"}

    def _inventory_docker(self):
        # Read-only collection has its own SDK session and timeout. Never mutate
        # the lifecycle client's timeout while a create/restart is in progress.
        if self._inventory_client is None:
            import docker
            self._inventory_client = docker.from_env(timeout=2)
        return self._inventory_client

    def inventory(self, node_address: str | None = None) -> dict:
        snapshot = self._inventory_cache.read(self._collect_inventory)
        return self.inventory_network_view(snapshot, node_address) if node_address is not None else snapshot

    def inventory_network_view(self, snapshot: dict, node_address: str) -> dict:
        """Project one requested model address without renewing a cached sample.

        The Agent's listening address is a separate management setting. Never
        infer a model interface from it, and never store this request-specific
        projection in the shared Docker/NPU observation cache.
        """
        if not isinstance(node_address, str):
            raise ValueError("请选择节点实际 IPv4 地址")
        address = ipaddress.IPv4Address(node_address)
        if address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError("请选择节点实际 IPv4 地址")
        address = str(address)
        return {**snapshot, "address": address, "nic_name": self._network_nic(address)}

    def _network_nic(self, node_address: str) -> str:
        """Resolve an exact, unambiguous local IPv4 match with native helpers."""
        try:
            interfaces = socket.if_nameindex()
        except OSError:
            return ""
        if len(interfaces) > 4096:
            return ""
        matches = []
        with self.native_adapter.official_helpers(self.upstream_dir) as helpers:
            for _, name in interfaces:
                if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", name):
                    continue
                try:
                    if helpers.docker.ip_from_nic(name) == node_address:
                        matches.append(name)
                except ValueError:
                    continue
        return matches[0] if len(matches) == 1 else ""

    @staticmethod
    def _inventory_containers(client, query, claims):
        collection = client.containers
        if hasattr(collection, "get"):
            # Docker SDK list(sparse=False) inspects every returned container.
            # Ask for bounded summaries, then inspect each identity once.
            summaries = query(collection.list, all=True, sparse=True, limit=4097)
        else:
            # Explicit injected local adapters can already return complete models.
            summaries = query(collection.list, all=True)
        if len(summaries) > 4096:
            raise InventoryBudgetExceeded("Docker container inventory exceeds 4096 entries")
        identities = set()
        referenced_ids, referenced_names = set(), set()
        for claim in claims.values():
            for field, target in (("container_ids", referenced_ids), ("container_names", referenced_names),
                                  ("unresolved_container_names", referenced_names)):
                values = claim.get(field, [])
                if (not isinstance(values, list) or len(values) > 128
                        or any(not isinstance(value, str) or not value or len(value) > 256 for value in values)):
                    raise InventoryBudgetExceeded("Allocation references are not a complete supported snapshot")
                target.update(values)
            for field in ("unresolved_restarts", "observed_creation_ids"):
                mapping = claim.get(field, {})
                if not isinstance(mapping, dict) or len(mapping) > 128:
                    raise InventoryBudgetExceeded("Unresolved allocation references exceed their supported bounds")
                values = mapping.keys() if field == "unresolved_restarts" else mapping.values()
                if any(not isinstance(value, str) or not value or len(value) > 256 for value in values):
                    raise InventoryBudgetExceeded("Unresolved allocation references are incomplete")
                referenced_ids.update(values)
        result = []
        for summary in summaries:
            if not isinstance(summary.id, str) or not summary.id or len(summary.id) > 128:
                raise InventoryBudgetExceeded("Docker inventory contains an invalid identity")
            if summary.id in identities:
                raise InventoryBudgetExceeded("Docker inventory contains duplicate identities")
            identities.add(summary.id)
            attributes = summary.attrs
            raw_state = attributes.get("State")
            state = raw_state.get("Status") if isinstance(raw_state, dict) else raw_state
            names = attributes.get("Names")
            if names is None and not hasattr(collection, "get"):
                names = [getattr(summary, "name", None)]
            reliable_names = (isinstance(names, list) and 0 < len(names) <= 128
                              and all(isinstance(name, str) and 0 < len(name) <= 256 for name in names))
            labels = attributes.get("Labels")
            if "Labels" not in attributes and not hasattr(collection, "get"):
                labels = (attributes.get("Config") or {}).get("Labels")
            referenced = (summary.id in referenced_ids or reliable_names and
                          bool({name.lstrip('/') for name in names} & referenced_names))
            if (state in {"created", "exited", "dead"} and reliable_names
                    and isinstance(labels, dict) and not referenced):
                # The full bounded list proves this unrelated container stopped.
                # Its HostConfig cannot contribute to running NPU declarations.
                continue
            container = query(collection.get, summary.id) if hasattr(collection, "get") else summary
            if not isinstance(container.attrs.get("HostConfig"), dict):
                raise InventoryBudgetExceeded("Docker inspect did not provide complete device declarations")
            if getattr(container, "status", "") not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}:
                raise InventoryBudgetExceeded("Docker inspect returned an unknown container state")
            raw_devices = container.attrs["HostConfig"].get("Devices") or []
            if not isinstance(raw_devices, list) or len(raw_devices) > 8192:
                raise InventoryBudgetExceeded("Docker device declarations exceed their supported bounds")
            declared = []
            for device in raw_devices:
                if not isinstance(device, dict) or not isinstance(device.get("PathOnHost"), str):
                    raise InventoryBudgetExceeded("Docker device declarations are incomplete")
                if re.fullmatch(r"/dev/davinci\d+", device["PathOnHost"]):
                    declared.append({"PathOnHost": device["PathOnHost"]})
            if len(declared) > 256:
                raise InventoryBudgetExceeded("Docker NPU declarations exceed their supported bounds")
            labels = DockerRuntime._labels(container)
            selected = {key: labels[key] for key in (MANAGED_LABEL, SERVICE_LABEL, NODE_LABEL, "motor-console.operation-id") if key in labels}
            if any(not isinstance(value, str) or len(value) > 128 for value in selected.values()):
                raise InventoryBudgetExceeded("Docker ownership labels exceed their supported bounds")
            # Do not retain unrelated container environment, mounts or secrets.
            state = container.attrs.get("State") or {}
            result.append(SimpleNamespace(id=container.id, name=container.name, status=container.status,
                attrs={"Config": {"Labels": selected}, "HostConfig": {"Devices": declared},
                       "State": {key: state[key] for key in ("Status", "StartedAt") if key in state}}))
        return result

    def _collect_inventory(self) -> dict:
        from .resource_inventory import observation_times
        from .resource_freshness import elapsed_ns, mark_sample
        from .resource_telemetry import collect_resource_telemetry
        times = observation_times()
        inventory_ttl = datetime.fromisoformat(times["expires_at"]) - datetime.fromisoformat(times["collected_at"])
        deadline = time.monotonic() + 8
        def query(function, *args, **kwargs):
            if time.monotonic() >= deadline:
                raise InventoryBudgetExceeded("Docker inventory exceeded its query budget")
            value = function(*args, **kwargs)
            if time.monotonic() >= deadline:
                raise InventoryBudgetExceeded("Docker inventory exceeded its query budget")
            return value

        info, containers, errors = {}, None, []
        docker_verified = False
        busy: set[int] = set()
        devices = None
        try:
            client = query(self._inventory_docker)
            query(client.ping)
            docker_verified = True
            info = query(client.info)
        except InventoryBudgetExceeded:
            errors.append({"source": "docker_inventory", "code": "query_budget_exceeded",
                           "message": "Docker inventory query budget was exceeded; declarations are unknown"})
        except Exception as error:
            raise DockerUnavailable("Docker daemon is unavailable for read-only inventory") from error
        daemon = self._daemon_id(info)
        state, claims = None, None
        if not errors and daemon:
            try:
                state = query(self._state, read_timeout=1)
                claims = query(state.inventory_claims, daemon, timeout=1)
            except Exception:
                errors.append({"source": "allocation-ledger", "code": "ledger_unavailable",
                               "message": "Complete allocation references could not be read; declaration observation is unknown"})
        if not errors and daemon:
            try:
                containers = self._inventory_containers(client, query, claims)
                devices = query(self._devices)
                for container in containers:
                    if container.status in {"running", "paused", "restarting", "removing"}:
                        busy.update(self._declared_devices(container))
            except (InventoryBudgetExceeded, DockerUnavailable):
                containers, devices = None, None
                errors.append({"source": "docker_inventory", "code": "incomplete_container_inventory",
                               "message": "Docker/device collection exceeded its bounds or was incomplete; declarations are unknown"})
            except Exception:
                containers, devices = None, None
                errors.append({"source": "docker_inventory", "code": "container_inspection_failed",
                               "message": "Complete Docker container inspection failed; declarations are unknown"})
        if devices is None and not errors:
            errors.append({"source": "device-discovery", "code": "device_discovery_failed",
                           "message": "Host device discovery failed; NPU capacity is unknown"})
        if devices is not None and not busy.issubset(set(devices)):
            errors.append({"source": "device-discovery", "code": "device_declarations_outside_inventory",
                           "message": "Running device declarations are not fully represented in the discovered inventory"})
            devices = None
        ledger = None
        if not daemon:
            errors.append({"source": "docker-identity", "code": "daemon_identity_unavailable",
                           "message": "Docker daemon identity is unavailable; coordination scope cannot be verified"})
        elif containers is not None:
            try:
                query(state.reconcile, daemon, containers, (MANAGED_LABEL, SERVICE_LABEL, NODE_LABEL),
                      timeout=1, deadline=deadline, expected_claims=claims)
                ledger_started = elapsed_ns()
                ledger = mark_sample(query(state.allocations, daemon, timeout=1), started_ns=ledger_started)
                # This is the current read snapshot's expiry, never a rewrite
                # of durable allocation operation/transition timestamps.
                ledger["expires_at"] = (datetime.fromisoformat(ledger["collected_at"]) + inventory_ttl).isoformat()
            except Exception:
                ledger = None
                errors.append({"source": "allocation-ledger", "code": "ledger_unavailable",
                               "message": "Allocation ledger could not be reconciled"})
        node_address = os.environ.get("MOTOR_NODE_ADDRESS", "")
        nic = os.environ.get("MOTOR_NODE_NIC", "")
        if not nic and node_address:
            nic = self._network_nic(node_address)
        telemetry = (self.host_telemetry or collect_resource_telemetry)(self.host_root)
        if time.monotonic() >= deadline:
            devices, ledger = None, None
            errors.append({"source": "inventory", "code": "collection_budget_exceeded",
                           "message": "Collection exceeded its budget; no partial Docker declaration counts were published"})
        return {"status": "online", **({"docker_available": True} if docker_verified else {}),
                "inventory_schema": 2, **times,
                "inventory_status": "partial" if errors else "ready", "collection_errors": errors,
                "daemon_id": daemon, **telemetry,
                **({"resource_ledger": ledger} if ledger is not None else {}),
                "architecture": info.get("Architecture") or "",
                "docker_version": info.get("ServerVersion", ""),
                "npu_total": len(devices) if devices is not None else None,
                "npu_used": len(busy) if devices is not None else None,
                "devices": devices or [], "devices_in_use": sorted(busy) if devices is not None else [],
                "nic_name": nic,
                "address": node_address, "last_seen": _now(), "error": None,
                "resource_scope": "Counts describe Docker whole-device declarations; hardware observations are separate. "
                                  "The ledger coordinates this Agent and daemon; external privileged changes may race admission."}

    def images(self) -> list[dict]:
        client = self._docker()
        return [{"id": image.id, "tags": list(image.tags or []), "size": image.attrs.get("Size"),
                 "architecture": image.attrs.get("Architecture")} for image in client.images.list()]

    @staticmethod
    def _port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("0.0.0.0", port))
                return False
            except OSError:
                return True

    def _name_exists(self, name: str) -> bool:
        # list/get errors must not be interpreted as 'absent'.
        for container in self._docker().containers.list(all=True, filters={"name": name}):
            if container.name == name:
                return True
        return False

    def _check_binds(self, spec: dict) -> list[str]:
        failures = []
        workspace = PurePosixPath(self.workspace_dir)
        for kwargs in (spec["create_kwargs"], spec.get("render_kwargs")):
            if not kwargs:
                continue
            for source, mount in kwargs.get("volumes", {}).items():
                # A fresh per-job examples tree is materialized only at deploy.
                planned = PurePosixPath(source) == PurePosixPath(spec["examples_path"])
                if planned and workspace in PurePosixPath(source).parents:
                    continue
                try:
                    path = self._host_path(source)
                except OSError:
                    failures.append(f"Host bind cannot be resolved: {source}")
                    continue
                if not path.exists():
                    failures.append(f"Host bind does not exist: {source}")
                if source == spec["original_user_config"]["motor_deploy_config"].get("weight_mount_path"):
                    reserved = {self.host_root / name for name in ("", "etc", "root", "proc", "sys", "dev")}
                    if source in {"/", "/etc", "/root", "/proc", "/sys", "/dev"} or path in reserved or not path.is_dir():
                        failures.append("Weight bind must be a dedicated existing directory")
            for device in kwargs.get("devices", []):
                source = device.split(":", 1)[0]
                try:
                    path = self._host_path(source)
                    mode = path.stat().st_mode
                    if not stat.S_ISCHR(mode) and not (path.is_dir() and source in {"/dev/ummu", "/dev/uburma"}):
                        failures.append(f"Host device is not a character device: {source}")
                except OSError:
                    failures.append(f"Host device does not exist: {source}")
        return failures

    def _check_workspace(self) -> list[str]:
        actual = Path(self.workspace_dir)
        try:
            host = self._host_path(self.workspace_dir)
            if not actual.is_dir() or not host.is_dir():
                return ["The agent and Docker host must mount an existing MOTOR_HOST_WORKSPACE_DIR at the same absolute path"]
            if not actual.samefile(host):
                return ["Agent workspace and host workspace are different filesystems/paths; Docker bind would use different content"]
        except OSError as exc:
            return [f"Cannot verify shared host workspace: {exc}"]
        return []

    @staticmethod
    def _resource_numbers(value, label):
        if (not isinstance(value, list) or len(value) > 256
                or any(type(number) is not int or not 0 <= number <= 65535 for number in value)
                or len(value) != len(set(value))):
            raise ContainerConflict("Owned container has invalid " + label + " resource metadata")
        return set(value)

    def _container_ports(self, container, *, required=False):
        labels = self._labels(container)
        if "motor-console.resource-ports" in labels:
            raw = labels["motor-console.resource-ports"]
            if not isinstance(raw, str) or len(raw) > 2048:
                raise ContainerConflict("Owned container port claims are malformed")
            try:
                return self._resource_numbers(json.loads(raw), "port")
            except (ValueError, TypeError) as error:
                raise ContainerConflict("Owned container port claims are malformed") from error
        host = container.attrs.get("HostConfig") or {}
        ports = set()
        for bindings in (host.get("PortBindings") or {}).values():
            for binding in bindings or []:
                value = str(binding.get("HostPort", ""))
                if not value.isdecimal() or not 1 <= int(value) <= 65535:
                    raise ContainerConflict("Container port binding is not a verified numeric port")
                ports.add(int(value))
        if required and host.get("NetworkMode", "host") == "host":
            raise ContainerConflict("Legacy host-network container has no reviewed resource record; use a reviewed rebuild before restart")
        return ports

    @staticmethod
    def _container_pids(containers):
        pids = set()
        for container in containers:
            if getattr(container, "status", "") not in {"running", "restarting", "paused"}:
                continue
            try:
                result = container.top(ps_args="-eo pid")
                titles = [str(value).upper() for value in result.get("Titles", [])]
                column = titles.index("PID")
                for values in result.get("Processes", []):
                    value = str(values[column])
                    if not value.isdecimal() or not 1 <= int(value) <= 2**31 - 1:
                        raise ValueError("Invalid PID")
                    pids.add(int(value))
                    if len(pids) > 512:
                        raise ValueError("Too many target processes")
            except Exception as error:
                raise ContainerConflict("Cannot verify host process ownership for the restart targets") from error
        return pids

    def admission_context(self):
        from .provider_host.contracts import AdmissionContextV1
        from .resource_telemetry import collect_resource_telemetry
        return AdmissionContextV1(self.host_root, self.host_telemetry or collect_resource_telemetry,
            self._container_pids, self._devices, self._labels, self._declared_devices,
            self._resource_numbers, self._container_ports, self._port_in_use,
            self._owned_listener_ports, self._check_physical_usage)

    def _check_physical_usage(self, requested, owned):
        return self.admission.check_physical_usage(self.admission_context(), requested, owned)


    def _owned_listener_ports(self, owned):
        """Attribute existing host-network listeners; never assume a bound port is ours."""
        pids = self._container_pids(owned)
        inodes = set()
        deadline = time.monotonic() + 2
        seen = 0
        try:
            for pid in pids:
                for entry in self._host_path(f"/proc/{pid}/fd").iterdir():
                    seen += 1
                    if seen > 16384 or time.monotonic() > deadline:
                        raise OSError("Target listener inspection exceeded its budget")
                    try:
                        match = re.fullmatch(r"socket:\[(\d+)\]", os.readlink(entry))
                    except FileNotFoundError:
                        continue
                    if match:
                        inodes.add(match[1])
            listeners = {}
            readable = False
            for name in ("tcp", "tcp6"):
                path = self._host_path("/proc/net/" + name)
                if not path.exists():
                    continue
                with path.open("r", encoding="ascii") as source:
                    raw = source.read(2 * 1024 * 1024 + 1)
                if len(raw) > 2 * 1024 * 1024:
                    raise OSError("Host listener table exceeds its limit")
                readable = True
                for row in raw.splitlines()[1:]:
                    values = row.split()
                    if len(values) >= 10 and values[3] == "0A":
                        port = int(values[1].rsplit(":", 1)[1], 16)
                        listeners.setdefault(port, set()).add(values[9])
            if not readable:
                raise OSError("Host listener table is unavailable")
            return {port for port, owners in listeners.items() if owners and owners <= inodes}
        except (OSError, ValueError) as error:
            raise ContainerConflict("Cannot verify ownership of an occupied restart port") from error

    def _restart_admission(self, daemon, owned, containers, operation_id):
        return self.admission.restart(self.admission_context(), daemon, owned, containers, operation_id)


    @native_operation
    def preflight(self, payload: dict) -> dict:
        mode = execution_metadata(self.read_only)
        if mode["read_only"]:
            return {"valid": False, "checks": [{"name": "Agent execution mode", "status": "fail",
                    "detail": "Agent is read-only; complete explicit resource ownership handover before deployment"}],
                    "warnings": [], "command": "", "validation": "not_tested", **mode}
        from .native_contracts import native_contract_checks
        from .compatibility import public_profile
        checks = self.native_adapter.native_contract_checks(payload.get("user_config"), payload.get("env"), runtime="docker",
                                        commit=self.native_adapter.public_profile()["commit"])
        if any(item["status"] == "fail" for item in checks):
            return {"valid": False, "checks": checks, "warnings": [], "command": "", "validation": "not_tested"}
        client = self._docker()  # Missing daemon is HTTP 503, not a synthetic plan.
        warnings = ["NPU startup/inference and transport compatibility require hardware acceptance.",
                    "Docker device declarations cannot detect all host-process or Kubernetes NPU allocations."]
        if os.getenv("MOTOR_NPU_ADMISSION_MODE", "observed") == "declarations":
            warnings.append("Explicit declarations-only admission is configured; external host NPU activity is not excluded.")

        def check(name: str, status: str, detail: str):
            checks.append({"name": name, "status": status, "detail": detail})

        try:
            if payload.get("full_placements"):
                checks.extend(self.native_adapter.validate_layout(payload["user_config"], payload["full_placements"], env=payload.get("env", {}),
                                              nodes=payload.get("placement_nodes", {}), upstream_dir=self.upstream_dir))
                if any(item["status"] == "fail" for item in checks):
                    return {"valid": False, "checks": checks, "warnings": warnings, "command": ""}
            spec = self.native_adapter.build_container_spec(payload, self.upstream_dir, self.workspace_dir)
        except (ValueError, TypeError, KeyError, OSError, ImportError) as exc:
            check("Motor configuration", "fail", str(exc))
            return {"valid": False, "checks": checks, "warnings": warnings, "command": ""}
        check("Motor configuration", "pass", "Pinned official helpers accepted the configuration and Docker template")
        try:
            with self.native_adapter.official_helpers(self.upstream_dir) as h:
                h.docker.require_pod_ip_and_nic(payload["node"]["address"], payload["node"]["nic_name"])
            check("Host network", "pass", "Node IPv4 matches its actual network interface")
        except ValueError as exc:
            check("Host network", "fail", str(exc))
        info = client.info()
        if info.get("OSType", "linux") != "linux":
            check("Docker platform", "fail", "Motor requires a Linux Docker host")
        images = {spec["create_kwargs"]["image"]}
        image_bindings = {}
        if spec.get("render_kwargs"):
            images.add(spec["render_kwargs"]["image"])
        for image_name in sorted(images):
            try:
                image = client.images.get(image_name)
                image_id = getattr(image, "id", None) or image.attrs.get("Id")
                if not isinstance(image_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
                    raise MotorConfigError("Local image identity is unavailable; a mutable tag cannot be admitted")
                image_bindings[image_name] = image_id
                image_labels = (image.attrs.get("Config") or {}).get("Labels") or {}
                image_commit = image_labels.get("motor-console.commit") or image_labels.get("motor.commit")
                expected_commit = spec["create_kwargs"]["labels"]["motor-console.commit"]
                if image_name == spec["create_kwargs"]["image"]:
                    if image_commit and image_commit != expected_commit:
                        check("Motor image revision", "fail", f"Image declares Motor commit {image_commit}; pinned source is {expected_commit}")
                    elif not image_commit:
                        warnings.append("Motor image has no source commit label; installed wheel/source compatibility cannot be established by image inspection.")
                architecture = (image.attrs.get("Architecture") or "").lower()
                host_arch = (info.get("Architecture") or "").lower()
                normalize = {"aarch64": "arm64", "x86_64": "amd64"}
                if architecture and host_arch and normalize.get(architecture, architecture) != normalize.get(host_arch, host_arch):
                    check("Image " + image_name, "fail", f"Image architecture {architecture} differs from host {host_arch}")
                else:
                    check("Image " + image_name, "pass", "Image is available in this node's Docker daemon")
            except Exception as exc:
                check("Image " + image_name, "fail", f"Local image inspect failed: {exc}")
        for kwargs in (spec["create_kwargs"], spec.get("render_kwargs")):
            if not kwargs:
                continue
            for existing in client.containers.list(all=True, filters={"name": kwargs["name"]}):
                if existing.name != kwargs["name"]:
                    continue
                labels = self._labels(existing)
                allow_stopped = payload.get("allow_owned_stopped") is True \
                    and labels.get(MANAGED_LABEL) == "true" \
                    and labels.get(SERVICE_LABEL) == payload.get("service_id") \
                    and labels.get(NODE_LABEL) == spec["node_id"] \
                    and existing.status in {"exited", "created"}
                if allow_stopped:
                    check("Container name", "warn", f"Owned stopped container requires explicit rebuild removal: {existing.name}")
                else:
                    check("Container name", "fail", f"Existing container is preserved: {existing.name}")
        bind_errors = self._check_binds(spec)
        bind_errors.extend(self._check_workspace())
        check("Host binds and devices", "fail" if bind_errors else "pass",
              "; ".join(bind_errors) if bind_errors else "Official host bind sources and devices exist")
        busy: dict[int, str] = {}
        for container in client.containers.list(all=False):
            for device in self._declared_devices(container):
                busy[device] = container.name
        for device in spec["devices"]:
            if device in busy:
                check("NPU allocation", "fail", f"davinci{device} is attached to running container {busy[device]}")
        try:
            found = set(self._devices())
            if not set(spec["devices"]).issubset(found):
                check("NPU discovery", "fail", "Requested devices are not present in the current host inventory")
            self._check_physical_usage(set(spec["devices"]), [])
        except (DockerUnavailable, ContainerConflict) as error:
            check("NPU resource observation", "fail", str(error))
        local_ports: dict[int, str] = {}
        for item in spec["ports"]:
            port, name = item["port"], item["name"]
            if port in local_ports:
                check("Internal port layout", "fail", f"{name} and {local_ports[port]} both use {port}")
            else:
                local_ports[port] = name
            if self._port_in_use(port):
                check("Host ports", "fail", f"{name} port {port} is already bound")
        if not any(c["name"] in {"Internal port layout", "Host ports"} and c["status"] == "fail" for c in checks):
            check("Host ports", "pass", f"{len(local_ports)} configured listen ports are currently free")
        if spec["original_user_config"]["motor_deploy_config"].get("hardware_type", "").startswith("Ascend950"):
            warnings.append("A5 hardware/privileged networking is rendered from upstream and remains untested on this host.")
        for role in ("prefill", "decode", "union"):
            engine = (spec["original_user_config"].get(f"motor_engine_{role}_config") or {}).get("engine_config") or {}
            if any(int(engine.get(key, 1)) > 1 for key in ("nnodes", "pipeline_parallel_size", "prefill_context_parallel_size")):
                warnings.append(f"{role}: native PP/PCP/multi-host parameters are preserved; rank/transport correctness has not been validated on NPU hardware.")
        return {"valid": not any(c["status"] == "fail" for c in checks), "checks": checks,
                "warnings": warnings, "validation": "not_tested", "command": spec["command"],
                "image_bindings": image_bindings}

    @staticmethod
    def _pin_image_bindings(client, spec, bindings):
        """Use the final preflight identities even if a registry tag changes."""
        for kwargs in (spec["create_kwargs"], spec.get("render_kwargs")):
            if not kwargs:
                continue
            reference = kwargs["image"]
            expected = bindings.get(reference)
            image = client.images.get(reference)
            actual = getattr(image, "id", None) or image.attrs.get("Id")
            if not expected or actual != expected:
                raise ContainerConflict("Image tag changed after final preflight; no container was created")
            kwargs.setdefault("labels", {})["motor-console.image-reference"] = reference
            kwargs["image"] = expected

    def _materialize(self, spec: dict) -> None:
        root = Path(spec["run_root"])
        workspace = Path(self.workspace_dir).resolve()
        if not root.resolve().is_relative_to(workspace):
            raise MotorConfigError("Deployment workspace escapes MOTOR_HOST_WORKSPACE_DIR")
        if root.exists():
            raise ContainerConflict("Deployment workspace already exists; it will not be overwritten")
        root.mkdir(parents=True, mode=0o700)
        examples = Path(spec["examples_path"])
        shutil.copytree(self.upstream_dir / "examples", examples,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "motor_workspace", ".git"))
        # copytree preserves the immutable image source modes. The workspace is
        # a private deployment copy and must be writable with all capabilities
        # dropped; leave the installed native source immutable.
        for path in [examples, *examples.rglob("*")]:
            path.chmod(path.stat().st_mode | stat.S_IWUSR)
        directory = Path(spec["config_dir"])
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        for name, value in (("user_config.json", spec["original_user_config"]), ("env.json", spec["original_env"])):
            target = directory / name
            target.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            target.chmod(0o600)
        manifest = {"motor_commit": spec["create_kwargs"]["labels"]["motor-console.commit"],
                    "command": spec["command"], "container_name": spec["container_name"], "created_at": _now()}
        (root / "deployment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def deploy(self, payload: dict) -> dict:
        return self._execute("deploy", payload, self._deploy)

    @native_operation
    def _deploy(self, payload: dict) -> dict:
        with native_service_lock(self._lock):
            client = self._docker()
            daemon, _ = self._reconcile(client)
            payload = {key: value for key, value in payload.items() if key != "allow_owned_stopped"}
            plan = self.preflight(payload)
            if not plan["valid"]:
                raise MotorConfigError("; ".join(c["detail"] for c in plan["checks"] if c["status"] == "fail"))
            request = {**payload, "_workspace_id": uuid.uuid4().hex}
            spec = self.native_adapter.build_container_spec(request, self.upstream_dir, self.workspace_dir)
            self._pin_image_bindings(client, spec, plan.get("image_bindings") or {})
            allocation_id = "allocation_" + hashlib.sha256((daemon + ":" + spec["create_kwargs"]["name"]).encode()).hexdigest()[:32]
            names = [kwargs["name"] for kwargs in (spec["create_kwargs"], spec.get("render_kwargs")) if kwargs]
            ports = sorted({int(item["port"]) for item in spec["ports"]})
            claim = {"allocation_id": allocation_id, "daemon_id": daemon, "service_id": payload["service_id"],
                     "node_id": spec["node_id"], "operation_id": payload["operation_id"],
                     "devices": sorted(set(spec["devices"])), "ports": ports,
                     "container_ids": [], "container_names": names}
            self._state().reserve_many([claim])
            for kwargs in (spec["create_kwargs"], spec.get("render_kwargs")):
                if kwargs:
                    kwargs["labels"].update({"motor-console.operation-id": payload["operation_id"],
                                             "motor-console.allocation-id": allocation_id,
                                             "motor-console.resource-ports": json.dumps(ports, separators=(",", ":")),
                                             "motor-console.resource-devices": json.dumps(claim["devices"], separators=(",", ":"))})
            self._materialize(spec)
            created = []
            try:
                if spec.get("render_kwargs"):
                    self._state().creating(allocation_id, spec["render_kwargs"]["name"])
                    created.append(client.containers.run(**spec["render_kwargs"]))
                    self._state().attach(allocation_id, [created[-1].id], names=[created[-1].name])
                self._state().creating(allocation_id, spec["create_kwargs"]["name"])
                main = client.containers.run(**spec["create_kwargs"])
                created.append(main)
                self._state().attach(allocation_id, [main.id], names=[main.name])
                main.reload()
                if main.status in {"exited", "dead"}:
                    code = (main.attrs.get("State") or {}).get("ExitCode")
                    raise MotorConfigError(f"Official Motor startup process exited immediately (exit code {code}); inspect logs")
                return {"container_id": main.id, "status": "starting",
                        "message": "Container started with official Motor --start; readiness and inference are not yet verified",
                        "workspace": spec["run_root"]}
            except Exception:
                # SDK run can create successfully and then fail in start().
                # Recover only containers bearing this attempt's nonce.
                known_ids = {c.id for c in created}
                for kwargs in (spec["create_kwargs"], spec.get("render_kwargs")):
                    if not kwargs:
                        continue
                    try:
                        candidates = client.containers.list(all=True, filters={"name": kwargs["name"]})
                        for candidate in candidates:
                            labels = self._labels(candidate)
                            if candidate.name == kwargs["name"] and candidate.id not in known_ids \
                                    and labels.get("motor-console.job") == request["_workspace_id"] \
                                    and labels.get(MANAGED_LABEL) == "true" \
                                    and labels.get(SERVICE_LABEL) == payload.get("service_id"):
                                created.append(candidate)
                                known_ids.add(candidate.id)
                    except Exception:
                        pass
                # Only containers created by this call can be rolled back.
                # Preserve the failed main container and its logs; stop it if needed.
                for container in reversed(created):
                    labels = self._labels(container)
                    if labels.get(MANAGED_LABEL) != "true" or labels.get(SERVICE_LABEL) != payload.get("service_id"):
                        continue
                    try:
                        container.stop(timeout=30)
                        if labels.get(ROLE_LABEL) == "render":
                            container.remove(v=False)
                    except Exception:
                        pass
                raise

    def action(self, action: str, service_id: str, node_id: str | None = None,
               operation_id: str | None = None) -> dict:
        if action not in {"stop", "restart", "remove"}:
            raise MotorConfigError("Unknown lifecycle action")
        payload = {"service_id": service_id, "node_id": node_id, "operation_id": operation_id}
        return self._execute(action, payload, lambda value: self._action(action, service_id, node_id, value["operation_id"]))

    @native_operation
    def _action(self, action: str, service_id: str, node_id: str | None, operation_id: str) -> dict:
        with native_service_lock(self._lock):
            client = self._docker()
            daemon, containers = self._reconcile(client)
            owned = self._owned(service_id, node_id)
            for container in owned:
                container.reload()
                labels = self._labels(container)
                if (labels.get(MANAGED_LABEL) != "true" or labels.get(SERVICE_LABEL) != service_id
                        or not labels.get(NODE_LABEL) or (node_id is not None and labels.get(NODE_LABEL) != node_id)):
                    raise ContainerConflict("Container ownership changed; lifecycle operation refused")
            if not owned:
                if action == "restart":
                    raise ContainerConflict("No owned containers exist for restart; review and rebuild the deployment")
                return {"container_id": "", "status": "stopped", "message": "No owned service containers exist on this node"}
            if action == "remove" and any(c.status in {"running", "restarting", "paused"} for c in owned):
                raise ContainerConflict("Stop all owned service containers before removal")
            if action == "restart":
                claims = self._restart_admission(daemon, owned, containers, operation_id)
                self._state().reserve_many(claims)
                allocations = {identifier: claim["allocation_id"] for claim in claims for identifier in claim["container_ids"]}
            # Render restarts before its coordinator; stop management first.
            owned.sort(key=lambda c: self._labels(c).get(ROLE_LABEL) != "render", reverse=action != "restart")
            for container in owned:
                labels = self._labels(container)
                if (labels.get(MANAGED_LABEL) != "true" or labels.get(SERVICE_LABEL) != service_id
                        or not labels.get(NODE_LABEL) or (node_id is not None and labels.get(NODE_LABEL) != node_id)):
                    raise ContainerConflict("Container ownership changed; lifecycle operation refused")
                if action == "stop": container.stop(timeout=30)
                elif action == "restart":
                    self._state().restarting(allocations[container.id], container.id,
                                             (container.attrs.get("State") or {}).get("StartedAt"))
                    container.restart(timeout=30)
                else: container.remove(v=False)
                if action != "remove":
                    container.reload()
                    if action == "restart":
                        if container.status not in {"running", "restarting", "paused"}:
                            raise MotorConfigError("Owned container did not enter a running state during restart; inspect its logs")
                        self._state().restarted(allocations[container.id], container.id)
                    elif container.status in {"created", "exited", "dead"}:
                        self._state().confirmed_terminal(daemon, service_id, labels[NODE_LABEL], container.id, container.name)
                    else:
                        raise ContainerConflict("Owned container stop did not reach a confirmed terminal state")
                else:
                    self._state().confirmed_terminal(daemon, service_id, labels[NODE_LABEL], container.id, container.name)
            return {"container_id": ",".join(c.id for c in owned),
                    "status": "starting" if action == "restart" else "stopped",
                    "message": f"{action} completed for {len(owned)} owned containers; weights were retained"}

    def logs(self, service_id: str, tail: int = 200, node_id: str | None = None, role: str | None = None) -> dict:
        from .log_records import bounded_stream_bytes, fair_tail, log_record, role_matches, validate_log_role
        if isinstance(tail, bool) or not isinstance(tail, int) or not 1 <= tail <= 2000:
            raise MotorConfigError("tail must be in 1..2000")
        try:
            role = validate_log_role(role)
        except ValueError as error:
            raise MotorConfigError(str(error)) from error
        # Filter containers before reading/truncating their output. Combined
        # management containers retain their real role even under an alias.
        targets = sorted((container for container in self._owned(service_id, node_id)
                          if role_matches(self._labels(container).get(ROLE_LABEL), role)), key=lambda container: container.id)
        items, errors = [], []
        truncated = len(targets) > 64
        deadline = time.monotonic() + 8
        quota = max(1024, 128 * 1024 // max(1, min(64, len(targets))))
        for container in targets[:64]:
            labels = self._labels(container)
            if time.monotonic() >= deadline:
                errors.append({"container_id": container.id, "role": labels.get(ROLE_LABEL), "code": "log_read_deadline"})
                truncated = True
                continue
            try:
                stream = container.logs(tail=tail, timestamps=True, stream=True, follow=False)
                content, partial = bounded_stream_bytes(stream, deadline=deadline, maximum=quota)
            except Exception:
                # One rotated/deleted source must not discard the other sources
                # already read. Never echo SDK transport diagnostics or secrets.
                errors.append({"container_id": container.id, "role": labels.get(ROLE_LABEL), "code": "log_source_unavailable"})
                continue
            truncated = truncated or partial
            content = content.decode("utf-8", errors="replace")
            for line in content.splitlines():
                item = log_record(line, source="docker_stdout", node=labels.get(NODE_LABEL) or "",
                                  role=labels.get(ROLE_LABEL) or "", container_id=container.id,
                                  container_name=container.name)
                if partial:
                    item["message_truncated"] = True
                items.append(item)
        return {"items": fair_tail(items, tail), "errors": errors, "partial": truncated or bool(errors),
                "sources_total": len(targets), "window": "bounded_recent_tail", "filters": {"node_id": node_id, "role": role}}
