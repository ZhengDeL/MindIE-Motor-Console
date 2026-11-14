"""Explicit, trusted integration modules with versioned capabilities.

Registration happens at application assembly, never through uploaded code.
Provisioning creates reviewed files; it does not pretend to install a gateway,
start Grafana or verify a CCAE deployment.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import time
from typing import Protocol
from urllib.parse import urlsplit
import zipfile

from fastapi import APIRouter, HTTPException, Request, Response
import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

from .network_budget import run_network
from .observability import register_secret
from .ports.storage import RevisionConflict
from .store import now, uid
from .version import CONSOLE_VERSION, BASELINE_MOTOR_COMMIT


_DASHBOARDS = {
    "motor-all-metrics.json": "64666674769ba5ff66c8e441559211a3951ec77771785d5b7946bca59f8ae819",
    "motor-kv-cache.json": "15e3512adcfca31bd3673f538d7e718536b3afb5a9569b3c4562138515674ae2",
    "motor-vllm-profiling.json": "a356d607f9f8e63f7a1ade7771f203751f03f6ddb4f3204d824e8e0d964b9072",
}
_VIEWS = (("full", {}), ("instance", {"type": ["instance"]}),
          ("role-prefill", {"type": ["role"], "role": ["prefill"]}),
          ("role-decode", {"type": ["role"], "role": ["decode"]}),
          ("dp", {"type": ["dp"]}), ("node", {"type": ["node"]}))
_GRAFANA_PROBE_TTL_SECONDS = 300
_PACKAGE_BASE_FILES = frozenset({"README.md", "prometheus.yml", "dependencies.json",
    "provisioning/datasources/motor.yml", "provisioning/dashboards/motor.yml",
    "dashboards/motor-all-metrics.json", "dashboards/motor-kv-cache.json",
    "optional-dashboards/motor-vllm-profiling.json"})
_PACKAGE_CHECKSUM_FILE = "SHA256SUMS.json"
_PACKAGE_DIRECTORIES = frozenset({"provisioning", "provisioning/datasources", "provisioning/dashboards",
    "dashboards", "optional-dashboards", "targets"})
_PACKAGE_MAX_FILES = 512
_PACKAGE_MAX_FILE_BYTES = 2 * 1024 * 1024
_PACKAGE_MAX_BYTES = 16 * 1024 * 1024
_PACKAGE_SCOPE = {"target_kind": "docker-coordinator-metrics", "kubernetes_targets_generated": False,
    "host_targets_generated": False, "direct_engine_targets_generated": False}


def _package_error():
    return HTTPException(409, "观测配置包文件缺失、变动或不符合安全边界，请重新生成")


def _package_name(value) -> str:
    if (not isinstance(value, str) or not value or len(value) > 200 or "\\" in value
            or PurePosixPath(value).is_absolute() or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise _package_error()
    if value not in _PACKAGE_BASE_FILES and value != _PACKAGE_CHECKSUM_FILE and not re.fullmatch(
            r"targets/motor-[0-9a-f]{20}-(?:full|instance|role-prefill|role-decode|dp|node)\.json", value):
        raise _package_error()
    return value


def _package_link(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _package_directory(data_directory: Path, operation: str) -> Path:
    directory = data_directory / "plugins" / "grafana" / operation
    try:
        if not directory.is_absolute():
            raise _package_error()
        for item in (directory, *directory.parents):
            info = item.lstat()
            if _package_link(info) or not stat.S_ISDIR(info.st_mode):
                raise _package_error()
    except OSError as exc:
        raise _package_error() from exc
    return directory


def _package_tree(directory: Path, expected: set[str]) -> None:
    """Enumerate a bounded allowlist, including unmanifested files and links."""
    found, pending, count, total = set(), [(directory, "")], 0, 0
    try:
        while pending:
            current, prefix = pending.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    count += 1
                    if count > _PACKAGE_MAX_FILES + len(_PACKAGE_DIRECTORIES):
                        raise _package_error()
                    relative = prefix + entry.name
                    # DirEntry.stat on Windows can omit the hard-link count.
                    info = Path(entry.path).lstat()
                    if _package_link(info):
                        raise _package_error()
                    if stat.S_ISDIR(info.st_mode):
                        if relative not in _PACKAGE_DIRECTORIES:
                            raise _package_error()
                        pending.append((Path(entry.path), relative + "/"))
                    elif stat.S_ISREG(info.st_mode):
                        _package_name(relative)
                        total += info.st_size
                        if (info.st_nlink != 1 or info.st_size > _PACKAGE_MAX_FILE_BYTES
                                or total > _PACKAGE_MAX_BYTES or len(found) >= _PACKAGE_MAX_FILES):
                            raise _package_error()
                        found.add(relative)
                    else:
                        raise _package_error()
    except OSError as exc:
        raise _package_error() from exc
    if found != expected:
        raise _package_error()


def _package_read(directory: Path, name: str) -> bytes:
    """Open each POSIX path component without following links; seal the read."""
    _package_name(name)
    descriptor = parent = None
    try:
        # A replaced FIFO must not block before the regular-file fstat check.
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
        if os.name == "posix" and os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
            folder_flags = flags | os.O_NOFOLLOW | os.O_DIRECTORY
            parent = os.open(directory.anchor, folder_flags)
            for part in (*directory.parts[1:], *name.split("/")[:-1]):
                child = os.open(part, folder_flags, dir_fd=parent)
                os.close(parent)
                parent = child
            descriptor = os.open(name.split("/")[-1], flags | os.O_NOFOLLOW, dir_fd=parent)
        else:
            # Windows has no openat: reject reparse points and bind the opened
            # handle to lstat, then verify metadata plus the recorded SHA.
            target = directory.joinpath(*name.split("/"))
            for item in (target, *target.parents):
                if _package_link(item.lstat()):
                    raise _package_error()
            before_open = target.lstat()
            descriptor = os.open(target, flags | getattr(os, "O_BINARY", 0))
            opened = os.fstat(descriptor)
            if (before_open.st_dev, before_open.st_ino) != (opened.st_dev, opened.st_ino):
                raise _package_error()
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _PACKAGE_MAX_FILE_BYTES:
            raise _package_error()
        content = bytearray()
        while len(content) <= _PACKAGE_MAX_FILE_BYTES:
            chunk = os.read(descriptor, min(65536, _PACKAGE_MAX_FILE_BYTES + 1 - len(content)))
            if not chunk:
                break
            content.extend(chunk)
        after = os.fstat(descriptor)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(content) != before.st_size or any(getattr(before, field) != getattr(after, field) for field in fields):
            raise _package_error()
        return bytes(content)
    except OSError as exc:
        raise _package_error() from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def _package_checksums(directory: Path, files: list[str]) -> list[dict]:
    if not files or len(files) > _PACKAGE_MAX_FILES or len(set(files)) != len(files):
        raise _package_error()
    _package_tree(directory, set(files))
    result, total = [], 0
    for name in sorted(files):
        raw = _package_read(directory, name)
        total += len(raw)
        if total > _PACKAGE_MAX_BYTES:
            raise _package_error()
        result.append({"path": name, "size_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    return result


def _grafana_connection_status(value: dict, *, captured: datetime | None = None) -> dict:
    """Project a saved check locally; a registered module is not a live service."""
    captured = captured or datetime.now(timezone.utc)
    result = {"state": "not_configured", "label": "未配置", "observed_at": captured.isoformat(),
              "ttl_seconds": _GRAFANA_PROBE_TTL_SECONDS, "checked_at": None, "expires_at": None}
    if not value.get("base_url"):
        return result
    if value.get("enabled") is not True:
        return {**result, "state": "disabled", "label": "未启用"}
    result.update(state="pending", label="待检查")
    probe = value.get("probe")
    if not isinstance(probe, dict) or probe.get("status") not in {"available", "unavailable"}:
        return result
    stamp = probe.get("checked_at")
    if not isinstance(stamp, str) or len(stamp) > 64 or "T" not in stamp:
        return result
    try:
        checked = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if checked.tzinfo is None:
            return result
        checked = checked.astimezone(timezone.utc)
        age = (captured - checked).total_seconds()
        if age < 0:
            return result
        expires = checked + timedelta(seconds=_GRAFANA_PROBE_TTL_SECONDS)
    except (ValueError, OverflowError):
        return result
    result.update(checked_at=checked.isoformat(), expires_at=expires.isoformat())
    if age >= _GRAFANA_PROBE_TTL_SECONDS:
        return {**result, "state": "expired", "label": "检查结果过期"}
    if probe["status"] == "available":
        return {**result, "state": "passed", "label": "最近连接检查通过"}
    return {**result, "state": "failed", "label": "最近检查失败"}


def _dashboard_overlay(document: dict, name: str):
    """Bind generated copies, retaining the original source bytes and hash.

    Native views represent alternative aggregation levels. Selecting several
    at once double-counts counters, so our generated overview selects one view.
    KV panels without a view selector read only the full view for the same reason.
    """
    def visit(value):
        if isinstance(value, list):
            for child in value: visit(child)
        elif isinstance(value, dict):
            datasource = value.get("datasource")
            if isinstance(datasource, dict) and datasource.get("type") == "prometheus":
                datasource["uid"] = "motor-prometheus"
            if name == "motor-kv-cache.json" and isinstance(value.get("expr"), str):
                value["expr"] = value["expr"].replace('{source=~"$source"', '{motor_metric_scope="full",source=~"$source"')
            for child in value.values(): visit(child)
    visit(document)
    for variable in document.get("templating", {}).get("list", []):
        if variable.get("name") == "motor_metric_scope":
            variable.update(includeAll=False, multi=False, current={"text": "full", "value": "full"})
            variable.pop("allValue", None)
    return document


@dataclass(frozen=True)
class ExtensionDescriptor:
    id: str
    name: str
    kind: str
    capabilities: tuple[str, ...]
    version: str = CONSOLE_VERSION
    api_version: int = 1


class Extension(Protocol):
    descriptor: ExtensionDescriptor
    def status(self) -> dict: ...


class ExtensionRegistry:
    """A bootstrap-only allowlist; this is not a code sandbox or package store."""
    def __init__(self):
        self._items: dict[str, Extension] = {}
        self._frozen = False

    def register(self, extension: Extension):
        descriptor = extension.descriptor
        if (self._frozen or descriptor.id in self._items or descriptor.api_version != 1
                or not re.fullmatch(r"[a-z][a-z0-9.-]{0,63}", descriptor.id)):
            raise ValueError("Extension is duplicate, incompatible or registered after assembly")
        self._items[descriptor.id] = extension

    def freeze(self):
        self._frozen = True

    def describe(self):
        return {"items": [{**asdict(item.descriptor), **item.status()} for item in self._items.values()],
                "api_version": 1, "trusted_code_only": True, "online_installation": False}


class NativeIntegration:
    def __init__(self, descriptor: ExtensionDescriptor, detail: str, *,
                 scope: str = "native-configuration", state_label: str = "原生配置通道"):
        self.descriptor, self.detail = descriptor, detail
        self.scope, self.state_label = scope, state_label

    def status(self):
        return {"enabled": True, "state": "available", "detail": self.detail,
                "scope": self.scope, "state_label": self.state_label, "external_system_verified": False}


def _base_url(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        if (len(value) > 2048 or any(c.isspace() or ord(c) < 32 for c in value)
                or "\\" in value or parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "%" in parsed.netloc
                or any(part in {".", ".."} for part in parsed.path.split("/"))):
            raise ValueError()
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except ValueError as exc:
        raise ValueError("Grafana 地址必须是无凭据、无查询参数的 HTTP(S) 基础地址") from exc
    return value.rstrip("/")


class GrafanaSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    base_url: str = Field(default="", max_length=2048)
    dashboard_path: str = Field(default="/dashboards", max_length=512)
    api_token: str | None = Field(default=None, max_length=8192)
    clear_api_token: bool = False
    expected_revision: int = Field(ge=0)

    @field_validator("base_url")
    @classmethod
    def valid_base(cls, value): return _base_url(value)

    @field_validator("dashboard_path")
    @classmethod
    def valid_path(cls, value):
        if (not value.startswith("/") or value.startswith("//") or "\\" in value
                or any(ord(c) < 33 or ord(c) == 127 for c in value)
                or urlsplit(value).scheme or urlsplit(value).netloc or ".." in value.split("/")):
            raise ValueError("看板路径必须是 Grafana 内部的相对路径")
        return value


def _atomic_write(path: Path, value: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(path.name + "." + uid() + ".part")
    try:
        with temporary.open("xb") as output:
            temporary.chmod(0o600)
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class GrafanaIntegration:
    descriptor = ExtensionDescriptor("observability.grafana", "Grafana 观测", "observability",
        ("existing-instance", "authenticated-read-probe", "offline-provisioning", "native-dashboards"))

    def __init__(self, store, secrets, lock, upstream: Path):
        self.store, self.secrets, self.lock, self.upstream = store, secrets, lock, upstream

    def settings(self):
        value = self.store.get("extension_settings", "grafana") or {"id": "grafana", "enabled": False,
                    "base_url": "", "dashboard_path": "/dashboards", "revision": 0, "probe": None}
        return {key: item for key, item in value.items() if not key.startswith("_")} | {
            "has_api_token": bool(value.get("_api_token")), "connection_status": _grafana_connection_status(value)}

    def status(self):
        value = self.settings()
        connection = value["connection_status"]
        return {"enabled": value["enabled"], "state": "connected" if connection["state"] == "passed"
                else "configured" if value.get("base_url") else "not_configured",
                "scope": "existing-instance-connection", "connection_status": connection,
                "external_system_verified": False,
                "detail": "接入已有 Grafana，保留其独立登录；连接检查不等于指标、看板或模型业务验收。"}

    def save(self, data: GrafanaSettings):
        with self.lock:
            current = self.store.get("extension_settings", "grafana")
            if data.expected_revision != int((current or {}).get("revision", 0)):
                raise RevisionConflict("Grafana settings changed")
            if data.enabled and not data.base_url:
                raise HTTPException(422, "启用 Grafana 连接前请填写基础地址")
            if data.clear_api_token and data.api_token:
                raise HTTPException(422, "不能同时清除和替换 Grafana 凭据")
            value = {"id": "grafana", "enabled": data.enabled, "base_url": data.base_url,
                     "dashboard_path": data.dashboard_path, "revision": data.expected_revision + 1,
                     "updated_at": now(), "probe": None}
            token = (current or {}).get("_api_token")
            if data.api_token is not None:
                if (not data.api_token.strip() or any(ord(c) < 33 or ord(c) == 127 for c in data.api_token)):
                    raise HTTPException(422, "Grafana API 凭据含无效字符")
                register_secret(data.api_token)
                token = self.secrets.encrypt(data.api_token)
            if data.clear_api_token: token = None
            if token: value["_api_token"] = token
            if current is None:
                self.store.mutate_audited("extension_settings", "grafana", value, action="配置观测插件", target="Grafana")
            elif not self.store.compare_and_mutate_audited("extension_settings", "grafana", current, value,
                        action="配置观测插件", target="Grafana"):
                raise RevisionConflict("Grafana settings changed")
            return self.settings()

    def probe(self):
        started_at = now()
        value = self.store.get("extension_settings", "grafana") or {}
        base = _base_url(value.get("base_url", ""))
        if not value.get("enabled") or not base:
            raise HTTPException(409, "Grafana 连接尚未启用")
        origin = urlsplit(base)
        permitted = {item.strip().rstrip("/") for item in os.getenv("MOTOR_GRAFANA_ALLOWED_ORIGINS", "").split(",") if item.strip()}
        if origin.scheme + "://" + origin.netloc not in permitted:
            raise HTTPException(409, "请先在部署配置 MOTOR_GRAFANA_ALLOWED_ORIGINS 授权该 Grafana 来源")
        token = self.secrets.decrypt(value["_api_token"]) if value.get("_api_token") else ""
        if token:
            register_secret(token)
            if origin.scheme != "https":
                raise HTTPException(422, "Grafana API 凭据只能通过 HTTPS 传输")

        async def query():
            cafile = os.getenv("MOTOR_GRAFANA_CA_BUNDLE")
            import ssl
            trust = await asyncio.to_thread(ssl.create_default_context, cafile=cafile or None)
            trust.keylog_filename = None
            trust.minimum_version = ssl.TLSVersion.TLSv1_2
            headers = {"Authorization": "Bearer " + token} if token else {}
            result = {}
            async with httpx.AsyncClient(verify=trust, trust_env=False, follow_redirects=False,
                                         timeout=8, headers=headers) as client:
                for route in ("/api/health", "/api/search?limit=1"):
                    async with client.stream("GET", base + route, headers={"Accept-Encoding": "identity"}) as response:
                        if not response.is_success:
                            raise ValueError("Grafana 未通过只读访问检查，HTTP " + str(response.status_code))
                        if response.headers.get("content-encoding", "identity") not in {"identity", ""}:
                            raise ValueError("Grafana 返回了未请求的压缩内容")
                        content = bytearray()
                        async for chunk in response.aiter_raw():
                            content.extend(chunk)
                            if len(content) > 262144: raise ValueError("Grafana 检查响应超出上限")
                        parsed = await asyncio.to_thread(json.loads, content)
                        if route == "/api/health":
                            if not isinstance(parsed, dict) or parsed.get("database") != "ok":
                                raise ValueError("Grafana 健康检查未确认数据库正常")
                            result["grafana_version"] = str(parsed.get("version", ""))[:64]
                        elif not isinstance(parsed, list):
                            raise ValueError("Grafana 看板查询没有返回列表")
            return result

        try:
            observation = run_network(query, time.monotonic() + 10)
            result = {**observation, "status": "available", "started_at": started_at, "checked_at": now()}
        except Exception as exc:
            # Do not return URLs, credential-bearing responses or transport traces.
            result = {"status": "unavailable", "started_at": started_at, "checked_at": now(), "detail":
                      str(exc) if isinstance(exc, ValueError) else "Grafana 网络、证书或总时间预算检查失败"}
        with self.lock:
            latest = self.store.get("extension_settings", "grafana")
            if latest is None or latest.get("revision") != value.get("revision"):
                raise RevisionConflict("Grafana connection changed during the probe")
            if (latest.get("probe") or {}).get("started_at", "") > started_at:
                raise RevisionConflict("A newer Grafana probe has already completed")
            updated = {**latest, "probe": result}
            if not self.store.compare_and_mutate_audited("extension_settings", "grafana", latest, updated,
                    action="检查观测连接", target="Grafana", detail=result["status"]):
                raise RevisionConflict("Grafana probe state changed while saving")
        return result

    def provision(self):
        from .fleet import endpoint
        from .deployment_view import runtime_service_view
        # These reviewed dashboard overlays apply to the bundled native version.
        # They are not silently reused for an independently installed profile.
        from .compatibility import public_profile
        if public_profile()["commit"] != BASELINE_MOTOR_COMMIT:
            raise HTTPException(409, "当前原生适配版本尚未审核 Grafana 看板，请使用对应版本的观测配置")
        source = self.upstream / "examples/features/observability/stack/grafana/dashboards"
        dashboards, sources = {}, []
        for name, expected in _DASHBOARDS.items():
            path = source / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                raise HTTPException(409, "固定版本原生看板缺失或超出安全边界，未发布配置")
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            if digest != expected:
                raise HTTPException(409, "原生看板与固定版本校验值不一致，未发布配置")
            dashboards[name] = _dashboard_overlay(json.loads(raw), name)
            sources.append({"name": name, "native_sha256": digest})
        operation = uid("grafana_")
        record = {"id": operation, "status": "accepted", "created_at": now()}
        self.store.mutate_audited("extension_provisions", operation, record, action="生成观测配置", target="Grafana", status="accepted")
        directory = self.store.directory / "plugins/grafana" / operation
        try:
            if any(p.is_symlink() for p in [directory, *directory.parents]):
                raise HTTPException(409, "观测配置目录不能经过符号链接")
            targets, jobs, warnings, required_mounts, seen, target_files = [], [], [], [], set(), []
            nodes = {n["id"]: n for n in self.store.list("nodes")}
            for service in self.store.list("services"):
                if not service.get("resources_may_exist") or service.get("status") not in {"running", "starting", "partial"}:
                    continue
                identifier = service["id"]
                try:
                    deployed = runtime_service_view(service)
                    if deployed["runtime"] != "docker":
                        warnings.append("服务 " + identifier + " 为 Kubernetes；需先确定集群内观测地址，未用 Agent 宿主地址替代 Pod 地址")
                        continue
                    address = urlsplit(endpoint(deployed, nodes, "obs"))
                    if not address.hostname:
                        raise ValueError("Missing owned Coordinator")
                except (ValueError, RuntimeError, KeyError):
                    warnings.append("服务 " + identifier + " 缺少可验证的部署地址或快照，未生成目标")
                    continue
                coordinator = deployed["user_config"].get("motor_coordinator_config") or {}
                authentication = coordinator.get("mgmt_api_key_config") or {}
                if authentication.get("enable_api_key") is True and address.scheme != "https":
                    warnings.append("服务 " + identifier + " 开启管理 Key 但观测地址使用 HTTP；未生成明文凭据采集任务，请先启用并验证 TLS")
                    continue
                identity = (address.scheme, address.netloc)
                if identity in seen:
                    warnings.append("服务 " + identifier + " 与已有服务共享观测地址，未重复采集；请核对资源归属")
                    continue
                seen.add(identity)
                name = "motor-" + hashlib.sha256(identifier.encode()).hexdigest()[:20]
                labels = {"service_id": identifier, "runtime": deployed["runtime"], "cluster": identifier,
                          "motor_component": "coordinator", "source": "real"}
                target = {"targets": [address.netloc], "labels": labels}
                targets.append(target)
                for view, params in _VIEWS:
                    scope = "role" if view.startswith("role-") else view
                    view_labels = {**labels, "motor_metric_scope": scope}
                    if view.startswith("role-"):
                        view_labels.update(role=view[5:], pd_role=view[5:])
                    filename = name + "-" + view + ".json"
                    _atomic_write(directory / "targets" / filename,
                                  json.dumps([{**target, "labels": view_labels}]).encode())
                    target_files.append("targets/" + filename)
                    job = {"job_name": name + "-" + view, "scheme": address.scheme, "metrics_path": "/metrics",
                           "params": {**params, "format": ["prometheus"]},
                           "file_sd_configs": [{"files": ["/etc/prometheus/targets/" + filename]}]}
                    if authentication.get("enable_api_key") is True:
                        key_path = "/run/secrets/" + name + "-management-key"
                        job["http_headers"] = {"X-Motor-Management-Key": {"files": [key_path]}}
                        if key_path not in required_mounts: required_mounts.append(key_path)
                    if address.scheme == "https":
                        material = {"ca_file": "/run/secrets/" + name + "-ca.pem",
                                    "cert_file": "/run/secrets/" + name + "-client.crt",
                                    "key_file": "/run/secrets/" + name + "-client.key"}
                        job["tls_config"] = {**material, "min_version": "TLS12", "insecure_skip_verify": False}
                        for path in material.values():
                            if path not in required_mounts: required_mounts.append(path)
                    jobs.append(job)
            if required_mounts:
                warnings.append("采集任务需要运维挂载 dependencies.json 中列出的独立客户端凭据；配置包不复制密钥或证书，未完成挂载前不能验收采集")
            if not targets:
                warnings.append("没有可验证的运行中 Docker Coordinator；已生成空目标配置，不表示采集就绪")
            dependencies = {"api_version": 1, "scope": "coordinator-metrics", "transport_verified": False,
                "required_secret_mounts": required_mounts, "prometheus_config_check_required": True,
                "coordinator_views": [view for view, _ in _VIEWS], "default_dashboard_view": "full",
                "disabled_integrations": {
                    "engine_metrics": "未探测或配置直接引擎端点；当前由原生 Coordinator 返回其已采集的引擎指标",
                    "vllm_profiling": "需要在引擎侧安装并启用 ms_service_metric，配置真实引擎目标；样例仅在 optional-dashboards，不自动导入",
                    "traces": "需要单独部署 Tempo 与 OpenTelemetry Collector 并配置原生 OTLP 上报",
                    "logs": "需要单独部署 Loki 与日志采集器；平台本地审计日志不自动成为 Loki 日志",
                    "host_metrics": "未安装或改动 NPU exporter、node-exporter、cAdvisor",
                    "kubernetes_targets": "需验证集群内 Coordinator Service/Pod 地址与网络连通，未自动生成宿主端口"}}
            prometheus = {"global": {"scrape_interval": "15s", "evaluation_interval": "15s",
                                    "metric_name_validation_scheme": "utf8",
                                    "external_labels": {"monitor": "motor-observability"}}, "scrape_configs": jobs}
            _atomic_write(directory / "prometheus.yml", yaml.safe_dump(prometheus, sort_keys=False).encode())
            _atomic_write(directory / "dependencies.json", json.dumps(dependencies, ensure_ascii=False, indent=2).encode())
            _atomic_write(directory / "provisioning/datasources/motor.yml", yaml.safe_dump({"apiVersion": 1, "datasources": [
                {"name": "Motor Prometheus", "uid": "motor-prometheus", "type": "prometheus", "access": "proxy",
                 "url": "http://prometheus:9090", "isDefault": True, "editable": False}]}).encode())
            _atomic_write(directory / "provisioning/dashboards/motor.yml", yaml.safe_dump({"apiVersion": 1, "providers": [
                {"name": "Motor", "folder": "Motor", "type": "file", "disableDeletion": True,
                 "options": {"path": "/var/lib/grafana/dashboards/motor"}}]}).encode())
            for name, document in dashboards.items():
                destination = "optional-dashboards" if name == "motor-vllm-profiling.json" else "dashboards"
                _atomic_write(directory / destination / name, json.dumps(document, ensure_ascii=False).encode())
            guidance = (
                "# 离线观测配置\n\n这是固定版本的配置包，未安装或启动 Grafana/Prometheus。\n\n"
                "1. 使用经过安全审核并固定版本/摘要的 Grafana 与 Prometheus 镜像，配置独立持久卷、登录和受限访问。\n"
                "2. Prometheus 只读挂载 prometheus.yml 到 /etc/prometheus/prometheus.yml，以及 targets 到 /etc/prometheus/targets。\n"
                "3. Grafana 只读挂载 provisioning 到 /etc/grafana/provisioning，以及 dashboards 到 /var/lib/grafana/dashboards/motor。\n"
                "4. 按 dependencies.json 挂载独立客户端凭据；HTTP API Key 自定义头依赖 Prometheus 的 http_headers.files 支持。不要把服务端校验值当作客户端 Key。\n"
                "5. 用目标镜像的 promtool check config 检查配置，再启动/重载；检查 Targets up、Grafana 数据源和实际指标后才算采集验收。\n\n"
                "同一 Coordinator 的不同 view 是不同聚合层级。概览默认 full 且单选；KV 看板固定 full，避免把 full/instance/role/dp/node 重复相加。\n"
                "Profiling 样例位于 optional-dashboards，需真实引擎端点和 ms_service_metric 后再手动导入。Tempo、Loki、主机/NPU 观测未自动部署。\n"
                "若需要更多维度，先核对原生指标是否存在，空面板不等于值为零。\n")
            _atomic_write(directory / "README.md", guidance.encode())
            directory = _package_directory(self.store.directory, operation)
            files = sorted(_PACKAGE_BASE_FILES | set(target_files))
            checksums = _package_checksums(directory, files)
            generated_at = now()
            scope = {**_PACKAGE_SCOPE, "coordinator_targets": len(targets)}
            package_manifest = {"api_version": 1, "operation_id": operation, "native_commit": BASELINE_MOTOR_COMMIT,
                "generated_at": generated_at, "installed": False, "scope": scope, "files": checksums}
            _atomic_write(directory / _PACKAGE_CHECKSUM_FILE,
                          json.dumps(package_manifest, ensure_ascii=False, indent=2).encode())
            files = sorted([*files, _PACKAGE_CHECKSUM_FILE])
            sha_manifest = _package_checksums(directory, files)
            result = {"files": files, "directory": str(directory), "generated_at": generated_at, "targets": targets,
                      "requires_prometheus_reload": True, "installed": False, "warnings": warnings,
                      "native_commit": BASELINE_MOTOR_COMMIT, "dashboard_sources": sources,
                      "provisioned_dashboards": [name for name in _DASHBOARDS if name != "motor-vllm-profiling.json"],
                      "optional_dashboards": ["motor-vllm-profiling.json"], "dependencies": dependencies,
                      "scope": scope, "operation_id": operation, "sha_manifest": sha_manifest,
                      "download_path": "/extensions/grafana/provisions/" + operation + "/download"}
            record.update(status="completed", result=result)
            self.store.mutate_audited("extension_provisions", operation, record, action="生成观测配置完成", target="Grafana",
                                     detail="已生成 Coordinator 离线配置；尚未启动服务或验证采集")
            return result
        except Exception:
            record.update(status="failed", error="观测配置未完整发布；保留操作标识，请修复后重新生成", finished_at=now())
            self.store.mutate_audited("extension_provisions", operation, record, action="生成观测配置失败", target="Grafana",
                                     status="failed", detail=record["error"])
            raise

    def download(self, operation: str) -> Response:
        if not isinstance(operation, str) or not re.fullmatch(r"grafana_[0-9a-f]{32}", operation):
            raise HTTPException(404, "观测配置生成记录不存在")
        record = self.store.get("extension_provisions", operation)
        if record is None:
            raise HTTPException(404, "观测配置生成记录不存在")
        if not isinstance(record, dict) or record.get("id") != operation or record.get("status") != "completed":
            raise HTTPException(409, "观测配置尚未成功生成，无法下载")
        result = record.get("result")
        if (not isinstance(result, dict) or result.get("installed") is not False
                or result.get("operation_id") != operation
                or result.get("download_path") != "/extensions/grafana/provisions/" + operation + "/download"):
            raise _package_error()
        scope = result.get("scope")
        if (not isinstance(scope, dict) or set(scope) != {*_PACKAGE_SCOPE, "coordinator_targets"}
                or any(type(scope.get(key)) is not type(value) or scope.get(key) != value for key, value in _PACKAGE_SCOPE.items())
                or type(scope.get("coordinator_targets")) is not int or scope["coordinator_targets"] < 0):
            raise _package_error()
        files, rows = result.get("files"), result.get("sha_manifest")
        if (not isinstance(files, list) or not isinstance(rows, list) or not files
                or len(files) > _PACKAGE_MAX_FILES or len(files) != len(rows)):
            raise _package_error()
        expected = {}
        total = 0
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "size_bytes", "sha256"}:
                raise _package_error()
            name = _package_name(row["path"])
            size, digest = row["size_bytes"], row["sha256"]
            if (name in expected or type(size) is not int or not 0 <= size <= _PACKAGE_MAX_FILE_BYTES
                    or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
                raise _package_error()
            total += size
            if total > _PACKAGE_MAX_BYTES:
                raise _package_error()
            expected[name] = row
        names = [_package_name(name) for name in files]
        if (len(set(names)) != len(names) or set(names) != set(expected)
                or not (_PACKAGE_BASE_FILES | {_PACKAGE_CHECKSUM_FILE}).issubset(expected)):
            raise _package_error()
        # The persisted display-only directory is never accepted as an input path.
        directory = _package_directory(self.store.directory, operation)
        if result.get("directory") != str(directory):
            raise _package_error()
        _package_tree(directory, set(expected))
        payloads = {}
        for name, row in expected.items():
            raw = _package_read(directory, name)
            if len(raw) != row["size_bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
                raise _package_error()
            payloads[name] = raw
        try:
            manifest = json.loads(payloads[_PACKAGE_CHECKSUM_FILE])
        except (ValueError, UnicodeError) as exc:
            raise _package_error() from exc
        if (not isinstance(manifest, dict) or type(manifest.get("api_version")) is not int or manifest.get("api_version") != 1
                or manifest.get("operation_id") != operation or manifest.get("installed") is not False
                or manifest.get("native_commit") != result.get("native_commit")
                or manifest.get("scope") != result.get("scope")
                or manifest.get("files") != [expected[name] for name in sorted(expected) if name != _PACKAGE_CHECKSUM_FILE]):
            raise _package_error()
        _package_directory(self.store.directory, operation)
        _package_tree(directory, set(expected))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for name in sorted(payloads):
                entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, payloads[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=1)
        body = buffer.getvalue()
        if len(body) > _PACKAGE_MAX_BYTES + _PACKAGE_MAX_FILES * 1024:
            raise _package_error()
        filename = "qinghuan-grafana-" + operation.removeprefix("grafana_") + ".zip"
        return Response(body, media_type="application/zip", headers={
            "Content-Disposition": 'attachment; filename="' + filename + '"',
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "ETag": '"' + hashlib.sha256(body).hexdigest() + '"'})


def create_extension_router(store, secrets, lock, upstream, *, enabled=None):
    router = APIRouter(prefix="/api/extensions", tags=["extensions"])
    selected = enabled or (lambda _identifier: True)
    grafana = GrafanaIntegration(store, secrets, lock, upstream) if selected("grafana") else None
    registry = ExtensionRegistry()
    if grafana:
        registry.register(grafana)
    if selected("ccae"):
        registry.register(NativeIntegration(ExtensionDescriptor("native.ccae", "CCAE 原生接入", "native",
        ("native-configuration", "offline-guidance", "explicit-control-effects")),
        "通过原生 Reporter 上报；precisioncontrol 可终止实例，真实注册/Kafka/控制需要外部系统验收"))
    if selected("gateways"):
        registry.register(NativeIntegration(ExtensionDescriptor("gateway.existing", "已有推理网关", "gateway",
        ("connection-registry", "model-discovery", "binding-check")),
        "接入已有 OpenAI 兼容网关；不等于安装 AIGW 或创建网关路由",
        scope="existing-gateway-connection", state_label="已有网关接入"))
    registry.freeze()

    @router.get("")
    def extensions(): return registry.describe()

    if grafana is None:
        return router

    @router.get("/grafana")
    def settings(): return grafana.settings()

    @router.put("/grafana")
    def save(body: GrafanaSettings): return grafana.save(body)

    @router.post("/grafana/probe")
    def probe(): return grafana.probe()

    @router.post("/grafana/provision")
    def provision(): return grafana.provision()

    @router.get("/grafana/provisions/{operation_id}/download")
    def download(operation_id: str, request: Request):
        if request.query_params:
            raise HTTPException(400, "配置包下载不接受文件路径或额外查询参数")
        return grafana.download(operation_id)

    return router
