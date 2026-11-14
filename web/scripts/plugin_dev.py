#!/usr/bin/env python3
"""Create, inspect and package independent plugins without executing their code.

Build JavaScript/TypeScript projects explicitly with reviewed tooling first.
This command never installs dependencies, runs package hooks, selects a plugin,
starts a provider factory or changes the running application.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

WEB = Path(__file__).resolve().parents[1]
ROOT = WEB.parent
sys.path.insert(0, str(WEB))
from backend.plugin_host.packages.files import parents, read_regular, write_new
from backend.plugin_host.packages.manifest import json_document

TEMPLATES = {
    "provider": ROOT / "web/plugins/examples/notes-provider",
    "ui": ROOT / "web/frontend/sdk/examples/extension-page",
    "node": ROOT / "platform/examples/maintenance-provider",
}
_ID = re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")
SOURCE_MAX = 8 * 1024 * 1024


def data(file: Path):
    return json_document(read_regular(file.absolute(), 1024 * 1024), maximum=1024 * 1024)


def detect(source: Path) -> str:
    manifest = data(source / "manifest.json")
    if not isinstance(manifest, dict):
        raise ValueError("Plugin manifest must be an object")
    if "factory" in manifest and "contracts" in manifest:
        return "provider"
    if "entrypoint" in manifest and "sdkVersion" in manifest:
        return "ui"
    if "entry" in manifest and "sdk" in manifest:
        return "node"
    raise ValueError("Unsupported plugin format; use provider, ui or node scaffolding")


@contextmanager
def scratch():
    # The only recursive removal is tempfile's own directory, whose resolved
    # location is checked before entering its cleanup scope on Windows too.
    holder = tempfile.TemporaryDirectory(prefix="motor-console-plugin-build-")
    path = Path(holder.name)
    if not path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError("Temporary build directory escaped the expected root")
    try:
        yield path
    finally:
        holder.cleanup()


def runtime_source(source: Path, kind: str) -> Path:
    if kind != "ui":
        return source
    manifest = data(source / "manifest.json")
    entry = manifest.get("entrypoint")
    if isinstance(entry, str) and (source / entry).is_file():
        return source
    built = source / "dist"
    if not (built / "manifest.json").is_file():
        raise ValueError("UI package is not built. Run its documented build command; this tool never executes project code")
    if data(built / "manifest.json") != manifest:
        raise ValueError("UI dist manifest is stale; rebuild before packaging")
    return built


def node_cli(*arguments: str):
    cli = ROOT / "platform/dist/cli.js"
    executable = shutil.which("node")
    if executable is None or not cli.is_file():
        raise ValueError("Node 22+ and a built platform CLI are required; follow platform/README.md")
    version = subprocess.run([executable, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    if not re.fullmatch(r"v(?:2[2-9]|[3-9][0-9])\.[0-9]+\.[0-9]+", version):
        raise ValueError("Node 22 or newer is required")
    result = subprocess.run([executable, str(cli), *arguments], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Node package validation failed")
    return json.loads(result.stdout)


def init(kind: str, identifier: str, directory: Path, namespace: str | None):
    if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or identifier.startswith(("reference.", "core.", "internal.")):
        raise ValueError("Choose an independent lowercase package ID")
    if kind == "node" and not re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", identifier):
        raise ValueError("Node package IDs require nonempty alphanumeric segments")
    namespace = namespace or identifier.removesuffix("-ui").replace(".", "-").replace("_", "-")
    if kind != "node":
        from motor_console_sdk.validation import extension_namespace
        extension_namespace("ext." + namespace + ".notes")
    target = directory.absolute()
    parents(target.parent)
    if target.exists() or target.is_symlink():
        raise ValueError("Scaffolding requires a new directory; existing work is never overwritten")
    template = TEMPLATES[kind]
    parents(template)
    if not template.is_dir():
        raise ValueError("This source distribution is missing its reviewed plugin template")
    replacements = {"example.notes-ui": identifier, "example.notes": identifier,
                    "example-notes": namespace, "example.maintenance-http": identifier}
    members = []
    for file in sorted(template.rglob("*")):
        relative = file.relative_to(template)
        if any(part in {"node_modules", "dist", "__pycache__", ".git"} for part in relative.parts):
            continue
        if file.is_dir() and not file.is_symlink():
            continue
        raw = read_regular(file, SOURCE_MAX)
        if file.name != "LICENSE":
            text = raw.decode("utf-8")
            # Match only original template bytes: a new ID may itself contain
            # an old example prefix and must never be rewritten a second time.
            pattern = "|".join(re.escape(old) for old in sorted(replacements, key=len, reverse=True))
            text = re.sub(pattern, lambda match: replacements[match.group(0)], text)
            # A generated external project must not depend on the checkout's
            # relative documentation layout.
            text = text.replace("../../../docs/PROVIDER_PLUGINS.md", "https://github.com/ZhengDeL/MindIE-Motor-Console/blob/v0.12.0/web/docs/PROVIDER_PLUGINS.md")
            text = text.replace("../../../frontend/sdk/examples/extension-page/README.md", "https://github.com/ZhengDeL/MindIE-Motor-Console/blob/v0.12.0/web/frontend/sdk/examples/extension-page/README.md")
            raw = text.replace("\r\n", "\n").encode("utf-8")
        members.append((relative, raw))
    if not members or sum(len(raw) for _, raw in members) > SOURCE_MAX:
        raise ValueError("Plugin template exceeds the source budget")
    target.mkdir(mode=0o700)
    for relative, raw in members:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_new(destination, raw)
    return {"created": True, "kind": kind, "id": identifier, "namespace": namespace,
            "directory": str(target), "files": [p.as_posix() for p, _ in members],
            "dependencies_installed": False, "plugin_code_executed": False,
            "next": "Read README.md; install only reviewed SDK/toolchain artifacts, build explicitly, then check and pack"}


def check(source: Path):
    source = source.absolute()
    parents(source)
    kind = detect(source)
    actual = runtime_source(source, kind)
    with scratch() as temporary:
        if kind == "provider":
            from scripts.provider_packages import build, inspect
            artifact = temporary / "inspect.mcprovider"
            build(actual, artifact)
            manifest, _ = inspect(read_regular(artifact, SOURCE_MAX))
        elif kind == "ui":
            from scripts.ui_plugins import build
            from backend.ui_plugin_packages import inspect_archive
            artifact = temporary / "inspect.mcui"
            build(actual, artifact)
            manifest = inspect_archive(read_regular(artifact, SOURCE_MAX))[0]
        else:
            result = node_cli("inspect", "--source", str(actual))
            manifest = result["manifest"]
    return {"valid": True, "kind": kind, "manifest": manifest, "runtime_source": str(actual),
            "plugin_code_executed": False, "dependencies_installed": False,
            "scope": "Static package, metadata and integrity checks; not type-check, factory execution or production acceptance"}


def pack(source: Path, output: Path):
    source, output = source.absolute(), output.absolute()
    parents(source)
    kind = detect(source)
    actual = runtime_source(source, kind)
    if kind == "provider":
        from scripts.provider_packages import build
        result = build(actual, output)
    elif kind == "ui":
        from scripts.ui_plugins import build
        result = build(actual, output)
    else:
        result = node_cli("import", "--source", str(actual), "--store", str(output))
    return {"kind": kind, "result": result, "output": str(output),
            "selected": False, "plugin_code_executed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    item = commands.add_parser("init"); item.add_argument("--kind", choices=tuple(TEMPLATES), required=True)
    item.add_argument("--id", required=True); item.add_argument("--directory", type=Path, required=True)
    item.add_argument("--namespace", help="Independent backend namespace; pair UI with the same backend explicitly")
    item = commands.add_parser("check"); item.add_argument("source", type=Path)
    item = commands.add_parser("pack"); item.add_argument("source", type=Path); item.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        result = init(args.kind, args.id, args.directory, args.namespace)
    elif args.command == "check":
        result = check(args.source)
    else:
        result = pack(args.source, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
