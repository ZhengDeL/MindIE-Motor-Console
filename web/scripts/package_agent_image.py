"""Export a verified local MindIE-Motor Console Agent image as an offline rollout artifact.

Python 3.9+ standard library only. This host-side tool never installs an Agent,
logs in to a registry, contacts a cluster or changes existing deployment files.
Only the local Linux Docker socket is used; candidate verification has no host
mounts, network, devices or capabilities. The output is host-admin supplied
material with a SHA256 inventory, not a signed upstream release.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import threading
from typing import Optional
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.version import MIN_AGENT_VERSION


MOTOR_COMMIT = "37d615f232a36b8dba8630ffc3a01a20d289c65e"
PRODUCT_ID = "motor-console"
AGENT_PROTOCOL = 1
EXECUTION_CAPABILITIES = {"operation_receipts", "resource_ledger", "resource_admission"}
CHUNK_BYTES = 1024 * 1024
METADATA_BYTES = 64 * 1024
# The rollout artifact inventory accepts packages up to 2 GiB. Keeping the tar
# stream under the same bound is deliberately conservative for this small Agent.
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")
_IMAGE_DIGEST = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[0-9a-f]{64}\Z")
_ARTIFACT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


class PackagingError(RuntimeError):
    """Public bounded error codes only; no Docker output or secret values."""

    def __init__(self, code: str, candidate_name: Optional[str] = None):
        self.code = code
        self.candidate_name = candidate_name
        super().__init__(code)


def _fail(code: str):
    raise PackagingError(code)


def _directory(value, allow_existing: bool) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        _fail("output_directory_must_be_absolute")
    for parent in (candidate, *candidate.parents):
        if parent.is_symlink():
            _fail("output_directory_symlink_refused")
    directory = candidate.resolve(strict=True)
    if not directory.is_dir():
        _fail("output_directory_required")
    if os.name == "posix":
        for parent in (directory, *directory.parents):
            info = parent.stat()
            if info.st_uid not in {0, os.geteuid()}:
                _fail("output_directory_untrusted_owner")
            if info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
                _fail("output_directory_untrusted_parent")
        info = directory.stat()
        if info.st_mode & 0o027:
            _fail("output_directory_must_be_private")
    if not allow_existing and any(directory.iterdir()):
        _fail("output_directory_must_be_empty")
    return directory


def _environment() -> dict:
    environment = dict(os.environ)
    for name in ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION"):
        environment.pop(name, None)
    return environment


def _docker_prefix() -> list:
    if not sys.platform.startswith("linux"):
        _fail("run_on_linux_docker_host")
    binary = shutil.which("docker")
    if not binary:
        _fail("local_docker_cli_unavailable")
    return [binary, "--host", "unix:///var/run/docker.sock"]


def _command(prefix: list, args: list, *, timeout: int, maximum: int) -> bytes:
    """Bound stdout before parsing; a watchdog also bounds a stalled pipe read."""
    process = subprocess.Popen(prefix + args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               stdin=subprocess.DEVNULL, env=_environment())
    stopped, expired = threading.Event(), threading.Event()

    def watchdog():
        if not stopped.wait(timeout):
            expired.set()
            try:
                process.kill()
            except OSError:
                pass

    thread = threading.Thread(target=watchdog, name="agent-package-command", daemon=True)
    thread.start()
    chunks, size = [], 0
    try:
        while True:
            chunk = process.stdout.read(min(CHUNK_BYTES, maximum + 1))
            if not chunk:
                break
            size += len(chunk)
            if size > maximum:
                process.kill()
                _fail("docker_metadata_size_limit")
            chunks.append(chunk)
        status = process.wait(timeout=10)
        if expired.is_set():
            _fail("docker_metadata_timeout")
        if status:
            _fail("docker_metadata_command_failed")
        return b"".join(chunks)
    finally:
        stopped.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdout.close()
        thread.join(timeout=1)


def _json(raw: bytes):
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                _fail("duplicate_metadata_key")
            value[key] = child
        return value

    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: _fail("invalid_metadata_number"))
    except (ValueError, UnicodeError):
        _fail("docker_metadata_invalid_json")


def _inspect(prefix: list, image: str) -> dict:
    # The Go template selects these four fields before stdout; raw image Env is
    # never collected, printed or written to an operational receipt.
    template = '{"id":{{json .Id}},"architecture":{{json .Architecture}},"os":{{json .Os}},"size":{{json .Size}}}'
    value = _json(_command(prefix, ["image", "inspect", "--format", template, image],
                           timeout=30, maximum=METADATA_BYTES))
    if (not isinstance(value, dict) or set(value) != {"id", "architecture", "os", "size"}
            or not isinstance(value["id"], str) or not _IMAGE_ID.fullmatch(value["id"])
            or not isinstance(value["architecture"], str) or value["architecture"] not in {"arm64", "amd64"}
            or value["os"] != "linux"
            or type(value["size"]) is not int or not 1 <= value["size"] <= MAX_ARCHIVE_BYTES):
        _fail("local_image_not_supported")
    if _IMAGE_ID.fullmatch(image) and value["id"] != image:
        _fail("local_image_identity_mismatch")
    return value


def _cleanup_probe(prefix: list, nonce: str, name: str, image_id: str):
    def identifiers():
        raw = _command(prefix, ["container", "ls", "--all", "--quiet", "--no-trunc", "--filter",
                                "label=motor-console.package-probe=" + nonce], timeout=30, maximum=METADATA_BYTES)
        values = raw.decode("ascii").split()
        if len(values) > 1 or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in values):
            _fail("candidate_cleanup_identity_mismatch")
        return values

    try:
        for identifier in identifiers():
            template = '{"id":{{json .Id}},"image":{{json .Image}},"owner":{{json (index .Config.Labels "motor-console.package-probe")}},"name":{{json .Name}}}'
            try:
                value = _json(_command(prefix, ["container", "inspect", "--format", template, identifier],
                                       timeout=30, maximum=METADATA_BYTES))
            except PackagingError:
                if not identifiers():
                    continue  # --rm completed between listing and inspection.
                raise
            if value != {"id": identifier, "image": image_id, "owner": nonce, "name": "/" + name}:
                _fail("candidate_cleanup_identity_mismatch")
            _command(prefix, ["container", "rm", "--force", identifier], timeout=30, maximum=METADATA_BYTES)
        if identifiers():
            _fail("candidate_cleanup_incomplete")
    except Exception:
        raise PackagingError("candidate_cleanup_unverified", candidate_name=name) from None


def _probe(prefix: list, image_id: str) -> dict:
    code = (
        "import json;from pathlib import Path;"
        "from backend.compatibility import agent_metadata;"
        "from backend.motor_adapter import verify_upstream_source;"
        "m=agent_metadata();n=verify_upstream_source();"
        "assert Path('/app/scripts/configure_agent_tls.py').is_file();"
        "assert Path('/app/scripts/prepare_agent.py').is_file();"
        "assert m['motor_profile']['bundled'] is True;"
        "assert type(n['verified_files']) is int and n['verified_files']==465;"
        "print(json.dumps({'console_version':m['agent_version'],"
        "'agent_protocol':m['agent_protocol'],'motor_commit':n['commit'],"
        "'product_id':m['product_id'],'operation_protocol':m['operation_protocol'],"
        "'execution_capabilities':m['execution_capabilities']}))"
    )
    nonce = uuid4().hex
    name = "motor-console-package-check-" + nonce
    try:
        raw = _command(prefix, ["run", "--rm", "--name", name, "--label", "motor-console.package-probe=" + nonce,
                                    "--pull", "never", "--runtime", "runc",
                                    "--network", "none", "--read-only", "--user", "10001:10001",
                                    "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                                    "--memory", "256m", "--cpus", "0.5", "--pids-limit", "64",
                                    "--tmpfs", "/tmp:size=16m,mode=1777", "--env", "PYTHONPATH=/app",
                                    "--env", "PYTHONDONTWRITEBYTECODE=1", "--entrypoint", "python",
                                    image_id, "-c", code], timeout=60, maximum=METADATA_BYTES)
    finally:
        _cleanup_probe(prefix, nonce, name, image_id)
    value = _json(raw)
    version_text = value.get("console_version") if isinstance(value, dict) else None
    version = _VERSION.fullmatch(version_text) if isinstance(version_text, str) else None
    if (not version or tuple(map(int, version.groups())) < MIN_AGENT_VERSION
            or type(value.get("agent_protocol")) is not int or value["agent_protocol"] != AGENT_PROTOCOL
            or value.get("motor_commit") != MOTOR_COMMIT
            or value.get("product_id") != PRODUCT_ID
            or type(value.get("operation_protocol")) is not int or value["operation_protocol"] != 1
            or not isinstance(value.get("execution_capabilities"), dict)
            or not all(value["execution_capabilities"].get(name) is True for name in EXECUTION_CAPABILITIES)
            or set(value) != {"console_version", "agent_protocol", "motor_commit", "product_id", "operation_protocol", "execution_capabilities"}):
        _fail("agent_rollout_image_contract_not_supported")
    return {key: value[key] for key in ("console_version", "agent_protocol", "motor_commit", "product_id")}


def _sync(directory: Path):
    if os.name == "posix":
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _export(prefix: list, image_id: str, path: Path, *, timeout: int):
    process = subprocess.Popen(prefix + ["image", "save", image_id], stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=_environment())
    stopped, expired = threading.Event(), threading.Event()

    def watchdog():
        if not stopped.wait(timeout):
            expired.set()
            try:
                process.kill()
            except OSError:
                pass

    thread = threading.Thread(target=watchdog, name="agent-package-export", daemon=True)
    thread.start()
    count = 0
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as output:
            with gzip.GzipFile(filename="", fileobj=output, mode="wb", compresslevel=1, mtime=0) as compressed:
                while True:
                    chunk = process.stdout.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > MAX_ARCHIVE_BYTES:
                        process.kill()
                        _fail("image_archive_size_limit")
                    compressed.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        status = process.wait(timeout=10)
        if expired.is_set():
            _fail("image_export_timeout")
        if status or count == 0:
            _fail("image_export_failed")
    finally:
        stopped.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        process.stdout.close()
        thread.join(timeout=1)


def package_image(image: str, output_dir, artifact_id: str, *, allow_existing_directory: bool = False,
                  timeout_seconds: int = 900) -> dict:
    if not isinstance(image, str) or not (_IMAGE_ID.fullmatch(image) or _IMAGE_DIGEST.fullmatch(image)):
        _fail("immutable_local_image_reference_required")
    if (not isinstance(artifact_id, str) or not _ARTIFACT.fullmatch(artifact_id)
            or ".." in artifact_id or artifact_id.endswith(".")):
        _fail("artifact_identifier_invalid")
    if type(timeout_seconds) is not int or not 60 <= timeout_seconds <= 3600:
        _fail("export_timeout_policy_invalid")
    directory = _directory(output_dir, allow_existing_directory)
    archive = directory / (artifact_id + ".tar.gz")
    manifest = directory / (artifact_id + ".json")
    archive_part = directory / (artifact_id + ".tar.gz.part")
    manifest_part = directory / (artifact_id + ".json.part")
    if any(path.exists() or path.is_symlink() for path in (archive, manifest, archive_part, manifest_part)):
        _fail("artifact_paths_already_exist")
    prefix = _docker_prefix()
    inspected = _inspect(prefix, image)
    metadata = _probe(prefix, inspected["id"])
    if shutil.disk_usage(directory).free < inspected["size"] + 64 * 1024 * 1024:
        _fail("insufficient_export_disk_space")
    _export(prefix, inspected["id"], archive_part, timeout=timeout_seconds)
    # SHA refers to the actual compressed file read in bounded 1 MiB blocks.
    size, digest = archive_part.stat().st_size, _sha(archive_part)
    if not 1 <= size <= MAX_ARCHIVE_BYTES:
        _fail("compressed_archive_size_limit")
    value = {"schema": 1, "id": artifact_id, "filename": archive.name, "sha256": digest,
             "size_bytes": size, "image_id": inspected["id"], "architecture": inspected["architecture"], **metadata}
    descriptor = os.open(manifest_part, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(value, output, ensure_ascii=False, sort_keys=True, indent=2)
        output.write("\n"); output.flush(); os.fsync(output.fileno())
    # Exclusive hard-link publication refuses every existing target. A manifest
    # is discoverable only after the complete archive has been published.
    os.link(archive_part, archive)
    archive_part.unlink()
    _sync(directory)
    os.link(manifest_part, manifest)
    manifest_part.unlink()
    _sync(directory)
    return {"packaged": True, "manifest": manifest.name, **value,
            "candidate_verified": True, "agent_installed": False,
            "validation": "host_admin_supplied_image_metadata_and_source_inventory",
            "signed_release": False}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Existing local image ID or registry@sha256 digest; never pulled")
    parser.add_argument("--output-dir", required=True, help="Existing absolute private empty output directory")
    parser.add_argument("--artifact-id", required=True, help="Lowercase artifact ID, at most 64 characters")
    parser.add_argument("--allow-existing-directory", action="store_true", help="Allow unrelated existing packages; never overwrite any target")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args(argv)
    try:
        result = package_image(**vars(args))
    except PackagingError as error:
        print(json.dumps({"packaged": False, "error_code": error.code,
                          "candidate_container": error.candidate_name,
                          "partial_material_policy": "new .part or unindexed archive retained for review; no automatic deletion"}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"packaged": False, "error_code": "image_packaging_failed",
                          "partial_material_policy": "new .part or unindexed archive retained for review; no automatic deletion"}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
