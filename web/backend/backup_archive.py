"""Encrypted, bounded application recovery bundles; no live database replacement.

Only explicitly allowed Console materials are read. Model weights, logs, runtime
workspaces and older backups are not included. A bundle is not an Agent backup
or proof of application/network/model recovery. Encryption keys stay external.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import sqlite3
import stat
import struct
import tarfile
import tempfile
import time
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .fleet import Secrets
from .maintenance import ProcessLease, backup_database, verify_backup
from .settings import sqlite_wal_fix_available
from .store import APPLICATION_ID, SCHEMA_VERSION
from .version import PRODUCT_ID


MAGIC = b"MCBA\x00\x01\r\n"
STATUS_FILE = ".backup-archive-status.json"
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
MAX_FILES = 10000
MAX_MANIFEST = 4 * 1024 * 1024
CHUNK = 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MATERIAL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
_CONFIG_EXTENSIONS = {".key", ".pem", ".crt", ".cer", ".token", ".pub", ".json", ".conf", ".cfg", ".yaml", ".yml", ".txt"}
_MODEL_EXTENSIONS = {".safetensors", ".bin", ".pt", ".pth", ".ckpt", ".onnx", ".gguf", ".npy", ".npz"}
_PROJECT_FILES = {"project/web/.env.production", "project/web/compose.production.yaml", "project/web/compose.production.host-network.yaml",
                  "project/web/compose.modular.yaml"}


class BackupArchiveError(RuntimeError):
    """Bounded public code only; never include paths, contents or exceptions."""
    def __init__(self, code="backup_archive_failed"):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise BackupArchiveError(code)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _json(raw):
    def unique(items):
        value = {}
        for key, child in items:
            if key in value:
                _fail("duplicate_json_key")
            value[key] = child
        return value
    return json.loads(raw, object_pairs_hook=unique, parse_constant=lambda _: _fail("invalid_json_number"))


def _raw_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _path(value, *, directory=False, exists=True):
    path = Path(value).absolute()
    for parent in (path, *path.parents):
        if parent.is_symlink():
            _fail("symlink_path_refused")
    if exists:
        info = path.stat()
        if directory and not stat.S_ISDIR(info.st_mode) or not directory and not stat.S_ISREG(info.st_mode):
            _fail("non_regular_material")
    return path.resolve(strict=exists)


def _identity(info):
    # Windows Python can report different legacy ctime values for stat/fstat.
    # POSIX change-time remains part of production rotation detection; both
    # platforms also compare immutable identity, size, mtime and SHA256.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns if os.name == "posix" else 0


@contextmanager
def _open_regular(path):
    path = Path(path).absolute()
    if os.name == "posix":
        directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                if part in {".", ".."}:
                    _fail("unsafe_material_path")
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                os.close(directory); directory = child
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)
    else:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail("linked_or_special_material_refused")
        yield handle, info


def _file_record(path, name):
    path = _path(path)
    with _open_regular(path) as (handle, before):
        if before.st_size > MAX_FILE:
            _fail("material_size_limit")
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
        if _identity(before) != _identity(os.fstat(handle.fileno())) or _identity(before) != _identity(path.stat()):
            _fail("material_changed_during_backup")
    return {"path": path, "name": name, "size": before.st_size, "sha256": digest,
            "identity": _identity(before), "mode": 0o500 if before.st_mode & 0o111 and "/upstream/" in name else 0o600}


def _bounded_bytes(path, maximum):
    path = _path(path)
    with _open_regular(path) as (handle, before):
        if before.st_size > maximum:
            _fail("material_size_limit")
        value = handle.read(maximum + 1)
        if len(value) > maximum or _identity(before) != _identity(os.fstat(handle.fileno())):
            _fail("material_changed_during_backup")
        return value


def _safe_name(name):
    if (not isinstance(name, str) or not name or len(name) > 512 or "\\" in name or ":" in name
            or any(ord(char) < 32 for char in name)):
        _fail("unsafe_archive_path")
    parts = PurePosixPath(name).parts
    if PurePosixPath(name).is_absolute() or any(part in {"", ".", ".."} for part in parts) or "/".join(parts) != name:
        _fail("unsafe_archive_path")
    if name in _PROJECT_FILES or name in {"data/console.sqlite3", "data/console.snapshot.json", "data/.secret-key"}:
        return name
    if len(parts) >= 3 and parts[:2] in {("data", "secrets"), ("data", "client-secrets"), ("data", "config")}:
        if all(_MATERIAL.fullmatch(part) for part in parts[2:]) and PurePosixPath(name).suffix in _CONFIG_EXTENSIONS:
            return name
    if len(parts) >= 4 and parts[:2] == ("data", "profiles") and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", parts[2]):
        if (len(parts) == 4 and parts[3] == "manifest.json" or len(parts) > 4 and parts[3] == "upstream"):
            if PurePosixPath(name).suffix.lower() not in _MODEL_EXTENSIONS and ".git" not in parts:
                return name
    _fail("archive_material_not_allowlisted")


def _collect(data, project):
    records = {}
    def add(path, name):
        _safe_name(name)
        if name in records or len(records) >= MAX_FILES:
            _fail("archive_file_count_limit")
        records[name] = _file_record(path, name)
    for folder in ("secrets", "client-secrets", "config"):
        source = data / folder
        if not source.exists():
            continue
        _path(source, directory=True)
        for current, directories, filenames in os.walk(source, followlinks=False):
            for child in directories:
                _path(Path(current) / child, directory=True)
            for filename in filenames:
                path = Path(current) / filename
                add(path, "data/" + path.relative_to(data).as_posix())
    if (data / ".secret-key").exists():
        add(data / ".secret-key", "data/.secret-key")
    for name in sorted(_PROJECT_FILES):
        path = project / name.removeprefix("project/")
        if path.exists():
            add(path, name)
    from .storage.configuration import read_environment
    environment = read_environment(project / 'web/.env.production')
    selected = environment.get('MOTOR_COMPOSE_FILE','compose.production.yaml')
    if selected not in {'compose.production.yaml','compose.modular.yaml'}:
        _fail('unsupported_compose_selection')
    if "project/web/.env.production" not in records or 'project/web/'+selected not in records:
        _fail("deployment_configuration_missing")
    profiles = data / "profiles"
    if profiles.exists():
        _path(profiles, directory=True)
        for folder in sorted(profiles.iterdir()):
            _path(folder, directory=True)
            manifest_path = _path(folder / "manifest.json")
            if manifest_path.stat().st_size > MAX_MANIFEST:
                _fail("profile_manifest_size_limit")
            manifest = _json(_bounded_bytes(manifest_path, MAX_MANIFEST))
            files = manifest.get("files") if isinstance(manifest, dict) else None
            if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
                _fail("profile_inventory_invalid")
            add(manifest_path, "data/profiles/" + folder.name + "/manifest.json")
            for relative, digest in files.items():
                if not isinstance(relative, str) or not isinstance(digest, str) or not _HEX.fullmatch(digest):
                    _fail("profile_inventory_invalid")
                name = "data/profiles/" + folder.name + "/upstream/" + relative
                _safe_name(name)
                path = folder / "upstream" / relative
                add(path, name)
                if records[name]["sha256"] != digest:
                    _fail("profile_integrity_mismatch")
    if sum(record["size"] for record in records.values()) > MAX_TOTAL:
        _fail("archive_total_size_limit")
    return records


def _seed(root):
    env = root / "project/web/.env.production"
    inline = None
    configured = None
    for line in _bounded_bytes(env, CHUNK).decode("utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() in {"MOTOR_SECRET_KEY", "MOTOR_SECRET_KEY_FILE"}:
            values = shlex.split(value, comments=True, posix=True)
            if len(values) != 1 or "$" in values[0]:
                _fail("encryption_key_configuration_unsupported")
            if key.strip() == "MOTOR_SECRET_KEY":
                inline = values[0]
            else:
                configured = values[0]
    if configured:
        prefix = "/run/motor-secrets/"
        if not configured.startswith(prefix) or "/" in configured[len(prefix):]:
            _fail("external_encryption_key_not_bundled")
        path = root / "data/secrets" / configured[len(prefix):]
    else:
        path = root / "data/secrets/encryption.key"
        if not path.exists():
            path = root / "data/.secret-key"
    file_value = _bounded_bytes(path, 16384).decode("utf-8").strip() if path.is_file() else None
    if inline and file_value and inline != file_value:
        _fail("conflicting_application_encryption_keys")
    value = inline or file_value
    if not value or len(value) < 32:
        _fail("application_encryption_key_missing")
    return value


def _validate_dependencies(root, source_data):
    """Reject unbundled custom file references rather than claim completeness."""
    values = {}
    names = {"MOTOR_CONTROL_DATA_DIR", "MOTOR_RELEASE_PUBLIC_KEY_HOST_FILE", "MOTOR_RELEASE_PUBLIC_KEY_FILE",
             "MOTOR_CONTROL_UPSTREAM_DIR", "MOTOR_CONTROL_SOURCE_MANIFEST", "MOTOR_CONTROL_SOURCE_MANIFEST_SHA256",
             "MOTOR_SECRET_KEY_FILE", "MOTOR_CONSOLE_TOKEN_FILE", "MOTOR_BOOTSTRAP_TOKEN_FILE", "MOTOR_DATABASE_URL_FILE",
             "MOTOR_DATABASE_URL_HOST_FILE", "MOTOR_WEB_CONFIG_FILE", "MOTOR_WEB_TLS_DIR",
             "MOTOR_AGENT_CA_BUNDLE", "MOTOR_GATEWAY_CA_BUNDLE", "MOTOR_CA_BUNDLE", "MOTOR_OIDC_CA_BUNDLE",
             "MOTOR_OIDC_CLIENT_SECRET_FILE", "MOTOR_MANAGEMENT_KEY_FILE", "MOTOR_CLIENT_CERT_FILE",
             "MOTOR_CLIENT_KEY_FILE", "MOTOR_CLIENT_KEY_PASSWORD_FILE", "MOTOR_CLIENT_CRL_FILE"}
    names.update({"MOTOR_AGENT_CLIENT_CERT_FILE", "MOTOR_AGENT_CLIENT_KEY_FILE",
                  "MOTOR_AGENT_CLIENT_KEY_PASSWORD_FILE", "MOTOR_AGENT_CLIENT_CRL_FILE"})
    for line in _bounded_bytes(root / "project/web/.env.production", CHUNK).decode("utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        if key.strip() not in names or not value.strip():
            continue
        parsed = shlex.split(value, comments=True, posix=True)
        if len(parsed) != 1 or "$" in parsed[0]:
            _fail("external_configuration_dependency_unsupported")
        values[key.strip()] = parsed[0]
    for key, value in values.items():
        if not value:
            continue
        if key == "MOTOR_CONTROL_DATA_DIR":
            if Path(value).resolve() != source_data:
                _fail("configured_data_directory_mismatch")
        elif key == "MOTOR_RELEASE_PUBLIC_KEY_HOST_FILE":
            path = _path(value)
            if not path.is_relative_to(source_data / "config") or not (root / "data" / path.relative_to(source_data)).is_file():
                _fail("external_configuration_dependency_not_bundled")
        elif key in {'MOTOR_DATABASE_URL_HOST_FILE','MOTOR_WEB_CONFIG_FILE','MOTOR_WEB_TLS_DIR'}:
            path = _path(value,directory=key=='MOTOR_WEB_TLS_DIR')
            if not any(path.is_relative_to(source_data/folder) for folder in ('config','secrets')):
                _fail('external_configuration_dependency_not_bundled')
            material = root/'data'/path.relative_to(source_data)
            if not (material.is_dir() if key=='MOTOR_WEB_TLS_DIR' else material.is_file()):
                _fail('external_configuration_dependency_not_bundled')
        elif key == "MOTOR_CONTROL_SOURCE_MANIFEST_SHA256":
            if not _HEX.fullmatch(value):
                _fail("profile_inventory_invalid")
        elif key in {"MOTOR_CONTROL_UPSTREAM_DIR", "MOTOR_CONTROL_SOURCE_MANIFEST"}:
            if key == "MOTOR_CONTROL_UPSTREAM_DIR" and value == "/opt/motor/upstream":
                continue
            if not value.startswith("/opt/motor-profiles/"):
                _fail("external_configuration_dependency_not_bundled")
            name = "data/profiles/" + value.removeprefix("/opt/motor-profiles/")
            relative = PurePosixPath(name)
            if ".." in relative.parts or not (root / name).exists():
                _fail("external_configuration_dependency_not_bundled")
            if key == "MOTOR_CONTROL_SOURCE_MANIFEST" and _sha(root / name) != values.get("MOTOR_CONTROL_SOURCE_MANIFEST_SHA256"):
                _fail("profile_integrity_mismatch")
        elif key == "MOTOR_RELEASE_PUBLIC_KEY_FILE":
            if value != "/run/motor-config/release.pub" or not (root / "data/config/release.pub").is_file():
                _fail("external_configuration_dependency_not_bundled")
        else:
            mappings = {"/run/motor-secrets/": "data/secrets/", "/run/motor-client/": "data/client-secrets/",
                        "/run/motor-config/": "data/config/"}
            prefix = next((prefix for prefix in mappings if value.startswith(prefix)), None)
            if prefix is None:
                _fail("external_configuration_dependency_not_bundled")
            name = mappings[prefix] + value.removeprefix(prefix)
            _safe_name(name)
            if not (root / name).is_file():
                _fail("external_configuration_dependency_not_bundled")


def _logical_evidence(root):
    database = root / "data/console.sqlite3"
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=20)) as db:
        if (db.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
                or db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION):
            _fail("application_database_identity_mismatch")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"resources", "auth_users", "auth_sessions", "auth_identities", "auth_throttle"}.issubset(tables):
            _fail("account_tables_missing")
        accounts = 0
        for identifier, password_hash, role, auth_source in db.execute("SELECT id,password_hash,role,auth_source FROM auth_users"):
            if not isinstance(identifier, str) or role not in {"admin", "operator", "viewer"}:
                _fail("account_rows_invalid")
            if auth_source == "local":
                try:
                    algorithm, n, r, p, salt, digest = password_hash.split("$")
                    valid = ((algorithm, n, r, p) == ("scrypt", "131072", "8", "1")
                             and len(base64.b64decode(salt, validate=True)) == 16
                             and len(base64.b64decode(digest, validate=True)) == 64)
                except (ValueError, TypeError, AttributeError):
                    valid = False
                if not valid:
                    _fail("account_rows_invalid")
            accounts += 1
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            _fail("database_foreign_keys_invalid")
        if db.execute("SELECT 1 FROM resources WHERE length(body)>? LIMIT 1", (16 * 1024 * 1024,)).fetchone():
            _fail("resource_json_size_limit")
        secrets = Secrets(_seed(root))
        credentials, configurations = 0, 0
        configuration_digest = hashlib.sha256(b"[")
        for kind, identifier, raw in db.execute("SELECT kind,id,body FROM resources ORDER BY kind,id"):
            body = _json(raw)
            if not isinstance(body, dict):
                _fail("resource_json_invalid")
            encrypted = body.get("_token") if kind == "nodes" else body.get("_api_key") if kind == "gateways" else None
            if encrypted:
                if not isinstance(encrypted, str):
                    _fail("encrypted_credential_invalid")
                secrets.decrypt(encrypted)  # Deliberately discard plaintext immediately.
                credentials += 1
            if "user_config" in body:
                if configurations:
                    configuration_digest.update(b",")
                configuration_digest.update(_raw_json([kind, identifier, hashlib.sha256(_raw_json(body["user_config"])).hexdigest()]))
                configurations += 1
        configuration_digest.update(b"]")
    return {"accounts_checked": accounts, "encrypted_credentials_checked": credentials,
            "native_json_count": configurations, "native_json_sha256": configuration_digest.hexdigest()}


def _key(key_file, *, excluded=()):
    path = _path(key_file)
    if any(path.is_relative_to(root) for root in excluded):
        _fail("backup_key_must_be_external")
    if os.name == "posix":
        _trusted_restore_parent(path.parent, error_code="backup_key_permissions_invalid")
    with _open_regular(path) as (handle, info):
        if info.st_size > 128 or os.name == "posix" and (info.st_mode & 0o077 or info.st_uid not in {0, os.geteuid()}):
            _fail("backup_key_permissions_invalid")
        raw = handle.read(129).strip()
        if _identity(info) != _identity(os.fstat(handle.fileno())):
            _fail("backup_key_changed_during_read")
    try:
        decoded = base64.b64decode(raw, altchars=b"-_", validate=True)
    except ValueError:
        _fail("backup_key_invalid")
    if len(decoded) != 32:
        _fail("backup_key_invalid")
    return decoded


def generate_key(key_file):
    try:
        path = _path(key_file, exists=False)
        with path.open("xb") as handle:
            path.chmod(0o600)
            handle.write(base64.urlsafe_b64encode(os.urandom(32)) + b"\n")
            handle.flush(); os.fsync(handle.fileno())
        return {"created": True, "key_disclosed": False}
    except Exception:
        raise BackupArchiveError("backup_key_creation_failed") from None


def _remote_destination(destination, mount):
    if not mount:
        return False
    mount = _path(mount, directory=True)
    if not destination.is_relative_to(mount) or not os.path.ismount(mount) or os.name != "posix":
        return False
    effective = _effective_mount(destination, Path("/proc/self/mountinfo").read_text().splitlines())
    return bool(effective and effective[0].is_relative_to(mount)
                and effective[1] in {"nfs", "nfs4", "cifs", "smb3", "ceph", "fuse.sshfs"})


def _effective_mount(destination, lines):
    matches = []
    for line in lines:
        before, separator, after = line.partition(" - ")
        fields = before.split()
        if separator and len(fields) > 4:
            decoded = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4])
            point = Path(decoded)
            if destination.is_relative_to(point):
                matches.append((len(point.parts), point, after.split()[0]))
    if not matches:
        return None
    _, effective, filesystem = max(matches, key=lambda item: item[0])
    return effective, filesystem


def _sync_directory(path):
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _atomic_json(path, value, *, owner=None):
    _path(path.parent, directory=True)
    if path.is_symlink():
        _fail("symlink_path_refused")
    temporary = path.with_name("." + uuid4().hex + ".part")
    try:
        with temporary.open("xb") as handle:
            temporary.chmod(0o600)
            if owner is not None and os.name == "posix" and os.geteuid() == 0:
                os.fchown(handle.fileno(), owner.st_uid, owner.st_gid)
            handle.write(_raw_json(value)); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class _EncryptedWriter:
    def __init__(self, handle, encryptor):
        self.handle, self.encryptor, self.size = handle, encryptor, 0

    def write(self, raw):
        self.size += len(raw)
        if self.size > MAX_TOTAL + MAX_FILES * 1024 + MAX_MANIFEST + 10240:
            _fail("archive_total_size_limit")
        self.handle.write(self.encryptor.update(raw))
        return len(raw)


def _add_bytes(archive, name, raw, mode=0o600):
    member = tarfile.TarInfo(name)
    member.size, member.mode, member.mtime = len(raw), mode, 0
    archive.addfile(member, io.BytesIO(raw))


def _encrypt(path, key, manifest, records):
    nonce = os.urandom(12)
    header = _raw_json({"format": 1, "product_id": PRODUCT_ID, "algorithm": "AES-256-GCM", "nonce": nonce.hex()})
    prefix = MAGIC + struct.pack(">I", len(header)) + header
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(prefix)
    with path.open("xb") as output:
        path.chmod(0o600); output.write(prefix)
        writer = _EncryptedWriter(output, encryptor)
        with tarfile.open(mode="w|", fileobj=writer, format=tarfile.PAX_FORMAT) as archive:
            _add_bytes(archive, "manifest.json", _raw_json(manifest))
            for name, record in sorted(records.items()):
                with _open_regular(record["path"]) as (handle, before):
                    if _identity(before) != record["identity"]:
                        _fail("material_changed_during_backup")
                    member = tarfile.TarInfo(name)
                    member.size, member.mode, member.mtime = record["size"], record["mode"], 0
                    archive.addfile(member, handle)
                    if _identity(before) != _identity(os.fstat(handle.fileno())):
                        _fail("material_changed_during_backup")
        output.write(encryptor.finalize()); output.write(encryptor.tag)
        output.flush(); os.fsync(output.fileno())


@contextmanager
def _decrypted(archive, key, directory):
    archive = _path(archive)
    size = archive.stat().st_size
    if size > MAX_TOTAL + MAX_FILES * 1024 + MAX_MANIFEST + 65536 or size < len(MAGIC) + 4 + 16:
        _fail("archive_size_invalid")
    with _open_regular(archive) as (source, before), tempfile.TemporaryFile(dir=directory) as plain:
        if source.read(len(MAGIC)) != MAGIC:
            _fail("archive_format_invalid")
        length = struct.unpack(">I", source.read(4))[0]
        if not 1 <= length <= 1024:
            _fail("archive_header_invalid")
        raw = source.read(length)
        header = _json(raw)
        if (set(header) != {"format", "product_id", "algorithm", "nonce"} or header["product_id"] != PRODUCT_ID
                or type(header["format"]) is not int or header["format"] != 1 or header["algorithm"] != "AES-256-GCM" or not re.fullmatch(r"[0-9a-f]{24}", header["nonce"])):
            _fail("archive_header_invalid")
        start = source.tell(); source.seek(-16, os.SEEK_END); tag = source.read(16); source.seek(start)
        decryptor = Cipher(algorithms.AES(key), modes.GCM(bytes.fromhex(header["nonce"]), tag)).decryptor()
        decryptor.authenticate_additional_data(MAGIC + struct.pack(">I", length) + raw)
        remaining = size - start - 16
        while remaining:
            chunk = source.read(min(CHUNK, remaining))
            if not chunk:
                _fail("archive_truncated")
            plain.write(decryptor.update(chunk)); remaining -= len(chunk)
        plain.write(decryptor.finalize())  # Authentication precedes parsing or extraction.
        if _identity(before) != _identity(os.fstat(source.fileno())) or _identity(before) != _identity(archive.stat()):
            _fail("archive_changed_during_read")
        plain.seek(0)
        yield plain


def _extract_verified(plain, destination):
    _preflight_tar(plain)
    with tarfile.open(fileobj=plain, mode="r:") as archive:
        members, total = {}, 0
        for member in archive:
            if (not member.isreg() or member.sparse is not None or set(member.pax_headers) - {"path"}
                    or member.name in members or len(members) >= MAX_FILES + 1 or member.size < 0 or member.size > MAX_FILE):
                _fail("archive_member_invalid")
            if member.name != "manifest.json":
                _safe_name(member.name)
            elif member.size > MAX_MANIFEST:
                _fail("archive_manifest_size_limit")
            total += member.size
            if total > MAX_TOTAL + MAX_MANIFEST:
                _fail("archive_total_size_limit")
            members[member.name] = member
        if "manifest.json" not in members:
            _fail("archive_manifest_missing")
        manifest = _json(archive.extractfile(members.pop("manifest.json")).read(MAX_MANIFEST + 1))
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if (manifest.get("product_id") != PRODUCT_ID or type(manifest.get("format")) is not int or manifest["format"] != 1 or not isinstance(files, dict) or set(files) != set(members)
                or not {"data/console.sqlite3", "data/console.snapshot.json", "project/web/.env.production"}.issubset(files)):
            _fail("archive_inventory_invalid")
        for name, member in members.items():
            record = files[name]
            if (not isinstance(record, dict) or set(record) != {"sha256", "size", "mode"}
                    or not isinstance(record["sha256"], str) or not _HEX.fullmatch(record["sha256"])
                    or type(record["size"]) is not int or record["size"] != member.size
                    or record["mode"] not in {0o500, 0o600}):
                _fail("archive_inventory_invalid")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            digest = hashlib.sha256()
            with archive.extractfile(member) as source, target.open("xb") as output:
                target.chmod(record["mode"])
                while chunk := source.read(CHUNK):
                    digest.update(chunk); output.write(chunk)
                output.flush(); os.fsync(output.fileno())
            if digest.hexdigest() != record["sha256"]:
                _fail("archive_member_checksum_invalid")
        end = archive.offset
    plain.seek(end)
    while chunk := plain.read(CHUNK):
        if any(chunk):
            _fail("archive_trailing_data_invalid")
    verify_backup(destination / "data/console.sqlite3", destination / "data/console.snapshot.json")
    from .storage.configuration import read_environment
    selected = read_environment(destination/'project/web/.env.production').get('MOTOR_COMPOSE_FILE','compose.production.yaml')
    if selected not in {'compose.production.yaml','compose.modular.yaml'} or not (destination/'project/web'/selected).is_file():
        _fail('deployment_configuration_missing')
    evidence = _logical_evidence(destination)
    if evidence != manifest.get("logical_evidence"):
        _fail("application_recovery_evidence_mismatch")
    return evidence


def _preflight_tar(plain):
    """Bound extension headers before tarfile can allocate or process them."""
    plain.seek(0, os.SEEK_END); length = plain.tell(); plain.seek(0)
    offset, headers = 0, 0
    while offset + 512 <= length:
        raw = plain.read(512)
        if raw == b"\0" * 512:
            if plain.read(512) != b"\0" * 512:
                _fail("archive_truncated")
            while chunk := plain.read(CHUNK):
                if any(chunk):
                    _fail("archive_trailing_data_invalid")
            plain.seek(0)
            return
        headers += 1
        if headers > MAX_FILES * 2 + 1:
            _fail("archive_file_count_limit")
        member = tarfile.TarInfo.frombuf(raw, "utf-8", "strict")
        if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.XHDTYPE}:
            _fail("archive_member_invalid")
        limit = 4096 if member.type == tarfile.XHDTYPE else MAX_FILE
        if not 0 <= member.size <= limit:
            _fail("archive_member_size_limit")
        if member.type == tarfile.XHDTYPE:
            payload = plain.read(member.size)
            remaining = payload
            names = set()
            while remaining:
                number, separator, _ = remaining.partition(b" ")
                if not separator or not number.isdigit() or len(number) > 5:
                    _fail("archive_extension_invalid")
                size = int(number)
                if size <= len(number) + 3 or size > len(remaining):
                    _fail("archive_extension_invalid")
                record = remaining[len(number) + 1:size]
                key, separator, value = record.partition(b"=")
                if not separator or key != b"path" or key in names or not value.endswith(b"\n"):
                    _fail("archive_extension_invalid")
                _safe_name(value[:-1].decode("utf-8")); names.add(key)
                remaining = remaining[size:]
        offset += 512 + ((member.size + 511) // 512) * 512
        if offset > length:
            _fail("archive_truncated")
        plain.seek(offset)
    _fail("archive_truncated")


def _trusted_restore_parent(path, *, error_code="restore_parent_not_private"):
    identities = []
    for parent in (path, *path.parents):
        _path(parent, directory=True)
        info = parent.stat()
        if os.name == "posix":
            sticky_root = info.st_uid == 0 and bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022 and not sticky_root:
                _fail(error_code)
        identities.append((str(parent), info.st_dev, info.st_ino))
    return identities


def _sha(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def verify_archive(archive, key_file, *, expected_sha256=None):
    try:
        key = _key(key_file)
        archive = _path(archive)
        before = _identity(archive.stat())
        if archive.stat().st_size > MAX_TOTAL + MAX_FILES * 1024 + MAX_MANIFEST + 65536:
            _fail("archive_size_invalid")
        digest = _sha(archive)
        if expected_sha256 is not None and (not _HEX.fullmatch(expected_sha256) or digest != expected_sha256):
            _fail("archive_checksum_invalid")
        with tempfile.TemporaryDirectory(prefix="motor-console-backup-verify-") as work:
            directory = Path(work)
            with _decrypted(archive, key, directory) as plain:
                evidence = _extract_verified(plain, directory)
        if archive.is_symlink() or _identity(archive.stat()) != before:
            _fail("archive_changed_during_read")
        return {"product_id": PRODUCT_ID, "verified": True, "encrypted": True, "sha256": digest, "size_bytes": archive.stat().st_size,
                **evidence, "network_accessed": False, "application_started": False}
    except BackupArchiveError:
        raise
    except Exception:
        raise BackupArchiveError("archive_verification_failed") from None


def restore_drill(archive, key_file, target_dir, *, expected_sha256=None):
    """Restore only into an exclusively created directory; never replace live DBs."""
    try:
        target = _path(target_dir, exists=False)
        if target.exists():
            _fail("restore_requires_new_directory")
        _path(target.parent, directory=True)
        parents = _trusted_restore_parent(target.parent)
        key = _key(key_file)
        result = verify_archive(archive, key_file, expected_sha256=expected_sha256)
        if _trusted_restore_parent(target.parent) != parents:
            _fail("restore_parent_changed")
        target.mkdir(mode=0o700, exist_ok=False)
        created = target.stat()
        with _decrypted(archive, key, target) as plain:
            if target.is_symlink() or (target.stat().st_dev, target.stat().st_ino) != (created.st_dev, created.st_ino):
                _fail("restore_target_changed")
            evidence = _extract_verified(plain, target)
        if _sha(_path(archive)) != result["sha256"]:
            _fail("archive_changed_during_read")
        if _trusted_restore_parent(target.parent) != parents or target.is_symlink() or (target.stat().st_dev, target.stat().st_ino) != (created.st_dev, created.st_ino):
            _fail("restore_target_changed")
        report = {**result, **evidence, "restored": True, "login_password_verified": False,
                  "agent_state_restored": False, "acceptance": "offline_database_keys_and_native_json_only"}
        _atomic_json(target / "restore-report.json", report)
        return report
    except BackupArchiveError:
        raise
    except Exception:
        raise BackupArchiveError("archive_restore_failed") from None


def _status(data, **value):
    path = data / STATUS_FILE
    previous = {}
    if path.exists() and path.stat().st_size <= 8192:
        previous = _json(_bounded_bytes(path, 8192))
    _atomic_json(path, {**previous, **value}, owner=data.stat())


def create_archive(data_dir, project_dir, destination, key_file, *, remote_mount=None, require_remote=False):
    """Snapshot the configured database and allowlisted files, then authenticate."""
    data = None
    try:
        data = _path(data_dir, directory=True)
        project = _path(project_dir, directory=True)
        destination = _path(destination, directory=True)
        if (data / "maintenance.json").exists():
            _fail("application_maintenance_in_progress")
        if destination.is_relative_to(data) or destination.is_relative_to(project):
            _fail("archive_destination_must_be_external")
        key = _key(key_file, excluded=(data, project, destination))
        remote = _remote_destination(destination, remote_mount)
        if require_remote and not remote:
            _fail("remote_mount_not_verified")
        with ProcessLease(data, name="backup-archive"):
            records = _collect(data, project)
            identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex
            name = "motor-console-" + identifier + ".mcba"
            temporary = destination / ("." + name + ".part")
            started = time.monotonic()
            try:
                with tempfile.TemporaryDirectory(prefix="motor-console-backup-snapshot-", dir=data) as work:
                    work = Path(work)
                    from .store import Store
                    from .storage.configuration import database_options, read_environment
                    environment = read_environment(project / "web/.env.production")
                    options = database_options(environment,data)
                    if options["backend"]=="sqlite" and not sqlite_wal_fix_available():
                        _fail("sqlite_wal_fix_required")
                    with Store(data,read_only=True,**options) as storage:
                        snapshot = storage.backup(work,reason="encrypted-application-archive")
                    verify_backup(snapshot["database_path"], snapshot["manifest_path"])
                    material_root = work / "materials"
                    material_root.mkdir(mode=0o700)
                    # A temporary private reconstruction supplies key/config context
                    # for semantic verification; only allowlisted files are copied.
                    for record in records.values():
                        target = material_root / record["name"]
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        with _open_regular(record["path"]) as (source, before), target.open("xb") as output:
                            target.chmod(0o600)
                            if _identity(before) != record["identity"]:
                                _fail("material_changed_during_backup")
                            while chunk := source.read(CHUNK):
                                output.write(chunk)
                    database = material_root / "data/console.sqlite3"
                    with Path(snapshot["database_path"]).open("rb") as source, database.open("xb") as output:
                        database.chmod(0o600)
                        while chunk := source.read(CHUNK):
                            output.write(chunk)
                    snapshot_manifest = {key: value for key, value in snapshot.items() if key not in {"database_path", "manifest_path"}}
                    snapshot_manifest["database"] = "console.sqlite3"
                    snapshot_json = material_root / "data/console.snapshot.json"
                    snapshot_json.write_bytes(_raw_json(snapshot_manifest)); snapshot_json.chmod(0o600)
                    _validate_dependencies(material_root, data)
                    evidence = _logical_evidence(material_root)
                    if _seed(material_root) == base64.urlsafe_b64encode(key).decode():
                        _fail("backup_key_not_independent")
                    records["data/console.sqlite3"] = _file_record(database, "data/console.sqlite3")
                    records["data/console.snapshot.json"] = _file_record(snapshot_json, "data/console.snapshot.json")
                    if len(records) > MAX_FILES or sum(record["size"] for record in records.values()) > MAX_TOTAL:
                        _fail("archive_total_size_limit")
                    manifest = {"format": 1, "product_id": PRODUCT_ID, "created_at": _now(), "logical_evidence": evidence,
                                "files": {name: {key: record[key] for key in ("sha256", "size", "mode")} for name, record in records.items()}}
                    if len(_raw_json(manifest)) > MAX_MANIFEST:
                        _fail("archive_manifest_size_limit")
                    _encrypt(temporary, key, manifest, records)
                    current = _collect(data, project)
                    expected = {name: record for name, record in records.items() if name not in {"data/console.sqlite3", "data/console.snapshot.json"}}
                    if current != expected:
                        _fail("material_changed_during_backup")
                    verified = verify_archive(temporary, key_file)
                    if (data / "maintenance.json").exists():
                        _fail("application_maintenance_in_progress")
                    if remote != _remote_destination(destination, remote_mount):
                        _fail("remote_mount_changed_during_backup")
                final = destination / name
                os.link(temporary, final)  # Exclusive publication: never replace an older backup.
                temporary.unlink()
                _sync_directory(destination)
                receipt = {"format": 1, "product_id": PRODUCT_ID, "archive_id": identifier, "created_at": _now(), "encrypted": True,
                           "sha256": verified["sha256"], "size_bytes": verified["size_bytes"],
                           "off_host_verified": remote, "duration_seconds": round(time.monotonic() - started, 3)}
                _atomic_json(final.with_suffix(".json"), receipt)
                _status(data, configured=True, last_attempt_status="success", last_attempt_at=receipt["created_at"],
                        last_success_at=receipt["created_at"], sha256=receipt["sha256"], size_bytes=receipt["size_bytes"],
                        encrypted=True, off_host_verified=remote, last_error_code=None)
                return {"created": True, **receipt, **evidence, "remote_required": require_remote}
            finally:
                temporary.unlink(missing_ok=True)
    except Exception as error:
        code = error.code if isinstance(error, BackupArchiveError) else "archive_creation_failed"
        if data is not None:
            try:
                _status(data, configured=True, last_attempt_status="failed", last_attempt_at=_now(), last_error_code=code)
            except Exception:
                pass
        raise BackupArchiveError(code) from None


def freshness_status(data_dir, max_age_seconds=93600):
    """Read a small safe receipt only; never hash archives on a health request."""
    result = {"configured": False, "status": "not_configured", "last_success_at": None,
              "age_seconds": None, "max_age_seconds": max_age_seconds if type(max_age_seconds) is int else None, "encrypted": False,
              "off_host_verified": False, "last_attempt_status": None, "sha256": None, "size_bytes": None}
    try:
        if type(max_age_seconds) is not int or not 60 <= max_age_seconds <= 365 * 86400:
            _fail("freshness_policy_invalid")
        path = _path(data_dir, directory=True) / STATUS_FILE
        if not path.exists():
            return result
        _path(path)
        if path.stat().st_size > 8192:
            _fail("backup_status_invalid")
        value = _json(_bounded_bytes(path, 8192))
        if not isinstance(value, dict) or value.get("configured") is not True:
            _fail("backup_status_invalid")
        result["configured"] = True
        attempt = value.get("last_attempt_status")
        if attempt not in {"success", "failed"}:
            _fail("backup_status_invalid")
        result["last_attempt_status"] = attempt
        stamp = value.get("last_success_at")
        if stamp:
            if not isinstance(stamp, str) or value.get("encrypted") is not True or type(value.get("off_host_verified")) is not bool:
                _fail("backup_status_invalid")
            instant = datetime.fromisoformat(stamp)
            if instant.tzinfo is None:
                _fail("backup_status_invalid")
            age = int((datetime.now(timezone.utc) - instant).total_seconds())
            if age < -60 or not isinstance(value.get("sha256"), str) or not _HEX.fullmatch(value["sha256"]) or type(value.get("size_bytes")) is not int or value["size_bytes"] < 1:
                _fail("backup_status_invalid")
            result.update(last_success_at=instant.isoformat(), age_seconds=max(0, age),
                          encrypted=value.get("encrypted") is True, off_host_verified=value.get("off_host_verified") is True,
                          sha256=value["sha256"], size_bytes=value["size_bytes"])
            result["status"] = "failed" if attempt == "failed" else "fresh" if age <= max_age_seconds else "stale"
        else:
            result["status"] = "failed" if attempt == "failed" else "never_succeeded"
        return result
    except Exception:
        return {**result, "configured": True, "status": "invalid"}
