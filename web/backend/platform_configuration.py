"""Deployment configuration, independent of the static Web and storage driver."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from .observability import register_secret


def _integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        result = int(os.getenv(name, str(default)))
    except (ValueError, TypeError) as exc:
        raise ValueError(name + " must be an integer") from exc
    if not minimum <= result <= maximum:
        raise ValueError(name + " is outside the supported range")
    return result


@dataclass(frozen=True)
class PlatformConfiguration:
    data: Path
    logs: Path
    workspace: Path
    database_backend: str
    database_url: str | None
    log_segment_bytes: int
    log_rotations: int
    static_enabled: bool

    @classmethod
    def from_env(cls, directory: str | Path):
        data = Path(directory).resolve()
        backend = os.getenv("MOTOR_DATABASE_BACKEND", "sqlite").strip().lower()
        from .storage.registry import PLUGINS
        if backend not in PLUGINS:
            raise ValueError("MOTOR_DATABASE_BACKEND must name a registered trusted storage plugin")
        url = os.getenv("MOTOR_DATABASE_URL") or None
        filename = os.getenv("MOTOR_DATABASE_URL_FILE")
        if filename:
            secret = Path(filename)
            if not secret.is_file() or secret.stat().st_size > 16384:
                raise ValueError("MOTOR_DATABASE_URL_FILE is invalid")
            mounted = secret.read_text(encoding="utf-8").strip()
            if url and url != mounted:
                raise ValueError("Database URL environment and mounted secret disagree")
            url = mounted
        if url:
            register_secret(url)
            if "\x00" in url or "\n" in url:
                raise ValueError("Database URL contains invalid characters")
        if backend == "postgresql" and not url:
            raise ValueError("PostgreSQL requires MOTOR_DATABASE_URL_FILE or MOTOR_DATABASE_URL")
        if backend == "sqlite" and url:
            raise ValueError("SQLite uses the dedicated data directory; a database URL is not accepted")
        logs = Path(os.getenv("MOTOR_LOG_DIR") or data / "logs")
        if not logs.is_absolute():
            raise ValueError("MOTOR_LOG_DIR must be absolute")
        segment = _integer("MOTOR_LOG_SEGMENT_BYTES", 10485760, 1024, 134217728)
        rotations = _integer("MOTOR_LOG_ROTATIONS", 7, 1, 32)
        if segment * (rotations + 1) > 536870912:
            raise ValueError("Request log window cannot exceed 512 MiB")
        static = os.getenv("MOTOR_STATIC_ENABLED", "true").lower()
        if static not in {"true", "false"}:
            raise ValueError("MOTOR_STATIC_ENABLED must be true or false")
        return cls(data, logs.resolve(), Path(os.getenv("MOTOR_HOST_WORKSPACE_DIR") or data / "workspace").resolve(),
                   backend, url, segment, rotations, static == "true")

    def public(self, storage: dict) -> dict:
        return {"storage": {**storage, "backend": storage.get("backend", storage.get("engine"))},
                "logging": {"directory": str(self.logs), "segment_bytes": self.log_segment_bytes,
                            "rotations": self.log_rotations,
                            "window_bytes": self.log_segment_bytes * (self.log_rotations + 1),
                            "source": "deployment", "change_requires_restart": True},
                "paths": {"data": str(self.data), "workspace": str(self.workspace)},
                "web": {"embedded_static": self.static_enabled, "api_contract": "/api"}}
