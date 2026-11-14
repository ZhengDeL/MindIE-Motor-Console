"""Bounded regular-file access and cross-process compare-and-set locking."""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading
import time
from uuid import uuid4

from .errors import PackageStoreUnavailable, PluginPackageError

_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def linked(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def parents(path: Path) -> None:
    """Reject links/reparse points in each existing directory component."""
    for directory in (path, *path.parents):
        try:
            info = directory.lstat()
        except FileNotFoundError:
            continue
        if linked(info) or not stat.S_ISDIR(info.st_mode):
            raise PackageStoreUnavailable("Plugin store path contains a link or a non-directory")


def ensure_directory(path: Path) -> None:
    parents(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        parents(path)
    except OSError as exc:
        raise PackageStoreUnavailable("Plugin directory could not be prepared") from exc


def _descriptor(path: Path, flags: int, mode=0o600, *, expected_links=1) -> int:
    parents(path.parent)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    parent = None
    try:
        if os.name == "posix" and os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            parent = os.open(path.anchor, directory_flags)
            for part in path.parts[1:-1]:
                child = os.open(part, directory_flags, dir_fd=parent)
                os.close(parent)
                parent = child
            descriptor = os.open(path.name, flags, mode, dir_fd=parent)
        else:
            before = path.lstat() if path.exists() or path.is_symlink() else None
            if before is not None and (linked(before) or not stat.S_ISREG(before.st_mode) or before.st_nlink != expected_links):
                raise PackageStoreUnavailable("Plugin files must be regular files without links")
            descriptor = os.open(path, flags, mode)
            if before is not None:
                opened = os.fstat(descriptor)
                if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                    os.close(descriptor)
                    raise PackageStoreUnavailable("Plugin file identity changed while opening")
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != expected_links:
            os.close(descriptor)
            raise PackageStoreUnavailable("Plugin files must be regular files without links")
        return descriptor
    finally:
        if parent is not None:
            os.close(parent)


def read_regular(path: Path, maximum: int, *, expected_links=1) -> bytes:
    descriptor = None
    try:
        descriptor = _descriptor(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0), expected_links=expected_links)
        before = os.fstat(descriptor)
        if before.st_size > maximum:
            raise PluginPackageError("Plugin file exceeds its size limit")
        result = bytearray()
        while len(result) <= maximum:
            chunk = os.read(descriptor, min(65536, maximum + 1 - len(result)))
            if not chunk:
                break
            result.extend(chunk)
        after = os.fstat(descriptor)
        identity = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_nlink")
        if len(result) > maximum or any(getattr(before, key) != getattr(after, key) for key in identity):
            raise PackageStoreUnavailable("Plugin file changed while reading")
        return bytes(result)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise PackageStoreUnavailable("Plugin file could not be read") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _sync_directory(path: Path):
    if os.name == "posix":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def write_new(path: Path, content: bytes) -> None:
    descriptor = None
    try:
        descriptor = _descriptor(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        _sync_directory(path.parent)
    finally:
        if descriptor is not None:
            os.close(descriptor)


def publish_new(path: Path, content: bytes) -> None:
    """Publish complete immutable bytes with an atomic create-if-absent link.

    The caller holds store_lock; inventory readers hold that same lock, so
    the intermediate hard-link count cannot appear as an imported artifact.
    Filesystems without hard-link support fail instead of overwriting a file
    or degrading to a visible partial copy.
    """
    temporary = path.with_name(".qhplugin-import-" + uuid4().hex + ".tmp")
    try:
        write_new(temporary, content)
        parents(path.parent)
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        _sync_directory(path.parent)
    except FileExistsError:
        raise
    except OSError as exc:
        raise PackageStoreUnavailable("Plugin artifact publication failed; writable storage with hard-link support is required") from exc
    finally:
        # Only this operation's exact temporary name is eligible for removal.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write(path: Path, content: bytes) -> None:
    """Replace only the named regular state file, never a computed tree."""
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        parents(path.parent)
        if path.exists() or path.is_symlink():
            info = path.lstat()
            if linked(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PackageStoreUnavailable("Plugin state is not a regular unlinked file")
        write_new(temporary, content)
        parents(path.parent)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except OSError as exc:
        raise PackageStoreUnavailable("Plugin state could not be committed atomically") from exc
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def store_lock(root: Path, *, timeout: float = 5):
    """Serialize state writers across threads and independent CLI/API processes."""
    key = os.path.normcase(str(root))
    with _GUARD:
        guard = _LOCKS.setdefault(key, threading.RLock())
    with guard:
        ensure_directory(root)
        descriptor = None
        acquired = False
        try:
            descriptor = _descriptor(root / ".lock", os.O_RDWR | os.O_CREAT)
            size = os.fstat(descriptor).st_size
            if size > 1:
                raise PackageStoreUnavailable("Plugin lock file has an invalid shape")
            if size == 0:
                os.write(descriptor, b"0")
                os.fsync(descriptor)
            expires = time.monotonic() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError:
                    if time.monotonic() >= expires:
                        raise PackageStoreUnavailable("Another process is changing plugin state; retry after it completes")
                    time.sleep(0.02)
            yield
        except OSError as exc:
            raise PackageStoreUnavailable("Plugin state lock is unavailable") from exc
        finally:
            if descriptor is not None:
                if acquired:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
