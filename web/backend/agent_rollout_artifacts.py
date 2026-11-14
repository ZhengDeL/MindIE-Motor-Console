"""Read-only inventory of administrator-staged Agent images, never arbitrary paths."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .version import AGENT_PROTOCOL_VERSION, BASELINE_MOTOR_COMMIT, MIN_AGENT_VERSION, PRODUCT_ID


MAX_ARTIFACT_BYTES = 2 * 1024**3
FIELDS = {"schema", "id", "filename", "sha256", "size_bytes", "image_id", "architecture",
          "console_version", "agent_protocol", "motor_commit", "product_id"}
ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")


class ArtifactError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _regular(path: Path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ArtifactError("artifact_symlink_refused")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ArtifactError("artifact_not_regular")
    if os.name == "posix" and (info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022):
        raise ArtifactError("artifact_permissions_invalid")
    return info


def _stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


class ArtifactInventory:
    def __init__(self, root: Path):
        self.root = Path(root).absolute()

    def _path(self, name):
        if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name or ".." in name:
            raise ArtifactError("artifact_name_invalid")
        path = self.root / name
        if not path.absolute().is_relative_to(self.root):
            raise ArtifactError("artifact_path_invalid")
        return path

    def metadata(self, identifier):
        if not isinstance(identifier, str) or not ID.fullmatch(identifier):
            raise ArtifactError("artifact_id_invalid")
        path = self._path(identifier + ".json")
        try:
            info = _regular(path)
            if info.st_size > 8192:
                raise ArtifactError("artifact_manifest_too_large")
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict) or set(value) != FIELDS:
                raise ArtifactError("artifact_manifest_invalid")
            if (type(value["schema"]) is not int or value["schema"] != 1 or value["id"] != identifier
                    or value["product_id"] != PRODUCT_ID):
                raise ArtifactError("artifact_manifest_invalid")
            filename = value["filename"]
            if (not isinstance(filename, str) or len(filename) > 160 or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*\.tar(?:\.gz)?", filename)
                    or ".." in filename):
                raise ArtifactError("artifact_name_invalid")
            if (not isinstance(value["sha256"], str) or not HEX.fullmatch(value["sha256"])
                    or type(value["size_bytes"]) is not int or not 1 <= value["size_bytes"] <= MAX_ARTIFACT_BYTES
                    or not isinstance(value["image_id"], str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["image_id"])
                    or value["architecture"] not in {"arm64", "amd64"}
                    or type(value["agent_protocol"]) is not int or value["agent_protocol"] != AGENT_PROTOCOL_VERSION
                    or value["motor_commit"] != BASELINE_MOTOR_COMMIT):
                raise ArtifactError("artifact_identity_invalid")
            version = value["console_version"]
            if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) or tuple(map(int, version.split("."))) < MIN_AGENT_VERSION:
                raise ArtifactError("artifact_installer_version_unsupported")
            archive = self._path(filename)
            if _regular(archive).st_size != value["size_bytes"]:
                raise ArtifactError("artifact_size_mismatch")
            if _stamp(path.stat()) != _stamp(info):
                raise ArtifactError("artifact_changed_during_read")
            return value
        except ArtifactError:
            raise
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            raise ArtifactError("artifact_unavailable") from None

    def list(self):
        if not self.root.exists():
            return {"items": [], "errors": []}
        if self.root.is_symlink() or not self.root.is_dir():
            raise ArtifactError("artifact_root_invalid")
        paths = sorted(self.root.glob("*.json"))
        if len(paths) > 100:
            raise ArtifactError("artifact_inventory_limit")
        items, errors = [], []
        for path in paths:
            try:
                items.append({**self.metadata(path.stem), "content_verified": False,
                              "trust": "administrator_staged_sha256"})
            except ArtifactError as error:
                errors.append({"id": path.stem[:64], "error_code": error.code})
        return {"items": items, "errors": errors}

    @contextmanager
    def open_verified(self, identifier):
        metadata = self.metadata(identifier)
        path = self._path(metadata["filename"])
        before = _regular(path)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        with os.fdopen(descriptor, "rb") as source:
            if _stamp(os.fstat(source.fileno())) != _stamp(before):
                raise ArtifactError("artifact_changed_during_read")
            digest = hashlib.sha256()
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
            if digest.hexdigest() != metadata["sha256"] or _stamp(os.fstat(source.fileno())) != _stamp(before):
                raise ArtifactError("artifact_checksum_mismatch")
            source.seek(0)
            yield metadata, source
            if _stamp(os.fstat(source.fileno())) != _stamp(before) or _stamp(path.stat()) != _stamp(before):
                raise ArtifactError("artifact_changed_during_read")
