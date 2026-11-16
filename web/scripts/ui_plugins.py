#!/usr/bin/env python3
"""Offline UI artifact build, inspection and revisioned next-start selection.

No command evaluates JavaScript. A successful selection is not an activated
plugin; restart the owning API and then explicitly refresh the browser.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import stat
import sys
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.plugin_host.packages.files import atomic_write, linked, parents, read_regular, store_lock
from backend.plugin_host.packages.manifest import canonical_json, json_document
from backend.ui_plugin_packages import (MAX_ARCHIVE_BYTES, MAX_ENTRY_BYTES, MAX_FILES, MAX_STATE_BYTES,
                                       UiPackageStore, UiPluginError, default_manifest, inspect_archive, member_name)


def build(source: Path, output: Path):
    source, output = source.absolute(), output.absolute()
    parents(source)
    if not source.is_dir():
        raise UiPluginError("UI source directory is missing")
    files = {}
    for item in source.rglob("*"):
        info = item.lstat()
        if linked(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise UiPluginError("UI source contains a link or special file")
        if item.is_dir():
            continue
        name = member_name(item.relative_to(source).as_posix())
        if len(files) >= MAX_FILES:
            raise UiPluginError("UI source has too many files")
        files[name] = read_regular(item, MAX_ENTRY_BYTES)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(files.items()):
            entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.external_attr = (stat.S_IFREG | 0o600) << 16
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, value)
    raw = buffer.getvalue()
    manifest, _ = inspect_archive(raw)
    parents(output.parent)
    if not output.parent.is_dir() or output.exists() or output.is_symlink():
        raise UiPluginError("Choose a new artifact filename in an existing regular directory")
    from backend.plugin_host.packages.files import write_new
    write_new(output, raw)
    return {"id": manifest["id"], "digest": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw), "executes_code": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    builder = sub.add_parser("build")
    builder.add_argument("source", type=Path)
    builder.add_argument("output", type=Path)
    inspector = sub.add_parser("inspect")
    inspector.add_argument("artifact", type=Path)
    for command in ("import", "status", "selection-hash", "select", "recover-default"):
        item = sub.add_parser(command)
        item.add_argument("--data-dir", type=Path, required=True)
        if command == "import":
            item.add_argument("artifact", type=Path)
        if command == "select":
            item.add_argument("--expected-revision", type=int, required=True)
        if command == "select":
            item.add_argument("--selection", type=Path, required=True)
            item.add_argument("--trust-code", action="store_true")
        if command == "recover-default":
            item.add_argument("--backup-file", type=Path, required=True)
            precondition = item.add_mutually_exclusive_group(required=True)
            precondition.add_argument("--expected-revision", type=int)
            precondition.add_argument("--expected-sha256")
    args = parser.parse_args()
    if args.command == "build":
        result = build(args.source, args.output)
    elif args.command == "inspect":
        raw = read_regular(args.artifact.absolute(), MAX_ARCHIVE_BYTES)
        manifest, _ = inspect_archive(raw)
        result = {"digest": hashlib.sha256(raw).hexdigest(), "manifest": manifest, "executes_code": False}
    else:
        if not args.data_dir.is_absolute():
            raise UiPluginError("UI data directory must be absolute")
        store = UiPackageStore(args.data_dir, default_manifest())
        if args.command == "import":
            result = store.import_package(read_regular(args.artifact.absolute(), MAX_ARCHIVE_BYTES))
        elif args.command == "status":
            graph = store.graph()
            result = store.inventory(graph.bootstrap["generation"])
            result["runtime_status"] = "offline inspection; running API generation is not verified"
        elif args.command == "selection-hash":
            with store_lock(store.root):
                path = store.root / "selection.json"
                raw = read_regular(path, MAX_STATE_BYTES) if path.exists() or path.is_symlink() else b""
                result = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "executes_code": False}
        elif args.command == "select":
            selection = json_document(read_regular(args.selection.absolute(), MAX_STATE_BYTES), maximum=MAX_STATE_BYTES)
            if (not isinstance(selection, dict) or not {"packages", "bindings"} <= selection.keys()
                    or set(selection) - {"packages", "bindings", "inherit_defaults"}):
                raise UiPluginError("Selection file requires packages and bindings, with optional inherit_defaults")
            result = store.select(packages=selection["packages"], bindings=selection["bindings"], expected_revision=args.expected_revision,
                                  trust_code=args.trust_code, inherit_defaults=selection.get("inherit_defaults", True))
        else:
            # This path intentionally does not load broken artifact manifests.
            # Keep the old bytes before changing only the named selection file.
            from backend.plugin_host.packages.files import write_new
            with store_lock(store.root):
                path = store.root / "selection.json"
                raw = read_regular(path, MAX_STATE_BYTES) if path.exists() or path.is_symlink() else b""
                if args.expected_sha256 is not None:
                    if hashlib.sha256(raw).hexdigest() != args.expected_sha256:
                        raise UiPluginError("UI file bytes changed; inspect before recovery", status=409)
                    # A new safe integer epoch prevents old browser CAS values
                    # from matching after a damaged JSON revision is reset.
                    revision = time.time_ns() // 1000
                else:
                    state = store._state()
                    if state["revision"] != args.expected_revision:
                        raise UiPluginError("UI revision changed; inspect before recovery", status=409)
                    revision = state["revision"]+1
                backup = args.backup_file.absolute()
                parents(backup.parent)
                if not backup.parent.is_dir() or backup.exists() or backup.is_symlink():
                    raise UiPluginError("Recovery needs a new backup file in a regular directory")
                write_new(backup, raw)
                candidate = {"schemaVersion": 1, "revision": revision, "packages": {}, "bindings": {}}
                store.graph(state=candidate)
                atomic_write(store.root / "selection.json", canonical_json(candidate, maximum=MAX_STATE_BYTES))
                result = {"revision": candidate["revision"], "activation": "next-start", "backup_sha256": hashlib.sha256(read_regular(backup, MAX_STATE_BYTES)).hexdigest()}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
