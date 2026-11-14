"""Fixed SSH installer protocol. Python 3.9 stdlib; no engine/RBAC installation.

The control plane sends one bounded JSON header on stdin. Only the upload
action may follow it with a bounded image stream. No user shell is accepted.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from uuid import uuid4


MAX_ARCHIVE = 2 * 1024**3
ENV = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": "/root", "LANG": "C.UTF-8"}
PRODUCT_ID = "motor-console"
# This standalone SSH contract mirrors backend.version without importing the application.
MIN_AGENT_VERSION = (0, 10, 1)
AGENT_VERSION_PATTERN = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
RUNTIME_PROJECTS = {"docker": "motor-console-docker", "kubernetes": "motor-console-kubernetes"}


class Refused(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def reject(code):
    raise Refused(code)


def raw_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def safe_path(value):
    if (not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]{2,450}", value)
            or ".." in value.split("/") or "//" in value or value.endswith("/")):
        reject("remote_path_invalid")
    path = Path(value)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            reject("remote_symlink_refused")
        if parent.exists():
            info = parent.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                reject("remote_path_permissions_invalid")
    return path


def write_exclusive(path, raw):
    descriptor = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(raw); handle.flush(); os.fsync(handle.fileno())


def read_json(path, maximum=16384):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum or info.st_mode & 0o077 or info.st_uid != 0:
        reject("remote_private_file_invalid")
    return json.loads(path.read_bytes())


def run(arguments, timeout=60, *, json_result=False):
    try:
        process = subprocess.Popen(arguments, env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL)
        chunks, size, deadline = [], 0, time.monotonic() + timeout
        # docker/compose command results are bounded at the pipe, before parsing.
        import selectors
        selector = selectors.DefaultSelector(); selector.register(process.stdout, selectors.EVENT_READ)
        try:
            while selector.get_map():
                if time.monotonic() > deadline:
                    process.kill(); process.wait(); reject("remote_command_timeout")
                for key, _ in selector.select(.2):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj); continue
                    size += len(chunk)
                    if size > 4 * 1024**2:
                        process.kill(); process.wait(); reject("remote_command_output_limit")
                    chunks.append(chunk)
            code = process.wait(timeout=max(1, deadline-time.monotonic()))
        finally:
            selector.close(); process.stdout.close()
            if process.poll() is None:
                process.kill(); process.wait()
        if code:
            reject("remote_command_failed")
        output = b"".join(chunks)
        return json.loads(output) if json_result else output.decode("utf-8", errors="strict").strip()
    except Refused:
        raise
    except (OSError, ValueError, subprocess.SubprocessError):
        reject("remote_command_unavailable")


def compose_command():
    candidates = [["docker", "compose"]]
    standalone = shutil.which("docker-compose", path=ENV["PATH"])
    if standalone:
        candidates.append([standalone])
    for command in candidates:
        try:
            version = run(command+["version", "--short"])
        except Refused:
            continue
        match = re.fullmatch(r"v?([0-9]+)\.([0-9]+)(?:\.[0-9]+)?(?:[-+][A-Za-z0-9._-]+)?", version)
        if match and tuple(map(int, match.groups())) >= (2, 20):
            return command
    reject("remote_compose_v2_required")


def image_reference(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:sha256:[0-9a-f]{64}|[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64})", value):
        reject("image_reference_invalid")
    return value


def locations(payload):
    if os.geteuid() != 0 or not sys.platform.startswith("linux") or sys.version_info < (3, 9):
        reject("remote_root_linux_python_required")
    base = safe_path(payload.get("install_root"))
    if (str(base) != "/opt/motor-console/agents"
            and not any(base.is_relative_to(Path(prefix)) for prefix in ("/var/lib", "/opt", "/srv", "/mnt/models"))
            or len(base.parts) < 3 or str(base) in {"/var/lib/docker", "/var/lib/containerd", "/var/ai-model", "/var/lib"}):
        reject("remote_install_root_invalid")
    parts = [part.casefold() for part in base.parts]
    if (any(part in {"hx", "shhc"} or part.startswith("hx-") or part.endswith("-hx") for part in parts)
            or not any(part in {"motor-console", "qinghuan"} or part.startswith(("motor-console-", "qinghuan-")) or part.endswith("-qh") for part in parts)):
        reject("remote_product_directory_required")
    operation = payload.get("operation_id")
    if not isinstance(operation, str) or not re.fullmatch(r"rollout_[0-9a-f]{32}", operation):
        reject("operation_identity_invalid")
    spec = payload.get("spec")
    if not isinstance(spec, dict) or spec.get("runtime") not in RUNTIME_PROJECTS:
        reject("runtime_invalid")
    name = spec.get("installation_name")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", name):
        reject("installation_name_invalid")
    install = safe_path(str(base / name))
    digest = hashlib.sha256(raw_json(spec)).hexdigest()
    return base, install, spec, operation, digest


def own_state(payload):
    base, install, spec, operation, digest = locations(payload)
    marker = read_json(install / ".motor-console-rollout.json")
    if marker.get("operation_id") != operation or marker.get("spec_digest") != digest:
        reject("installation_ownership_conflict")
    reservation = read_json(base / ".active-rollout.json")
    if reservation.get("operation_id") != operation:
        reject("host_installation_busy")
    return base, install, spec, operation, digest, marker


def private_kubeconfig(spec):
    path = safe_path(spec.get("kubeconfig_path"))
    try:
        info = path.lstat()
    except OSError:
        reject("kubeconfig_unavailable")
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
            or info.st_mode & 0o077 or not 1 <= info.st_size <= 2 * 1024**2):
        reject("kubeconfig_private_file_required")
    return path


def platform_agents():
    ids = run(["docker", "ps", "-aq"]).splitlines()
    if len(ids) > 512:
        reject("remote_container_inventory_limit")
    values = run(["docker", "inspect", *ids], json_result=True) if ids else []
    agents = []
    for value in values:
        config = value.get("Config") or {}; labels = config.get("Labels") or {}
        environment = config.get("Env", [])
        mode = next((v.split("=", 1)[1] for v in environment if v.startswith("MOTOR_MODE=")), "")
        project = labels.get("com.docker.compose.project", "")
        recognized_project = project in {
            "motor-console-docker", "motor-console-kubernetes", "motor-console-docker-agent",
            "motor-console-kubernetes-agent", "qinghuan-docker", "qinghuan-kubernetes"}
        name = value.get("Name", "").lstrip("/")
        if mode == "agent" or recognized_project or (name.startswith("motor-console-") and name.endswith("-agent-1")):
            agents.append({"id": value["Id"], "name": name[:100], "image_id": value["Image"],
                           "running": value.get("State", {}).get("Running") is True,
                           "runtime": "kubernetes" if labels.get("com.docker.compose.project") == RUNTIME_PROJECTS["kubernetes"] else "docker",
                           "project": project,
                           "operation_id": labels.get("motor-console.rollout.id", "")})
    return agents


def probe(payload):
    base, install, spec, operation, digest = locations(payload)
    if spec["runtime"] == "kubernetes":
        private_kubeconfig(spec)
    info = run(["docker", "info", "--format", "{{json .}}"], json_result=True)
    compose = compose_command()
    architecture = {"aarch64": "arm64", "x86_64": "amd64"}.get(info.get("Architecture"), info.get("Architecture"))
    if architecture not in {"arm64", "amd64"} or info.get("OSType") != "linux":
        reject("remote_architecture_unsupported")
    existing = platform_agents()
    port = spec.get("agent_port")
    if type(port) is not int or not 1024 <= port <= 65535:
        reject("agent_port_invalid")
    bind = spec.get("address") if spec.get("tls_mode") == "managed_tls" else "127.0.0.1"
    import ipaddress
    address = ipaddress.IPv4Address(bind)
    available = False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((str(address), port)); available = True
        except OSError:
            pass
    boot = Path("/proc/sys/kernel/random/boot_id").read_bytes()
    docker_root = str(info.get("DockerRootDir", ""))
    if docker_root and (base.is_relative_to(Path(docker_root)) or Path(docker_root).is_relative_to(install)):
        reject("install_path_overlaps_docker_storage")
    identity = {"boot_id_sha256": hashlib.sha256(boot).hexdigest(), "docker_id": str(info.get("ID", ""))[:200],
                "architecture": architecture, "docker_root": docker_root[:512]}
    return {"identity": identity, "architecture": architecture, "docker_version": str(info.get("ServerVersion", ""))[:100],
            "compose_version": run(compose + ["version", "--short"])[:100], "existing_installation": existing,
            "installation_path_exists": install.exists(), "host_reservation_exists": (base / ".active-rollout.json").exists(),
            "agent_port_available": available, "installation_dir": str(install),
            "can_install": not existing and not install.exists() and available and not (base / ".active-rollout.json").exists()}


def stage(payload):
    current = probe(payload)
    if current["identity"] != payload.get("expected_identity"):
        reject("remote_identity_changed")
    if not current["can_install"]:
        reject("remote_installation_conflict")
    base, install, spec, operation, digest = locations(payload)
    base.mkdir(mode=0o700, parents=True, exist_ok=True); safe_path(str(base))
    write_exclusive(base / ".active-rollout.json", raw_json({"operation_id": operation}))
    try:
        install.mkdir(mode=0o700, exist_ok=False)
        marker = {"schema": 1, "operation_id": operation, "spec_digest": digest, "created_at": time.time()}
        write_exclusive(install / ".motor-console-rollout.json", raw_json(marker))
        package = install / "package/web"; package.mkdir(mode=0o700, parents=True)
        files = payload.get("materials")
        if not isinstance(files, dict) or set(files) != {"compose.docker-agent.yaml", "compose.kubernetes-agent.yaml"}:
            reject("installer_materials_invalid")
        for name, value in files.items():
            if not isinstance(value, str) or len(value.encode()) > 65536:
                reject("installer_materials_invalid")
            write_exclusive(package / name, value.encode())
        override = {"services": {"agent": {"labels": {"motor-console.rollout.id": operation,
                    "motor-console.rollout.installation": spec["installation_name"]},
                    "environment": {"MOTOR_NODE_ADDRESS": spec["address"], "MOTOR_NODE_NIC": spec.get("nic_name", "")}}}}
        write_exclusive(package / "rollout.override.yaml", raw_json(override))
        return {"installation_dir": str(install), "archive_path": str(install / "agent-image.tar.gz")}
    except Exception:
        reservation = base / ".active-rollout.json"
        if reservation.exists() and read_json(reservation).get("operation_id") == operation:
            reservation.unlink()
        raise


def archive_properties(payload):
    artifact = payload.get("artifact")
    if (not isinstance(artifact, dict) or type(artifact.get("size_bytes")) is not int
            or not 1 <= artifact["size_bytes"] <= MAX_ARCHIVE
            or not isinstance(artifact.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])):
        reject("artifact_identity_invalid")
    return artifact


def receive(payload):
    _, install, _, _, _, _ = own_state(payload); artifact = archive_properties(payload)
    path = install / "agent-image.tar.gz"; digest = hashlib.sha256(); size = 0
    with path.open("xb") as handle:
        path.chmod(0o600)
        while size < artifact["size_bytes"]:
            chunk = sys.stdin.buffer.read(min(1024**2, artifact["size_bytes"]-size))
            if not chunk:
                reject("artifact_stream_incomplete")
            size += len(chunk); digest.update(chunk); handle.write(chunk)
        handle.flush(); os.fsync(handle.fileno())
    if digest.hexdigest() != artifact["sha256"]:
        reject("artifact_checksum_mismatch")
    return {"uploaded_bytes": size, "sha256": digest.hexdigest()}


def load_image(payload):
    _, install, _, _, _, _ = own_state(payload); artifact = archive_properties(payload)
    path = install / "agent-image.tar.gz"; before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size != artifact["size_bytes"]:
        reject("artifact_size_mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024**2), b""):digest.update(chunk)
    if digest.hexdigest() != artifact["sha256"]:
        reject("artifact_checksum_mismatch")
    stamp = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if stamp(path.lstat()) != stamp(before):
        reject("artifact_changed_during_read")
    run(["docker", "load", "--input", str(path)], timeout=600)
    if stamp(path.lstat()) != stamp(before):
        reject("artifact_changed_during_read")
    return inspect_image(artifact["image_id"], payload)


def inspect_image(reference, payload):
    image_reference(reference)
    values = run(["docker", "image", "inspect", reference], json_result=True)
    if not isinstance(values, list) or len(values) != 1:
        reject("loaded_image_invalid")
    value = values[0]
    architecture = value.get("Architecture")
    if architecture != payload.get("expected_identity", {}).get("architecture"):
        reject("image_architecture_mismatch")
    identifier = value.get("Id")
    if not isinstance(identifier, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", identifier):
        reject("loaded_image_invalid")
    if reference.startswith("sha256:") and reference != identifier:
        reject("loaded_image_identity_mismatch")
    return {"image_id": identifier, "architecture": architecture}


def pull_image(payload):
    own_state(payload); reference = image_reference(payload.get("image"))
    if not reference.startswith("sha256:"):
        run(["docker", "pull", "--quiet", reference], timeout=600)
    return inspect_image(reference, payload)


def helper_command(install, image, operation, name):
    return ["docker", "run", "--rm", "--pull", "never", "--name", name, "--label", "motor-console.rollout.helper="+operation,
            "--runtime", "runc", "--network", "none", "--read-only", "--user", "0",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--memory", "256m", "--cpus", "1",
            "--pids-limit", "64", "--tmpfs", "/tmp:size=64m,mode=1777", "--mount",
            "type=bind,source="+str(install)+",target="+str(install), "--entrypoint", "python", image]


def run_helper(install, image, operation, stage_name, arguments, *, timeout=90, kube=None):
    name = "motor-console-bootstrap-"+operation[8:20]+"-"+stage_name+"-"+uuid4().hex[:10]
    command = helper_command(install, image, operation, name)
    if kube is not None:
        command[-1:-1] = ["--mount", "type=bind,source="+str(kube)+",target="+str(kube)+",readonly"]
    try:
        return run(command+arguments, timeout=timeout, json_result=True)
    finally:
        # Killing a docker CLI does not terminate its container. Cleanup only a
        # full CID whose unique name, image and operation label all match.
        try:
            identifiers = run(["docker", "ps", "-aq", "--filter", "name=^/"+name+"$"]).splitlines()
            if identifiers:
                try:
                    values = run(["docker", "inspect", *identifiers], json_result=True)
                except Refused:
                    # --rm may complete between ps and inspect.
                    if run(["docker", "ps", "-aq", "--filter", "name=^/"+name+"$"]):
                        reject("helper_cleanup_unverified")
                    values = []
                if values:
                    if len(values) != 1:
                        reject("helper_cleanup_unverified")
                    value = values[0]
                    if (value.get("Name") != "/"+name or value.get("Image") != image
                            or (value.get("Config", {}).get("Labels") or {}).get("motor-console.rollout.helper") != operation
                            or not re.fullmatch(r"[0-9a-f]{64}", value.get("Id", ""))):
                        reject("helper_cleanup_unverified")
                    try:
                        run(["docker", "rm", "--force", value["Id"]], timeout=30)
                    except Refused:
                        if run(["docker", "ps", "-aq", "--filter", "name=^/"+name+"$"]):
                            reject("helper_cleanup_unverified")
        except Refused:
            reject("helper_cleanup_unverified")


def prepare(payload):
    _, install, spec, operation, _, _ = own_state(payload)
    image = image_reference(payload.get("image")); inspect_image(image, payload)
    code = (
        "import json,re;from pathlib import Path;from backend.compatibility import agent_metadata;"
        "from backend.motor_adapter import verify_upstream_source;"
        "m=agent_metadata();n=verify_upstream_source();"
        "assert m['product_id']=='motor-console';"
        f"assert isinstance(m['agent_version'],str) and re.fullmatch({AGENT_VERSION_PATTERN!r},m['agent_version']);"
        f"assert tuple(map(int,m['agent_version'].split('.'))) >= {MIN_AGENT_VERSION!r};"
        "assert type(m['agent_protocol']) is int and m['agent_protocol']==1;"
        "assert type(m['operation_protocol']) is int and m['operation_protocol']==1;"
        "assert all(m['execution_capabilities'].get(k) is True for k in ('operation_receipts','resource_ledger','resource_admission'));"
        "assert n['commit']=='37d615f232a36b8dba8630ffc3a01a20d289c65e';"
        "assert type(n['verified_files']) is int and n['verified_files']==465;"
        "assert Path('/app/scripts/prepare_agent.py').is_file() and Path('/app/scripts/configure_agent_tls.py').is_file();"
        "print(json.dumps({'compatible':True,'product_id':m['product_id']}))"
    )
    verified = run_helper(install, image, operation, "verify", ["-c", code], timeout=60)
    if verified != {"compatible": True, "product_id": PRODUCT_ID}:
        reject("agent_product_contract_invalid")
    token = payload.get("agent_token")
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,200}", token):
        reject("agent_credential_invalid")
    token_file = install / "bootstrap.token"; write_exclusive(token_file, token.encode())
    arguments = ["/app/scripts/prepare_agent.py", "--runtime", spec["runtime"], "--project-dir", str(install / "package"),
                 "--data-dir", str(install / "data"), "--env-file", str(install / "agent.env"), "--image", image,
                 "--bind-ip", spec["address"] if spec["tls_mode"] == "managed_tls" else "127.0.0.1",
                 "--port", str(spec["agent_port"]), "--public-url", spec["agent_url"], "--https-proxy-confirmed",
                 "--token-file", str(token_file), "--cpus", spec["cpus"], "--memory", spec["memory"],
                 "--pids", str(spec["pids"]), "--native-concurrency", str(spec["native_concurrency"])]
    if spec["runtime"] == "docker":
        arguments += ["--workspace-dir", str(install / "workspace")]
    else:
        kube = private_kubeconfig(spec)
        arguments += ["--kubeconfig", str(kube)]
    try:
        result = run_helper(install, image, operation, "prepare", arguments, timeout=90,
                            kube=kube if spec["runtime"] == "kubernetes" else None)
    finally:
        token_file.unlink(missing_ok=True)
    if result.get("prepared") is not True:
        reject("agent_preparation_failed")
    tls = {}
    if spec["tls_mode"] == "managed_tls":
        tls = run_helper(install, image, operation, "tls", ["/app/scripts/configure_agent_tls.py", "--data-dir", str(install / "data"),
                  "--env-file", str(install / "agent.env"), "--advertised-url", spec["agent_url"]], timeout=60)
        if (not isinstance(tls, dict) or not isinstance(tls.get("ca_pem"), str) or len(tls["ca_pem"]) > 16384
                or "PRIVATE KEY" in tls["ca_pem"] or tls.get("agent_url") != spec["agent_url"]):
            reject("agent_tls_preparation_failed")
        environment = (install / "agent.env").read_text()
        if not all(name+"=" in environment for name in ("MOTOR_AGENT_SERVER_CERT_FILE", "MOTOR_AGENT_SERVER_KEY_FILE")):
            reject("agent_tls_environment_missing")
        write_exclusive(install / ".motor-console-tls.json", raw_json({key: tls[key] for key in
                        ("ca_pem", "certificate_fingerprint_sha256", "not_after", "agent_url") if key in tls}))
    return {"prepared": True, "tls": {key: tls[key] for key in ("ca_pem", "certificate_fingerprint_sha256", "not_after", "agent_url") if key in tls}}


def start(payload):
    _, install, spec, operation, _, _ = own_state(payload)
    if platform_agents():
        reject("existing_agent_refused")
    compose = compose_command()+["--env-file", str(install / "agent.env"), "-p", RUNTIME_PROJECTS[spec["runtime"]],
               "-f", str(install / "package/web" / ("compose."+spec["runtime"]+"-agent.yaml")),
               "-f", str(install / "package/web/rollout.override.yaml")]
    run(compose+["config", "--quiet"])
    run(compose+["up", "-d", "--no-deps", "--pull", "never", "agent"], timeout=120)
    agents = platform_agents()
    owned = [item for item in agents if item["operation_id"] == operation]
    if len(owned) != 1 or owned[0]["image_id"] != payload.get("image") or not owned[0]["running"]:
        reject("started_agent_identity_unverified")
    return {"started": True, "container_id": owned[0]["id"], "image_id": owned[0]["image_id"], "running": owned[0]["running"]}


def installation_status(payload):
    """Read-only reconciliation; never repeats prepare/up or removes resources."""
    _, install, spec, operation, digest = locations(payload)
    marker = read_json(install / ".motor-console-rollout.json")
    if marker.get("operation_id") != operation or marker.get("spec_digest") != digest:
        reject("installation_ownership_conflict")
    helpers = run(["docker", "ps", "-aq", "--filter", "label=motor-console.rollout.helper="+operation]).splitlines()
    if len(helpers) > 16:
        reject("helper_inventory_limit")
    owned = [item for item in platform_agents() if item["operation_id"] == operation]
    if len(owned) > 1:
        reject("started_agent_identity_unverified")
    tls_path = install / ".motor-console-tls.json"
    tls = read_json(tls_path, maximum=32768) if tls_path.exists() else {}
    if tls and (set(tls) - {"ca_pem", "certificate_fingerprint_sha256", "not_after", "agent_url"}
                or tls.get("agent_url") != spec["agent_url"]):
        reject("agent_tls_preparation_failed")
    return {"owned_agent": owned[0] if owned else None, "helper_containers_present": bool(helpers), "tls": tls}


def finish(payload):
    base, _, _, operation, _ = locations(payload)
    path = base / ".active-rollout.json"
    if path.exists():
        if read_json(path).get("operation_id") != operation:
            reject("host_reservation_ownership_conflict")
        path.unlink()
    return {"reservation_released": True, "installation_files_retained": True}


def main():
    try:
        line = sys.stdin.buffer.readline(256 * 1024 + 1)
        if len(line) > 256 * 1024 or not line.endswith(b"\n"):
            reject("installer_request_limit")
        payload = json.loads(line)
        action = payload.get("action") if isinstance(payload, dict) else None
        actions = {"probe": probe, "stage": stage, "upload": receive, "load": load_image, "pull": pull_image,
                   "prepare": prepare, "start": start, "finish": finish, "status": installation_status}
        if action not in actions:
            reject("installer_action_invalid")
        result = actions[action](payload)
        print(json.dumps({"ok": True, "result": result}, separators=(",", ":")), flush=True)
        return 0
    except Refused as error:
        code = error.code
    except Exception:
        code = "remote_installer_failed"
    print(json.dumps({"ok": False, "error_code": code}), flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
