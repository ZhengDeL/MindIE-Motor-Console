"""Pinned Motor YAML renderer and UID-scoped Kubernetes agent operations.

Rendering always happens in an isolated copy in a short-lived helper process.
No cluster is contacted at import time. A plan never creates cluster resources.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import ssl
import subprocess
import sys
import threading
from .observation_cache import ObservationCache
from .observation_process import run_observation, ObservationCommandBusy, ObservationCommandFailed
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
import httpx
import yaml

from .kubernetes_autoscaling import hpa_is_paused, pause_managed_hpas, restore_managed_hpas
from .agent_credentials import authorize_agent
from .agent_capacity import native_operation, native_service_lock
from .execution_policy import AgentReadOnlyError, execution_metadata, require_mutations
from .operation_budget import operation_budget, remaining_time


MANAGED = "motor-console.managed"
SERVICE = "motor-console.service"
REVISION = "motor-console.revision"
CLUSTER_KINDS = {"ClusterRole", "ClusterRoleBinding"}
RESOURCE_NAMES = {
    "Namespace": "namespaces", "Deployment": "deployments", "StatefulSet": "statefulsets",
    "DaemonSet": "daemonsets", "Job": "jobs", "CronJob": "cronjobs", "Pod": "pods",
    "Service": "services", "ConfigMap": "configmaps", "Secret": "secrets",
    "ServiceAccount": "serviceaccounts", "Role": "roles", "RoleBinding": "rolebindings",
    "ClusterRole": "clusterroles", "ClusterRoleBinding": "clusterrolebindings",
    "PersistentVolumeClaim": "persistentvolumeclaims", "HorizontalPodAutoscaler": "horizontalpodautoscalers",
    "InferServiceSet": "inferservicesets", "InferService": "inferservices", "InstanceSet": "instancesets",
}
SCALABLE = {"Deployment", "StatefulSet", "InferServiceSet", "InferService", "InstanceSet"}
RESTARTABLE = {"Deployment", "StatefulSet", "DaemonSet"}
INSTANCE_FIELDS = {"p_instances_num": "prefill", "d_instances_num": "decode", "hybrid_instances_num": "union"}
_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def _lock(service_id: str) -> threading.RLock:
    with _locks_guard:
        return _locks.setdefault(service_id, threading.RLock())


def service_key(service_id: str) -> str:
    if not isinstance(service_id, str) or not service_id or len(service_id) > 256:
        raise HTTPException(422, "service_id must be a non-empty string of at most 256 characters")
    return hashlib.sha256(service_id.encode()).hexdigest()[:24]


def namespace_for(service_id: str) -> str:
    return "motor-console-" + service_key(service_id)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def _reference(doc: dict) -> dict:
    metadata = doc["metadata"]
    return {"apiVersion": doc["apiVersion"], "kind": doc["kind"], "name": metadata["name"],
            "namespace": metadata.get("namespace"), "uid": metadata.get("uid")}


def _identity(ref: dict) -> tuple[str, str, str]:
    return ref["apiVersion"], ref["kind"], ref["name"]


def _resource_path(ref: dict) -> str:
    api = ref["apiVersion"]
    base = "/api/v1" if api == "v1" else "/apis/" + api
    if ref.get("namespace"):
        base += "/namespaces/" + ref["namespace"]
    return base + "/" + RESOURCE_NAMES[ref["kind"]] + "/" + ref["name"]


class KubernetesRuntime:
    def __init__(self, data_dir: str | Path | None = None, upstream_dir: str | Path | None = None,
                 runner=None, read_only: bool | None = None):
        self.base = Path(data_dir or os.getenv("MOTOR_DATA_DIR", "/var/lib/motor-console")).resolve() / "kubernetes"
        self.upstream = Path(upstream_dir or os.getenv("MOTOR_UPSTREAM_DIR", "/opt/motor/upstream")).resolve()
        self.runner = runner or subprocess.run
        self._cluster_nodes_cache = ObservationCache()
        self.read_only = execution_metadata(read_only)["read_only"]

    def _require_mutations(self):
        try:
            require_mutations(self.read_only)
        except AgentReadOnlyError as error:
            raise HTTPException(403, str(error)) from error

    @staticmethod
    def _observation_source():
        # A replaced read-only kubeconfig is a different observation source.
        paths = os.getenv("KUBECONFIG", "").split(os.pathsep)
        if paths == [""]:
            paths = [str(Path.home() / ".kube/config")]
        if len(paths) > 8:
            raise HTTPException(503, "Kubeconfig source exceeds its supported bounds")
        result = []
        for name in paths:
            try:
                details = Path(name).stat()
                result.append((name, details.st_ino, details.st_mtime_ns, details.st_size))
            except OSError:
                result.append((name, None))
        return tuple(result)

    def _directory(self, service_id: str) -> Path:
        return self.base / service_key(service_id)

    def _load(self, service_id: str) -> dict:
        path = self._directory(service_id) / "ownership.json"
        if not path.is_file():
            raise HTTPException(404, "This agent has no ownership record for this Kubernetes service")
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("service_id") != service_id or state.get("namespace") != namespace_for(service_id):
            raise HTTPException(409, "Kubernetes ownership record does not match the service")
        return state

    def _save(self, state: dict) -> None:
        state["updated_at"] = _now()
        _json_write(self._directory(state["service_id"]) / "ownership.json", state)

    def _require_connection(self) -> None:
        if not shutil.which("kubectl"):
            raise HTTPException(503, "kubectl is not installed in this agent")
        configs = os.getenv("KUBECONFIG", "")
        if configs:
            paths = [p for p in configs.split(os.pathsep) if p]
            if not paths or not all(Path(p).is_file() for p in paths):
                raise HTTPException(503, "Configured kubeconfig is missing in this agent")
        elif not Path.home().joinpath(".kube/config").is_file():
            raise HTTPException(503, "A read-only kubeconfig mount and KUBECONFIG are required")

    def _kubectl(self, args: list[str], *, document: Any = None, allow_missing: bool = False) -> dict:
        timeout = remaining_time(40)
        argv = ["kubectl", "--cache-dir=/tmp/motor-console-kubectl-cache",
                f"--request-timeout={min(25, timeout):.3f}s", *args]
        raw = None if document is None else json.dumps(document, ensure_ascii=False)
        mutating = bool(args and args[0] in {"create", "replace", "patch", "delete", "apply", "scale", "rollout"}) and "--dry-run=client" not in args
        if mutating:
            self._cluster_nodes_cache.invalidate()
        try:
            result = self.runner(argv, input=raw, text=True, capture_output=True, timeout=timeout,
                                 check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, "Kubernetes command is unavailable or timed out") from exc
        finally:
            if mutating:
                self._cluster_nodes_cache.invalidate()
        if result.returncode:
            if allow_missing and ("NotFound" in result.stderr or "not found" in result.stderr.lower()):
                return {}
            raise HTTPException(502, "kubectl failed: " + (result.stderr or result.stdout)[-1600:])
        if not result.stdout.strip():
            return {}
        if args and args[0] == "logs":
            return {"text": result.stdout}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return {"text": result.stdout}

    def _get(self, ref: dict, missing: bool = False) -> dict:
        return self._kubectl(["get", "--raw", _resource_path(ref)], allow_missing=missing)

    def _read_endpoint_json(self, state: dict, path: str, *, timeout: float, maximum: int) -> dict | None:
        """Fixed read-only API paths for public exposure proof, never a URL proxy."""
        import math
        namespace = namespace_for(state["service_id"])
        if state.get("namespace") != namespace:
            raise HTTPException(409, "Endpoint namespace does not match the saved service identity")
        name = r"(?:[a-z0-9]|[a-z0-9][a-z0-9.-]{0,251}[a-z0-9])"
        scope = re.escape(namespace)
        paths = (
            rf"/api/v1/namespaces/{scope}",
            rf"/api/v1/namespaces/{scope}/services\?limit=17",
            r"/api/v1/nodes\?limit=501",
            rf"/apis/discovery\.k8s\.io/v1/namespaces/{scope}/endpointslices\?labelSelector=kubernetes\.io%2Fservice-name%3D{name}&limit=65",
            rf"/api/v1/namespaces/{scope}/(?:endpoints|pods|services)/{name}",
            rf"/apis/apps/v1/namespaces/{scope}/(?:deployments|replicasets|statefulsets)/{name}",
            rf"/apis/mindcluster\.huawei\.com/v1/namespaces/{scope}/(?:inferservicesets|inferservices|instancesets)/{name}",
        )
        if not isinstance(path, str) or not any(re.fullmatch(pattern, path) for pattern in paths):
            raise HTTPException(400, "Unsupported Kubernetes endpoint observation path")
        if (type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0
                or type(maximum) is not int or not 1 <= maximum <= 1024 * 1024):
            raise HTTPException(400, "Unsupported Kubernetes endpoint observation budget")
        # run_observation may spend two seconds reaping a killed subprocess or
        # inherited pipe. Reserve that cleanup within this operation's budget.
        budget = min(timeout, 2.0, remaining_time(8) - 2.05)
        if budget <= 0:
            raise HTTPException(504, "Kubernetes endpoint observation exhausted its total budget")
        argv = ["kubectl", "--cache-dir=/tmp/motor-console-kubectl-cache",
                f"--request-timeout={budget:.3f}s", "get", "--raw", path]
        try:
            result = run_observation(argv, timeout=budget, maximum=maximum,
                                     runner=None if self.runner is subprocess.run else self.runner)
        except ObservationCommandBusy as error:
            raise HTTPException(429, "Kubernetes observation is busy", headers={"Retry-After": "2"}) from error
        except (OSError, subprocess.TimeoutExpired, ObservationCommandFailed) as error:
            raise HTTPException(503, "Kubernetes endpoint observation exceeded its collection budget") from error
        if result.returncode:
            if re.match(r"^Error from server \(NotFound\):", (result.stderr or "").lstrip()):
                return None
            raise HTTPException(502, "Kubernetes endpoint observation failed; check API connectivity and read permissions")
        try:
            document = json.loads(result.stdout)
        except (TypeError, ValueError) as error:
            raise HTTPException(502, "Kubernetes endpoint observation returned invalid JSON") from error
        if not isinstance(document, dict):
            raise HTTPException(502, "Kubernetes endpoint observation must be an object")
        return document

    @operation_budget(8)
    def external_endpoints(self, service_id: str) -> dict:
        """Observe owned Service exposure; the Control constructs display URLs."""
        from .kubernetes_endpoints import discover_endpoints
        deadline = time.monotonic() + remaining_time(8)
        self._require_connection()
        state = self._load(service_id)
        def read_json(path: str, *, timeout: float, maximum: int) -> dict | None:
            return self._read_endpoint_json(state, path, timeout=timeout, maximum=maximum)
        # The producer verifies namespace/root UIDs and every owner chain using
        # this same bounded read path before returning any external candidates.
        result = discover_endpoints(state, read_json, deadline=deadline)
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 96 * 1024:
            raise HTTPException(502, "Kubernetes endpoint snapshot exceeds its supported bounds")
        return result

    @operation_budget(20)
    def cluster_nodes(self) -> dict:
        return self._cluster_nodes_cache.read(self._collect_cluster_nodes, source=self._observation_source())

    @operation_budget(20)
    def observation_version(self) -> dict:
        """Read-only discovery; execution queries keep their original path."""
        self._require_connection()
        return self._read_kubectl(["get", "--raw", "/version"])

    def _read_kubectl(self, args: list[str]) -> dict:
        from .fleet import CLUSTER_NODES_LIMIT
        if args not in (["get", "--raw", "/version"],
                        ["get", "--raw", f"/api/v1/nodes?limit={CLUSTER_NODES_LIMIT}"]):
            raise HTTPException(400, "Unsupported read-only Kubernetes observation")
        timeout = remaining_time(6)
        argv = ["kubectl", "--cache-dir=/tmp/motor-console-kubectl-cache",
                f"--request-timeout={min(4, timeout):.3f}s", *args]
        try:
            result = run_observation(argv, timeout=timeout,
                                     runner=None if self.runner is subprocess.run else self.runner)
        except ObservationCommandBusy as error:
            raise HTTPException(429, str(error), headers={"Retry-After": "2"}) from error
        except (OSError, subprocess.TimeoutExpired, ObservationCommandFailed) as error:
            raise HTTPException(503, "Kubernetes read-only observation exceeded its collection budget") from error
        if result.returncode:
            raise HTTPException(502, "Kubernetes read-only observation failed; check API connectivity and read permissions")
        try:
            return json.loads(result.stdout)
        except (ValueError, TypeError) as error:
            raise HTTPException(502, "Kubernetes returned an invalid observation document") from error

    def _collect_cluster_nodes(self) -> dict:
        """Read actual cluster Nodes, independently of this execution Agent's host."""
        from .fleet import CLUSTER_NODES_LIMIT, FleetError, cluster_nodes_projection
        self._require_connection()
        try:
            listing = self._read_kubectl(["get", "--raw", f"/api/v1/nodes?limit={CLUSTER_NODES_LIMIT}"])
            version = self._read_kubectl(["get", "--raw", "/version"])
        except HTTPException as exc:
            raise HTTPException(exc.status_code,
                                "Kubernetes node discovery failed; check API connectivity and list Nodes permission",
                                headers=exc.headers) from exc
        if (not isinstance(listing, dict) or not isinstance(listing.get("items"), list)
                or not isinstance(listing.get("metadata", {}), dict)):
            raise HTTPException(502, "Kubernetes API returned an invalid NodeList")
        if listing.get("metadata", {}).get("continue") or len(listing["items"]) > CLUSTER_NODES_LIMIT:
            raise HTTPException(502, "Kubernetes node inventory exceeds the 500 node limit; no partial list was returned")
        result = {"items": [], "collected_at": _now(),
                  "cluster_version": version.get("gitVersion", "") if isinstance(version, dict) else ""}
        def optional(document, field, default):
            value = document.get(field)
            return default if value is None else value

        try:
            for document in listing["items"]:
                if not isinstance(document, dict):
                    raise ValueError("invalid Node")
                metadata, spec, status = document.get("metadata", {}), document.get("spec", {}), document.get("status", {})
                if not all(isinstance(v, dict) for v in (metadata, spec, status)):
                    raise ValueError("invalid Node fields")
                labels, info = optional(metadata, "labels", {}), optional(status, "nodeInfo", {})
                if not isinstance(labels, dict) or not isinstance(info, dict):
                    raise ValueError("invalid Node metadata")
                conditions, addresses = optional(status, "conditions", []), optional(status, "addresses", [])
                if (not isinstance(conditions, list) or len(conditions) > 64
                        or not isinstance(addresses, list) or len(addresses) > 64
                        or any(not isinstance(v, dict) for v in [*conditions, *addresses])):
                    raise ValueError("invalid Node status")
                readiness = [v.get("status") for v in conditions if v.get("type") == "Ready"]
                if len(readiness) > 1:
                    raise ValueError("duplicate Ready condition")
                roles = sorted(k.removeprefix("node-role.kubernetes.io/") for k in labels
                               if isinstance(k, str) and k.startswith("node-role.kubernetes.io/")
                               and k != "node-role.kubernetes.io/")
                result["items"].append({
                    "name": metadata.get("name"), "uid": metadata.get("uid"), "roles": roles,
                    "internal_ips": [v.get("address") for v in addresses if v.get("type") == "InternalIP"],
                    "ready": readiness[0] if readiness else "Unknown",
                    "unschedulable": spec.get("unschedulable", False), "labels": labels,
                    "taints": optional(spec, "taints", []), "kubelet_version": info.get("kubeletVersion", ""),
                    "container_runtime": info.get("containerRuntimeVersion", ""),
                    "architecture": info.get("architecture", ""), "os": info.get("osImage") or info.get("operatingSystem", ""),
                    "resources": {"capacity": optional(status, "capacity", {}),
                                  "allocatable": optional(status, "allocatable", {})},
                })
            projected = cluster_nodes_projection(result)
        except (FleetError, ValueError, TypeError) as exc:
            raise HTTPException(502, "Kubernetes node inventory is invalid or exceeds the supported bounds") from exc
        projected["items"].sort(key=lambda item: item["name"])
        return projected

    @staticmethod
    def _owned(doc: dict, service_id: str, uid: str | None = None) -> None:
        metadata = doc.get("metadata", {})
        labels = metadata.get("labels", {})
        if labels.get(MANAGED) != "true" or labels.get(SERVICE) != service_key(service_id):
            raise HTTPException(409, "Resource ownership labels do not match; no mutation was performed")
        if not uid or metadata.get("uid") != uid:
            raise HTTPException(409, "Resource UID changed; refusing to adopt or mutate the replacement")

    def _verify_namespace(self, state: dict) -> dict:
        ref = {"apiVersion": "v1", "kind": "Namespace", "name": state["namespace"]}
        doc = self._get(ref)
        self._owned(doc, state["service_id"], state["namespace_uid"])
        return doc

    @native_operation
    def _render(self, payload: dict, namespace: str, revision: str) -> tuple[list[dict], Path]:
        """Use the pinned CLI and native configmap assets in isolated subprocesses."""
        source = self.upstream / "examples/deployer"
        if not (source / "deploy.py").is_file():
            raise HTTPException(503, "Pinned Motor deploy.py checkout is missing")
        from .motor_adapter import MotorConfigError, verify_upstream_source
        try:
            verify_upstream_source(self.upstream)
        except MotorConfigError as exc:
            raise HTTPException(409, "Pinned upstream verification failed: " + str(exc)) from exc
        directory = self._directory(payload["service_id"]) / "revisions" / revision
        directory.mkdir(parents=True, exist_ok=False)
        directory.chmod(0o700)
        _json_write(directory / "original.json", {k: payload.get(k) for k in ("user_config", "env")})
        config = copy.deepcopy(payload.get("user_config"))
        if not isinstance(config, dict) or not isinstance(config.get("motor_deploy_config"), dict):
            raise HTTPException(422, "user_config.motor_deploy_config is required")
        if not isinstance(payload.get("env"), dict):
            raise HTTPException(422, "env must be a JSON object")
        config["motor_deploy_config"]["job_id"] = namespace
        config_dir = directory / "config"
        _json_write(config_dir / "user_config.json", config)
        _json_write(config_dir / "env.json", payload["env"])
        deployer = directory / "deployer"
        shutil.copytree(source, deployer, ignore=shutil.ignore_patterns("__pycache__", "output_yamls", "motor_workspace"))
        # The image checkout is read-only; only this isolated copy is made writable.
        for path in [deployer, *deployer.rglob("*")]:
            path.chmod(path.stat().st_mode | 0o200)
        process_env = os.environ.copy()
        process_env["PYTHONDONTWRITEBYTECODE"] = "1"
        commands = [sys.executable, str(deployer / "deploy.py"), "--dry-run", "--nostep", "--skip-check",
                    "--config_dir", str(config_dir)]
        try:
            rendered = self.runner(commands, cwd=str(deployer), env=process_env, text=True, capture_output=True,
                                   timeout=remaining_time(150), check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, "Native Motor rendering failed to start or timed out") from exc
        (directory / "render.log").write_text(rendered.stdout + rendered.stderr, encoding="utf-8")
        if rendered.returncode:
            raise HTTPException(422, "Native Motor dry-run failed; inspect the agent revision render.log")
        # Native dry-run intentionally skips shell-env rendering and ConfigMap creation.
        # Do the file-only preparation on the private copy using the original helpers.
        helper = (
            "import json,pathlib;from lib.utils import read_json,set_env_to_shell;"
            "from lib.generator.k8s_utils import get_deploy_mode_from_config;"
            "from lib.config_validator import validate_node_selectors;"
            "from lib.prepare_utils import configmap_assets;"
            "u=read_json(" + repr(str(config_dir / "user_config.json")) + ");"
            "validate_node_selectors(u);"
            "set_env_to_shell(u," + repr(str(config_dir / "env.json")) + ","
            "get_deploy_mode_from_config(u['motor_deploy_config']));"
            "d={n:p.read_text(encoding='utf-8') for p,n in configmap_assets(pathlib.Path.cwd())};"
            "d['user_config.json']=json.dumps(u,ensure_ascii=False);"
            "d['nodeport_conflict_coordinator.txt']='';d['nodeport_conflict_controller.txt']='';"
            "pathlib.Path('assets.json').write_text(json.dumps(d),encoding='utf-8')"
        )
        try:
            prepared = self.runner([sys.executable, "-c", helper], cwd=str(deployer), env=process_env, text=True,
                                   capture_output=True, timeout=remaining_time(45), check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, "Native ConfigMap preparation failed to start or timed out") from exc
        if prepared.returncode:
            raise HTTPException(422, "Native Motor ConfigMap preparation failed")
        documents: list[dict] = []
        for path in sorted((deployer / "output_yamls").glob("*.y*ml")):
            documents.extend(doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc)
        assets = json.loads((deployer / "assets.json").read_text(encoding="utf-8"))
        documents.insert(0, {"apiVersion": "v1", "kind": "ConfigMap",
                             "metadata": {"name": "motor-config", "namespace": namespace}, "data": assets})
        if len(documents) == 1:
            raise HTTPException(422, "Native Motor renderer produced no workload YAML")
        return documents, directory

    @staticmethod
    def _scope(documents: list[dict], namespace: str, service_id: str, revision: str) -> list[dict]:
        scoped = copy.deepcopy(documents)
        seen = set()
        for doc in scoped:
            if not isinstance(doc, dict):
                raise HTTPException(422, "Every manifest must be a Kubernetes JSON object")
            kind = doc.get("kind")
            if kind not in RESOURCE_NAMES or kind == "Namespace":
                raise HTTPException(422, "Unsupported or cluster-wide manifest kind: " + str(kind))
            if not re.fullmatch(r"(?:[a-z0-9][a-z0-9.-]*/)?v[0-9]+(?:(?:alpha|beta)[0-9]+)?", doc.get("apiVersion", "")):
                raise HTTPException(422, "Invalid Kubernetes apiVersion")
            metadata = doc.setdefault("metadata", {})
            if not isinstance(metadata, dict):
                raise HTTPException(422, "Manifest metadata must be an object")
            name = metadata.get("name", "")
            if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]|[a-z0-9]", name):
                raise HTTPException(422, "Invalid Kubernetes resource name")
            if kind in CLUSTER_KINDS:
                if not name.endswith("-" + namespace):
                    raise HTTPException(422, "Native cluster RBAC must be named for the isolated namespace")
                metadata.pop("namespace", None)
                if kind == "ClusterRoleBinding":
                    if not doc.get("roleRef", {}).get("name", "").endswith("-" + namespace):
                        raise HTTPException(422, "ClusterRoleBinding references an unowned role")
                    for subject in doc.get("subjects", []):
                        if subject.get("kind") != "ServiceAccount" or subject.get("namespace") != namespace:
                            raise HTTPException(422, "ClusterRoleBinding has an unowned subject")
            else:
                if metadata.get("namespace", namespace) != namespace:
                    raise HTTPException(422, "Manifest references an unrelated namespace")
                metadata["namespace"] = namespace
            if kind == "StatefulSet" and "Delete" in doc.get("spec", {}).get("persistentVolumeClaimRetentionPolicy", {}).values():
                raise HTTPException(422, "StatefulSet PVC retention must not delete storage on scale or removal")
            labels = {MANAGED: "true", SERVICE: service_key(service_id), REVISION: revision[:63]}
            metadata.setdefault("labels", {}).update(labels)
            # Propagate ownership to native Pod templates without changing selectors.
            def label_templates(value):
                if isinstance(value, dict):
                    for key, item in value.items():
                        template_spec = item.get("spec") if isinstance(item, dict) else None
                        if (key == "template" and isinstance(template_spec, dict)
                                and any(isinstance(template_spec.get(field), list) for field in ("containers", "initContainers"))):
                            item.setdefault("metadata", {}).setdefault("labels", {}).update(labels)
                        label_templates(item)
                elif isinstance(value, list):
                    for item in value:
                        label_templates(item)
            label_templates(doc.get("spec", {}))
            identity = _identity(_reference(doc))
            if identity in seen:
                raise HTTPException(422, "Native render produced duplicate resource identities")
            seen.add(identity)
        return scoped

    @native_operation
    @operation_budget(110)
    def plan(self, payload: dict) -> dict:
        service_id = payload.get("service_id")
        namespace = namespace_for(service_id)
        from .native_contracts import native_contract_checks
        from .compatibility import public_profile
        contract_checks = native_contract_checks(payload.get("user_config"), payload.get("env"), runtime="kubernetes",
                                                 commit=public_profile()["commit"])
        if any(item["status"] == "fail" for item in contract_checks):
            return {"valid": False, "checks": contract_checks, "warnings": [], "command": "", "namespace": namespace,
                    "resources": [], "manifest_yaml": "", "extra_manifest_sha256": None}
        self._require_connection()
        revision = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        revision += "-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        documents, directory = self._render(payload, namespace, revision)
        extra = payload.get("extra_manifests") or []
        if extra:
            if not isinstance(extra, list) or any(not isinstance(d, dict) or d.get("kind") in CLUSTER_KINDS for d in extra):
                raise HTTPException(422, "Extra manifests must be reviewed namespace-scoped objects")
            documents.extend(copy.deepcopy(extra))
        documents = self._scope(documents, namespace, service_id, revision)
        # Client schema validation is read-only even for a namespace that has
        # not been created. Validate the entire batch before any resource write;
        # this does not prove server admission or permission to create objects.
        self._kubectl(["create", "--dry-run=client", "--validate=true", "-f", "-", "-o", "json"],
                      document={"apiVersion": "v1", "kind": "List", "items": documents})
        manifest = directory / "reviewed-manifests.yaml"
        manifest.write_text(yaml.safe_dump_all(documents, allow_unicode=True, sort_keys=False), encoding="utf-8")
        try:
            state = self._load(service_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            state = None
        existing_ns = self._get({"apiVersion": "v1", "kind": "Namespace", "name": namespace}, missing=True)
        if existing_ns:
            if not state:
                raise HTTPException(409, "Target namespace already exists without this agent's ownership record")
            self._owned(existing_ns, service_id, state["namespace_uid"])
        previous = {_identity(ref): ref for ref in (state or {}).get("resources", [])}
        rendered_identities = {_identity(_reference(doc)) for doc in documents}
        removed = []
        for key, ref in previous.items():
            if key in rendered_identities or ref["kind"] == "PersistentVolumeClaim":
                continue
            if state and hpa_is_paused(state, ref):
                if self._get(ref, missing=True):
                    raise HTTPException(409, "A paused HPA still exists; complete its deletion before changing the desired policy")
                continue
            removed.append(ref)
        if removed:
            raise HTTPException(409, "This revision removes existing workload resources; explicitly remove the old deployment before changing topology")
        for doc in documents:
            ref = _reference(doc)
            existing = self._get(ref, missing=True)
            if existing:
                tracked = previous.get(_identity(ref))
                if not tracked:
                    raise HTTPException(409, "A rendered resource already exists and is not tracked by this service")
                self._owned(existing, service_id, tracked["uid"])
        # Namespace/resource UID checks remain independent of schema validation.
        # Actual create/replace still performs server admission and authorization.
        checks = [*contract_checks, {"name": "native-render", "status": "pass", "detail": "Pinned deploy.py --dry-run generated YAML"},
                  {"name": "client-schema", "status": "pass", "detail": "Complete scoped manifest list passed kubectl client schema validation; this is not server admission or RBAC acceptance"},
                  {"name": "ownership", "status": "pass", "detail": "Namespace and existing resource identities checked"},
                  {"name": "server-admission", "status": "warn", "detail": "Server admission and create/replace authorization are checked at deployment; plan does not create the namespace or workloads"}]
        mode = execution_metadata(self.read_only)
        if mode["read_only"]:
            checks.append({"name": "execution-mode", "status": "fail",
                           "detail": "Agent is read-only; explicit ownership handover is required before applying resources"})
        extra_hash = hashlib.sha256(json.dumps(extra, sort_keys=True).encode()).hexdigest() if extra else None
        result = {"valid": not mode["read_only"], **mode, "checks": checks, "warnings": ["Kubernetes/NPU readiness and inference are not yet verified"],
                  "command": "kubectl create/replace <reviewed native YAML> --namespace " + namespace,
                  "namespace": namespace, "revision_id": revision, "resources": [_reference(d) for d in documents],
                  "extra_manifest_sha256": extra_hash, "manifest_path": str(manifest),
                  "manifest_yaml": manifest.read_text(encoding="utf-8"), "_documents": documents}
        _json_write(directory / "plan.json", {k: v for k, v in result.items() if k != "_documents"})
        return result

    @native_operation
    @operation_budget(230)
    def deploy(self, payload: dict) -> dict:
        self._require_mutations()
        service_id = payload.get("service_id")
        with native_service_lock(_lock(service_key(service_id))):
            plan = self.plan(payload)
            if not plan.get("valid"):
                raise HTTPException(409, "Native deployment contract failed: " + "; ".join(
                    item["detail"] for item in plan.get("checks", []) if item.get("status") == "fail"))
            if plan["extra_manifest_sha256"] and payload.get("extra_manifests_approved_sha256") != plan["extra_manifest_sha256"]:
                raise HTTPException(409, "Extra manifest hash must be explicitly reviewed and approved before deployment")
            try:
                state = self._load(service_id)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                state = {"service_id": service_id, "namespace": plan["namespace"], "resources": [], "namespace_uid": None}
            try:
                if not state["namespace_uid"]:
                    doc = self._kubectl(["create", "-f", "-", "-o", "json"], document={
                        "apiVersion": "v1", "kind": "Namespace", "metadata": {"name": state["namespace"],
                        "labels": {MANAGED: "true", SERVICE: service_key(service_id)}}})
                    state["namespace_uid"] = doc["metadata"]["uid"]
                    self._save(state)
                self._verify_namespace(state)
                for doc in plan["_documents"]:
                    ref = _reference(doc)
                    tracked = next((r for r in state["resources"] if _identity(r) == _identity(ref)), None)
                    if tracked and hpa_is_paused(state, tracked):
                        continue  # Restore the approved HPA only after its tracked workloads.
                    existing = self._get(ref, missing=True)
                    if existing:
                        if not tracked:
                            raise HTTPException(409, "Refusing to overwrite an untracked resource")
                        self._owned(existing, service_id, tracked["uid"])
                        if doc["kind"] == "Service":
                            for field in ("clusterIP", "clusterIPs", "ipFamilies", "ipFamilyPolicy", "healthCheckNodePort"):
                                if field in existing.get("spec", {}) and field not in doc.get("spec", {}):
                                    doc.setdefault("spec", {})[field] = existing["spec"][field]
                        doc["metadata"].update({"uid": tracked["uid"], "resourceVersion": existing["metadata"]["resourceVersion"]})
                        created = self._kubectl(["replace", "-f", "-", "-o", "json"], document=doc)
                    else:
                        created = self._kubectl(["create", "-f", "-", "-o", "json"], document=doc)
                    current = _reference(created)
                    if tracked:
                        state["resources"].remove(tracked)
                    state["resources"].append(current)
                    self._save(state)  # Preserve every UID even after a later partial failure.
                if state.get("paused_hpas"):
                    restore_managed_hpas(self, state, reason="deploy", desired_documents=plan["_documents"])
                state.update(status="starting", revision_id=plan["revision_id"], last_error=None,
                             original_user_config=copy.deepcopy(payload["user_config"]),
                             original_env=copy.deepcopy(payload["env"]))
                # A fully applied revision supplies the desired replica counts.
                # A previous stop cycle must not override this reviewed baseline.
                state.pop("saved_replicas", None)
                state.pop("iss_role_stop_baselines", None)
                self._save(state)
            except Exception as exc:
                state.update(status="error", last_error=str(getattr(exc, "detail", exc)))
                self._save(state)
                raise
            return {"container_id": state["namespace"], "namespace": state["namespace"], "status": "starting",
                    "message": "Native Kubernetes resources submitted; readiness is not yet verified", "resources": state["resources"]}

    def _verified_resources(self, service_id: str) -> tuple[dict, list[tuple[dict, dict]]]:
        self._require_connection()
        state = self._load(service_id)
        self._verify_namespace(state)
        resources = []
        for ref in state["resources"]:
            doc = self._get(ref, missing=True)
            if doc:
                self._owned(doc, service_id, ref["uid"])
                resources.append((ref, doc))
        return state, resources

    def _patch(self, ref: dict, operations: list[dict]) -> dict:
        patch = [{"op": "test", "path": "/metadata/uid", "value": ref["uid"]}, *operations]
        target = RESOURCE_NAMES[ref["kind"]]
        if "/" in ref["apiVersion"]:
            target += "." + ref["apiVersion"].split("/")[0]
        args = ["patch", target, ref["name"], "--type=json", "-p", json.dumps(patch), "-o", "json"]
        if ref.get("namespace"):
            args += ["--namespace", ref["namespace"]]
        return self._kubectl(args)

    @native_operation
    def _render_scale(self, baseline: dict, target: dict, document: dict,
                      service_id: str, revision: str) -> tuple[dict, Path]:
        """Run only the native, file-only replica updater; never run the update CLI.

        Pinned deploy.py --dry-run does not cover --update_instance_num. Its CLI
        reads and mutates the cluster, so scaling uses its original pure helpers
        against a private copy of the already owned InferServiceSet instead.
        """
        from .motor_adapter import MotorConfigError, verify_upstream_source
        try:
            verify_upstream_source(self.upstream)
        except MotorConfigError as exc:
            raise HTTPException(409, "Pinned upstream verification failed: " + str(exc)) from exc
        source = self.upstream / "examples/deployer"
        directory = self._directory(service_id) / "revisions" / revision
        directory.mkdir(parents=True, exist_ok=False)
        directory.chmod(0o700)
        _json_write(directory / "baseline.json", baseline)
        _json_write(directory / "target.json", target)
        manifest = directory / "infer-service.yaml"
        manifest.write_text(yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")
        deployer = directory / "deployer"
        shutil.copytree(source, deployer, ignore=shutil.ignore_patterns("__pycache__", "output_yamls", "motor_workspace"))
        for path in [deployer, *deployer.rglob("*")]:
            path.chmod(path.stat().st_mode | 0o200)
        helper = (
            "import json,pathlib;"
            "from lib.config_validator import validate_only_instance_changed;"
            "from lib.generator.engine import validate_instance_nums;"
            "from lib.generator.infer_service import update_infer_service_replicas_only;"
            "b=json.loads(pathlib.Path(" + repr(str(directory / "baseline.json")) + ").read_text(encoding='utf-8'));"
            "u=json.loads(pathlib.Path(" + repr(str(directory / "target.json")) + ").read_text(encoding='utf-8'));"
            "validate_only_instance_changed(u,b);validate_instance_nums(u);"
            "update_infer_service_replicas_only(" + repr(str(manifest)) + ",u['motor_deploy_config'],u)"
        )
        process_env = os.environ.copy()
        process_env["PYTHONDONTWRITEBYTECODE"] = "1"
        process_env["KUBECONFIG"] = str(directory / "no-cluster-access")
        try:
            result = self.runner([sys.executable, "-c", helper], cwd=str(deployer), env=process_env,
                                 text=True, capture_output=True, timeout=remaining_time(45), check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, "Native Motor replica rendering failed to start or timed out") from exc
        (directory / "render.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        if result.returncode:
            raise HTTPException(422, "Native Motor replica rendering failed; inspect the agent revision render.log")
        documents = [d for d in yaml.safe_load_all(manifest.read_text(encoding="utf-8")) if d]
        if len(documents) != 1 or not isinstance(documents[0], dict):
            raise HTTPException(422, "Native replica updater must return the single owned InferServiceSet")
        return documents[0], directory

    @native_operation
    @operation_budget(230)
    def scale(self, payload: dict) -> dict:
        """Online, counts-only scaling for the pinned InferServiceSet contract.

        Multi-deployment scaling creates/deletes engine identities and single
        container scaling may restart its control plane. Neither is inferred from
        generic spec.replicas. A failed multi-resource update is recorded without
        an automatic rollback that could overwrite later cluster changes.
        """
        if set(payload) - {"service_id", "counts", "dry_run", "motor_profile"}:
            raise HTTPException(422, "Scaling accepts only service_id, counts and dry_run")
        if payload.get("motor_profile"):
            from .compatibility import check_binding
            check_binding(payload["motor_profile"])
        counts = payload.get("counts")
        dry_run = payload.get("dry_run", False)
        if not isinstance(dry_run, bool):
            raise HTTPException(422, "dry_run must be a boolean")
        if not dry_run:
            self._require_mutations()
        if not isinstance(counts, dict) or not counts or set(counts) - INSTANCE_FIELDS.keys():
            raise HTTPException(422, "counts must contain only native P/D or hybrid instance fields")
        if any(type(value) is not int or not 1 <= value <= 16 for value in counts.values()):
            raise HTTPException(422, "Instance counts must be integers between 1 and 16")
        service_id = payload.get("service_id")
        with native_service_lock(_lock(service_key(service_id))):
            state, resources = self._verified_resources(service_id)
            if len(resources) != len(state["resources"]) or any(d.get("metadata", {}).get("deletionTimestamp") for _, d in resources):
                raise HTTPException(409, "Scaling requires every tracked resource to exist without pending deletion")
            if state.get("status") in {"stopped", "stopping", "removed", "error"} or state.get("scale_pending"):
                raise HTTPException(409, "Resolve the stopped, failed or partial deployment before scaling")
            original = state.get("original_user_config")
            if not isinstance(original, dict) or not isinstance(original.get("motor_deploy_config"), dict):
                raise HTTPException(409, "Saved original Motor configuration is unavailable on this agent")
            deploy = original["motor_deploy_config"]
            if deploy.get("deploy_mode", "infer_service_set") != "infer_service_set":
                raise HTTPException(409, "Online scaling supports only native InferServiceSet deployments")
            fields = {"hybrid_instances_num"} if "hybrid_instances_num" in deploy else {"p_instances_num", "d_instances_num"}
            if set(counts) - fields or any(field not in deploy for field in fields):
                raise HTTPException(422, "Instance fields must match the currently deployed P/D or hybrid topology")
            if any(type(deploy[field]) is not int or not 1 <= deploy[field] <= 16 for field in fields):
                raise HTTPException(409, "Saved instance baseline is outside this scaling contract")
            workloads = [(r, d) for r, d in resources if r["kind"] == "InferServiceSet"]
            configmaps = [(r, d) for r, d in resources if r["kind"] == "ConfigMap" and r["name"] == "motor-config"]
            if len(workloads) != 1 or workloads[0][0]["apiVersion"] != "mindcluster.huawei.com/v1" or len(configmaps) != 1:
                raise HTTPException(409, "Scaling requires one tracked native InferServiceSet and its motor-config ConfigMap")
            ref, document = workloads[0]
            cm_ref, configmap = configmaps[0]
            if document.get("spec", {}).get("replicas", 1) != 1:
                raise HTTPException(409, "Scaling supports the native single InferServiceSet replica layout only")
            roles = document.get("spec", {}).get("template", {}).get("roles", [])
            if not isinstance(roles, list) or any(not isinstance(role, dict) or not isinstance(role.get("name"), str) for role in roles):
                raise HTTPException(409, "InferServiceSet roles are malformed")
            role_names = [role["name"] for role in roles]
            if len(set(role_names)) != len(role_names):
                raise HTTPException(409, "InferServiceSet role names must be unique")
            if (original.get("scaling_policy") or any(role.get("scalingPolicy") for role in roles)
                    or any(r["kind"] == "HorizontalPodAutoscaler" for r, _ in resources)):
                raise HTTPException(409, "Manual scaling cannot compete with configured HPA/scalingPolicy")
            hpas = self._kubectl(["get", "horizontalpodautoscalers", "--namespace", state["namespace"], "-o", "json"])
            if hpas.get("items"):
                raise HTTPException(409, "Manual scaling requires an isolated namespace without HPA controllers")
            baseline = copy.deepcopy(original)
            baseline["motor_deploy_config"]["job_id"] = state["namespace"]
            raw_baseline = configmap.get("data", {}).get("user_config.json")
            try:
                current_baseline = json.loads(raw_baseline)
            except (TypeError, ValueError) as exc:
                raise HTTPException(409, "Owned motor-config has no valid user_config baseline") from exc
            if current_baseline != baseline:
                raise HTTPException(409, "Cluster ConfigMap drifted from the saved deployment; reconcile before scaling")
            for resource in (document, configmap):
                if not resource.get("metadata", {}).get("resourceVersion"):
                    raise HTTPException(409, "Scaling requires resourceVersion concurrency preconditions")
            target_config = copy.deepcopy(original)
            target_config["motor_deploy_config"].update(counts)
            target = copy.deepcopy(target_config)
            target["motor_deploy_config"]["job_id"] = state["namespace"]
            expected = copy.deepcopy(document)
            operations = [{"op": "test", "path": "/metadata/resourceVersion", "value": document["metadata"]["resourceVersion"]}]
            for field in sorted(fields):
                name = INSTANCE_FIELDS[field]
                if name not in role_names:
                    raise HTTPException(409, "Native engine role is missing: " + name)
                index = role_names.index(name)
                before = roles[index].get("replicas")
                if type(before) is not int or before != deploy[field]:
                    raise HTTPException(409, "Live role replicas drifted from the saved instance baseline")
                value = target["motor_deploy_config"][field]
                expected["spec"]["template"]["roles"][index]["replicas"] = value
                operations.extend([{"op": "test", "path": f"/spec/template/roles/{index}/name", "value": name},
                                   {"op": "test", "path": f"/spec/template/roles/{index}/replicas", "value": before}])
                if value != before:
                    operations.append({"op": "replace", "path": f"/spec/template/roles/{index}/replicas", "value": value})
            revision = "scale-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
            rendered, directory = self._render_scale(baseline, target, document, service_id, revision)
            if rendered != expected:
                raise HTTPException(409, "Native update changed fields beyond the allowed engine-role replicas")
            changed = any(op["op"] == "replace" for op in operations)
            configmap_operations = [
                {"op": "test", "path": "/metadata/resourceVersion", "value": configmap["metadata"]["resourceVersion"]},
                {"op": "test", "path": "/data/user_config.json", "value": raw_baseline},
                {"op": "replace", "path": "/data/user_config.json", "value": json.dumps(target, ensure_ascii=False)},
            ]
            changes = [{"resource": ref, "operations": operations},
                       {"resource": cm_ref, "operations": configmap_operations}] if changed else []
            result = {"supported": True, "valid": True, "dry_run": dry_run, "submitted": False,
                      "status": "planned" if dry_run else state.get("status", "starting"),
                      "namespace": state["namespace"], "container_id": state["namespace"], "revision_id": revision,
                      "counts": {field: target["motor_deploy_config"][field] for field in sorted(fields)},
                      "user_config": target_config, "resources": state["resources"], "changes": changes,
                      "message": "Native counts-only scale plan validated; readiness is not yet verified"}
            _json_write(directory / "scale-plan.json", result)
            if dry_run or not changed:
                return result
            pending = {"revision_id": revision, "counts": result["counts"], "previous_counts": {f: deploy[f] for f in sorted(fields)},
                       "applied_resources": [], "planned_resources": [ref, cm_ref], "started_at": _now()}
            state["scale_pending"] = pending
            self._save(state)
            try:
                self._patch(ref, operations)
                pending["applied_resources"].append(ref)
                self._save(state)
                self._patch(cm_ref, configmap_operations)
                pending["applied_resources"].append(cm_ref)
                state.update(status="starting", revision_id=revision, original_user_config=target_config, last_error=None)
                state["last_scale"] = copy.deepcopy(pending)
                state.pop("scale_pending", None)
                self._save(state)
            except Exception as exc:
                state.update(status="error", last_error=str(getattr(exc, "detail", exc)))
                pending["last_error"] = state["last_error"]
                pending["recovery"] = "Inspect applied resources and reconcile with a reviewed full deployment; no automatic rollback was attempted"
                self._save(state)
                raise
            result.update(status="starting", submitted=True,
                          message="Owned native role replica changes submitted; readiness and inference are not yet verified")
            return result

    @operation_budget(80)
    def action(self, service_id: str, action: str) -> dict:
        self._require_mutations()
        with _lock(service_key(service_id)):
            state, resources = self._verified_resources(service_id)
            workloads = [(r, d) for r, d in resources if r["kind"] in SCALABLE | RESTARTABLE]
            if action in {"stop", "restart"} and not workloads:
                raise HTTPException(409, "No tracked native workloads are available for this action")
            if action == "stop":
                if any(r["kind"] in {"DaemonSet", "Job", "CronJob", "Pod"} for r, _ in resources):
                    raise HTTPException(409, "Stop requires replica-scalable native workloads; use explicit undeploy for DaemonSet/Job/Pod resources")
                from .kubernetes_role_stop import BASELINES, plan_role_stop, verify_role_stopped
                role_plans = {ref["uid"]: plan_role_stop(state, ref, document)
                              for ref, document in workloads if ref["kind"] == "InferServiceSet"}
                if state.get("status") not in {"stopping", "stopped"}:
                    state["saved_replicas"] = {}
                state["saved_replicas"] = state.get("saved_replicas", {})
                for ref, doc in workloads:
                    original = role_plans[ref["uid"]]["baseline"]["set_replicas"] if ref["uid"] in role_plans else doc.get("spec", {}).get("replicas", 1)
                    state["saved_replicas"].setdefault(ref["uid"], original)
                if role_plans:
                    state.setdefault(BASELINES, {})
                    for identifier, plan in role_plans.items():
                        state[BASELINES].setdefault(identifier, plan["baseline"])
                # Persist the entire stop baseline and phase before any patch.
                # A retry after a partial failure retains the original counts.
                state["status"] = "stopping"
                self._save(state)
                pause_managed_hpas(self, state, resources)
                for ref, document in workloads:
                    if ref["uid"] in role_plans:
                        plan = role_plans[ref["uid"]]
                        response = self._patch(ref, plan["operations"]) if plan["operations"] else document
                        verify_role_stopped(plan, response)
                    else:
                        self._patch(ref, [{"op": "add", "path": "/spec/replicas", "value": 0}])
            elif action == "restart":
                if any(r["kind"] not in RESTARTABLE for r, _ in workloads):
                    raise HTTPException(409, "Native InferServiceSet restart requires an operator-specific contract; use stop then deploy")
                for ref, doc in workloads:
                    operations = []
                    if ref["uid"] in state.get("saved_replicas", {}):
                        operations.append({"op": "add", "path": "/spec/replicas", "value": state["saved_replicas"][ref["uid"]]})
                    annotations = dict(doc.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {}))
                    annotations["kubectl.kubernetes.io/restartedAt"] = _now()
                    operations.append({"op": "add", "path": "/spec/template/metadata/annotations", "value": annotations})
                    self._patch(ref, operations)
                    if ref["uid"] in state.get("saved_replicas", {}):
                        state["saved_replicas"].pop(ref["uid"])
                        self._save(state)  # Consume only the successfully restored UID.
                if state.get("paused_hpas"):
                    restore_managed_hpas(self, state, reason="restart")
                state["status"] = "starting"
            elif action == "remove":
                retained = []
                state["status"] = "removing"
                self._save(state)
                for ref, _doc in reversed(resources):
                    if ref["kind"] == "PersistentVolumeClaim":
                        retained.append(ref)
                        continue
                    self._kubectl(["delete", "--raw", _resource_path(ref), "-f", "-"], document={
                        "apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": ref["uid"]},
                        "propagationPolicy": "Foreground"})
                    state.setdefault("deletion_requested_resources", []).append({**ref, "requested_at": _now()})
                    state["resources"] = [r for r in state["resources"] if r["uid"] != ref["uid"]]
                    self._save(state)
                state["status"] = "removing"
                state["retained_resources"] = retained
                state["resources"] = [ref for ref in state["resources"] if ref["kind"] == "PersistentVolumeClaim"]
                for entry in state.get("paused_hpas", []):
                    entry.update(phase="retired", retired_at=_now(), retired_reason="explicit resource removal")
            else:
                raise HTTPException(422, "Unknown Kubernetes action")
            self._save(state)
            return {"container_id": state["namespace"], "namespace": state["namespace"], "status": state["status"],
                    "message": "Owned resource changes submitted; controllers may reconcile asynchronously; namespace/PVC are retained on remove"}

    @staticmethod
    def _infer_role_observations(ref: dict, document: dict, pods: list[dict], single_set: bool) -> list[dict]:
        """Observe native role Pod counts without inventing Infer Operator status fields.

        The pinned generator injects literal ROLE into each role's main container.
        Its documented layout has set replicas * role replicas * role.spec.replicas
        Pods. These are Pod-count observations, not inference or instance health.
        """
        observations = []
        set_replicas = document.get("spec", {}).get("replicas", 1)
        roles = document.get("spec", {}).get("template", {}).get("roles", [])
        if not isinstance(roles, list) or not roles:
            return [{"resource": ref, "converged": False, "observable": False,
                     "reason": "Native role replica layout is missing"}]
        for role in roles:
            if not isinstance(role, dict):
                observations.append({"resource": ref, "converged": False, "observable": False,
                                     "reason": "Native role is malformed"})
                continue
            name = role.get("name")
            replicas, per_instance = role.get("replicas"), role.get("spec", {}).get("replicas")
            template = role.get("spec", {}).get("template", {})
            labels = template.get("metadata", {}).get("labels", {})
            env_roles = {entry.get("value") for container in template.get("spec", {}).get("containers", [])
                         for entry in container.get("env", []) if entry.get("name") == "ROLE" and "value" in entry}
            numeric = (type(set_replicas) is int and set_replicas >= 0 and type(replicas) is int
                       and replicas >= 0 and type(per_instance) is int and per_instance >= 0)
            desired = set_replicas * replicas * per_instance if numeric else None
            role_token = {"kv-store": "kv_store", "kv-conductor": "kv_conductor"}.get(name, name)
            observable = single_set and isinstance(name, str) and numeric and (desired == 0 or env_roles == {role_token})
            matching = [pod for pod in pods if pod["role"] == role_token and all(
                pod["labels"].get(key) == value for key, value in labels.items())]
            active = [pod for pod in matching if pod["phase"] not in {"Succeeded", "Failed"}]
            ready = [pod for pod in active if pod["ready"]]
            converged = bool(observable and len(active) == desired and len(ready) == desired)
            observation = {"resource": ref, "role": name, "desired_instances": replicas,
                           "pods_per_instance": per_instance, "desired_pods": desired,
                           "active_pods": len(active), "ready_pods": len(ready), "observable": observable,
                           "converged": converged, "source": "native-role-template-and-pod-ROLE"}
            if not observable:
                observation["reason"] = "Cannot unambiguously observe this native role layout from the available fields"
            elif not converged:
                observation["reason"] = "Role Pod counts have not converged to the native replica target"
            observations.append(observation)
        return observations

    @operation_budget(20)
    def status(self, service_id: str) -> dict:
        # Status can persist deletion/stop convergence. Serialize that write with
        # lifecycle/deploy so an older observation cannot erase newly saved UIDs.
        with native_service_lock(_lock(service_key(service_id))):
            return self._status_locked(service_id)

    def _status_locked(self, service_id: str) -> dict:
        state, resources = self._verified_resources(service_id)
        pending_deletions = []
        saved_deletions = False
        for ref in state.get("deletion_requested_resources", []):
            if ref.get("confirmed_deleted_at"):
                continue
            document = self._get(ref, missing=True)
            if document:
                if document.get("metadata", {}).get("uid") != ref["uid"]:
                    raise HTTPException(409, "Deletion identity conflict for " + ref["kind"] + "/" + ref["name"]
                                        + ": the current UID differs from the requested deletion; replacement is untouched")
                self._owned(document, service_id, ref["uid"])
                pending_deletions.append({**ref, "deletionTimestamp": document.get("metadata", {}).get("deletionTimestamp")})
            else:
                ref["confirmed_deleted_at"] = _now()
                saved_deletions = True
        if saved_deletions:
            self._save(state)
        pods = self._kubectl(["get", "pods", "--namespace", state["namespace"], "-l",
                              f"{MANAGED}=true,{SERVICE}={service_key(service_id)}", "-o", "json"])
        items = []
        observed_pods = []
        for pod in pods.get("items", []):
            metadata, status = pod.get("metadata", {}), pod.get("status", {})
            ready = (status.get("phase") == "Running" and not metadata.get("deletionTimestamp")
                     and any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", [])))
            roles = {entry.get("value") for container in pod.get("spec", {}).get("containers", [])
                     for entry in container.get("env", []) if entry.get("name") == "ROLE" and isinstance(entry.get("value"), str)}
            item = {"name": metadata.get("name"), "uid": metadata.get("uid"), "phase": status.get("phase"),
                    "ready": bool(ready), "deleting": bool(metadata.get("deletionTimestamp")),
                    "role": next(iter(roles)) if len(roles) == 1 else None}
            items.append(item)
            observed_pods.append({**item, "labels": metadata.get("labels", {})})
        ready = bool(items) and all(p["ready"] for p in items)
        role_observations = []
        infer_sets = [ref for ref, _ in resources if ref["kind"] == "InferServiceSet"]
        missing_workloads = [ref for ref in state["resources"] if ref["kind"] in SCALABLE | RESTARTABLE
                             and not any(current["uid"] == ref["uid"] for current, _ in resources)]
        if missing_workloads:
            ready = False
        for ref, document in resources:
            if ref["kind"] in {"Deployment", "StatefulSet"}:
                desired = int(document.get("spec", {}).get("replicas", 1))
                observed = int(document.get("status", {}).get("readyReplicas", 0))
                ready = ready and desired > 0 and observed >= desired
            elif ref["kind"] == "InferServiceSet":
                role_observations.extend(self._infer_role_observations(ref, document, observed_pods, len(infer_sets) == 1))
        role_converged = bool(role_observations) and all(role["converged"] for role in role_observations)
        if infer_sets:
            role_converged = role_converged and all(pod["role"] is not None for pod in observed_pods
                                                   if pod["phase"] not in {"Succeeded", "Failed"})
            ready = ready and role_converged
        status = state.get("status", "starting")
        active_pods = [p for p in items if p.get("phase") not in {"Succeeded", "Failed"}]
        if status in {"removed", "removing"}:
            remaining = [ref for ref, _ in resources if ref["kind"] != "PersistentVolumeClaim"]
            # A terminal Pod is still a Kubernetes object. Foreground deletion
            # is only converged when all tracked UIDs and labeled Pods are gone.
            status = "removing" if pending_deletions or items or remaining else "removed"
            if state.get("status") != status:
                state["status"] = status
                self._save(state)
            ready = False
        elif status == "stopping":
            # Scaling is only an accepted request. Wait for every managed Pod
            # to leave Running/Pending/Unknown before claiming the service stopped.
            if not active_pods:
                status = "stopped"
                state["status"] = status
                self._save(state)
            ready = False
        elif status in {"stopped", "error"}:
            ready = False
        else:
            status = "running" if ready else "starting"
        return {"namespace": state["namespace"], "status": status, "ready": ready, "items": items,
                "resources": [_reference(d) for _, d in resources], "last_error": state.get("last_error"),
                "pending_deletions": pending_deletions, "role_observations": role_observations,
                "role_converged": role_converged if infer_sets else None,
                "observed_at": _now(), "removal_converged": status == "removed",
                "functional_acceptance": "not_tested"}

    @operation_budget(20)
    def logs(self, service_id: str, tail: int = 200, role: str | None = None) -> dict:
        from .log_records import fair_tail, log_record, role_matches, validate_log_role
        if type(tail) is not int or not 1 <= tail <= 2000:
            raise HTTPException(422, "tail must be in 1..2000")
        try:
            role = validate_log_role(role)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        state, _resources = self._verified_resources(service_id)
        listing = self._kubectl(["get", "pods", "--namespace", state["namespace"], "-l",
                                 f"{MANAGED}=true,{SERVICE}={service_key(service_id)}", "-o", "json"])
        targets = []
        for pod in listing.get("items", []):
            metadata, spec = pod.get("metadata", {}), pod.get("spec", {})
            self._owned(pod, service_id, metadata.get("uid"))
            name = metadata.get("name", "")
            if metadata.get("namespace") != state["namespace"] or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", name):
                raise HTTPException(409, "Log Pod identity does not match the owned namespace")
            containers = [*spec.get("initContainers", []), *spec.get("containers", []), *spec.get("ephemeralContainers", [])]
            roles = {entry.get("value") for container in containers for entry in container.get("env", [])
                     if entry.get("name") == "ROLE" and isinstance(entry.get("value"), str)}
            pod_role = next(iter(roles)) if len(roles) == 1 else ""
            for container in containers:
                container_name = container.get("name", "")
                if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", container_name):
                    raise HTTPException(409, "Log container identity is invalid")
                values = {entry.get("value") for entry in container.get("env", [])
                          if entry.get("name") == "ROLE" and isinstance(entry.get("value"), str)}
                actual_role = next(iter(values)) if len(values) == 1 else pod_role
                if actual_role == "SINGLE_CONTAINER":
                    actual_role = "single_container"
                try:
                    actual_role = validate_log_role(actual_role) or ""
                except ValueError:
                    actual_role = ""
                if role_matches(actual_role, role):
                    targets.append((name, container_name, actual_role, spec.get("nodeName") or ""))
        targets.sort()
        items, errors = [], []
        partial = len(targets) > 32
        quota = max(1024, 128 * 1024 // max(1, min(32, len(targets))))
        for pod, container, actual_role, node_name in targets[:32]:
            try:
                remaining_time(20)
                result = self._kubectl(["logs", pod, "--namespace", state["namespace"], "--container", container,
                                        "--prefix=true", "--timestamps=true", "--tail=" + str(tail),
                                        "--limit-bytes=" + str(quota)])
                text = result.get("text", "")
                partial = partial or len(text.encode()) >= quota
                for line in text.splitlines():
                    items.append(log_record(line, source="kubernetes_stdout", node=node_name, role=actual_role,
                                            container_name=container, pod=pod, namespace=state["namespace"]))
            except HTTPException as error:
                errors.append({"pod": pod, "container_name": container, "code": "log_source_unavailable",
                               "http_status": error.status_code})
                if error.status_code == 504:
                    break
            except ValueError:
                errors.append({"pod": pod, "container_name": container, "code": "log_source_identity_conflict"})
        return {"items": fair_tail(items, tail), "errors": errors, "partial": partial or bool(errors),
                "sources_total": len(targets), "window": "bounded_recent_tail", "filters": {"role": role}}

    def _coordinator_context(self, service_id: str) -> tuple[dict, dict]:
        """Resolve the actual Pod IPv4 inside the tracked service namespace."""
        state, _resources = self._verified_resources(service_id)
        config = state.get("original_user_config")
        if not isinstance(config, dict):
            raise HTTPException(409, "Saved original Motor configuration is unavailable on this agent")
        pods = self._kubectl(["get", "pods", "--namespace", state["namespace"], "-l",
                              f"{MANAGED}=true,{SERVICE}={service_key(service_id)}", "-o", "json"])
        candidates = []
        single = config.get("motor_deploy_config", {}).get("deploy_mode") == "single_container"
        for pod in pods.get("items", []):
            metadata, status = pod.get("metadata", {}), pod.get("status", {})
            labels = metadata.get("labels", {})
            if labels.get(MANAGED) != "true" or labels.get(SERVICE) != service_key(service_id):
                continue
            if metadata.get("namespace") != state["namespace"] or status.get("phase") != "Running":
                continue
            identity = " ".join([metadata.get("name", ""), *map(str, labels.values()),
                                 *(c.get("name", "") for c in pod.get("spec", {}).get("containers", []))]).lower()
            if single or "coordinator" in identity:
                ready = any(c.get("type") == "Ready" and c.get("status") == "True" for c in status.get("conditions", []))
                candidates.append((not ready, metadata.get("name", ""), status.get("podIP")))
        candidates.sort()
        if not candidates or not candidates[0][2]:
            raise HTTPException(503, "No running Coordinator Pod IP was resolved from the owned namespace")
        # fleet.endpoint validates this as an explicit IPv4, never an arbitrary URL.
        service = {"user_config": config, "placements": [{"role": "coordinator_controller", "node_id": "pod"}]}
        nodes = {"pod": {"address": candidates[0][2]}}
        return service, nodes

    @staticmethod
    def _agent_file(value: str, purpose: str) -> str:
        from .motor_http import resolve_file
        return resolve_file(value, purpose)

    @staticmethod
    def _client_tls_context(tls: dict) -> ssl.SSLContext | bool:
        from .motor_http import client_tls_context
        return client_tls_context(tls)

    @staticmethod
    def _motor_call(service: dict, nodes: dict, plane: str, path: str, *, body: dict | None = None):
        from .motor_http import request_motor
        return request_motor(service, nodes, plane, path, method="POST" if body is not None else "GET",
                             body=body, timeout=remaining_time(180 if body is not None else 10))

    @operation_budget(20)
    def aigw_models(self, service_id: str) -> dict:
        from .integrations import discover_models
        service, nodes = self._coordinator_context(service_id)
        return discover_models(service, nodes)

    @operation_budget(35)
    def observations(self, service_id: str) -> dict:
        from .fleet import FleetError
        result = {"instances": None, "metrics_text": "", "readiness": None, "native_readiness": None,
                  "runtime_readiness": {"ready": False}, "errors": [], "observed_at": _now()}
        try:
            observed = self.status(service_id)
            result["runtime_readiness"] = {key: observed[key] for key in
                                           ("ready", "status", "role_converged", "role_observations", "pending_deletions")}
        except HTTPException as exc:
            result["errors"].append({"source": "workload", "detail": str(exc.detail)})
        try:
            service, nodes = self._coordinator_context(service_id)
        except HTTPException as exc:
            result["errors"].append({"source": "kubernetes/coordinator", "detail": str(exc.detail)})
            return result
        for field, plane, path in (("instances", "obs", "/instances"), ("readiness", "mgmt", "/readiness"),
                                   ("metrics_text", "obs", "/metrics?type=full&format=prometheus")):
            try:
                result[field] = self._motor_call(service, nodes, plane, path)
            except FleetError as exc:
                result["errors"].append({"source": plane + path, "detail": str(exc)})
        result["native_readiness"] = copy.deepcopy(result["readiness"])
        if not result["runtime_readiness"]["ready"]:
            native = result["readiness"] if isinstance(result["readiness"], dict) else {}
            result["readiness"] = {**native, "ready": False,
                                   "runtime_detail": "Managed workload replicas or deletion state have not converged"}
            if not any(error["source"] == "workload" for error in result["errors"]):
                result["errors"].append({"source": "workload", "detail": result["readiness"]["runtime_detail"]})
        return result

    @operation_budget(185)
    def inference(self, payload: dict) -> dict:
        from .fleet import FleetError
        service, nodes = self._coordinator_context(payload.get("service_id"))
        prompt = payload.get("prompt")
        maximum = payload.get("max_tokens", 128)
        if not isinstance(prompt, str) or not prompt or len(prompt) > 100000:
            raise HTTPException(422, "prompt must be a non-empty string of at most 100000 characters")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 32768:
            raise HTTPException(422, "max_tokens must be between 1 and 32768")
        config = service["user_config"]
        engine = config.get("motor_engine_union_config") or config.get("motor_engine_prefill_config") or {}
        engine_config = engine.get("engine_config") or {}
        model = engine_config.get("served_model_name") or engine_config.get("served-model-name") or "model"
        request = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": maximum, "stream": False}
        extra = payload.get("request") or {}
        if not isinstance(extra, dict):
            raise HTTPException(422, "request must be a JSON object")
        allowed = {"temperature", "top_p", "frequency_penalty", "presence_penalty", "seed", "stop"}
        if set(extra) - allowed:
            raise HTTPException(422, "Unsupported inference request fields; endpoint/model/messages remain service-scoped")
        request.update(extra)
        started = time.monotonic()
        try:
            result = self._motor_call(service, nodes, "infer", "/v1/chat/completions", body=request)
        except FleetError as exc:
            raise HTTPException(502, str(exc)) from exc
        if not isinstance(result, dict) or not result.get("choices"):
            raise HTTPException(502, "Motor returned no chat completion choices")
        return {"text": result["choices"][0].get("message", {}).get("content", ""),
                "usage": result.get("usage", {}), "latency_ms": round((time.monotonic() - started) * 1000, 2),
                # The resolved Pod IP is an Agent-internal route. It does not
                # prove any public NodePort/LoadBalancer ingress for the user.
                "endpoint": "", "endpoint_status": "external_unresolved"}


def create_kubernetes_router(runtime: KubernetesRuntime | None = None) -> APIRouter:
    instance = runtime or KubernetesRuntime()

    router = APIRouter(prefix="/agent/kubernetes", dependencies=[Depends(authorize_agent)])

    @router.get("/nodes")
    def nodes(request: Request) -> dict:
        if request.query_params:
            raise HTTPException(422, "Kubernetes node discovery does not accept query parameters")
        return instance.cluster_nodes()

    @router.get("/{service_id}/endpoints")
    def endpoints(service_id: str, request: Request, response: Response) -> dict:
        if request.query_params:
            raise HTTPException(422, "Kubernetes endpoint observation does not accept query parameters")
        response.headers["Cache-Control"] = "no-store"
        return instance.external_endpoints(service_id)

    @router.post("/plan")
    @native_operation
    def plan(payload: dict) -> dict:
        with native_service_lock(_lock(service_key(payload.get("service_id")))):
            result = instance.plan(payload)
        return {k: v for k, v in result.items() if not k.startswith("_")}

    @router.post("/deploy")
    def deploy(payload: dict) -> dict:
        return instance.deploy(payload)

    @router.post("/scale")
    def scale(payload: dict) -> dict:
        return instance.scale(payload)

    @router.post("/stop")
    def stop(payload: dict) -> dict:
        return instance.action(payload.get("service_id"), "stop")

    @router.post("/restart")
    def restart(payload: dict) -> dict:
        return instance.action(payload.get("service_id"), "restart")

    @router.post("/remove")
    def remove(payload: dict) -> dict:
        return instance.action(payload.get("service_id"), "remove")

    @router.get("/status")
    def status(service_id: str) -> dict:
        return instance.status(service_id)

    @router.get("/logs")
    def logs(service_id: str, tail: int = 200, role: str | None = None) -> dict:
        return instance.logs(service_id, tail, role)

    @router.get("/observations")
    def observations(service_id: str) -> dict:
        return instance.observations(service_id)

    @router.get("/aigw-models")
    def aigw_models(service_id: str) -> dict:
        return instance.aigw_models(service_id)

    @router.post("/inference")
    def inference(payload: dict) -> dict:
        return instance.inference(payload)

    return router
