"""Bounded node-local image inspection; runtime compatibility is never inferred from a tag."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import threading
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .compatibility import check_binding, source_profile
from .docker_runtime import DockerRuntime
from .observability import redact


_REFERENCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./:@-]{0,255}\Z")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,79}\Z")
_PROBE_SLOT = threading.BoundedSemaphore(1)
_PROBE_SECONDS = 30
_OUTPUT_LIMIT = 65536
_OWNER_LABEL = "motor-console.inspection"
_PACKAGE_NAMES = ("motor", "vllm", "vllm_ascend", "torch", "torch_npu")
_MODULE_NAMES = ("coordinator", "controller", "node_manager")
_CLI_NAMES = ("bash", "python3", "vllm")


def validate_image_reference(value: str) -> str:
    if not isinstance(value, str) or not _REFERENCE.fullmatch(value) or "://" in value:
        raise ValueError("Use a literal Docker image name, ID or SHA256 reference")
    if "@" in value and (value.count("@") != 1 or not _DIGEST.fullmatch(value.rsplit("@", 1)[1])):
        raise ValueError("Image digest references must end with @sha256:<64 lowercase hex characters>")
    return value


class ImageInspectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(min_length=1, max_length=256)
    probe_runtime: bool = Field(default=False, strict=True)
    motor_profile: str | None = Field(default=None, max_length=96)

    @field_validator("image")
    @classmethod
    def literal_image(cls, value: str) -> str:
        return validate_image_reference(value)


class ImageNotLocal(RuntimeError):
    pass


class ImageInspectionBusy(RuntimeError):
    pass


# No engine imports, model loading or user-supplied code. Package source files
# are read only beneath the installed Motor package; output contains no file
# contents, environment variables, command history or host information.
_PROBE_SCRIPT = r'''
import hashlib, importlib.machinery, importlib.metadata, json, pathlib, re, shutil, site, sys
# -S prevents sitecustomize/.pth imports; enumerate ordinary system/venv
# package directories without executing any installed package startup code.
sys.path.extend(path for path in site.getsitepackages() if path not in sys.path)
keys = json.loads(sys.argv[1])
if not isinstance(keys, list) or len(keys) > 1024 or any(not isinstance(k, str) or not k.startswith("motor/") or ".." in pathlib.PurePosixPath(k).parts or "\\" in k or not k.endswith(".py") for k in keys):
    raise ValueError("Invalid fixed source inventory")
packages = {}
for public, name in [("motor", "motor"), ("vllm", "vllm"), ("vllm_ascend", "vllm-ascend"), ("torch", "torch"), ("torch_npu", "torch-npu")]:
    try:
        value = importlib.metadata.version(name)
        packages[public] = value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.!+_-]{0,79}", value) else None
    except importlib.metadata.PackageNotFoundError:
        packages[public] = None
spec = importlib.machinery.PathFinder.find_spec("motor", sys.path)
root = pathlib.Path(spec.origin).resolve().parent if spec and spec.origin else None
def package_file(relative):
    if root is None:
        return None
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
        return None
    return target
modules = {key: package_file(key + "/main.py") is not None for key in ["coordinator", "controller", "node_manager"]}
hashes = {}
total_bytes = 0
for key in keys:
    target = package_file(key.removeprefix("motor/"))
    if target is not None:
        total_bytes += target.stat().st_size
        if total_bytes > 16 * 1024 * 1024:
            raise ValueError("Motor source inventory exceeds bounded inspection")
        hashes[key] = hashlib.sha256(target.read_bytes()).hexdigest()
print(json.dumps({"packages": packages, "modules": modules, "cli": {key: shutil.which(key) is not None for key in ["bash", "python3", "vllm"]}, "source_hashes": hashes}, separators=(",", ":")))
'''


def _text(value, maximum=256):
    return value if isinstance(value, str) and len(value) <= maximum and not any(ord(c) < 32 for c in value) else None


def _command(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 64 or any(_text(item, 1024) is None for item in value):
        raise ValueError("Image command metadata is invalid or exceeds inspection bounds")
    projected = redact(value)
    for index, item in enumerate(value[:-1]):
        if item.startswith("-") and "=" not in item and re.search(r"password|passwd|secret|token|api[-_]?key|credential", item, re.I):
            projected[index + 1] = "[REDACTED]"
    return projected


def sanitize_inspection_result(payload: dict, *, expected_image: str | None = None,
                               expected_profile: dict | None = None) -> dict:
    """Project an untrusted Agent report; no ENV/history/unknown fields survive."""
    def obj(value, maximum=32):
        if not isinstance(value, dict) or len(value) > maximum:
            raise ValueError("Image inspection contains an invalid or oversized object")
        return value
    def text(value, maximum=256, optional=False):
        if optional and value is None:
            return None
        result = _text(value, maximum)
        if result is None:
            raise ValueError("Image inspection contains invalid text")
        return redact(result)
    def choice(value, choices):
        if not isinstance(value, str) or value not in choices:
            raise ValueError("Image inspection contains an unsupported status")
        return value
    def count(value, maximum=1024):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError("Image inspection contains an invalid count")
        return value
    def boolean(value):
        if type(value) is not bool:
            raise ValueError("Image inspection contains an invalid boolean")
        return value
    def commit(value, optional=False):
        if optional and value is None:
            return None
        if not isinstance(value, str) or not _COMMIT.fullmatch(value):
            raise ValueError("Image inspection contains an invalid source revision")
        return value
    body = obj(payload)
    image = validate_image_reference(body.get("image"))
    if expected_image is not None and image != validate_image_reference(expected_image):
        raise ValueError("Image inspection does not match the requested image")
    image_id = body.get("image_id")
    if not isinstance(image_id, str) or not _DIGEST.fullmatch(image_id):
        raise ValueError("Image inspection does not identify an immutable image")
    digests = body.get("repo_digests")
    if not isinstance(digests, list) or len(digests) > 32:
        raise ValueError("Image inspection contains invalid repository digests")
    digests = [validate_image_reference(value) for value in digests]
    if any("@" not in value for value in digests):
        raise ValueError("Image inspection repository entries must identify digests")
    profile = obj(body.get("motor_profile"))
    native = source_profile()
    coordinates = ("profile_id", "commit", "driver", "agent_protocol", "manifest_sha256", "bundled")
    local = {key: native[key] for key in coordinates}
    def same_profile(value):
        return isinstance(value, dict) and all(type(value.get(key)) is type(local[key]) and value[key] == local[key]
                                               for key in coordinates)
    if expected_profile is not None and not same_profile(expected_profile):
        raise ValueError("Expected image inspection profile is no longer the active profile")
    if not same_profile(profile):
        raise ValueError("Image inspection belongs to a different Motor profile")
    public_profile = {key: local[key] for key in ("profile_id", "commit", "driver", "agent_protocol", "manifest_sha256", "bundled") if key in local}
    identity = obj(body.get("identity"))
    state = choice(identity.get("status"), {"unknown", "declared", "mismatch", "verified"})
    evidence = choice(identity.get("evidence"), {"none", "image_label", "static_runtime_sources"})
    declared = commit(identity.get("declared_commit"), optional=True)
    top_declared = commit(body.get("declared_revision"), optional=True)
    if top_declared != declared:
        raise ValueError("Image revision declaration differs from its identity evidence")
    if commit(identity.get("expected_commit")) != local["commit"]:
        raise ValueError("Image identity refers to a different native revision")
    matched, expected = count(identity.get("matched_files")), count(identity.get("expected_files"))
    if matched > expected or (state == "declared" and (declared != local["commit"] or evidence != "image_label")):
        raise ValueError("Image identity evidence is inconsistent")
    raw_probe = obj(body.get("probe"))
    requested = boolean(raw_probe.get("requested"))
    status = choice(raw_probe.get("status"), {"not_requested", "passed", "failed", "timeout", "unavailable"})
    cleanup = choice(raw_probe.get("cleanup"), {"not_created", "removed", "failed"})
    if requested == (status == "not_requested"):
        raise ValueError("Image probe request and status are inconsistent")
    packages = obj(raw_probe.get("packages"), 8)
    modules, cli = obj(raw_probe.get("modules"), 8), obj(raw_probe.get("cli"), 8)
    if set(packages) - set(_PACKAGE_NAMES) or set(modules) - set(_MODULE_NAMES) or set(cli) - set(_CLI_NAMES):
        raise ValueError("Image probe contains unsupported package/module/CLI fields")
    if any(value is not None and (not isinstance(value, str) or not _VERSION.fullmatch(value)) for value in packages.values()):
        raise ValueError("Image probe contains an invalid package version")
    modules = {key: boolean(value) for key, value in modules.items()}
    cli = {key: boolean(value) for key, value in cli.items()}
    errors = raw_probe.get("errors")
    if (not isinstance(errors, list) or len(errors) > 32
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-z0-9_:]{1,96}", value) for value in errors)):
        raise ValueError("Image probe contains invalid bounded error codes")
    if status == "passed" and (cleanup != "removed" or errors or set(packages) != set(_PACKAGE_NAMES)
                               or any(value is None for value in packages.values()) or set(modules) != set(_MODULE_NAMES)
                               or not all(modules.values()) or set(cli) != set(_CLI_NAMES) or not all(cli.values())):
        raise ValueError("A successful image probe lacks required evidence")
    runtime_files = sum(name.startswith("motor/") and name.endswith(".py") for name in native["files"])
    if state == "verified" and (evidence != "static_runtime_sources" or expected == 0 or expected != runtime_files
                                or matched != expected or not requested or status != "passed"
                                or declared not in {None, local["commit"]}):
        raise ValueError("Verified image source identity lacks a complete static source comparison")
    if body.get("compatibility") != "unverified":
        raise ValueError("An image inspection cannot assert inference compatibility")
    checks = body.get("checks")
    if not isinstance(checks, list) or len(checks) > 64:
        raise ValueError("Image inspection checks exceed their bounds")
    checks = [{"name": text(obj(item, 8).get("name"), 160),
               "status": choice(item.get("status"), {"pass", "fail", "warn"}),
               "detail": text(item.get("detail"), 1024)} for item in checks]
    collected_at = text(body.get("collected_at"), 80)
    if datetime.fromisoformat(collected_at.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("Image inspection collection time requires a timezone")
    result = {"image": image, "image_id": image_id, "repo_digests": digests,
              "architecture": text(body.get("architecture"), 32, optional=True), "os": text(body.get("os"), 32, optional=True),
              "created": text(body.get("created"), 80, optional=True), "entrypoint": _command(body.get("entrypoint")),
              "command": _command(body.get("command")), "declared_revision": top_declared,
              "motor_profile": public_profile,
              "identity": {"status": state, "evidence": evidence, "expected_commit": local["commit"],
                           "declared_commit": declared, "matched_files": matched, "expected_files": expected},
              "probe": {"requested": requested, "status": status, "packages": packages, "modules": modules,
                        "cli": cli, "errors": list(errors), "cleanup": cleanup},
              "compatibility": "unverified", "checks": checks, "collected_at": collected_at}
    if requested:
        result["probe"]["scope"] = "CPU-only static runtime inspection; no engine imports, model loading or NPU access"
    return result


class ImageContractInspector:
    def __init__(self, runtime: DockerRuntime | None = None):
        self.runtime = runtime or DockerRuntime()

    def inspect(self, payload: ImageInspectionInput | dict) -> dict:
        request = payload if isinstance(payload, ImageInspectionInput) else ImageInspectionInput.model_validate(payload)
        profile = check_binding(request.motor_profile)
        client = self.runtime._docker()
        from docker.errors import ImageNotFound
        try:
            image = client.images.get(request.image)  # Deliberately never pull.
        except ImageNotFound as exc:
            raise ImageNotLocal("The image is not present in this node's Docker daemon; prepare it on the node first") from exc
        attrs = image.attrs
        image_id = attrs.get("Id") or image.id
        if not isinstance(image_id, str) or not _DIGEST.fullmatch(image_id):
            raise ValueError("Docker did not return a valid immutable image ID")
        config = attrs.get("Config") or {}
        if not isinstance(config, dict):
            raise ValueError("Image configuration metadata is invalid")
        digests = attrs.get("RepoDigests") or []
        if not isinstance(digests, list) or len(digests) > 32:
            raise ValueError("Image repository digest metadata exceeds inspection bounds")
        digests = [validate_image_reference(value) for value in digests]
        labels = config.get("Labels") or {}
        if not isinstance(labels, dict):
            raise ValueError("Image revision labels are invalid")
        revisions = [labels[key] for key in ("motor-console.commit", "motor.commit") if key in labels]
        declared = revisions[0] if revisions and isinstance(revisions[0], str) and _COMMIT.fullmatch(revisions[0]) else None
        invalid_revision = any(not isinstance(value, str) or not _COMMIT.fullmatch(value) for value in revisions)
        revision_mismatch = invalid_revision or any(value != profile["commit"] for value in revisions)
        identity = {"status": "mismatch" if revision_mismatch else "declared" if declared else "unknown",
                    "evidence": "image_label" if revisions else "none", "expected_commit": profile["commit"],
                    "declared_commit": declared, "matched_files": 0, "expected_files": 0}
        checks = []
        def check(name, status, detail):
            checks.append({"name": name, "status": status, "detail": detail})
        check("Local image", "pass", "Inspected an existing immutable image ID; no image was pulled")
        architecture, operating_system = _text(attrs.get("Architecture"), 32), _text(attrs.get("Os"), 32)
        host = client.info()
        normal = {"aarch64": "arm64", "x86_64": "amd64"}
        host_arch = str(host.get("Architecture") or "").lower()
        image_arch = str(architecture or "").lower()
        if not image_arch or not host_arch:
            check("Architecture", "warn", "Image or host architecture is unavailable")
        elif normal.get(image_arch, image_arch) != normal.get(host_arch, host_arch):
            check("Architecture", "fail", "Image architecture differs from this Docker host")
        else:
            check("Architecture", "pass", "Image architecture matches this Docker host")
        check("Operating system", "pass" if operating_system == "linux" else "fail", "Motor runtime requires a Linux image")
        check("Motor revision", "fail" if revision_mismatch else "warn",
              "Image revision declaration differs from the selected Motor source profile" if revision_mismatch
              else "Image revision matches its declaration; runtime source identity is not established" if declared
              else "The image has no Motor source revision label; source identity remains unknown")
        result = {"image": request.image, "image_id": image_id, "repo_digests": digests,
                  "architecture": architecture, "os": operating_system, "created": _text(attrs.get("Created"), 80),
                  "entrypoint": _command(config.get("Entrypoint")), "command": _command(config.get("Cmd")),
                  "declared_revision": declared, "motor_profile": profile, "identity": identity,
                  "probe": {"requested": request.probe_runtime, "status": "not_requested", "packages": {},
                            "modules": {}, "cli": {}, "errors": [], "cleanup": "not_created"},
                  "compatibility": "unverified", "checks": checks,
                  "collected_at": datetime.now(timezone.utc).isoformat()}
        if request.probe_runtime:
            if any(item["name"] in {"Architecture", "Operating system"} and item["status"] == "fail" for item in checks):
                result["probe"].update(status="unavailable", errors=["image_platform_mismatch"])
                check("CPU runtime probe", "fail", "The image platform cannot be probed on this Docker host")
            elif config.get("Volumes"):
                result["probe"].update(status="unavailable", errors=["image_declares_volumes"])
                check("CPU runtime probe", "fail", "This image declares implicit volumes; the inspection refuses all image-defined mounts")
            else:
                expected = {name: digest for name, digest in source_profile()["files"].items()
                            if name.startswith("motor/") and name.endswith(".py")}
                probe = self._probe(client, image_id, sorted(expected))
                result["probe"] = probe
                hashes = probe.pop("source_hashes", {})
                check("CPU runtime probe", "pass" if probe["status"] == "passed" else "fail",
                      "Read package metadata, native module files and CLI presence without importing engines or loading a model"
                      if probe["status"] == "passed" else "The bounded CPU-only inspection did not pass; see probe errors")
                if probe["status"] == "passed":
                    matched = sum(hashes.get(name) == digest for name, digest in expected.items())
                    identity.update(expected_files=len(expected), matched_files=matched)
                    if expected and matched == len(expected) and not revision_mismatch:
                        identity.update(status="verified", evidence="static_runtime_sources")
                        check("Motor source identity", "pass", "All pinned Python runtime source files match; this is static identity evidence only")
                    else:
                        check("Motor source identity", "warn", "Runtime Python sources do not fully match the selected profile; review is required")
        return result

    @staticmethod
    def _probe(client, image_id: str, expected_paths: list[str]) -> dict:
        if not _PROBE_SLOT.acquire(blocking=False):
            raise ImageInspectionBusy("Another runtime image inspection is running on this Agent; retry after it completes")
        marker = uuid.uuid4().hex
        name = "motor-console-image-inspect-" + marker
        probe = {"requested": True, "status": "unavailable", "packages": {}, "modules": {}, "cli": {},
                 "errors": [], "cleanup": "not_created", "scope": "CPU-only static runtime inspection; no engine imports, model loading or NPU access"}
        container = None
        try:
            from docker.types import LogConfig
            container = client.containers.create(
                image=image_id, name=name, command=["-I", "-S", "-c", _PROBE_SCRIPT, json.dumps(expected_paths)],
                entrypoint=["python3"], runtime="runc", network_mode="none", network_disabled=True,
                read_only=True, cap_drop=["ALL"], security_opt=["no-new-privileges:true"], user="65534:65534",
                mem_limit="256m", nano_cpus=500_000_000, pids_limit=32,
                volumes={}, devices=[], privileged=False, ipc_mode="private", working_dir="/tmp",
                tmpfs={"/tmp": "rw,noexec,nosuid,size=16m,mode=1777"},
                environment={"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"},
                labels={_OWNER_LABEL: marker, "motor-console.purpose": "image-contract"},
                restart_policy={"Name": "no"}, stdin_open=False, tty=False,
                healthcheck={"test": ["NONE"]},
                log_config=LogConfig(type="local", config={"max-size": "64k", "max-file": "1", "compress": "false"}))
            probe["cleanup"] = "pending"
            container.start()
            state = container.wait(timeout=_PROBE_SECONDS)
            if not isinstance(state, dict) or type(state.get("StatusCode")) is not int or state["StatusCode"] != 0:
                probe.update(status="failed", errors=["probe_process_failed"])
                return probe
            chunks = container.logs(stdout=True, stderr=False, stream=True, follow=False)
            data = bytearray()
            try:
                for chunk in chunks:
                    if not isinstance(chunk, bytes) or len(data) + len(chunk) > _OUTPUT_LIMIT:
                        raise ValueError("Probe output exceeds its bound")
                    data.extend(chunk)
            finally:
                close = getattr(chunks, "close", None)
                if close:
                    close()
            body = json.loads(data)
            if not isinstance(body, dict) or set(body) != {"packages", "modules", "cli", "source_hashes"}:
                raise ValueError("Unexpected probe structure")
            packages, modules, cli, hashes = (body[key] for key in ("packages", "modules", "cli", "source_hashes"))
            if (not isinstance(packages, dict) or set(packages) != set(_PACKAGE_NAMES)
                    or any(value is not None and (not isinstance(value, str) or not _VERSION.fullmatch(value)) for value in packages.values())
                    or not isinstance(modules, dict) or set(modules) != set(_MODULE_NAMES) or any(type(v) is not bool for v in modules.values())
                    or not isinstance(cli, dict) or set(cli) != set(_CLI_NAMES) or any(type(v) is not bool for v in cli.values())
                    or not isinstance(hashes, dict) or set(hashes) - set(expected_paths)
                    or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values())):
                raise ValueError("Invalid probe payload")
            errors = ["missing_package:" + key for key, value in packages.items() if value is None]
            errors += ["missing_module:" + key for key, value in modules.items() if not value]
            errors += ["missing_cli:" + key for key, value in cli.items() if not value]
            probe.update(status="failed" if errors else "passed", packages=packages, modules=modules, cli=cli,
                         source_hashes=hashes, errors=errors)
        except Exception as exc:
            timeout = isinstance(exc, TimeoutError) or type(exc).__name__ in {"ReadTimeout", "ReadTimeoutError", "Timeout"}
            probe.update(status="timeout" if timeout else "failed", errors=["probe_timeout" if timeout else "probe_execution_or_output_invalid"])
        finally:
            try:
                if container is None:
                    # A create request may have succeeded before its response timed out.
                    from docker.errors import NotFound
                    try:
                        container = client.containers.get(name)
                    except NotFound:
                        container = None
                if container is not None:
                    container.reload()
                    attrs = container.attrs
                    labels = (attrs.get("Config") or {}).get("Labels") or {}
                    if labels.get(_OWNER_LABEL) != marker or attrs.get("Image") != image_id:
                        raise ValueError("Probe container ownership changed")
                    container.remove(force=True, v=True)
                    probe["cleanup"] = "removed"
            except Exception:
                probe.update(status="failed", cleanup="failed")
                probe["errors"].append("probe_cleanup_failed")
            _PROBE_SLOT.release()
        return probe
