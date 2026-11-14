"""Pinned Motor semantics, with a structured boundary for the console's Docker agent.

The original JSON is never modified. Official functions render the container
templates; the Docker SDK consumes only the resulting allow-listed options.
"""
from __future__ import annotations

import ast
from contextlib import contextmanager
import copy
import importlib
import hashlib
import ipaddress
import json
import os
import posixpath
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import Any

from .compatibility import CompatibilityError, source_profile
from .node_configuration import execution_node, nic_configuration_error, valid_nic_name

MOTOR_COMMIT = "37d615f232a36b8dba8630ffc3a01a20d289c65e"
MANAGED_LABEL = "motor-console.managed"
SERVICE_LABEL = "motor-console.service"
NODE_LABEL = "motor-console.node"
ROLE_LABEL = "motor-console.role"
INSTANCE_LABEL = "motor-console.instance"
_IMPORT_LOCK = threading.RLock()
_MODULE_CACHE: dict[str, dict[str, Any]] = {}
_SOURCE_VERIFICATION: dict[str, tuple[tuple, dict]] = {}
_SOURCE_MANIFEST = Path(__file__).with_name("motor_source_manifest.json")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IMAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,255}$")
_ENGINE_ROLES = {"prefill", "decode", "union"}
_ROLES = _ENGINE_ROLES | {"coordinator_controller", "kv_store", "single_container"}


class MotorConfigError(ValueError):
    """Configuration cannot be safely executed by this adapter."""


def upstream_path(value: str | Path | None = None) -> Path:
    return Path(value or os.environ.get("MOTOR_UPSTREAM_DIR") or
                Path(__file__).resolve().parents[2] / "mindie-motor" / "upstream").resolve()


def verify_upstream_source(upstream_dir: str | Path | None = None) -> dict:
    """Verify shipped sources even when image packaging excludes .git."""
    source = upstream_path(upstream_dir)
    try:
        profile = source_profile() if os.environ.get("MOTOR_SOURCE_MANIFEST") else {
            "profile_id": "motor-37d615f232a3", "commit": MOTOR_COMMIT, "manifest_path": str(_SOURCE_MANIFEST)}
    except CompatibilityError as exc:
        raise MotorConfigError(str(exc)) from exc
    manifest_file = Path(profile["manifest_path"])
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        expected = manifest.get("files")
        if manifest.get("commit") != profile["commit"] or not isinstance(expected, dict) or not expected:
            raise MotorConfigError("Motor source manifest is empty or does not match the pinned commit")
        signature: list[tuple] = []
        for path in (manifest_file,):
            state = path.stat()
            signature.append((str(path), state.st_mtime_ns, state.st_size, state.st_ctime_ns))
        paths = []
        for relative, digest in sorted(expected.items()):
            fragment = PurePosixPath(relative)
            if fragment.is_absolute() or ".." in fragment.parts or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise MotorConfigError("Invalid path or digest in the trusted Motor source manifest")
            path = (source / relative).resolve()
            if not path.is_relative_to(source):
                raise MotorConfigError("Motor manifest path escapes the source checkout")
            state = path.stat()
            signature.append((str(path), state.st_mtime_ns, state.st_size, state.st_ctime_ns))
            paths.append((path, digest, relative))
        key = str(source)
        cache_key = tuple(signature)
        cached = _SOURCE_VERIFICATION.get(key)
        if cached and cached[0] == cache_key:
            return dict(cached[1])
        for path, digest, relative in paths:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise MotorConfigError(f"Pinned Motor source integrity mismatch: {relative}")
        result = {"commit": profile["commit"], "profile_id": profile["profile_id"], "verified_files": len(paths),
                  "manifest_sha256": hashlib.sha256(manifest_file.read_bytes()).hexdigest(), "validation": "sha256"}
        _SOURCE_VERIFICATION[key] = (cache_key, result)
        return dict(result)
    except (OSError, ValueError, TypeError) as exc:
        if isinstance(exc, MotorConfigError):
            raise
        raise MotorConfigError(f"Cannot verify pinned Motor source manifest: {exc}") from exc


def _module_owned(name: str) -> bool:
    return name == "lib" or name.startswith("lib.") or name in {"docker_deploy", "set_env_docker"}


