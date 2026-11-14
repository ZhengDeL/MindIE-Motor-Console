"""Deterministic, bounded source archives. Inspection never imports code."""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from ...version import CONSOLE_VERSION
from .errors import PluginPackageError
from .files import linked, parents, read_regular
from .manifest import RESERVED_IDS, canonical_json, json_document, validate_manifest
from .limits import MAX_PACKAGE_BYTES, MAX_UNCOMPRESSED_BYTES, MAX_FILE_BYTES, MAX_FILES

_SUFFIXES = {".py", ".json", ".md", ".txt", ".toml", ".yaml", ".yml"}
_REQUIRED = {"manifest.json", "LICENSE", "README.md"}
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *("com" + str(i) for i in range(1, 10)), *("lpt" + str(i) for i in range(1, 10))}


def member_name(name: str, *, directory=False) -> str:
    if not isinstance(name, str):
        raise PluginPackageError("Package member path must be a string")
    value = name[:-1] if directory and name.endswith("/") else name
    path = PurePosixPath(value)
    if (not isinstance(name, str) or not value or len(value) > 512 or path.is_absolute() or "\\" in value
            or not path.parts or any(part in {".", "..", ""} or not re.fullmatch(r"[A-Za-z0-9_.-]+", part)
            or part.startswith(".") or part.endswith(".") or part.split(".")[0].lower() in _WINDOWS_RESERVED
            or part == "__pycache__" for part in value.split("/"))):
        raise PluginPackageError("Package contains an unsafe member path")
    if not directory and path.name not in {"LICENSE", "NOTICE"} and path.suffix.lower() not in _SUFFIXES:
        raise PluginPackageError("Only source code and declarative documentation/data files are supported")
    return value


def inspect_archive(raw: bytes, *, host_version=CONSOLE_VERSION, reserved_ids=RESERVED_IDS,
                    check_compatibility=True) -> tuple[dict, dict[str, bytes]]:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_PACKAGE_BYTES:
        raise PluginPackageError("Plugin package must be at most 8 MiB")
    files, folded, total = {}, set(), 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if not 1 <= len(entries) <= MAX_FILES:
                raise PluginPackageError("Plugin package exceeds its member-count limit")
            for info in entries:
                if info.orig_filename != info.filename:
                    raise PluginPackageError("Package member spelling changed during ZIP path normalization")
                directory = info.is_dir()
                name = member_name(info.filename, directory=directory)
                if name.lower() in folded:
                    raise PluginPackageError("Package member names are duplicated or differ only by case")
                folded.add(name.lower())
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if info.flag_bits & 1 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    raise PluginPackageError("Encrypted or unsupported compression is not accepted")
                if kind not in ({0, stat.S_IFDIR} if directory else {0, stat.S_IFREG}):
                    raise PluginPackageError("Package contains a link or special file")
                if directory:
                    if info.file_size:
                        raise PluginPackageError("Package directory contains file data")
                    continue
                if not 0 <= info.file_size <= MAX_FILE_BYTES or info.compress_size < 0:
                    raise PluginPackageError("Package member exceeds its size limit")
                total += info.file_size
                if total > MAX_UNCOMPRESSED_BYTES or (info.file_size > 65536 and info.file_size > max(1, info.compress_size) * 256):
                    raise PluginPackageError("Package exceeds its expanded size or compression-ratio budget")
                with archive.open(info) as source:
                    data = source.read(MAX_FILE_BYTES + 1)
                    if len(data) != info.file_size or len(data) > MAX_FILE_BYTES:
                        raise PluginPackageError("Package member has inconsistent expanded size")
                if name.endswith(".py"):
                    try:
                        compile(data.decode("utf-8-sig"), "qhplugin://" + name, "exec", dont_inherit=True)
                    except (UnicodeDecodeError, SyntaxError, ValueError, RecursionError) as exc:
                        raise PluginPackageError("Plugin Python source is not valid UTF-8 syntax: " + name) from exc
                files[name] = data
    except (zipfile.BadZipFile, RuntimeError, OSError, NotImplementedError, EOFError, ValueError) as exc:
        if isinstance(exc, PluginPackageError):
            raise
        raise PluginPackageError("Plugin package is not a valid bounded ZIP archive") from exc
    if not _REQUIRED <= files.keys() or not files["LICENSE"].strip() or not files["README.md"].strip():
        raise PluginPackageError("Package requires manifest.json, nonempty LICENSE and README.md")
    manifest = validate_manifest(json_document(files["manifest.json"]), host_version=host_version, reserved_ids=reserved_ids,
                                 check_compatibility=check_compatibility)
    module = manifest["entrypoint"].split(":")[0].replace(".", "/")
    if module + ".py" not in files and module + "/__init__.py" not in files:
        raise PluginPackageError("Declared package-local entry module is missing")
    # File/directory ambiguity would otherwise produce platform-dependent imports.
    for name in files:
        parent_names = PurePosixPath(name).parents
        if any(str(parent).lower() in {other.lower() for other in files} for parent in parent_names if str(parent) != "."):
            raise PluginPackageError("Package contains conflicting file and directory names")
    record = {"schemaVersion": 1, "digest": hashlib.sha256(raw).hexdigest(), "manifest": manifest,
              "compressed_bytes": len(raw), "uncompressed_bytes": total,
              "files": [{"path": name, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                        for name, data in sorted(files.items())]}
    return record, files


def build_archive(source: str | Path, *, host_version=CONSOLE_VERSION, reserved_ids=RESERVED_IDS) -> bytes:
    root = Path(source).absolute()
    parents(root)
    if not root.is_dir():
        raise PluginPackageError("Plugin source must be an existing regular directory")
    files, pending, total, count = {}, [(root, "")], 0, 0
    while pending:
        current, prefix = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                count += 1
                if count > MAX_FILES:
                    raise PluginPackageError("Plugin source exceeds the member-count budget")
                name = prefix + entry.name
                info = Path(entry.path).lstat()
                if linked(info):
                    raise PluginPackageError("Plugin source cannot contain links or reparse points")
                if stat.S_ISDIR(info.st_mode):
                    member_name(name, directory=True)
                    pending.append((Path(entry.path), name + "/"))
                elif stat.S_ISREG(info.st_mode):
                    member_name(name)
                    if info.st_nlink != 1 or info.st_size > MAX_FILE_BYTES:
                        raise PluginPackageError("Plugin source member must be a bounded regular file without hard links: " + name)
                    data = read_regular(Path(entry.path), MAX_FILE_BYTES)
                    total += len(data)
                    files[name] = data
                    if len(files) > MAX_FILES or total > MAX_UNCOMPRESSED_BYTES:
                        raise PluginPackageError("Plugin source exceeds the package size or file-count budget")
                else:
                    raise PluginPackageError("Plugin source cannot contain special files")
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    raw = output.getvalue()
    inspect_archive(raw, host_version=host_version, reserved_ids=reserved_ids)
    return raw
