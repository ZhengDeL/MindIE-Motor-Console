"""Explicit host-side encrypted Console backup; never prints key/material paths."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.backup_archive import (BackupArchiveError, create_archive, freshness_status,
                                    generate_key, restore_drill, verify_archive)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen")
    keygen.add_argument("--key-file", required=True)
    create = sub.add_parser("create")
    for name in ("data-dir", "project-dir", "destination", "key-file"):
        create.add_argument("--" + name, required=True)
    create.add_argument("--remote-mount")
    create.add_argument("--require-remote", action="store_true")
    for name in ("verify", "drill"):
        command = sub.add_parser(name)
        command.add_argument("--archive", required=True)
        command.add_argument("--key-file", required=True)
        command.add_argument("--sha256")
        if name == "drill":
            command.add_argument("--target-dir", required=True)
    status = sub.add_parser("status")
    status.add_argument("--data-dir", required=True)
    status.add_argument("--max-age-seconds", type=int, default=93600)
    args = parser.parse_args(argv)
    try:
        if args.command == "keygen":
            result = generate_key(args.key_file)
        elif args.command == "create":
            result = create_archive(args.data_dir, args.project_dir, args.destination, args.key_file,
                                    remote_mount=args.remote_mount, require_remote=args.require_remote)
        elif args.command == "verify":
            result = verify_archive(args.archive, args.key_file, expected_sha256=args.sha256)
        elif args.command == "drill":
            result = restore_drill(args.archive, args.key_file, args.target_dir, expected_sha256=args.sha256)
        else:
            result = freshness_status(args.data_dir, args.max_age_seconds)
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
        return 0 if result.get("status") not in {"invalid", "failed", "stale", "not_configured", "never_succeeded"} else 2
    except BackupArchiveError as error:
        print(json.dumps({"ok": False, "error_code": error.code}), file=sys.stderr)
        return 2
    except Exception:
        print(json.dumps({"ok": False, "error_code": "backup_command_failed"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
