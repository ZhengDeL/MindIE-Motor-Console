"""Authenticated maintenance APIs; host upgrades never execute inside the Web.

Release manifests are operator-staged and Ed25519-signed. A plan is not an
upgrade result: only the deployment-host CLI writes execution/rollback states.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from pathlib import Path
import re
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .compatibility import public_profile
from .maintenance import backup_database, verify_backup
from .backup_archive import freshness_status
from .store import now, uid
from .version import AGENT_PROTOCOL_VERSION, CONSOLE_VERSION, PRODUCT_ID
from .platform_configuration import PlatformConfiguration
from .maintenance_receipts import synchronize_host_results
from .image_receipts import image_operation_unresolved

from .releases import canonical_manifest, read_release, version_tuple


class ReleaseSelection(BaseModel):
    release_id: str = Field(min_length=1, max_length=96)


class SystemManager:
    def __init__(self, store, logs, *, mode: str, release_dir: str | Path | None = None, key_file: str | Path | None = None,
                 configuration: PlatformConfiguration | None = None, secret_seed: str | None = None, native_adapter=None):
        self.store, self.logs, self.mode = store, logs, mode
        self.configuration = configuration or PlatformConfiguration.from_env(store.directory)
        self.secret_seed = secret_seed
        self.native_profile = native_adapter.public_profile if native_adapter is not None else public_profile
        self.release_dir = Path(release_dir or os.getenv("MOTOR_RELEASE_DIR") or store.directory / "releases")
        self.key_file = key_file or os.getenv("MOTOR_RELEASE_PUBLIC_KEY_FILE")
        self.backup_dir = store.directory / "backups"
        self.synchronize_results()

    def synchronize_results(self):
        return synchronize_host_results(self.store, seed=self.secret_seed, current_version=CONSOLE_VERSION)

    def versions(self):
        return {"console": {"product_id": PRODUCT_ID, "version": CONSOLE_VERSION, "agent_protocol": AGENT_PROTOCOL_VERSION},
                "storage": self.store.storage_info(),
                "recovery_archive": freshness_status(self.store.directory),
                "motor": self.native_profile(),
                "upgrade": {"mode": "host-cli", "release_directory_configured": bool(self.key_file),
                            "requires_host_execution": True, "automatic_motor_upgrade": False}, "demo": self.mode == "demo"}

    def releases(self):
        items = []
        if self.release_dir.is_dir():
            paths = sorted(self.release_dir.glob("*.json"))
            if len(paths) > 500: raise HTTPException(503, "Release inventory exceeds the configured maintenance limit")
            for path in paths:
                try:
                    document = read_release(self.release_dir, path.stem, self.key_file)
                    items.append({**{k: document[k] for k in ("id", "version", "image")}, "status": "verified", "errors": []})
                except (ValueError, OSError, TypeError, KeyError) as exc:
                    items.append({"id": path.stem, "version": None, "image": None, "status": "blocked", "errors": [str(exc)]})
        return {"items": items}

    def plan(self, release_id: str):
        try: release = read_release(self.release_dir, release_id, self.key_file)
        except (OSError, ValueError, TypeError, KeyError) as exc: raise HTTPException(422, str(exc)) from exc
        checks = []
        def check(name, passed, detail): checks.append({"name": name, "status": "pass" if passed else "fail", "detail": detail})
        check("发布签名", True, "Ed25519 signature and digest verified")
        check("版本方向", version_tuple(release["version"]) > version_tuple(CONSOLE_VERSION), "Normal upgrades require a newer stable version; rollback uses a recorded snapshot")
        check("Agent 协议", release["agent_protocol"] == AGENT_PROTOCOL_VERSION, "The current Agent protocol must remain supported")
        check("数据库兼容", release["db_schema_min"] <= self.store.schema_version <= release["db_schema_max"], "Current schema must be explicitly supported by the target image")
        check("正在执行的任务", not any(j["status"] in {"queued", "running"}
              for kind in ("jobs", "agent_rollout_jobs") for j in self.store.list(kind)),
              "No deployment or Agent installation task may be active when applying a platform upgrade")
        check("未决镜像任务", not any(image_operation_unresolved(j) for j in self.store.list("image_operations")),
              "Image mutations must finish or reconcile before applying an upgrade")
        check("运行模式", self.mode != "agent", "Platform plans run on the control plane; agent/Motor upgrades are separate operations")
        identifier = uid("upgrade_")
        value = {"id": identifier, "status": "planned" if all(c["status"] == "pass" for c in checks) else "blocked",
                 "checks": checks, "current_version": CONSOLE_VERSION, "target_version": release["version"],
                 "image": release["image"], "release_id": release_id, "release": release,
                 "manifest_sha256": release["manifest_sha256"], "created_at": now(),
                 "host_command": "python3 web/scripts/console_admin.py apply --project-dir /absolute/path/to/Motor-Console --data-dir /absolute/path/to/control-data --plan-id " + identifier,
                 "requires_host_execution": True, "backup_required": True, "demo": self.mode == "demo"}
        if release.get("web_image"): value["web_image"] = release["web_image"]
        self.store.mutate_audited("upgrade_plans", identifier, value, action="平台升级计划", status=value["status"],
                                 detail="Signed release plan created; no container or Motor workload was changed")
        return value

    def backup(self):
        result = self.store.backup(self.backup_dir, reason="administrator-request")
        database = Path(result["database_path"])
        value = {"id": uid("backup_"), "created_at": now(), "sha256": result["sha256"], "size_bytes": database.stat().st_size,
                 "schema_version": result["schema_version"], "verified": True, "_database": str(database), "_manifest": result["manifest_path"]}
        try:
            self.store.mutate_audited("backups", value["id"], value, action="平台备份",
                detail="Consistent database snapshot verified; secret-key retention is separately required")
        except Exception:
            # The already verified files remain recoverable if recording their
            # metadata fails; no filesystem operation occurs in the SQL transaction.
            self.logs.write("system.backup_uncommitted", severity="ERROR", backup_id=value["id"],
                            database_file=database.name, sha256=value["sha256"])
            raise
        return self.public_backup(value)

    @staticmethod
    def public_backup(value): return {k: v for k, v in value.items() if not k.startswith("_")}

    def verify(self, identifier):
        value = self.store.get("backups", identifier)
        if not value: raise HTTPException(404, "备份不存在")
        if not Path(value["_database"]).resolve().is_relative_to(self.backup_dir.resolve()): raise HTTPException(409, "备份路径不属于当前控制面")
        result = verify_backup(value["_database"], value["_manifest"])
        self.store.audit("备份校验", identifier, "success", "Snapshot SHA256 and portable artifact integrity verified")
        return {"id": identifier, "verified": True, "sha256": value["sha256"], "schema_version": result.get("schema_version"), "verified_at": now()}


def create_system_router(manager: SystemManager):
    router = APIRouter(prefix="/api/system", tags=["system-maintenance"])

    @router.get("/version")
    def version(): return manager.versions()

    @router.get("/configuration")
    def configuration(): return manager.configuration.public(manager.store.storage_info())

    @router.get("/releases")
    def releases(): return manager.releases()

    @router.post("/upgrades/plan")
    def plan(body: ReleaseSelection): return manager.plan(body.release_id)

    @router.get("/upgrades")
    def upgrades():
        manager.synchronize_results()
        return {"items": manager.store.list("upgrade_plans")[:200]}

    @router.get("/backups")
    def backups(): return {"items": [manager.public_backup(v) for v in manager.store.list("backups")[:200]]}

    @router.post("/backups", status_code=201)
    def backup(): return manager.backup()

    @router.post("/backups/{identifier}/verify")
    def verify(identifier: str): return manager.verify(identifier)

    @router.get("/logs")
    def logs(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0), severity: str | None = None,
             actor: str | None = None, request_id: str | None = None, event: str | None = None, path: str | None = None,
             status_code: int | None = None, since: str | None = None, until: str | None = None, q: str | None = None):
        values = {k: v for k, v in locals().items() if k != "manager"}
        try: return manager.logs.query(**values)
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc

    @router.get("/logs/export")
    def export(limit: int = Query(10000, ge=1, le=10000), severity: str | None = None, actor: str | None = None,
               request_id: str | None = None, event: str | None = None, path: str | None = None,
               status_code: int | None = None, since: str | None = None, until: str | None = None, q: str | None = None):
        values = {k: v for k, v in locals().items() if k != "manager"}
        try: manager.logs.query(limit=1, **{k: v for k, v in values.items() if k != "limit"})
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
        return StreamingResponse(manager.logs.export_jsonl(**values), media_type="application/x-ndjson",
                                 headers={"Content-Disposition": 'attachment; filename="motor-console-logs.jsonl"'})

    return router