@contextmanager
def official_helpers(upstream_dir: str | Path | None = None):
    """Load the pinned deployer without leaking its generic ``lib`` package.

    Only trusted, installed source is imported. No deployment function is
    executed here. Bytecode writes to the upstream checkout are disabled.
    """
    source = upstream_path(upstream_dir)
    verified = verify_upstream_source(source)
    deployer = source / "examples" / "deployer"
    if not (deployer / "docker_deploy.py").is_file():
        raise MotorConfigError(f"Pinned Motor deployer is unavailable: {deployer}")
    git_head = source / ".git" / "HEAD"
    if git_head.is_file():
        head = git_head.read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = source / ".git" / head[5:]
            head = ref.read_text(encoding="utf-8").strip() if ref.is_file() else ""
        if head and head != verified["commit"]:
            raise MotorConfigError(f"Motor source must be pinned to {verified['commit']}; found {head}")
    module_key = str(source) + ":" + verified["manifest_sha256"]
    with _IMPORT_LOCK:
        saved = {k: v for k, v in list(sys.modules.items()) if _module_owned(k)}
        original_path = list(sys.path)
        no_bytecode = sys.dont_write_bytecode
        for key in saved:
            del sys.modules[key]
        sys.modules.update(_MODULE_CACHE.get(module_key, {}))
        sys.path.insert(0, str(deployer))
        sys.dont_write_bytecode = True
        try:
            if sys.platform == "win32" and "docker_deploy" not in sys.modules:
                # Windows can plan Linux containers, but cannot import pty /
                # termios. Load the same source with only the runtime import
                # removed; deployment still executes the unmodified file in
                # its Linux container. Calling the missing runner fails hard.
                path = deployer / "docker_deploy.py"
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                tree.body = [n for n in tree.body if not (
                    isinstance(n, ast.ImportFrom) and n.module == "lib.in_place_run")]
                module = ModuleType("docker_deploy")
                module.__file__ = str(path)
                def no_host_runner(*_args, **_kwargs):
                    raise MotorConfigError("Motor runtime execution requires a Linux container")
                module.run_in_place = no_host_runner
                sys.modules["docker_deploy"] = module
                exec(compile(tree, str(path), "exec"), module.__dict__)
            deploy = importlib.import_module("docker_deploy")
            yield SimpleNamespace(deploy=deploy, docker=deploy.D, constant=deploy.C,
                                  render=importlib.import_module("lib.generator.render"))
        finally:
            current = {k: v for k, v in list(sys.modules.items()) if _module_owned(k)}
            _MODULE_CACHE[module_key] = current
            for key in current:
                sys.modules.pop(key, None)
            sys.modules.update(saved)
            sys.path[:] = original_path
            sys.dont_write_bytecode = no_bytecode


def preserve_json(value: dict) -> dict:
    if not isinstance(value, dict):
        raise MotorConfigError("Motor configuration and env must be JSON objects")
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError) as exc:
        raise MotorConfigError("Configuration must contain valid JSON values") from exc


