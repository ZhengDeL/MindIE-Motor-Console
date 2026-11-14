"""Node-local Agent credentials and offline atomic lifecycle commands.

Legacy plain token files remain readable. JSON schema 1 supports one current
token, one time-limited previous token, and disabling the complete identity.
Mount the private parent DIRECTORY, not a single file, for atomic hot rotation.
This module never exposes credentials over an HTTP management endpoint.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile

from fastapi import Header, HTTPException


MAX_FILE_BYTES = 16384
MAX_OVERLAP_SECONDS = 86400
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")


class AgentCredentialError(ValueError):
    """Deployment-owned credential configuration failed closed."""


def _private_metadata(info) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise AgentCredentialError("Agent credential material must be a regular private file")
    if os.name == "posix":
        if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o077:
            raise AgentCredentialError("Agent credential material must be owned by root/current UID with mode 0600 or 0400")


def _private_parent(path: Path) -> None:
    # Check the immediate parent: it must not permit another account to swap the
    # credential pathname. Production paths must be real directories, not links.
    parent = path.parent
    if os.name == "posix":
        # A protected leaf directory is insufficient if an untrusted account
        # can swap an ancestor. Production mounts use real absolute paths.
        absolute = parent.absolute()
        if absolute.resolve() != absolute:
            raise AgentCredentialError("Agent credential parent must not traverse symbolic links")
        for ancestor in (absolute, *absolute.parents):
            metadata = ancestor.lstat()
            if metadata.st_uid not in {0, os.geteuid()}:
                raise AgentCredentialError("Agent credential ancestors must be owned by root/current UID")
            if metadata.st_mode & 0o022 and not metadata.st_mode & stat.S_ISVTX:
                raise AgentCredentialError("Agent credential ancestors must not be writable by other accounts")
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise AgentCredentialError("Agent credential parent must be a real directory")
    if os.name == "posix" and (info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o022):
        raise AgentCredentialError("Agent credential parent must not be writable by other accounts")


def read_private_file(path: Path, *, maximum: int = MAX_FILE_BYTES) -> bytes:
    """Bounded descriptor-based read; do not follow a final symlink or log paths."""
    try:
        _private_parent(path)
        if path.is_symlink():
            raise AgentCredentialError("Agent credential material must not be a symbolic link")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            before = os.fstat(descriptor)
            _private_metadata(before)
            if before.st_size < 1 or before.st_size > maximum:
                raise AgentCredentialError("Agent credential material exceeds its size bounds")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                raw = source.read(maximum + 1)
            after = os.fstat(descriptor)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise AgentCredentialError("Agent credential material changed during reading; retry")
            if len(raw) != before.st_size or len(raw) > maximum:
                raise AgentCredentialError("Agent credential material exceeds its size bounds")
            return raw
        finally:
            os.close(descriptor)
    except AgentCredentialError:
        raise
    except (OSError, ValueError):
        raise AgentCredentialError("Agent credential material could not be read safely") from None


def _token(value) -> str:
    if (not isinstance(value, str) or not 24 <= len(value) <= 2000
            or any(ord(c) <= 32 or ord(c) >= 127 for c in value)):
        raise AgentCredentialError("Agent tokens must contain 24..2000 visible ASCII characters without whitespace")
    return value


def _expiry(value, *, required=False) -> datetime | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise AgentCredentialError("Agent credential expiry must be an ISO8601 timestamp with timezone")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise AgentCredentialError("Agent credential expiry must be an ISO8601 timestamp with timezone") from None


@dataclass(frozen=True)
class Credential:
    identifier: str
    token: str = field(repr=False)
    expires_at: datetime | None = None

    def active(self, now: datetime) -> bool:
        return self.expires_at is None or now < self.expires_at


@dataclass(frozen=True)
class CredentialPolicy:
    current: Credential
    previous: Credential | None = None
    disabled: bool = False
    legacy: bool = False

    def accepts(self, supplied: str, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        # Compare both slots even when the first matches; no prefix/substring auth.
        matches = False
        for credential in (self.current, self.previous):
            if credential:
                equal = hmac.compare_digest(supplied.encode(), credential.token.encode())
                matches = (equal and credential.active(now)) or matches
        return matches and not self.disabled


def _no_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AgentCredentialError("Agent credential JSON must not contain duplicate keys")
        result[key] = value
    return result


def parse_policy(raw: bytes, *, now: datetime | None = None) -> CredentialPolicy:
    try:
        text = raw.decode("utf-8").strip()
        if not text.startswith("{"):
            return CredentialPolicy(Credential("legacy", _token(text)), legacy=True)
        value = json.loads(text, object_pairs_hook=_no_duplicate_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise AgentCredentialError("Agent credential file is not a supported token or JSON policy") from None
    if (not isinstance(value, dict) or set(value) - {"schema", "disabled", "current", "previous"}
            or type(value.get("schema")) is not int or value["schema"] != 1
            or type(value.get("disabled", False)) is not bool):
        raise AgentCredentialError("Agent credential policy schema is invalid")
    def credential(item, *, previous=False):
        if (not isinstance(item, dict) or set(item) - {"id", "token", "expires_at"}
                or not isinstance(item.get("id"), str) or not _ID.fullmatch(item["id"])):
            raise AgentCredentialError("Agent credential entry is invalid")
        return Credential(item["id"], _token(item.get("token")), _expiry(item.get("expires_at"), required=previous))
    current = credential(value.get("current"))
    previous = credential(value["previous"], previous=True) if value.get("previous") is not None else None
    now = now or datetime.now(timezone.utc)
    if previous:
        if previous.identifier == current.identifier or hmac.compare_digest(previous.token, current.token):
            raise AgentCredentialError("Current and previous Agent credentials must be distinct")
        if previous.expires_at > now + timedelta(seconds=MAX_OVERLAP_SECONDS):
            raise AgentCredentialError("Previous Agent credential overlap must not exceed 24 hours")
    return CredentialPolicy(current, previous, value.get("disabled", False))


def load_agent_credentials() -> CredentialPolicy:
    path = os.getenv("MOTOR_AGENT_TOKEN_FILE")
    supplied = os.getenv("MOTOR_AGENT_TOKEN", "")
    if path:
        policy = parse_policy(read_private_file(Path(path)))
        if supplied and (not policy.legacy or not hmac.compare_digest(supplied, policy.current.token)):
            raise AgentCredentialError("Agent token environment conflicts with its mounted credential policy")
    else:
        policy = CredentialPolicy(Credential("environment", _token(supplied)), legacy=True)
    from .observability import register_secret
    for credential in (policy.current, policy.previous):
        if credential:
            register_secret(credential.token)
    return policy


def validate_agent_credentials() -> None:
    """Startup validation; an expired/disabled identity may start but cannot authorize."""
    load_agent_credentials()


def authorize_agent(authorization: str | None = Header(default=None)) -> None:
    try:
        policy = load_agent_credentials()
    except AgentCredentialError:
        raise HTTPException(503, "Agent credential configuration is unavailable; access is denied") from None
    supplied = authorization[7:] if isinstance(authorization, str) and authorization.startswith("Bearer ") else ""
    if len(supplied) > 2000 or not policy.accepts(supplied):
        raise HTTPException(401, "Invalid or expired Agent credentials")


def _document(policy: CredentialPolicy) -> dict:
    def entry(credential):
        return {"id": credential.identifier, "token": credential.token,
                "expires_at": credential.expires_at.isoformat() if credential.expires_at else None}
    return {"schema": 1, "disabled": policy.disabled, "current": entry(policy.current),
            "previous": entry(policy.previous) if policy.previous else None}


def public_status(policy: CredentialPolicy) -> dict:
    now = datetime.now(timezone.utc)
    def entry(credential):
        return None if credential is None else {"id": credential.identifier, "active": not policy.disabled and credential.active(now),
                                               "expires_at": credential.expires_at.isoformat() if credential.expires_at else None}
    return {"schema": 1, "disabled": policy.disabled, "legacy": policy.legacy,
            "current": entry(policy.current), "previous": entry(policy.previous)}


def agent_credential_metadata() -> dict:
    """Authenticated Agent metadata: identities/expiry only, never bearer values."""
    try:
        return public_status(load_agent_credentials())
    except AgentCredentialError:
        raise HTTPException(503, "Agent credential configuration is unavailable; access is denied") from None


@contextmanager
def _exclusive_file_lock(path: Path):
    _private_parent(path)
    lock = path.with_name(path.name + ".lock")
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        _private_metadata(os.fstat(descriptor))
        if os.name == "posix":
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            import msvcrt
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        yield
    except OSError:
        raise AgentCredentialError("Agent credential file is locked or cannot be updated safely") from None
    finally:
        os.close(descriptor)


def _write_atomic(path: Path, raw: bytes, *, create_only=False) -> None:
    _private_parent(path)
    if path.is_symlink():
        raise AgentCredentialError("Refusing to replace a symbolic link")
    if path.exists():
        read_private_file(path)
        if create_only:
            raise AgentCredentialError("Credential file already exists; use rotate or a new path")
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        if create_only:
            # Publishing a complete inode with link() is create-if-absent; an
            # unrelated writer cannot be overwritten between our check and use.
            os.link(temporary, path)
            os.unlink(temporary)
        else:
            os.replace(temporary, path)
        if os.name == "posix":
            parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(parent)
            finally:
                os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _new_credential(expires_in: int | None) -> Credential:
    return Credential("agt_" + secrets.token_hex(8), secrets.token_urlsafe(48),
                      datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in else None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline Agent credential lifecycle; never prints a secret")
    parser.add_argument("action", choices=("init", "rotate", "revoke", "status", "export", "migrate"))
    parser.add_argument("--file", required=True, type=Path, help="Private credential file in a directory mounted into the Agent")
    parser.add_argument("--grace-seconds", type=int, default=900, help="Previous-token overlap for rotate, 0..86400 seconds")
    parser.add_argument("--expires-in", type=int, help="New current token lifetime in seconds, at most 365 days")
    parser.add_argument("--previous-only", action="store_true", help="Revoke only the previous token")
    parser.add_argument("--enable", action="store_true", help="Explicitly reactivate a disabled identity while rotating, without overlap")
    parser.add_argument("--output", type=Path, help="New private output file: plain token for export, or new directory/JSON policy for migrate")
    args = parser.parse_args(argv)
    try:
        if (not 0 <= args.grace_seconds <= MAX_OVERLAP_SECONDS
                or args.expires_in is not None and not 1 <= args.expires_in <= 365 * 86400
                or args.previous_only and args.action != "revoke" or args.enable and args.action != "rotate"
                or bool(args.output) != (args.action in {"export", "migrate"})):
            raise AgentCredentialError("Credential command options are invalid")
        path = args.file.absolute()
        with _exclusive_file_lock(path):
            if args.action == "init":
                policy = CredentialPolicy(_new_credential(args.expires_in))
            else:
                policy = parse_policy(read_private_file(path))
            if args.action == "rotate":
                if policy.legacy:
                    raise AgentCredentialError("Migrate a legacy token to a dedicated directory before hot rotation")
                if policy.disabled and not args.enable:
                    raise AgentCredentialError("Disabled identities require rotate --enable to reactivate")
                now = datetime.now(timezone.utc)
                previous = None
                if not policy.disabled and policy.current.active(now) and args.grace_seconds:
                    deadline = now + timedelta(seconds=args.grace_seconds)
                    if policy.current.expires_at:
                        deadline = min(deadline, policy.current.expires_at)
                    previous = Credential(policy.current.identifier, policy.current.token, deadline)
                policy = CredentialPolicy(_new_credential(args.expires_in), previous)
            elif args.action == "revoke":
                policy = CredentialPolicy(policy.current, None, disabled=policy.disabled if args.previous_only else True)
            if args.action in {"init", "rotate", "revoke"}:
                raw = (json.dumps(_document(policy), indent=2) + "\n").encode()
                _write_atomic(path, raw, create_only=args.action == "init")
            elif args.action == "export":
                if policy.disabled or not policy.current.active(datetime.now(timezone.utc)):
                    raise AgentCredentialError("Disabled or expired credentials cannot be exported")
                destination = args.output.absolute()
                if destination == path:
                    raise AgentCredentialError("Export destination must differ from the policy file")
                with _exclusive_file_lock(destination):
                    _write_atomic(destination, (policy.current.token + "\n").encode(), create_only=True)
            elif args.action == "migrate":
                destination = args.output.absolute()
                if not policy.legacy or destination.parent == path.parent:
                    raise AgentCredentialError("Migration requires a legacy source and a new dedicated destination directory")
                with _exclusive_file_lock(destination):
                    if any(item.name != destination.name + ".lock" for item in destination.parent.iterdir()):
                        raise AgentCredentialError("Migration destination must be an empty dedicated private directory")
                    policy = CredentialPolicy(Credential("agt_" + secrets.token_hex(8), policy.current.token))
                    _write_atomic(destination, (json.dumps(_document(policy), indent=2) + "\n").encode(), create_only=True)
            print(json.dumps({"ok": True, "action": args.action, **public_status(policy)}))
        return 0
    except AgentCredentialError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print(json.dumps({"ok": False, "error": "Credential command refused; check the private path, permissions, lock and policy options"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
