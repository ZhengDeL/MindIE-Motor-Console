"""Inspect a trusted local Motor checkout without importing or executing it.

This is a source-review aid, never a compatibility certificate. A completed
static audit always requires separate, commit-bound contract acceptance.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import difflib
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tokenize

WEB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WEB))
from backend.compatibility import source_profile
from backend.version import ADAPTER_DRIVER, BASELINE_MOTOR_COMMIT

DEPLOYER = "examples/deployer/"
ENTRY_POINTS = (DEPLOYER + "docker_deploy.py", DEPLOYER + "deploy.py")
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_FILES = 30000
# These are actual dependencies of motor_adapter.py and kubernetes_runtime.py,
# including helpers re-exported by docker_utils. They are not a native API claim.
HELPERS = {
    "docker_deploy": (
        "enter_docker_run_template", "_enter_template_attaches_npu", "apply_enter_devices",
        "extra_create_bind_paths", "insert_create_binds", "apply_enter_weight", "apply_enter_shm",
        "drop_duplicate_bind_mounts", "build_in_place_inner_command",
    ),
    "lib.docker_utils": (
        "validate_instance_nums", "validate_pd_hybrid_config", "validate_pod_layouts", "derive_params",
        "npu_docker_card_count", "required_visible_device_count", "docker_controller_ports",
        "collect_engine_listen_ports", "_engine_parallel_sizes", "_engine_world_size", "_read_kv_port",
        "_apply_kv_port", "engine_port_overrides_from_args", "apply_engine_port_overrides",
        "apply_docker_controller_port_defaults", "container_npu_count", "_derive_dshm_size",
    ),
    "lib.generator.render": (
        "is_render_enabled", "resolve_render_image", "docker_render_container_name",
        "role_needs_docker_render", "build_docker_render_run_argv",
    ),
    "lib.container_ipc": ("host_ipc_enabled",),
    "lib.a5_host_nic": ("env_requests_a5_host_nic_overlay", "env_file_requests_a5_host_nic_overlay",
                        "apply_a5_host_nic_privileged"),
    "lib.utils": ("read_json", "set_env_to_shell"),
    "lib.generator.k8s_utils": ("get_deploy_mode_from_config",),
    "lib.config_validator": ("validate_node_selectors", "validate_only_instance_changed"),
    "lib.prepare_utils": ("configmap_assets",),
    "lib.generator.engine": ("validate_instance_nums",),
    "lib.generator.infer_service": ("update_infer_service_replicas_only",),
    "lib.constant": ("HARDWARE_TYPE_A5",),
}
CLI_FLAGS = {
    ENTRY_POINTS[0]: {"--start", "--config_dir", "--container-name", "--role", "--instance-name",
                      "--pod-ip", "--nic-name", "--coordinator-ip", "--controller-ip", "--kv-store-ip",
                      "--node-manager-port", "--data-parallel-rpc-port", "--kv-port", "--base-port"},
    ENTRY_POINTS[1]: {"--dry-run", "--nostep", "--skip-check", "--config_dir"},
}
ADAPTER_SOURCE_PATHS = ("backend/compatibility.py", "backend/motor_adapter.py",
                        "backend/kubernetes_runtime.py", "scripts/package_motor_profile.py")
CONTRACTS = (
    ("source_and_binding", "both", "Verify exact candidate commit, clean packaged bytes, immutable profile ID, manifest SHA, driver/Agent protocol and service binding; old services retain their original profile."),
    ("json_round_trip", "both", "Save/import/export/revise arbitrary original Motor JSON and env, including unknown nested fields and future version values, without silently rewriting them; reject unsupported execution explicitly."),
    ("docker_helpers_and_templates", "docker", "Run reviewed candidate helper/render contracts in an isolated environment: role/CLI argv, hardware/device IDs, host mounts, IPC, ports, environment, Render sibling and allow-listed Docker SDK options; compare original/effective configuration."),
    ("docker_lifecycle_and_admission", "docker", "On dedicated NPU nodes verify real create/start/stop/restart/remove, stale/unknown inventory, final resource admission/ledger, operation receipts, ownership and cleanup. Restart must recheck occupied devices; retain unrelated containers/exporters."),
    ("docker_distributed_inference", "docker", "Verify actual multi-node Prefill/Decode or Union topology, HCCS/RoCE prerequisites, readiness/instances, normal and streaming inference, failure and recovery; a rendered plan or Running container is insufficient."),
    ("kubernetes_native_render", "kubernetes", "Run candidate deploy.py --dry-run on private copies; verify native YAML kinds/schema, ConfigMap assets, unknown JSON preservation, namespace/UID ownership, RBAC limits, PVC retention, EPD and supported role layouts; dry-run can still perform node-label reads."),
    ("kubernetes_scale_and_lifecycle", "kubernetes", "Exercise original validate_only_instance_changed/update_infer_service_replicas_only helpers plus real CRDs/operator, HPA pause/resume, stop/restart/scale/delete, replica convergence and partial-failure receipts; never use the mutating update CLI as a harmless dry-run."),
    ("native_http_and_security", "both", "Verify actual Coordinator/Controller inference/management/observability endpoints, response schemas, error and timeout handling, auth and HTTPS/mTLS with no silent plaintext fallback. Check /instances, /readiness, /metrics and /v1/models against this commit."),
    ("native_features", "feature-dependent", "Reconcile official feature declarations with executable entries; separately accept KV Conductor/affinity/pool recovery, fault-tolerance boundaries, snapshots and any newly declared feature. Table checkmarks do not enable a Docker role."),
    ("external_integrations", "integration-dependent", "Use real approved endpoints for Prometheus/Grafana, CCAE registry/Kafka/control receipts and AIGW discovery/routing. Document unavailable external conditions; fixtures do not establish external production integration."),
    ("upgrade_rollback", "both", "Keep previous API/Web/Agent/native profile images and configuration/database backups; verify upgrade and rollback without migrating existing service bindings or changing native scheduling/routing semantics."),
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def safe_relative(name: str) -> None:
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name or ":" in name
            or any(ord(c) < 32 for c in name)):
        raise ValueError("Unsafe native inventory path")


def local_path(value: str | Path) -> Path:
    text = str(value)
    if text.startswith(("\\\\", "//")) or re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        raise ValueError("Use an operator-verified local filesystem path; network/URI paths are not supported")
    return Path(value)


class Checkout:
    """Read Git objects/index and filesystem bytes; never invoke status filters."""

    def __init__(self, directory: str | Path):
        self.root = local_path(directory).resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Native checkout must be a directory")
        top = Path(self.git("rev-parse", "--show-toplevel").decode().strip()).resolve()
        if top != self.root:
            raise ValueError("Specify the native Git checkout root, not its parent or a subdirectory")
        self.commit = self.git("rev-parse", "--verify", "HEAD").decode().strip()
        if not re.fullmatch(r"[0-9a-f]{40}", self.commit):
            raise ValueError("Expected a 40-character native Git commit")
        self.head = self.entries(self.git("ls-tree", "-r", "-z", "--full-tree", "HEAD"), tree=True)
        self.index = self.entries(self.git("ls-files", "--stage", "-z"), tree=False)
        self.untracked = [n.decode("utf-8") for n in self.git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if n]
        for name in self.untracked:
            safe_relative(name)
        self.raw: dict[str, bytes] = {}
        self.unsafe: list[dict] = []
        self.missing: list[str] = []
        total = 0
        for name in sorted(self.head.keys() | self.index.keys()):
            metadata = self.index.get(name) or self.head[name]
            if metadata["mode"] not in {"100644", "100755"} or metadata.get("stage", "0") != "0":
                self.unsafe.append({"path": name, "reason": "Symlink, submodule or unresolved index stage"})
                continue
            path = self.root
            for part in PurePosixPath(name).parts:
                path = path / part
                if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                    self.unsafe.append({"path": name, "reason": "Filesystem symlink/junction is not followed"})
                    break
            else:
                if not path.is_file():
                    self.missing.append(name)
                    continue
                size = path.stat().st_size
                total += size
                if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                    raise ValueError("Native source exceeds bounded audit size; no candidate code was executed")
                raw = path.read_bytes()
                if len(raw) > MAX_FILE_BYTES:
                    raise ValueError("Native source changed size while reading")
                self.raw[name] = raw
        self.head_hashes = self.object_hashes()
        self.initial = self.fingerprint()

    def git(self, *arguments: str, stdin: bytes | None = None) -> bytes:
        # No status/diff/show/help invocation: status can run configured clean
        # filters, diff can run external commands, and native --help runs code.
        env = os.environ.copy()
        for key in list(env):
            if key.startswith("GIT_"):
                env.pop(key)
        result = subprocess.run(["git", "--no-optional-locks", "--no-pager", "-c", "core.fsmonitor=false",
                                 "-c", "core.hooksPath=/dev/null", "-C", str(self.root), *arguments],
                                input=stdin, capture_output=True, check=False, env=env, timeout=60)
        if result.returncode:
            raise ValueError("Local Git metadata read failed: " + " ".join(arguments[:2]))
        return result.stdout

    @staticmethod
    def entries(raw: bytes, *, tree: bool) -> dict:
        result = {}
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            metadata, raw_name = entry.split(b"\t", 1)
            name = raw_name.decode("utf-8")
            safe_relative(name)
            mode, second, third = metadata.decode("ascii").split()
            value = {"mode": mode, "oid": third if tree else second}
            if tree and second not in {"blob", "commit"}:
                raise ValueError("Unexpected Git object type")
            if not tree:
                value["stage"] = third
            if name in result:
                value["stage"] = "conflict"
            result[name] = value
            if len(result) > MAX_FILES:
                raise ValueError("Native source inventory exceeds supported file count")
        return result

    def object_hashes(self) -> dict[str, str]:
        # cat-file --batch never applies checkout filters or text conversion.
        objects = sorted({m["oid"] for m in self.head.values() if m["mode"] in {"100644", "100755"}})
        if not objects:
            return {}
        request = ("\n".join(objects) + "\n").encode("ascii")
        sizes = self.git("cat-file", "--batch-check", stdin=request).decode("ascii").splitlines()
        if len(sizes) != len(objects):
            raise ValueError("Incomplete native Git object size inventory")
        total = 0
        for expected, line in zip(objects, sizes):
            header = line.split()
            if len(header) != 3 or header[:2] != [expected, "blob"]:
                raise ValueError("Unexpected native Git object size response")
            size = int(header[2])
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("Native Git source blobs exceed bounded audit size")
        raw = self.git("cat-file", "--batch", stdin=request)
        if len(raw) > MAX_TOTAL_BYTES + len(objects) * 100:
            raise ValueError("Cannot inspect bounded native Git blobs")
        stream = io.BytesIO(raw)
        hashes = {}
        for expected in objects:
            header = stream.readline().decode("ascii").strip().split()
            if len(header) != 3 or header[:2] != [expected, "blob"]:
                raise ValueError("Unexpected native Git blob response")
            size = int(header[2])
            if size > MAX_FILE_BYTES:
                raise ValueError("Git source blob exceeds supported size")
            blob = stream.read(size)
            if len(blob) != size or stream.read(1) != b"\n":
                raise ValueError("Incomplete native Git blob")
            hashes[expected] = digest(blob)
        return hashes

    def fingerprint(self) -> dict:
        hashes = {name: digest(raw) for name, raw in self.raw.items()}
        return {"commit": self.commit, "head": self.head, "index": self.index,
                "worktree_sha256": hashes, "untracked": self.untracked}

    def state(self) -> dict:
        changed = [name for name, metadata in self.head.items()
                   if name in self.raw and digest(self.raw[name]) != self.head_hashes.get(metadata["oid"])]
        index_changed = sorted(name for name in self.head.keys() | self.index.keys()
                               if self.head.get(name) != {k: v for k, v in self.index.get(name, {}).items() if k != "stage"})
        return {"commit": self.commit, "tracked_files": len(self.head), "index_changes": index_changed,
                "tracked_worktree_byte_changes": sorted(changed), "missing_tracked_files": self.missing,
                "unsafe_entries": self.unsafe, "untracked_files": self.untracked,
                "exact_tracked_bytes_and_index_match_head": not (changed or index_changed or self.missing or self.unsafe),
                "scope": "Raw bytes plus Git tree/index metadata; not porcelain normalization. CRLF/filter differences are reported, never normalized by executing checkout filters. Ignored files are not inventoried or executed."}

    def unchanged(self) -> bool:
        return Checkout(self.root).initial == self.initial


def decode(raw: bytes) -> str:
    encoding, _ = tokenize.detect_encoding(io.BytesIO(raw).readline)
    return raw.decode(encoding)


def syntax(raw: bytes) -> ast.Module:
    return ast.parse(decode(raw))


def node_text(node: ast.AST | None) -> str | None:
    return ast.unparse(node) if node is not None else None


def function_shape(node: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    return {"kind": type(node).__name__, "arguments": ast.dump(node.args, include_attributes=False),
            "signature": "(" + ast.unparse(node.args) + ")", "returns": node_text(node.returns),
            "decorators": [node_text(d) for d in node.decorator_list]}


def python_surface(raw: bytes) -> dict:
    tree = syntax(raw)
    functions, classes, flags, routes = {}, {}, {}, []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = function_shape(node)
        elif isinstance(node, ast.ClassDef):
            classes[node.name] = {"bases": [node_text(b) for b in node.bases],
                "fields": {n.target.id: {"annotation": node_text(n.annotation), "default": node_text(n.value)}
                           for n in node.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)},
                "methods": {n.name: function_shape(n) for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument":
            names = [a.value for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("-")]
            declaration = {"aliases": names, "keywords": {k.arg: node_text(k.value) for k in node.keywords if k.arg}}
            for name in names:
                flags[name] = declaration
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr in {"get", "post", "put", "patch", "delete", "websocket"}):
                    routes.append({"method": decorator.func.attr, "path_expression": node_text(decorator.args[0]) if decorator.args else None,
                                   "function": node.name, "signature": function_shape(node)})
    return {"functions": functions, "classes": classes, "cli_arguments": flags, "http_routes": routes}


def resolve_symbol(files: dict[str, bytes], module: str, symbol: str, visited=None) -> dict | None:
    visited = (visited or set()) | {(module, symbol)}
    if len(visited) > 12:
        return None
    path = DEPLOYER + module.replace(".", "/") + ".py"
    raw = files.get(path)
    if raw is None:
        return None
    tree = syntax(raw)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            shape = function_shape(node) if not isinstance(node, ast.ClassDef) else python_surface(raw)["classes"][symbol]
            return {"path": path, "line": node.lineno, "shape": shape}
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == symbol for t in targets):
                return {"path": path, "line": node.lineno, "shape": {"constant_expression": node_text(node.value)}}
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if (alias.asname or alias.name) == symbol and (node.module, alias.name) not in visited:
                    return resolve_symbol(files, node.module, alias.name, visited)
    return None


def category(path: str) -> str:
    if path in ENTRY_POINTS:
        return "entry_points"
    if path.endswith("supported_features.md"):
        return "feature_matrix"
    if path.startswith("model_configs/") or (path.startswith(DEPLOYER) and path.endswith(("user_config.json", "env.json"))):
        return "config_examples"
    if path.startswith(DEPLOYER + "lib/"):
        return "deployer_helpers"
    if path.startswith(DEPLOYER):
        return "startup_and_templates"
    if path.startswith("motor/"):
        return "native_runtime"
    return "other_tracked_source"


def structural_diff(before: object, after: object, prefix="") -> list[dict]:
    if isinstance(before, dict) and isinstance(after, dict):
        result = []
        for key in sorted(before.keys() | after.keys()):
            pointer = prefix + "/" + str(key).replace("~", "~0").replace("/", "~1")
            if key not in before:
                result.append({"path": pointer, "change": "added", "candidate": after[key]})
            elif key not in after:
                result.append({"path": pointer, "change": "removed", "baseline": before[key]})
            else:
                result.extend(structural_diff(before[key], after[key], pointer))
        return result
    if type(before) != type(after) or before != after:
        return [{"path": prefix or "/", "change": "type_changed" if type(before) != type(after) else "value_changed",
                 "baseline": before, "candidate": after}]
    return []


def adapter_snapshot() -> dict[str, str]:
    files = {name: (WEB / name).read_bytes() for name in ADAPTER_SOURCE_PATHS}
    modules = {"deploy": "docker_deploy", "docker": "lib.docker_utils",
               "render": "lib.generator.render", "constant": "lib.constant"}
    declared = {(module, symbol) for module, symbols in HELPERS.items() for symbol in symbols}
    for node in ast.walk(syntax(files["backend/motor_adapter.py"])):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
                and isinstance(node.value.value, ast.Name) and node.value.value.id == "h"
                and node.value.attr in modules):
            reference = (modules[node.value.attr], node.attr)
            # The adapter temporarily supplies Linux os.path during Windows
            # planning. It is an imported module, not a callable helper.
            if reference != ("docker_deploy", "os") and reference not in declared:
                raise ValueError("Audit helper inventory must be reviewed for current adapter reference: " + ".".join(reference))
    return {name: digest(raw) for name, raw in files.items()}


def audit(candidate_dir, baseline_dir=None, *, diff_lines=160) -> dict:
    adapter_sources = adapter_snapshot()
    profile = source_profile()
    if not profile["bundled"] or profile["commit"] != BASELINE_MOTOR_COMMIT:
        raise ValueError("Audit uses the bundled exact baseline; do not inherit a custom MOTOR_SOURCE_MANIFEST override")
    baseline = Checkout(baseline_dir or WEB.parent / "mindie-motor/upstream")
    if baseline.commit != BASELINE_MOTOR_COMMIT:
        raise ValueError("Baseline checkout HEAD differs from the recorded native pin")
    baseline_state = baseline.state()
    if (baseline_state["index_changes"] or baseline_state["tracked_worktree_byte_changes"]
            or baseline_state["missing_tracked_files"]):
        raise ValueError("Baseline regular tracked bytes and index must match its exact pinned Git tree; modified or missing baseline files cannot serve as a comparison origin")
    mismatch = [p for p, sha in profile["files"].items() if p not in baseline.raw or digest(baseline.raw[p]) != sha]
    if mismatch:
        raise ValueError("Baseline source-profile inventory differs: " + ", ".join(mismatch[:8]))
    candidate = Checkout(candidate_dir)
    findings, groups = [], defaultdict(list)
    for name in ENTRY_POINTS:
        if name not in candidate.raw:
            findings.append({"code": "required_entry_missing", "path": name, "severity": "blocker",
                             "detail": "Current adapter entry cannot be inspected; restore or redesign and accept its contract."})
    if candidate.unsafe:
        findings.extend({"code": "uninspected_nonregular_inventory",
                         "severity": "blocker" if entry["path"] in profile["files"] or entry["path"].startswith(DEPLOYER) else "review",
                         **entry} for entry in candidate.unsafe)
    for module, symbols in HELPERS.items():
        for symbol in symbols:
            try:
                before = resolve_symbol(baseline.raw, module, symbol)
                after = resolve_symbol(candidate.raw, module, symbol)
            except (SyntaxError, UnicodeError, LookupError) as exc:
                findings.append({"code": "helper_unparseable", "path": DEPLOYER + module.replace(".", "/") + ".py",
                                 "symbol": symbol, "severity": "blocker", "detail": type(exc).__name__})
                continue
            if before is None:
                raise ValueError("Audit dependency inventory does not match current adapter baseline: " + module + "." + symbol)
            if after is None:
                findings.append({"code": "required_helper_unresolved", "path": before["path"], "symbol": symbol,
                                 "severity": "blocker", "detail": "Static resolver cannot find the expected helper/re-export; this is not a proof about dynamically generated exports."})
            elif before["shape"] != after["shape"]:
                findings.append({"code": "helper_shape_changed", "path": after["path"], "symbol": symbol,
                                 "severity": "review", "baseline": before, "candidate": after,
                                 "detail": "Call signature/constant shape changed; inspect caller arguments and run the real contract. A change alone is not a compatibility verdict."})
    for path in sorted(baseline.head.keys() | candidate.head.keys() | candidate.index.keys()):
        before, after = baseline.raw.get(path), candidate.raw.get(path)
        if before == after:
            continue
        item = {"path": path, "change": "added" if before is None else "removed" if after is None else "modified",
                "in_baseline_integrity_profile": path in profile["files"],
                "baseline_sha256": digest(before) if before is not None else None,
                "candidate_sha256": digest(after) if after is not None else None}
        if path.endswith(".py"):
            try:
                old = python_surface(before) if before is not None else {}
                new = python_surface(after) if after is not None else {}
                item["python_interface_changes"] = structural_diff(old, new)
            except (SyntaxError, UnicodeError, LookupError) as exc:
                item["parse_error"] = type(exc).__name__
                findings.append({"code": "python_parse_error", "severity": "blocker" if path.startswith(DEPLOYER) else "review", "path": path})
        elif path.endswith(".json"):
            try:
                old = json.loads(before) if before is not None else {}
                new = json.loads(after) if after is not None else {}
                item["json_changes"] = structural_diff(old, new)
                item["unknown_fields_policy"] = "Report and preserve all fields; no version/key allowlist migration is performed."
            except (ValueError, UnicodeError) as exc:
                item["parse_error"] = type(exc).__name__
                findings.append({"code": "json_parse_error", "severity": "review", "path": path})
        try:
            lines = list(difflib.unified_diff((before or b"").decode("utf-8").splitlines(),
                                            (after or b"").decode("utf-8").splitlines(),
                                            fromfile="baseline/" + path, tofile="candidate/" + path, lineterm=""))
            item["unified_diff"] = "\n".join(lines[:diff_lines])
            item["diff_truncated"] = len(lines) > diff_lines
            item["diff_total_lines"] = len(lines)
        except UnicodeError:
            item["text_diff"] = "Non-UTF8/binary bytes; manually review exact hashes."
        groups[category(path)].append(item)
    entry_contracts = []
    for path, flags in CLI_FLAGS.items():
        before = python_surface(baseline.raw[path])["cli_arguments"]
        try:
            after = python_surface(candidate.raw[path])["cli_arguments"] if path in candidate.raw else {}
        except (SyntaxError, UnicodeError, LookupError):
            after = {}
        missing = sorted(flags - after.keys())
        entry_contracts.append({"path": path, "required_flags": sorted(flags), "missing_flags": missing,
                                "declaration_changes": structural_diff(before, after)})
        if missing:
            findings.append({"code": "required_cli_flag_missing", "severity": "blocker", "path": path, "flags": missing})
    if not baseline.unchanged() or not candidate.unchanged() or adapter_snapshot() != adapter_sources:
        raise ValueError("Source checkout changed during audit; report is not a coherent snapshot")
    inventory = [{"path": path, "head": candidate.head.get(path), "index": candidate.index.get(path),
                  "worktree_sha256": digest(candidate.raw[path]) if path in candidate.raw else None}
                 for path in sorted(candidate.head.keys() | candidate.index.keys())]
    return {"audit_schema": 1, "status": "requires_contract_acceptance", "static_audit_complete": True,
            "compatible": None, "contract_evidence": "not_executed_or_accepted_by_this_read_only_tool",
            "baseline": {"profile_id": profile["profile_id"], "commit": profile["commit"], "driver": ADAPTER_DRIVER,
                         "manifest_sha256": profile["manifest_sha256"], "verified_profile_files": len(profile["files"]),
                         "checkout": str(baseline.root), "state": baseline.state()},
            "adapter_sources": adapter_sources,
            "adapter_helper_dependencies": {module: list(symbols) for module, symbols in HELPERS.items()},
            "candidate": {"checkout": str(candidate.root), "state": candidate.state(), "source_inventory": inventory,
                          "inventory_sha256": digest(json.dumps(inventory, sort_keys=True).encode())},
            "findings": findings, "finding_counts": dict(Counter(f["severity"] for f in findings)),
            "entry_contracts": entry_contracts, "changes_by_path_group": dict(groups),
            "changed_file_count": sum(map(len, groups.values())),
            "required_real_contracts": [{"id": identifier, "runtime": runtime, "required_acceptance": detail,
                                          "status": "requires_contract_acceptance"} for identifier, runtime, detail in CONTRACTS],
            "limits": ["No candidate Python import/exec, CLI --help/dry-run, dependency install, server access, submodule update or Git checkout/filter execution.",
                       "Equal hashes/signatures are static facts only; changed hashes are not evidence of incompatibility.",
                       "AST cannot resolve dynamic imports/generated CLI/routes or prove runtime response schemas, side effects or complete dependency closure.",
                       "Untracked nonignored paths are listed but not read; ignored paths are excluded. Raw tracked bytes differ from Git porcelain normalization.",
                       "No profile package, contract-tested marker, deployment approval or automatic upgrade is generated."]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", required=True, help="Trusted local native Git checkout root")
    parser.add_argument("--baseline-dir", help="Local checkout of the exact recorded baseline; defaults to pinned submodule")
    parser.add_argument("--output", help="New report path outside both checkouts; stdout if omitted")
    parser.add_argument("--diff-lines", type=int, default=160, help="Maximum diff lines per changed path (0..2000)")
    args = parser.parse_args()
    try:
        if not 0 <= args.diff_lines <= 2000:
            raise ValueError("diff-lines must be between 0 and 2000")
        report = audit(args.candidate_dir, args.baseline_dir, diff_lines=args.diff_lines)
        raw = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if args.output:
            output = local_path(args.output).resolve()
            if any(output.is_relative_to(Path(report[side]["checkout"])) for side in ("baseline", "candidate")):
                raise ValueError("Report output must be outside native source checkouts")
            with output.open("xb") as handle:
                handle.write(raw)
            print(json.dumps({"report": str(output), "status": report["status"], "changed_files": report["changed_file_count"], "finding_counts": report["finding_counts"]}))
        else:
            sys.stdout.buffer.write(raw)
        return 3 if report["finding_counts"].get("blocker") else 0
    except (OSError, ValueError, subprocess.SubprocessError, UnicodeError, RecursionError) as exc:
        print(json.dumps({"static_audit_complete": False, "status": "requires_contract_acceptance", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
