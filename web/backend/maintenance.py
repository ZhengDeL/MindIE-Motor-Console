"""Local process exclusion and verified SQLite snapshots; never replace a live DB."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import threading
import time
from uuid import uuid4


class MaintenanceError(RuntimeError):
    pass


class LeaseHeld(MaintenanceError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex + ".part")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            temporary.chmod(0o600)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@contextmanager
def _readonly(path: Path):
    if not path.is_file():
        raise MaintenanceError("SQLite database file does not exist")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=20)
    try:
        yield connection
    finally:
        connection.close()


def _inspect(connection: sqlite3.Connection) -> dict:
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise MaintenanceError("SQLite integrity_check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MaintenanceError("SQLite foreign_key_check failed")
    tables = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts = {}
    for name in tables:
        quoted = '"' + name.replace('"', '""') + '"'
        counts[name] = connection.execute("SELECT COUNT(*) FROM " + quoted).fetchone()[0]
    return {"schema_version": connection.execute("PRAGMA user_version").fetchone()[0],
            "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
            "row_counts": counts, "integrity_check": "ok", "foreign_key_check": "ok"}


def _copy_snapshot(source: sqlite3.Connection, destination: Path, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout

    def progress(_status, _remaining, _total):
        if time.monotonic() > deadline:
            raise MaintenanceError("SQLite online backup exceeded its deadline")

    target = sqlite3.connect(destination, timeout=20)
    try:
        target.execute("PRAGMA synchronous=FULL")
        source.backup(target, pages=256, progress=progress, sleep=0.05)
        # The published snapshot must be self-contained, with no required WAL.
        target.execute("PRAGMA journal_mode=DELETE")
        target.commit()
    finally:
        target.close()
    destination.chmod(0o600)


def backup_database(source_path: str | Path, backup_dir: str | Path, *, reason: str = "manual") -> dict:
    """Snapshot every SQL table (including auth) with SQLite's online backup API."""
    source_path, backup_dir = Path(source_path).resolve(), Path(backup_dir).resolve()
    _private_directory(backup_dir)
    token = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:10]
    destination = backup_dir / ("console-" + token + ".sqlite3")
    temporary = backup_dir / (destination.name + ".part")
    manifest_path = destination.with_suffix(".json")
    try:
        with _readonly(source_path) as source:
            _copy_snapshot(source, temporary)
        with _readonly(temporary) as snapshot:
            evidence = _inspect(snapshot)
        os.replace(temporary, destination)
        manifest = {"format_version": 1, "created_at": _now(), "method": "sqlite-online-backup-api",
                    "reason": str(reason)[:200], "database": destination.name,
                    "sha256": _digest(destination), "size_bytes": destination.stat().st_size,
                    "sqlite_version": sqlite3.sqlite_version, **evidence}
        _atomic_json(manifest_path, manifest)
        return {**manifest, "database_path": str(destination), "manifest_path": str(manifest_path)}
    except (sqlite3.Error, OSError) as exc:
        raise MaintenanceError("SQLite snapshot could not be completed") from exc
    finally:
        temporary.unlink(missing_ok=True)
        # No live source, WAL or SHM file is copied, renamed, truncated or removed.