def _int(value: Any, name: str, minimum: int = 1, maximum: int = 65535) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MotorConfigError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def safe_name(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_NAME.fullmatch(value):
        raise MotorConfigError(f"{name} must use letters, digits, dot, dash or underscore (1..96 characters)")
    return value


def docker_container_name(service_id: str, node_id: str, role: str, instance_name: str,
                          *, render_suffix: str) -> str:
    """Readable Docker identity with full-input hashing and native sibling budget."""
    values = [safe_name(value, field) for value, field in (
        (service_id, "service_id"), (node_id, "node.id"),
        (role, "role"), (instance_name, "instance_name"))]
    digest = hashlib.sha256(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]
    prefix, ending = "motor-console-", "-" + digest
    budget = 96 - len(render_suffix) - len(prefix) - len(ending)
    if budget < 1:
        raise MotorConfigError("Native Render suffix leaves no room for a managed Docker name")
    def slug(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    readable = "-".join((slug(service_id)[:16].rstrip("-"), slug(role), slug(instance_name)[:16].rstrip("-")))
    return prefix + readable[:budget].rstrip("-") + ending


def validate_execution_inputs(user_config: dict, env: dict) -> None:
    """Protect shell-rendered upstream fields; this does not restrict storage/export."""
    preserve_json(user_config)
    preserve_json(env)
    for key in ("motor_deploy_config", "motor_controller_config", "motor_coordinator_config",
                "motor_engine_prefill_config", "motor_engine_decode_config", "motor_engine_union_config",
                "kv_cache_store_config", "north_config", "motor_container_snapshot_config"):
        if key in user_config and not isinstance(user_config[key], dict):
            raise MotorConfigError(f"{key} must be an object")
    for section, values in env.items():
        if section == "version":
            continue
        if not isinstance(values, dict):
            raise MotorConfigError(f"env.{section} must be an object")
        for key, value in values.items():
            if not isinstance(key, str) or not _ENV_NAME.fullmatch(key):
                raise MotorConfigError(f"Unsafe environment variable name in {section}")
            # Upstream writes double-quoted exports and does not escape '$'.
            if not isinstance(value, (str, int, float, bool)) and value is not None:
                raise MotorConfigError(f"env.{section}.{key} must be scalar; serialize structured values as JSON strings")
            if isinstance(value, str) and ("$" in value or "\x00" in value):
                raise MotorConfigError(f"env.{section}.{key} contains shell expansion syntax; execution refused")
    deploy = user_config.get("motor_deploy_config") or {}
    for key in ("job_id",):
        if key in deploy:
            safe_name(deploy[key], f"motor_deploy_config.{key}")
    for section_name in ("motor_engine_prefill_config", "motor_engine_decode_config", "motor_engine_union_config"):
        section = user_config.get(section_name) or {}
        for key in ("engine_config", "model_config", "motor_nodemanger_config"):
            if key in section and not isinstance(section[key], dict):
                raise MotorConfigError(f"{section_name}.{key} must be an object")
        engine = section.get("engine_config") or {}
        candidates = [engine.get("served_model_name"), engine.get("model"),
                      (section.get("model_config") or {}).get("model_name")]
        for value in candidates:
            if isinstance(value, list):
                raise MotorConfigError("Model name arrays are not safely handled by the pinned environment renderer")
            if isinstance(value, str) and ("$" in value or "\x00" in value):
                raise MotorConfigError(f"{section_name} model metadata contains shell expansion syntax")
    north_name = (user_config.get("north_config") or {}).get("name")
    if north_name is not None and not isinstance(north_name, str):
        raise MotorConfigError("north_config.name must be a literal string")
    if isinstance(north_name, str) and ("$" in north_name or "\x00" in north_name):
        raise MotorConfigError("north_config.name contains shell expansion syntax")
    # Values in these objects are inserted by official render_start_motor_sh.
    kv = user_config.get("kv_cache_store_config") or {}
    for key, value in kv.items():
        if isinstance(value, str) and any(c in value for c in ('$','`','"','\n','\r','\x00')):
            raise MotorConfigError(f"kv_cache_store_config.{key} contains unsafe shell syntax")


def analyze_config(user_config: dict, env: dict | None = None) -> dict:
    config = preserve_json(user_config)
    deploy = config.get("motor_deploy_config") or {}
    if not isinstance(deploy, dict):
        raise MotorConfigError("motor_deploy_config must be a JSON object")
    hybrid = "hybrid_instances_num" in deploy
    sections = [config.get("motor_engine_union_config", {})] if hybrid else [
        config.get("motor_engine_prefill_config", {}), config.get("motor_engine_decode_config", {})]
    section = next((s for s in sections if isinstance(s, dict) and s), {})
    engine_config = section.get("engine_config") or {}
    if not isinstance(engine_config, dict) or not isinstance(section.get("model_config") or {}, dict):
        raise MotorConfigError("engine_config and model_config must be JSON objects")
    model = engine_config.get("served_model_name") or engine_config.get("served-model-name") or \
        (section.get("model_config") or {}).get("model_name") or engine_config.get("model") or ""
    if isinstance(model, list):
        model = model[0] if model else ""
    return {
        "scenario": "hybrid" if hybrid else "separate",
        "engine": section.get("engine_type", "vllm"), "model": str(model),
        "image": deploy.get("image_name", ""), "hardware": deploy.get("hardware_type", ""),
        "deploy_mode": deploy.get("deploy_mode", ""),
        "replicas": {"prefill": 0 if hybrid else deploy.get("p_instances_num", 0),
                     "decode": 0 if hybrid else deploy.get("d_instances_num", 0),
                     "union": deploy.get("hybrid_instances_num", 0) if hybrid else 0},
    }


def _kv_enabled(config: dict) -> bool:
    section = config.get("motor_engine_prefill_config") or config.get("motor_engine_union_config") or {}
    connector = (section.get("engine_config") or {}).get("kv_transfer_config") or {}
    return connector.get("kv_connector") == "MultiConnector" or bool(config.get("kv_cache_store_config"))


def validate_config(config: dict, env: dict, upstream_dir: str | Path | None = None) -> dict:
    validate_execution_inputs(config, env)
    deploy = config.get("motor_deploy_config")
    if not isinstance(deploy, dict):
        raise MotorConfigError("motor_deploy_config is required")
    image = deploy.get("image_name")
    if not isinstance(image, str) or not _IMAGE_NAME.fullmatch(image):
        raise MotorConfigError("Set a valid local motor_deploy_config.image_name")
    if deploy.get("deploy_mode") not in {"single_container", "infer_service_set", "multi_deployment"}:
        raise MotorConfigError("deploy_mode must be single_container, infer_service_set or multi_deployment")
    if deploy.get("e_instances_num", 0):
        if deploy.get("deploy_mode") == "single_container":
            raise MotorConfigError("Pinned single_container boot does not launch encode and its device-count helper omits E NPUs; use the Kubernetes EPD path instead of an incomplete Docker plan")
        raise MotorConfigError("The pinned multi-container Docker entry point has no encode role; use the Kubernetes EPD path")
    if deploy.get("storage"):
        raise MotorConfigError("Docker does not apply Motor PVC/NFS storage declarations; mount storage on the host first")
    if config.get("motor_container_snapshot_config", {}).get("enable_snapshot"):
        raise MotorConfigError("Container snapshots require the Kubernetes adapter")
    for scope in (config.get("motor_controller_config") or {}, config.get("motor_coordinator_config") or {}):
        if (scope.get("standby_config") or {}).get("enable_master_standby"):
            raise MotorConfigError("Master/standby control plane requires Kubernetes")
    if _kv_enabled(config):
        backend = (config.get("kv_cache_store_config") or {}).get("backend", "memcache")
        if backend not in {"memcache", "mooncake"}:
            raise MotorConfigError(f"Docker KV backend {backend!r} has no packaged upstream startup script")
    info = analyze_config(config)
    roles = {"union"} if info["scenario"] == "hybrid" else {"prefill", "decode"}
    for role in roles:
        section = config.get(f"motor_engine_{role}_config")
        if not isinstance(section, dict) or section.get("engine_type", "vllm") not in {"vllm", "sglang"}:
            raise MotorConfigError(f"motor_engine_{role}_config must select vllm or sglang")
        engine = section.get("engine_config") or {}
        for key in ("nnodes", "pipeline_parallel_size", "prefill_context_parallel_size"):
            if key in engine:
                _int(engine[key], f"engine_config.{key}", 1, 65535)
        count_key = "hybrid_instances_num" if role == "union" else f"{role[0]}_instances_num"
        _int(deploy.get(count_key), count_key, 1, 16)
    with official_helpers(upstream_dir) as h:
        copied = preserve_json(config)
        h.docker.validate_instance_nums(copied)
        if info["scenario"] == "hybrid":
            h.docker.validate_pd_hybrid_config(copied)
        h.docker.validate_pod_layouts(copied)
        h.docker.derive_params(copied)
        h.docker.npu_docker_card_count(deploy.get("hardware_type"))
        h.render.resolve_render_image(copied) if h.render.is_render_enabled(copied) else None
    return info


def _node_map(nodes: list[dict] | dict | None) -> dict[str, dict]:
    if isinstance(nodes, dict):
        return {identifier: execution_node(node) for identifier, node in nodes.items()}
    return {node["id"]: execution_node(node) for node in nodes or []}


def validate_layout(user_config: dict, placements: list[dict], env: dict | None = None,
                    nodes: list[dict] | dict | None = None, upstream_dir: str | Path | None = None) -> list[dict]:
    try:
        info = validate_config(user_config, env or {}, upstream_dir)
        deploy = user_config["motor_deploy_config"]
        if not isinstance(placements, list) or not placements:
            raise MotorConfigError("At least one placement is required")
        node_lookup = _node_map(nodes)
        used: dict[str, set[int]] = {}
        groups: dict[tuple[str, str], list[str]] = {}
        management = 0
        kv_count = 0
        for place in placements:
            node_id = safe_name(place.get("node_id"), "placement.node_id")
            role = place.get("role")
            if role not in _ROLES:
                raise MotorConfigError(f"Unsupported Docker role: {role}")
            devices = place.get("devices", [])
            if not isinstance(devices, list) or any(isinstance(d, bool) or not isinstance(d, int) or d < 0 for d in devices):
                raise MotorConfigError("devices must be a list of non-negative integer IDs")
            if len(devices) != len(set(devices)) or used.setdefault(node_id, set()).intersection(devices):
                raise MotorConfigError(f"Overlapping NPU assignments on {node_id}")
            used[node_id].update(devices)
            if role in {"coordinator_controller", "kv_store"} and devices:
                raise MotorConfigError("Management and KV store placements must not attach NPUs")
            if role == "coordinator_controller":
                management += 1
            if role == "kv_store":
                kv_count += 1
            if role in _ENGINE_ROLES:
                name = safe_name(place.get("instance_name"), "placement.instance_name")
                group = groups.setdefault((role, name), [])
                address = node_lookup.get(node_id, {}).get("address") or node_id
                if address in group:
                    raise MotorConfigError("One instance cannot have two host-network containers on the same node IP")
                group.append(address)
                expected = deploy.get("hybrid_pod_npu_num" if role == "union" else f"{role[0]}_pod_npu_num")
                if len(devices) < int(expected or 0):
                    raise MotorConfigError(f"{role}/{name} needs at least {expected} assigned NPUs")
        if deploy.get("deploy_mode") == "single_container":
            if len(placements) != 1 or placements[0].get("role") != "single_container":
                raise MotorConfigError("single_container needs one single_container placement")
            with official_helpers(upstream_dir) as h:
                required = h.docker.required_visible_device_count(preserve_json(user_config))
            if len(placements[0].get("devices", [])) < required:
                raise MotorConfigError(f"single_container needs {required} NPUs for all instances")
        else:
            if management != 1:
                raise MotorConfigError("Exactly one coordinator_controller placement is required")
            if kv_count != int(_kv_enabled(user_config)):
                raise MotorConfigError("KV store placement count must match the original pool configuration")
            for role in _ENGINE_ROLES:
                expected_groups = int(info["replicas"].get(role, 0))
                role_groups = [v for (r, _), v in groups.items() if r == role]
                if len(role_groups) != expected_groups:
                    raise MotorConfigError(f"{role} needs {expected_groups} distinct instance groups")
                pod_key = "single_hybrid_instance_pod_num" if role == "union" else f"single_{role[0]}_instance_pod_num"
                expected_pods = int(deploy.get(pod_key, 1))
                if any(len(v) != expected_pods for v in role_groups):
                    raise MotorConfigError(f"Each {role} instance needs {expected_pods} containers on distinct node IPs")
        if node_lookup:
            manager = next((node_lookup.get(p.get("node_id"), {}) for p in placements
                            if p.get("role") in {"coordinator_controller", "single_container"}), {})
            reserved: dict[tuple[str, int], str] = {}
            for place in placements:
                node = node_lookup.get(place.get("node_id"), {})
                spec = build_container_spec({"service_id": "preview", "user_config": user_config,
                                             "env": env or {}, "placement": place, "node": node,
                                             "management": {"address": manager.get("address")}}, upstream_dir)
                for port in spec["ports"]:
                    key = (node.get("address") or place["node_id"], port["port"])
                    owner = f"{place['role']}/{place.get('instance_name', '')}/{port['name']}"
                    if key in reserved:
                        raise MotorConfigError(f"Port {key[1]} on {key[0]} overlaps {reserved[key]} and {owner}")
                    reserved[key] = owner
        return [{"name": "Motor topology", "status": "pass", "detail": "Original configuration and placements agree"}]
    except (ValueError, KeyError, TypeError) as exc:
        return [{"name": "Motor topology", "status": "fail", "detail": str(exc)}]


def _expand_token(token: str, env: dict[str, str]) -> str:
    return re.sub(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?", lambda m: env.get(m[1], m[0]), token)


def docker_argv_to_spec(argv: list[str], variables: dict[str, str] | None = None) -> dict:
    """Parse only official Docker options, without shell expansion/evaluation."""
    variables = variables or {}
    tokens = [_expand_token(v, variables) for v in argv]
    if tokens[:2] != ["docker", "run"]:
        raise MotorConfigError("Official template must start with docker run")
    spec: dict[str, Any] = {"environment": {}, "volumes": {}, "devices": [], "network_mode": "host"}
    index = 2
    while index < len(tokens):
        flag = tokens[index]
        index += 1
        if flag in {"-it", "-i", "-t", "-d"}:
            continue
        if flag == "--privileged":
            spec["privileged"] = True
            continue
        if flag in {"--name", "-u", "-e", "-v", "--device", "--entrypoint"}:
            if index >= len(tokens):
                raise MotorConfigError(f"Missing value for official template option {flag}")
            value = tokens[index]
            index += 1
            if flag == "--name": spec["name"] = value
            elif flag == "-u": spec["user"] = value
            elif flag == "--entrypoint": spec["entrypoint"] = value
            elif flag == "-e":
                key, separator, content = value.partition("=")
                if not separator or not _ENV_NAME.fullmatch(key):
                    raise MotorConfigError("Invalid official environment assignment")
                spec["environment"][key] = content
            elif flag == "--device": spec["devices"].append(value)
            elif flag == "-v":
                fields = value.split(":")
                if len(fields) not in {2, 3} or not all(PurePosixPath(p).is_absolute() for p in fields[:2]):
                    raise MotorConfigError(f"Invalid Linux bind in official template: {value}")
                spec["volumes"][fields[0]] = {"bind": fields[1], "mode": fields[2] if len(fields) == 3 else "rw"}
            continue
        if flag.startswith("--net="):
            if flag != "--net=host": raise MotorConfigError("Only official host networking is supported")
            continue
        if flag.startswith("--shm-size="):
            spec["shm_size"] = flag.split("=", 1)[1]
            continue
        if flag.startswith("--ipc="):
            if flag != "--ipc=host": raise MotorConfigError("Unknown official IPC mode")
            spec["ipc_mode"] = "host"
            continue
        if flag.startswith("-"):
            raise MotorConfigError(f"Unimplemented upstream Docker option: {flag}")
        spec["image"] = flag
        spec["command"] = tokens[index:]
        if not spec["command"]:
            raise MotorConfigError("Official template has no command")
        return spec
    raise MotorConfigError("Official template has no image")


def _port_list(config: dict, role: str, h) -> list[tuple[str, int]]:
    coord = config.get("motor_coordinator_config", {}).get("api_config", {})
    ports: list[tuple[str, int]] = []
    if role in {"coordinator_controller", "single_container"}:
        ports.extend((key, int(coord.get(key, default))) for key, default in (
            ("coordinator_api_infer_port", 1025), ("coordinator_api_mgmt_port", 1026), ("coordinator_obs_port", 1027)))
        ports.extend(zip(("controller", "controller-observability"), h.docker.docker_controller_ports(config)))
    if role in _ENGINE_ROLES:
        ports.extend(h.docker.collect_engine_listen_ports(config, role))
        engine = config[f"motor_engine_{role}_config"].get("engine_config") or {}
        for key in ("master_port", "master-port"):
            if key in engine: ports.append((key, int(engine[key])))
        kv = engine.get("kv_transfer_config") or {}
        connectors = (kv.get("kv_connector_extra_config") or {}).get("connectors") or []
        for i, connector in enumerate(connectors):
            if isinstance(connector, dict) and "lookup_rpc_port" in connector:
                ports.append((f"lookup-rpc-{i}", int(connector["lookup_rpc_port"])))
    if role == "kv_store" or role == "single_container" and _kv_enabled(config):
        kv = config.get("kv_cache_store_config") or {}
        ports.append(("kv-store", int(kv.get("port", 50088))))
        ports.append(("kv-metrics", int(kv.get("metrics_port", 50090))))
        if kv.get("backend", "memcache") == "memcache":
            ports.append(("kv-config-store", int(kv.get("config_store_port", 50089))))
    if role == "single_container":
        info = analyze_config(config)
        before_p = int(info["replicas"].get("prefill", 0))
        p_section = config.get("motor_engine_prefill_config") or {}
        p_parallel = h.docker._engine_parallel_sizes(p_section) if p_section else (1, 1, 1)
        d_section = config.get("motor_engine_decode_config") or {}
        d_parallel = h.docker._engine_parallel_sizes(d_section) if d_section else (1, 1, 1)
        for engine_role in ("union", "prefill", "decode"):
            count = int(info["replicas"].get(engine_role, 0))
            for i in range(count):
                copied = preserve_json(config)
                section = copied[f"motor_engine_{engine_role}_config"]
                nm = section.setdefault("motor_nodemanger_config", {})
                api = nm.setdefault("api_config", {})
                endpoint = nm.setdefault("endpoint_config", {})
                dp, _, _ = h.docker._engine_parallel_sizes(section)
                if engine_role == "union":
                    nm_offset, base_offset, rpc_offset = i, i * dp * 2, i
                    device_offset = 0
                elif engine_role == "prefill":
                    nm_offset, base_offset, rpc_offset = i, i * d_parallel[0] * 2, i
                    device_offset = i * h.docker._engine_world_size(p_section)
                else:
                    nm_offset = before_p * p_parallel[0] + i
                    base_offset = (before_p * p_parallel[0] + i * dp) * 2
                    rpc_offset = before_p + i
                    device_offset = before_p * h.docker._engine_world_size(p_section) + i * h.docker._engine_world_size(d_section)
                api["node_manager_port"] = int(api.get("node_manager_port", 1026)) + nm_offset
                endpoint["base_port"] = int(endpoint.get("base_port", 10000)) + base_offset
                engine = section.setdefault("engine_config", {})
                if engine_role != "union" and p_section:
                    source_rpc = (p_section.get("engine_config") or {}).get("data_parallel_rpc_port", 9000)
                else:
                    source_rpc = engine.get("data_parallel_rpc_port", 9000)
                engine["data_parallel_rpc_port"] = int(source_rpc) + rpc_offset
                if engine_role != "union" and p_section:
                    source_kv = (p_section.get("engine_config") or {}).get("kv_transfer_config") or {}
                    source_port = h.docker._read_kv_port(p_section.get("engine_config") or {})
                    if source_port is not None and engine.get("kv_transfer_config"):
                        h.docker._apply_kv_port(engine, source_port + device_offset)
                    source_connectors = (source_kv.get("kv_connector_extra_config") or {}).get("connectors") or []
                    target_connectors = ((engine.get("kv_transfer_config") or {}).get("kv_connector_extra_config") or {}).get("connectors") or []
                    if len(source_connectors) > 1 and len(target_connectors) > 1:
                        lookup = source_connectors[1].get("lookup_rpc_port")
                        if lookup is not None and source_connectors[1].get("kv_connector") != "UCMConnector":
                            target_connectors[1]["lookup_rpc_port"] = int(lookup) + rpc_offset
                # Official functions remain authoritative for the engine port span.
                ports.extend((f"{engine_role}{i}/{name}", port) for name, port in
                             h.docker.collect_engine_listen_ports(copied, engine_role))
    return ports


def build_container_spec(payload: dict, upstream_dir: str | Path | None = None,
                         workspace_dir: str | Path | None = None) -> dict:
    config = preserve_json(payload.get("user_config"))
    env = preserve_json(payload.get("env", {}))
    info = validate_config(config, env, upstream_dir)
    service = safe_name(payload.get("service_id"), "service_id")
    node = execution_node(payload.get("node") or {})
    node_id = safe_name(node.get("id"), "node.id")
    placement = payload.get("placement") or {}
    role = placement.get("role")
    if role not in _ROLES:
        raise MotorConfigError(f"Unsupported role: {role}")
    single = role == "single_container"
    if single != (info["deploy_mode"] == "single_container"):
        raise MotorConfigError("Placement role and deploy_mode disagree")
    instance = safe_name(placement.get("instance_name") or ("single" if single else "ctrl"), "instance_name")
    address = str(ipaddress.IPv4Address(node.get("address", "")))
    management = payload.get("management") or {}
    management_address = str(ipaddress.IPv4Address(management.get("address") or address))
    nic = node.get("nic_name")
    if not valid_nic_name(nic):
        raise MotorConfigError(nic_configuration_error(node))
    devices = placement.get("devices", [])
    if not isinstance(devices, list) or any(isinstance(d, bool) or not isinstance(d, int) or d < 0 for d in devices):
        raise MotorConfigError("devices must contain non-negative integer IDs")
    if len(devices) != len(set(devices)):
        raise MotorConfigError("Duplicate device IDs")
    if role in {"coordinator_controller", "kv_store"} and devices:
        raise MotorConfigError("Management/KV containers cannot attach NPUs")
    # Reserve the actual pinned sibling suffix even when Render is disabled,
    # so enabling it does not change the main container's stable identity.
    with official_helpers(upstream_dir) as h:
        render_suffix = h.render.docker_render_container_name("")
    name = docker_container_name(service, node_id, role, instance, render_suffix=render_suffix)
    attempt = safe_name(payload.get("_workspace_id", "planned"), "workspace id")
    workspace = str(workspace_dir or os.environ.get("MOTOR_HOST_WORKSPACE_DIR", "/opt/motor-console/workspace"))
    if not PurePosixPath(workspace).is_absolute() or ".." in PurePosixPath(workspace).parts \
            or any(c in workspace for c in ('$', '`', '"', '\n', '\r', '\x00', ':')):
        raise MotorConfigError("MOTOR_HOST_WORKSPACE_DIR must be an absolute Linux host path")
    run_root = str(PurePosixPath(workspace) / service / node_id / attempt / name)
    examples = str(PurePosixPath(run_root) / "examples")
    config_dir = str(PurePosixPath(examples) / "config")
    deployer = str(PurePosixPath(examples) / "deployer")
    port_values = placement.get("ports") or {}
    for key, value in port_values.items():
        if key not in {"node_manager", "rpc", "base", "kv"}:
            raise MotorConfigError(f"Unknown placement port: {key}")
        _int(value, f"ports.{key}")
    args = SimpleNamespace(config_dir=config_dir, user_config_path=None, env_config_path=None,
                           container_name=name, role=None if single else role,
                           job_name=instance if role in _ENGINE_ROLES else None,
                           pod_ip=address, nic_name=nic,
                           coordinator_ip=management_address if not single else None,
                           controller_ip=management_address if not single else None,
                           kv_store_ip=(management.get("kv_store_address") or management_address)
                           if _kv_enabled(config) and not single else None,
                           devices=",".join(map(str, devices)) if devices else None,
                           node_manager_port=port_values.get("node_manager"), data_parallel_rpc_port=port_values.get("rpc"),
                           base_port=port_values.get("base"), kv_port=port_values.get("kv"))
    if args.kv_store_ip:
        args.kv_store_ip = str(ipaddress.IPv4Address(args.kv_store_ip))
    with official_helpers(upstream_dir) as h:
        effective = preserve_json(config)
        overrides = h.docker.engine_port_overrides_from_args(args)
        if role in _ENGINE_ROLES:
            h.docker.apply_engine_port_overrides(effective, role, overrides)
        elif overrides.specified():
            raise MotorConfigError("Engine port overrides apply only to prefill/decode/union")
        h.docker.apply_docker_controller_port_defaults(effective)
        if role in _ENGINE_ROLES or single:
            expected = h.docker.container_npu_count(effective, None if single else role)
            if len(devices) < expected:
                raise MotorConfigError(f"Placement needs at least {expected} selected NPU IDs")
        template = h.deploy.enter_docker_run_template(args.role, config["motor_deploy_config"]["hardware_type"])
        attach = h.deploy._enter_template_attaches_npu(template)
        template = h.deploy.apply_enter_devices(template, args.devices, attach_npu=attach)
        # The official helper accepts a path; provide a read-only in-memory view
        # through its pure underlying predicate instead of writing env during plan.
        ipc = importlib.import_module("lib.container_ipc").host_ipc_enabled(env)
        a5 = importlib.import_module("lib.a5_host_nic")
        if attach and config["motor_deploy_config"]["hardware_type"] in h.constant.HARDWARE_TYPE_A5 \
                and a5.env_requests_a5_host_nic_overlay(env):
            # Invoke the official mutator with its file-predicate supplied from
            # the validated in-memory env; no temporary file is written.
            original = a5.env_file_requests_a5_host_nic_overlay
            a5.env_file_requests_a5_host_nic_overlay = lambda _: True
            try:
                template = a5.apply_a5_host_nic_privileged(template,
                    config["motor_deploy_config"]["hardware_type"], "<validated-env>", attach_npu=attach)
            finally:
                a5.env_file_requests_a5_host_nic_overlay = original
        weight = str(config["motor_deploy_config"].get("weight_mount_path") or "").strip()
        if weight and (not PurePosixPath(weight).is_absolute() or ".." in PurePosixPath(weight).parts or
                       any(c in weight for c in ('$', '`', '"', "\n", "\r", "\x00", ":"))):
            raise MotorConfigError("weight_mount_path must be a literal absolute Linux directory path")
        variables = {"NAME": name, "IMAGE": info["image"], "EXAMPLES": examples, "WEIGHT": weight}
        template = h.deploy.insert_create_binds(template, h.deploy.extra_create_bind_paths(args, examples))
        template = h.deploy.apply_enter_weight(template, weight)
        template = h.deploy.apply_enter_shm(template, h.docker._derive_dshm_size(config["motor_deploy_config"]),
                                          ipc_host=bool(attach and ipc))
        template = h.deploy.drop_duplicate_bind_mounts(template, variables)
        spec = docker_argv_to_spec(shlex.split(template.replace("\\\n", "")), variables)
        # The target is Linux even when an operator previews the plan on
        # Windows. Keep the official argv builder, with Linux path semantics.
        native_os = h.deploy.os
        if sys.platform == "win32":
            h.deploy.os = SimpleNamespace(path=posixpath, environ=os.environ)
        try:
            inner = h.deploy.build_in_place_inner_command(args, deployer)
        finally:
            h.deploy.os = native_os
        # This official builder intentionally omits outer physical --devices.
        argv = shlex.split(inner)
        ports = _port_list(effective, role, h)
        render_spec = None
        if h.render.role_needs_docker_render(args.role) and h.render.is_render_enabled(config):
            render_name = h.render.docker_render_container_name(name)
            render_argv = h.render.build_docker_render_run_argv(render_name, preserve_json(config))
            render_spec = docker_argv_to_spec(render_argv)
            render_port = (config.get("motor_coordinator_config", {}).get("render_config", {}).get("endpoint") or {}).get("port", 8100)
            ports.append(("render", int(render_port)))
        for label, port in ports:
            _int(port, label)
    labels = {MANAGED_LABEL: "true", SERVICE_LABEL: service, NODE_LABEL: node_id,
              ROLE_LABEL: role, INSTANCE_LABEL: instance, "motor-console.commit": verify_upstream_source(upstream_dir)["commit"],
              "motor-console.job": attempt}
    spec.update(command=["exec " + shlex.join(argv)], entrypoint=["/bin/bash", "-lc"],
                labels=labels, detach=True, stdin_open=False, tty=False,
                stop_signal="SIGTERM")
    if single and _kv_enabled(config):
        # Single-container has no --kv-store-ip CLI flag. Its upstream runtime
        # uses this service name; resolve it to the local host-network IP just
        # as Kubernetes DNS does, without rewriting original Motor JSON.
        spec["extra_hosts"] = {"mindie-motor-kvs-master": address}
    if render_spec:
        render_spec.update(labels={**labels, ROLE_LABEL: "render"}, detach=True,
                           stdin_open=False, tty=False)
    return {"create_kwargs": spec, "render_kwargs": render_spec, "command": shlex.join(argv),
            "ports": [{"name": k, "port": v} for k, v in ports], "devices": devices,
            "run_root": run_root, "examples_path": examples, "config_dir": config_dir,
            "original_user_config": config, "original_env": env, "effective_config": effective,
            "role": role, "instance_name": instance, "node_id": node_id, "container_name": name,
            "upstream_dir": str(upstream_path(upstream_dir))}


def generate_commands(user_config: dict, env: dict, placements: list[dict], nodes: list[dict] | dict) -> list[dict]:
    lookup = _node_map(nodes)
    manager = next((lookup.get(p.get("node_id"), {}) for p in placements
                    if p.get("role") in {"coordinator_controller", "single_container"}), {})
    result = []
    for place in placements:
        payload = {"service_id": "preview", "user_config": user_config, "env": env, "placement": place,
                   "node": lookup.get(place.get("node_id"), {}), "management": {"address": manager.get("address")}}
        try:
            command = build_container_spec(payload)["command"]
        except (ValueError, KeyError, TypeError) as exc:
            command = f"Plan unavailable: {exc}"
        result.append({"node_id": place.get("node_id"), "role": place.get("role"), "command": command})
    return result
