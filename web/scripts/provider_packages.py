#!/usr/bin/env python3
"""Offline import and explicit composition of reviewed Python provider artifacts.

The .mcprovider format is deliberately small: one self-contained provider.py,
manifest.json, README.md and LICENSE. It installs no dependencies and executes
no package hooks. Installed bytes are not selected until an assembly is saved.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from uuid import uuid4
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.plugin_host.packages.files import (atomic_write, ensure_directory, parents, read_regular,
                                               store_lock, write_new)
from backend.plugin_host.packages.manifest import canonical_json, json_document
from backend.provider_host import ProviderHost
from backend.provider_host.catalogue import CONTRACTS

MAX_BYTES = 4 * 1024 * 1024
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,95}")
_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def inspect(raw: bytes):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= 8 * 1024 * 1024:
        raise ValueError("Provider artifact must be at most 8 MiB")
    expected = {"manifest.json", "provider.py", "README.md", "LICENSE"}
    files, total = {}, 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if len(archive.infolist()) != len(expected):
                raise ValueError("Provider artifact requires exactly manifest.json, provider.py, README.md and LICENSE")
            for item in archive.infolist():
                if (item.filename not in expected or item.filename in files or item.orig_filename != item.filename
                        or item.is_dir() or item.flag_bits & 1
                        or stat.S_IFMT(item.external_attr >> 16) not in {0, stat.S_IFREG}
                        or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                        or not 0 < item.file_size <= MAX_BYTES):
                    raise ValueError("Invalid provider artifact member")
                total += item.file_size
                if total > 8 * 1024 * 1024 or item.file_size > max(item.compress_size, 1) * 256 and item.file_size > 65536:
                    raise ValueError("Provider artifact exceeds expansion budget")
                with archive.open(item) as source:
                    value = source.read(MAX_BYTES+1)
                if len(value) != item.file_size:
                    raise ValueError("Provider artifact member has inconsistent size")
                files[item.filename] = value
    except (zipfile.BadZipFile, RuntimeError, EOFError, NotImplementedError) as error:
        raise ValueError("Invalid provider ZIP artifact") from error
    manifest = json_document(files["manifest.json"])
    if (not isinstance(manifest, dict) or set(manifest) != {"schemaVersion", "sdkVersion", "id", "version", "contracts", "dependencies", "factory"}
            or type(manifest["schemaVersion"]) is not int or manifest["schemaVersion"] != 1
            or type(manifest["sdkVersion"]) is not int or manifest["sdkVersion"] != 1
            or not isinstance(manifest["id"], str) or not _ID.fullmatch(manifest["id"])
            or manifest["id"].startswith("reference.") or not isinstance(manifest["version"], str) or not _VERSION.fullmatch(manifest["version"])
            or not isinstance(manifest["factory"], str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", manifest["factory"])):
        raise ValueError("Invalid provider manifest schema or identity")
    contracts, dependencies = manifest["contracts"], manifest["dependencies"]
    if (not isinstance(contracts, dict) or not contracts or len(contracts) > len(CONTRACTS)
            or any(name not in CONTRACTS or CONTRACTS[name].internal or type(version) is not int or version != CONTRACTS[name].version for name, version in contracts.items())
            or not isinstance(dependencies, list) or len(dependencies) > len(CONTRACTS)
            or any(not isinstance(name, str) or name not in CONTRACTS or CONTRACTS[name].internal for name in dependencies)
            or len(set(dependencies)) != len(dependencies)):
        raise ValueError("Unsupported provider contracts or dependencies")
    if not files["README.md"].strip() or not files["LICENSE"].strip():
        raise ValueError("Provider requires nonempty README.md and LICENSE")
    compile(files["provider.py"].decode("utf-8"), "provider-artifact://provider.py", "exec", dont_inherit=True)
    return manifest, files


def build(source: Path, output: Path):
    source, output = source.absolute(), output.absolute()
    parents(source)
    if {p.name for p in source.iterdir()} != {"manifest.json", "provider.py", "README.md", "LICENSE"}:
        raise ValueError("Provider source must contain exactly the four documented files")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ("LICENSE", "README.md", "manifest.json", "provider.py"):
            content = read_regular(source / name, MAX_BYTES)
            member = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            member.external_attr = (stat.S_IFREG | 0o600) << 16
            member.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(member, content)
    raw = buffer.getvalue()
    manifest, _ = inspect(raw)
    parents(output.parent)
    write_new(output, raw)
    return {"id": manifest["id"], "artifact_sha256": hashlib.sha256(raw).hexdigest(), "executes_code": False}


def install(artifact: Path, directory: Path, *, runtime_root: str | None = None):
    raw = read_regular(artifact.absolute(), 8*1024*1024)
    manifest, files = inspect(raw)
    digest = hashlib.sha256(raw).hexdigest()
    runtime = None
    if runtime_root is not None:
        runtime = PurePosixPath(runtime_root)
        if (not runtime.is_absolute() or runtime_root.startswith("//") or ".." in runtime.parts
                or any(ord(character) < 32 for character in runtime_root)):
            raise ValueError("Container runtime root must be an explicit absolute POSIX directory")
    root = directory.absolute()
    ensure_directory(root)
    with store_lock(root):
        target = root / digest
        if target.exists():
            parents(target)
            for name, data in files.items():
                if read_regular(target / name, MAX_BYTES) != data:
                    raise ValueError("Installed provider artifact has changed")
        else:
            temporary = root / ("import-"+uuid4().hex)
            ensure_directory(temporary)
            for name, data in files.items():
                write_new(temporary / name, data)
            os.rename(temporary, target)
        runtime_module = str(target / "provider.py")
        if runtime is not None:
            runtime_module = str(runtime / digest / "provider.py")
        fragment = {"id": manifest["id"], "version": manifest["version"], "contracts": manifest["contracts"],
                    "dependencies": manifest["dependencies"], "module_file": runtime_module,
                    "sha256": hashlib.sha256(files["provider.py"]).hexdigest(), "factory": manifest["factory"], "trust_code": False}
    return {"artifact_sha256": digest, "provider": fragment, "installed_not_selected": True, "executes_code": False}


def validate(document):
    with ExitStack() as cleanup:
        host = ProviderHost(settings={}, cleanup=cleanup, document=document)
        return {"generation": host.generation, "bindings": host.describe(), "executes_code": False}


def save_selection(candidate: Path, output: Path, expected_sha256: str, backup: Path):
    document = json_document(read_regular(candidate.absolute(), 1024*1024), maximum=1024*1024)
    result = validate(document)
    output, backup = output.absolute(), backup.absolute()
    ensure_directory(output.parent)
    parents(backup.parent)
    with store_lock(output.parent):
        old = read_regular(output, 1024*1024) if output.exists() or output.is_symlink() else b""
        if hashlib.sha256(old).hexdigest() != expected_sha256:
            raise ValueError("Provider assembly changed; inspect before saving")
        write_new(backup, old)
        atomic_write(output, canonical_json(document, maximum=1024*1024))
    return {**result, "activation": "next-start", "backup_sha256": hashlib.sha256(old).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("build"); item.add_argument("source", type=Path); item.add_argument("output", type=Path)
    item = sub.add_parser("inspect"); item.add_argument("artifact", type=Path)
    item = sub.add_parser("import"); item.add_argument("artifact", type=Path); item.add_argument("--directory", type=Path, required=True)
    item.add_argument("--runtime-root", help="Absolute container POSIX directory corresponding to the imported artifact directory")
    item = sub.add_parser("validate"); item.add_argument("assembly", type=Path)
    item = sub.add_parser("select")
    item.add_argument("candidate", type=Path); item.add_argument("--output", type=Path, required=True)
    item.add_argument("--expected-sha256", required=True); item.add_argument("--backup-file", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "build": result = build(args.source,args.output)
    elif args.command == "inspect":
        raw = read_regular(args.artifact.absolute(),8*1024*1024)
        manifest,_ = inspect(raw)
        result = {"manifest":manifest,"artifact_sha256":hashlib.sha256(raw).hexdigest(),"executes_code":False}
    elif args.command == "import": result=install(args.artifact,args.directory,runtime_root=args.runtime_root)
    elif args.command == "validate": result=validate(json_document(read_regular(args.assembly.absolute(),1024*1024),maximum=1024*1024))
    else: result=save_selection(args.candidate,args.output,args.expected_sha256,args.backup_file)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    try: main()
    except (ValueError, OSError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(1)