def verify_backup(database_path: str | Path, manifest_path: str | Path | None = None) -> dict:
    database_path = Path(database_path).resolve()
    manifest_path = Path(manifest_path).resolve() if manifest_path else database_path.with_suffix(".json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("format_version") != 1 or manifest.get("database") != database_path.name:
            raise MaintenanceError("Backup manifest format or database identity does not match")
        if not re.fullmatch(r"[0-9a-f]{64}", str(manifest.get("sha256", ""))):
            raise MaintenanceError("Backup manifest has no valid SHA256")
        if database_path.stat().st_size != manifest["size_bytes"] or _digest(database_path) != manifest["sha256"]:
            raise MaintenanceError("Backup checksum or file size does not match")
        with _readonly(database_path) as snapshot:
            evidence = _inspect(snapshot)
        for field in ("schema_version", "application_id", "row_counts"):
            if evidence[field] != manifest.get(field):
                raise MaintenanceError("Backup logical verification does not match: " + field)
        return {"valid": True, "database_path": str(database_path), "manifest_path": str(manifest_path),
                "sha256": manifest["sha256"], **evidence}
    except (OSError, ValueError, KeyError, sqlite3.Error) as exc:
        raise MaintenanceError("Backup manifest or SQLite snapshot cannot be verified") from exc


def restore_drill(database_path: str | Path, target_dir: str | Path,
                  manifest_path: str | Path | None = None) -> dict:
    """Restore and read-check an isolated new directory, never overwrite anything."""
    evidence = verify_backup(database_path, manifest_path)
    source_path, target_dir = Path(database_path).resolve(), Path(target_dir).resolve()
    if source_path.is_relative_to(target_dir):
        raise MaintenanceError("Restore drill destination must not contain the source backup")
    if target_dir.exists() and any(target_dir.iterdir()):
        raise MaintenanceError("Restore drill requires a new or empty directory; live data is never overwritten")
    _private_directory(target_dir)
    target = target_dir / "console.sqlite3"
    temporary = target_dir / ("restore-" + uuid4().hex + ".part")
    try:
        with _readonly(source_path) as source:
            _copy_snapshot(source, temporary)
        with _readonly(temporary) as restored:
            observed = _inspect(restored)
        for field in ("schema_version", "application_id", "row_counts"):
            if observed[field] != evidence[field]:
                raise MaintenanceError("Restore drill logical verification failed: " + field)
        # A concurrent creator wins. Link is exclusive, unlike replace/rename.
        os.link(temporary, target)
        temporary.unlink()
        report = {"valid": True, "kind": "isolated-sqlite-restore-drill", "restored_path": str(target),
                  "backup_sha256": evidence["sha256"], "completed_at": _now(), **observed,
                  "acceptance": "SQL integrity and table counts verified; application/agent/key recovery not exercised"}
        _atomic_json(target_dir / "restore-drill.json", report)
        return report
    except (sqlite3.Error, OSError) as exc:
        raise MaintenanceError("Isolated restore drill could not be completed") from exc
    finally:
        temporary.unlink(missing_ok=True)


class ProcessLease:
    """Kernel-held exclusive writer lease for one local deployment directory.

    No timestamp expiry or PID guessing can steal the lock. The OS releases it
    when the owning process exits. This is not a distributed/network-FS lease.
    """
    def __init__(self, data_dir: str | Path, name: str = "control"):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,48}", name):
            raise ValueError("Lease name must be a simple identifier")
        self.directory = Path(data_dir).resolve()
        self.path = self.directory / ("." + name + ".writer.lock")
        self._file = None
        self._guard = threading.RLock()
        self.owner = {"pid": os.getpid(), "host": socket.gethostname(), "owner_id": uuid4().hex}

    def acquire(self):
        with self._guard:
            if self._file is not None:
                return self
            _private_directory(self.directory)
            if self.path.is_symlink():
                raise MaintenanceError("Writer lease must not be a symbolic link")
            descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            handle = os.fdopen(descriptor, "r+b")
            try:
                opened, linked = os.fstat(handle.fileno()), self.path.lstat()
                if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)):
                    raise MaintenanceError("Writer lease must be an independent regular file")
            except BaseException:
                handle.close()
                raise
            self.path.chmod(0o600)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b" ")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                elif os.name == "posix":
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    raise MaintenanceError("This OS has no supported local writer-lease implementation")
            except (OSError, MaintenanceError) as exc:
                handle.close()
                if isinstance(exc, MaintenanceError):
                    raise
                raise LeaseHeld("Another control process holds this data directory; multiple writers are unsupported") from exc
            self._file = handle
            self.owner["acquired_at"] = _now()
            handle.seek(0)
            handle.write(json.dumps(self.owner).encode("utf-8"))
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
            return self

    def assert_held(self) -> None:
        """Refuse a removed/replaced lock pathname rather than bypass exclusion."""
        with self._guard:
            if self._file is None or self._file.closed:
                raise MaintenanceError("Writer lease is no longer held")
            try:
                opened, linked = os.fstat(self._file.fileno()), self.path.lstat()
            except OSError as error:
                raise MaintenanceError("Writer lease pathname is unavailable") from error
            if (not stat.S_ISREG(linked.st_mode) or linked.st_nlink != 1
                    or (opened.st_dev, opened.st_ino) != (linked.st_dev, linked.st_ino)):
                raise MaintenanceError("Writer lease pathname was replaced")

    def release(self) -> None:
        with self._guard:
            if self._file is None:
                return
            handle, self._file = self._file, None
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()
            # Keep the inode: deleting a lock file can let a new pathname bypass it.

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_exc):
        self.release()


def main(argv=None) -> int:
    from .ports.storage import StorageError
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--data-dir", required=True)
    backup.add_argument("--destination", required=True)
    backup.add_argument("--backend", choices=("sqlite", "postgresql"))
    backup.add_argument("--database-url-file")
    backup.add_argument("--env-file")
    verify = commands.add_parser("verify")
    verify.add_argument("--database", required=True)
    verify.add_argument("--manifest")
    drill = commands.add_parser("drill")
    drill.add_argument("--database", required=True)
    drill.add_argument("--manifest")
    drill.add_argument("--destination", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            # Resolve the same reviewed backend as Control. A PostgreSQL backup
            # must never silently read an unrelated local SQLite file.
            from .store import Store
            from .storage.configuration import database_options, read_environment
            environment = read_environment(Path(args.env_file)) if args.env_file else dict(os.environ)
            if args.backend:
                environment["MOTOR_DATABASE_BACKEND"] = args.backend
            if args.database_url_file:
                environment.pop("MOTOR_DATABASE_URL", None)
                environment.pop("MOTOR_DATABASE_URL_HOST_FILE", None)
                environment["MOTOR_DATABASE_URL_FILE"] = args.database_url_file
            with Store(args.data_dir, read_only=True, initialize=False,
                       **database_options(environment, args.data_dir)) as storage:
                result = storage.backup(args.destination)
        elif args.command == "verify":
            result = verify_backup(args.database, args.manifest)
        else:
            result = restore_drill(args.database, args.destination, args.manifest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (MaintenanceError, StorageError, ValueError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
