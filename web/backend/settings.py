"""Read mounted secrets without including their contents in diagnostics."""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit


SECRET_NAMES = ("MOTOR_SECRET_KEY", "MOTOR_CONSOLE_TOKEN", "MOTOR_AGENT_TOKEN", "MOTOR_BOOTSTRAP_TOKEN")


def documentation_url() -> str:
    """Return the optional browser link; invalid auxiliary settings stay hidden."""
    value = os.getenv("MOTOR_DOCUMENTATION_URL", "")
    if (not value or len(value) > 4096 or "\\" in value
            or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)):
        return ""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in {"http", "https"} or not host or "@" in parsed.netloc
                or "%" in host):
            return ""
        if parsed.netloc.startswith("["):
            authority = re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::([0-9]{1,5}))?", parsed.netloc)
            ipaddress.IPv6Address(host)
        else:
            authority = re.fullmatch(r"[^:]+(?::([0-9]{1,5}))?", parsed.netloc)
            ascii_host = host.encode("idna").decode("ascii")
            domain = ascii_host[:-1] if ascii_host.endswith(".") else ascii_host
            if not domain or len(domain) > 253:
                return ""
            try:
                ipaddress.IPv4Address(domain)
            except ValueError:
                if (re.fullmatch(r"[0-9.]+", domain)
                        or any(not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                               for label in domain.split("."))):
                    return ""
        if not authority or (authority.group(1) is not None and not 1 <= parsed.port <= 65535):
            return ""
    except (ValueError, UnicodeError):
        return ""
    return value


def load_secret_files() -> None:
    for name in SECRET_NAMES:
        # Agent identities are hot-read by agent_credentials. Loading the file
        # into an environment variable would pin an old token after rotation.
        if name == "MOTOR_AGENT_TOKEN":
            continue
        filename = os.getenv(name + "_FILE")
        if not filename:
            continue
        try:
            path = Path(filename)
            if not path.is_file() or path.stat().st_size > 16384:
                raise ValueError("invalid secret file")
            value = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError(name + " mounted secret could not be read") from exc
        if not value or "\x00" in value:
            raise RuntimeError(name + " mounted secret is empty or invalid")
        if os.getenv(name) and os.environ[name] != value:
            raise RuntimeError(name + " has conflicting environment and mounted values")
        os.environ[name] = value


def sqlite_wal_fix_available(version=None) -> bool:
    value = tuple(version or sqlite3.sqlite_version_info)
    return value >= (3, 51, 3) or (value[:2] == (3, 50) and value >= (3, 50, 7)) or (value[:2] == (3, 44) and value >= (3, 44, 6))


def validate_production_settings(mode: str) -> None:
    if os.getenv("MOTOR_ENV", "development") != "production": return
    if mode == "demo": raise RuntimeError("Production mode cannot run simulated workloads")
    if not sqlite_wal_fix_available(): raise RuntimeError("Production SQLite must include the upstream WAL-reset fix; build the pinned production image")
    if mode == "control":
        if len(os.getenv("MOTOR_SECRET_KEY", "")) < 32: raise RuntimeError("Production requires a stable independent MOTOR_SECRET_KEY of at least 32 characters")
        if os.getenv("MOTOR_COOKIE_SECURE", "true").lower() != "true": raise RuntimeError("Production account sessions require secure cookies and an HTTPS entry point")
        if not os.getenv("MOTOR_TRUSTED_ORIGINS"): raise RuntimeError("Production requires the exact trusted public HTTPS origin")
        if any(not item.strip().startswith("https://") for item in os.environ["MOTOR_TRUSTED_ORIGINS"].split(",")):
            raise RuntimeError("Production trusted origins must use HTTPS")
