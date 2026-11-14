"""Offline, non-networked package build/import/next-start selection commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .archive import MAX_PACKAGE_BYTES, build_archive, inspect_archive
from .errors import PluginPackageError
from .files import read_regular, write_new
from .manifest import json_document
from .store import PackageStore


def parser():
    result = argparse.ArgumentParser(description="Build and explicitly select reviewed MindIE-Motor Console source plugins; selection takes effect after host restart")
    commands = result.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build a deterministic .qhplugin source archive without running its code")
    build.add_argument("source")
    build.add_argument("--output", required=True)
    inspect = commands.add_parser("inspect", help="Inspect a package without importing its Python code")
    inspect.add_argument("file", nargs="?")
    inspect.add_argument("--directory")
    inspect.add_argument("--digest")
    imported = commands.add_parser("import", help="Retain an immutable package; do not execute or activate it")
    imported.add_argument("file")
    imported.add_argument("--directory", required=True)
    listed = commands.add_parser("list")
    listed.add_argument("--directory", required=True)
    active = commands.add_parser("activate", help="Select one trusted artifact for the next host start; does not hot-load code")
    active.add_argument("id")
    active.add_argument("--directory", required=True)
    active.add_argument("--digest", required=True)
    active.add_argument("--expected-revision", required=True, type=int)
    active.add_argument("--trust-code-sha256", required=True)
    active.add_argument("--configuration-file")
    inactive = commands.add_parser("deactivate", help="Deselect a package for the next host start; does not unload the running process")
    inactive.add_argument("id")
    inactive.add_argument("--directory", required=True)
    inactive.add_argument("--expected-revision", required=True, type=int)
    reset = commands.add_parser("reset-selection", help="Explicit recovery: clear all next-start pointers without reading or deleting plugin artifacts")
    reset.add_argument("--directory", required=True)
    reset.add_argument("--expected-revision", required=True, type=int)
    reset.add_argument("--confirm-clear-selection", required=True, action="store_true")
    return result


def main(argv=None):
    arguments = parser().parse_args(argv)
    try:
        if arguments.command == "build":
            raw = build_archive(arguments.source)
            output = Path(arguments.output).absolute()
            if output.suffix != ".qhplugin":
                raise PluginPackageError("Output must use the .qhplugin extension")
            write_new(output, raw)
            result = {**inspect_archive(raw)[0], "output": str(output), "code_executed": False}
        elif arguments.command == "inspect":
            if arguments.file and (arguments.directory or arguments.digest):
                raise PluginPackageError("Inspect either a file or a store digest, not both")
            if arguments.file:
                result = inspect_archive(read_regular(Path(arguments.file).absolute(), MAX_PACKAGE_BYTES))[0]
            elif arguments.directory and arguments.digest:
                result = PackageStore(arguments.directory).inspect(arguments.digest)
            else:
                raise PluginPackageError("Inspect requires FILE or --directory plus --digest")
        elif arguments.command == "import":
            result = PackageStore(arguments.directory).import_bytes(read_regular(Path(arguments.file).absolute(), MAX_PACKAGE_BYTES))
        elif arguments.command == "list":
            result = PackageStore(arguments.directory).list()
        elif arguments.command == "activate":
            if arguments.trust_code_sha256 != arguments.digest:
                raise PluginPackageError("The explicit trust digest must exactly match the selected package")
            configuration = json_document(read_regular(Path(arguments.configuration_file).absolute(), 32768), maximum=32768) if arguments.configuration_file else {}
            result = PackageStore(arguments.directory).activate(arguments.id, arguments.digest,
                expected_revision=arguments.expected_revision, trust_code=True, configuration=configuration)
        elif arguments.command == "deactivate":
            result = PackageStore(arguments.directory).deactivate(arguments.id, expected_revision=arguments.expected_revision)
        else:
            result = PackageStore(arguments.directory, recover=False).reset_selection(
                expected_revision=arguments.expected_revision, confirm_clear_selection=arguments.confirm_clear_selection)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PluginPackageError, OSError) as error:
        code = error.code if isinstance(error, PluginPackageError) else "plugin_file_unavailable"
        message = str(error) if isinstance(error, PluginPackageError) else "Input/output file is unavailable; no activation was performed"
        print(json.dumps({"error": code, "message": message}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
