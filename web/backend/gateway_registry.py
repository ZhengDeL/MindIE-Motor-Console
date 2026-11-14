"""Manage existing gateway connections; native AIGW metadata is not an installer.

Only a deployment-approved origin can receive a bounded model-discovery GET.
Remote content, credentials and transport exceptions never enter public state.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import ipaddress
import json
import os
import re
import socket
import ssl
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from .observability import redact, register_secret
from .store import now, uid


MAX_MODELS = 200
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
PROBE_BUDGET_SECONDS = 8
SERVICE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}\Z")
DEPLOYMENT = {"status": "artifact_not_configured", "message": "尚未配置可验证的官方 AIGW 部署包；当前仅管理已有网关连接"}
CONFIG_FIELDS = ("user_config", "env", "runtime", "placements", "motor_profile", "extra_manifests")


def gateway_url(value: str, *, origin_only: bool = False) -> str:
    """Canonicalize a narrowly scoped OpenAI base URL, without encoded paths."""
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("网关地址必须是可编码的 UTF-8 文本") from error
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or any(char in value for char in ("\\", "%", "?", "#"))):
        raise ValueError("网关地址不能包含控制字符、凭据、查询参数、片段或编码路径")
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username is not None or parts.password is not None:
            raise ValueError()
        hostname = parts.hostname
        port = parts.port
        if not hostname or port is not None and not 1 <= port <= 65535:
            raise ValueError()
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            hostname = hostname.encode("idna").decode("ascii").lower()
            if (len(hostname) > 253 or hostname.endswith(".")
                    or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in hostname.split("."))
                    or re.fullmatch(r"(?:0x[0-9a-f]+|[0-9]+)(?:\.(?:0x[0-9a-f]+|[0-9]+)){0,3}", hostname)):
                raise ValueError()
        else:
            effective = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
            if effective.is_unspecified or effective.is_multicast or effective.is_link_local:
                raise ValueError()
            hostname = "[" + address.compressed + "]" if address.version == 6 else str(address)
        path = parts.path.rstrip("/")
        if (not re.fullmatch(r"(?:/[A-Za-z0-9._~@+\-]+)*", path)
                or any(part in {".", ".."} for part in path.split("/")) or origin_only and path):
            raise ValueError()
        port_text = "" if port is None or port == (443 if parts.scheme == "https" else 80) else ":" + str(port)
        return parts.scheme + "://" + hostname + port_text + path
    except (ValueError, UnicodeError) as error:
        raise ValueError("请输入有效的 HTTP(S) 网关地址；不允许路径穿越或特殊目标地址") from error


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return parts.scheme + "://" + parts.netloc


def _safe_address(address):
    effective = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
    return not (effective.is_unspecified or effective.is_multicast or effective.is_link_local)


async def _gateway_destination(base_url: str) -> tuple[httpx.URL, str]:
    """Resolve once, reject special targets, and pin the HTTP connection's IP.

    The original Host and TLS SNI remain unchanged. A second DNS lookup cannot
    turn an approved hostname into a link-local/metadata-service destination.
    """
    value = httpx.URL(base_url)
    hostname = value.host
    try:
        addresses = [ipaddress.ip_address(hostname)]
    except ValueError:
        answers = await asyncio.get_running_loop().getaddrinfo(hostname, value.port or (443 if value.scheme == "https" else 80),
                                                              family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(ipaddress.ip_address(answer[4][0]) for answer in answers))
    if not addresses or any(not _safe_address(address) for address in addresses):
        raise ValueError("Gateway resolves to an unsupported address")
    addresses.sort(key=lambda address: address.version)
    return value.copy_with(host=str(addresses[0])), hostname


def _allowed_origins() -> set[str]:
    raw = os.getenv("MOTOR_GATEWAY_ALLOWED_ORIGINS", "").strip()
    if not raw:
        return set()
    try:
        values = json.loads(raw) if raw.startswith("[") else raw.split(",")
        if not isinstance(values, list) or len(values) > 100 or not all(isinstance(value, str) and value.strip() for value in values):
            raise ValueError()
        return {gateway_url(value.strip(), origin_only=True) for value in values}
    except (ValueError, TypeError):
        raise HTTPException(503, "部署配置 MOTOR_GATEWAY_ALLOWED_ORIGINS 格式无效") from None


def _safe_model(value) -> bool:
    if (not isinstance(value, str) or not value.strip() or len(value) > 256
            or any(ord(char) < 32 or ord(char) == 127 for char in value) or redact(value) != value):
        return False
    try:
        value.encode("utf-8")
        return True
    except UnicodeError:
        return False


class GatewayInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=100)
    base_url: StrictStr = Field(min_length=1, max_length=2048)
    service_ids: list[StrictStr] = Field(default_factory=list, max_length=64)
    api_key: StrictStr | None = Field(default=None, max_length=2000, repr=False)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("网关名称不能为空或包含控制字符")
        try:
            value.encode("utf-8")
        except UnicodeError as error:
            raise ValueError("网关名称必须是可编码的 UTF-8 文本") from error
        return value

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value):
        return gateway_url(value)

    @field_validator("service_ids")
    @classmethod
    def valid_services(cls, values):
        if any(not SERVICE_ID.fullmatch(value) for value in values) or len(set(values)) != len(values):
            raise ValueError("绑定服务标识无效或重复")
        return values

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value):
        if value and any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValueError("网关凭据不能包含空格、控制字符或非 ASCII 字符")
        return value

    @model_validator(mode="after")
    def encrypted_transport(self):
        if self.api_key:
            register_secret(self.api_key)
            if not self.base_url.startswith("https://"):
                raise ValueError("携带网关凭据的连接必须使用 HTTPS")
            if self.api_key in self.name or self.api_key in self.base_url:
                raise ValueError("网关名称和地址不能包含凭据")
        return self


class GatewayUpdate(GatewayInput):
    revision: StrictInt = Field(ge=1)
    clear_api_key: StrictBool = False

    @model_validator(mode="after")
    def unambiguous_key(self):
        if self.clear_api_key and self.api_key:
            raise ValueError("不能同时清除和替换网关凭据")
        return self


class GatewayProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: StrictInt = Field(ge=1)


class _GatewayRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except RequestValidationError:
                # FastAPI's default validation response includes rejected secrets.
                raise HTTPException(422, "网关请求字段格式无效，请核对名称、地址、服务绑定和修订号",
                                    headers={"Cache-Control": "no-store"}) from None
        return handle


def _service_fingerprint(service: dict) -> str:
    raw = json.dumps({key: service.get(key) for key in CONFIG_FIELDS}, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _expected_models(service: dict) -> list[str]:
    """Read native model-name precedence without flattening served-name lists."""
    config = service.get("user_config") or {}
    if not isinstance(config, dict):
        return []
    coordinator = config.get("motor_coordinator_config") or {}
    if not isinstance(coordinator, dict):
        return []
    aigw = coordinator.get("aigw", {})
    if not isinstance(aigw, dict):
        return []
    if "id" in aigw:
        value = aigw["id"]
    else:
        prefill, decode, union = (config.get("motor_engine_" + role + "_config") for role in ("prefill", "decode", "union"))
        section = prefill if prefill and decode else union
        if not isinstance(section, dict):
            return []
        engine, legacy = section.get("engine_config") or {}, section.get("model_config") or {}
        if not isinstance(engine, dict) or not isinstance(legacy, dict):
            return []
        # Match motor/config/resolver.py: keys are normalized before lookup;
        # engine values, including empty ones, take precedence over legacy.
        engine = {key.replace("-", "_"): val for key, val in engine.items()}
        legacy = {key.replace("-", "_"): val for key, val in legacy.items()}
        value = engine.get("served_model_name")
        if value is None:
            value = legacy.get("model_name")
        if not value and section.get("engine_type", "vllm") != "sglang":
            value = engine.get("model")
            if value is None:
                value = legacy.get("model_path")
    values = value if isinstance(value, list) else [value]
    if len(values) > MAX_MODELS or any(not _safe_model(item) for item in values):
        return []
    return list(dict.fromkeys(values))


class GatewayRegistry:
    def __init__(self, store, secrets, mutation_lock):
        self.store, self.secrets, self.mutation_lock = store, secrets, mutation_lock

    @staticmethod
    def _gateway(db, identifier: str) -> dict:
        row = db.resources.get("gateways",identifier)
        if row is None:
            raise HTTPException(404, "网关连接不存在")
        return row

    @staticmethod
    def _bindings(db, identifiers: list[str]) -> tuple[list[dict], dict[str, str]]:
        bindings, fingerprints = [], {}
        for identifier in identifiers:
            row = db.resources.get("services",identifier)
            if row is None:
                raise HTTPException(422, "绑定的 Motor 服务不存在，请刷新服务列表")
            service = row
            bindings.append({"service_id": identifier, "expected_model_ids": _expected_models(service)})
            fingerprints[identifier] = _service_fingerprint(service)
        return bindings, fingerprints

    @staticmethod
    def _name_available(db, name: str, excluding: str = ""):
        rows = db.resources.list("gateways")
        if len(rows) >= 200 and not excluding:
            raise HTTPException(409, "网关连接数量已达到上限 200")
        if any(value["id"] != excluding and value["name"].casefold() == name.casefold() for value in rows):
            raise HTTPException(409, "网关连接名称已存在")

    def view(self, value: dict) -> dict:
        result = {key: copy.deepcopy(value[key]) for key in ("id", "name", "base_url", "service_ids", "revision", "created_at", "updated_at")}
        result["name"] = redact(result["name"])
        result.update(has_api_key=bool(value.get("_api_key")), deployment=copy.deepcopy(DEPLOYMENT), last_check=None)
        try:
            result["probe_allowed"] = _origin(result["base_url"]) in _allowed_origins()
        except HTTPException:
            result["probe_allowed"] = False
        check = value.get("last_check")
        if isinstance(check, dict):
            result["last_check"] = {key: copy.deepcopy(check[key]) for key in
                ("status", "checked_at", "reason", "message", "model_ids", "model_count", "bindings", "http_status") if key in check}
            try:
                with self.store.unit_of_work(write=True) as db:
                    _, current = self._bindings(db, value["service_ids"])
                stale = current != value.get("_service_fingerprints", {})
            except HTTPException:
                stale = True
            if stale:
                result["last_check"].update(status="missing", reason="binding_configuration_changed",
                    message="绑定服务配置已变化，请重新检查网关连接", stale=True)
        return result

    def get(self, identifier: str) -> dict:
        with self.store.unit_of_work(write=True) as db:
            value = self._gateway(db, identifier)
        return self.view(value)

    def create(self, data: GatewayInput) -> dict:
        with self.mutation_lock, self.store.unit_of_work(write=True) as db:
            self._name_available(db, data.name)
            self._bindings(db, data.service_ids)
            timestamp = now()
            value = {"id": uid("gw_"), "name": data.name, "base_url": data.base_url, "service_ids": data.service_ids,
                     "revision": 1, "created_at": timestamp, "updated_at": timestamp,
                     "_api_key": self.secrets.encrypt(data.api_key or "")}
            self.store._put(db, "gateways", value)
            self.store.audit("创建网关连接", value["id"], "success", "已保存已有网关连接；未部署网关进程", connection=db)
        return self.view(value)

    def update(self, identifier: str, data: GatewayUpdate) -> dict:
        with self.mutation_lock, self.store.unit_of_work(write=True) as db:
            previous = self._gateway(db, identifier)
            if previous["revision"] != data.revision:
                raise HTTPException(409, "网关连接已更新，请重新加载后保存")
            self._name_available(db, data.name, excluding=identifier)
            self._bindings(db, data.service_ids)
            encoded = "" if data.clear_api_key else self.secrets.encrypt(data.api_key) if data.api_key else previous.get("_api_key", "")
            if encoded and not data.base_url.startswith("https://"):
                raise HTTPException(422, "携带网关凭据的连接必须使用 HTTPS；请先明确清除凭据")
            if encoded:
                try:
                    key = self.secrets.decrypt(encoded)
                except Exception:
                    raise HTTPException(503, "网关凭据无法解密，请明确替换或清除凭据") from None
                register_secret(key)
                if key and (key in data.name or key in data.base_url):
                    raise HTTPException(422, "网关名称和地址不能包含凭据")
            value = {"id": identifier, "name": data.name, "base_url": data.base_url, "service_ids": data.service_ids,
                     "revision": previous["revision"] + 1, "created_at": previous["created_at"], "updated_at": now(), "_api_key": encoded}
            self.store._put(db, "gateways", value)
            self.store.audit("更新网关连接", identifier, "success", "已保存新修订，先前检查结果已失效", connection=db)
        return self.view(value)

    def delete(self, identifier: str, revision: int) -> dict:
        with self.mutation_lock, self.store.unit_of_work(write=True) as db:
            previous = self._gateway(db, identifier)
            if previous["revision"] != revision:
                raise HTTPException(409, "网关连接已更新，请重新加载后删除")
            db.resources.delete("gateways",identifier)
            self.store.audit("删除网关连接", identifier, "success", "仅删除连接记录，未停止或删除网关进程", connection=db)
        return {"ok": True, "id": identifier}

    @staticmethod
    async def _fetch_models(base_url: str, headers: dict, context: ssl.SSLContext) -> tuple[int, list[str]]:
        from .integrations import project_models
        destination, hostname = await _gateway_destination(base_url)
        headers = {**headers, "Host": urlsplit(base_url).netloc}
        async with httpx.AsyncClient(timeout=8, verify=context, trust_env=False, follow_redirects=False,
                                     limits=httpx.Limits(max_connections=1, max_keepalive_connections=0)) as client:
            async with client.stream("GET", str(destination) + "/models", headers=headers,
                                     extensions={"sni_hostname": hostname}) as response:
                if response.status_code != 200:
                    return response.status_code, []
                media = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                encoding = response.headers.get("content-encoding", "identity").lower()
                length = response.headers.get("content-length")
                if ((media != "application/json" and not media.endswith("+json")) or encoding not in {"identity", "gzip"}
                        or length is not None and (not length.isascii() or not length.isdigit() or int(length) > MAX_RESPONSE_BYTES)):
                    raise ValueError()
                body, raw_bytes = bytearray(), 0
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
                async for chunk in response.aiter_raw(chunk_size=65536):
                    raw_bytes += len(chunk)
                    if raw_bytes > MAX_RESPONSE_BYTES:
                        raise ValueError()
                    # max_length bounds allocation before decompression, including
                    # a gzip bomb sent despite Accept-Encoding: identity.
                    decoded = decoder.decompress(chunk, MAX_RESPONSE_BYTES - len(body) + 1) if decoder else chunk
                    if len(body) + len(decoded) > MAX_RESPONSE_BYTES or decoder and decoder.unconsumed_tail:
                        raise ValueError()
                    body.extend(decoded)
                if decoder and (not decoder.eof or decoder.unused_data):
                    raise ValueError()
                document = json.loads(body)
        projected = project_models(document)
        return 200, [item["id"] for item in projected]

    def _discover(self, value: dict) -> dict:
        result = {"status": "unreachable", "checked_at": now(), "reason": "request_failed", "message": "无法读取网关模型列表",
                  "model_ids": [], "model_count": 0, "bindings": []}
        key = ""
        try:
            key = self.secrets.decrypt(value.get("_api_key", ""))
            if key:
                register_secret(key)
                if not value["base_url"].startswith("https://"):
                    raise HTTPException(422, "携带网关凭据的连接必须使用 HTTPS")
            try:
                ca = os.getenv("MOTOR_GATEWAY_CA_BUNDLE") or None
                if ca and (not Path(ca).is_file() or Path(ca).stat().st_size > 4 * 1024 * 1024):
                    raise OSError()
                context = ssl.create_default_context(cafile=ca)
                if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
                    raise ValueError()
            except (OSError, ValueError):
                raise HTTPException(503, "部署配置的网关 TLS 信任材料不可用") from None
            headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
            if key:
                headers["Authorization"] = "Bearer " + key
            async def fetch():
                return await asyncio.wait_for(self._fetch_models(value["base_url"], headers, context), timeout=PROBE_BUDGET_SECONDS)
            status, identifiers = asyncio.run(fetch())
            result["http_status"] = status
            if status != 200:
                result.update(reason="http_error", message="网关模型发现返回非 200 状态")
                return result
            for identifier in identifiers:
                if not _safe_model(identifier) or key and key in identifier:
                    raise ValueError()
            result.update(status="discovered", reason="discovery_only", message="已读取模型列表；尚未核对绑定服务路由",
                          model_ids=identifiers, model_count=len(identifiers))
            return result
        except HTTPException:
            raise
        except TimeoutError:
            result.update(reason="timeout", message="网关模型发现超过 8 秒总时限")
            return result
        except (httpx.HTTPError, OSError, UnicodeError, TypeError, ValueError, RecursionError, zlib.error):
            result.update(reason="invalid_or_unavailable_response", message="网关响应不可用或不符合受支持的模型发现格式")
            return result
        except Exception:
            # Fernet/key-store failures must not expose ciphertext or exception text.
            result.update(reason="credential_or_transport_unavailable", message="网关凭据或请求环境不可用")
            return result
        finally:
            key = ""

    def probe(self, identifier: str, revision: int) -> dict:
        with self.mutation_lock, self.store.unit_of_work(write=True) as db:
            value = self._gateway(db, identifier)
            if value["revision"] != revision:
                raise HTTPException(409, "网关连接已更新，请重新加载后检查")
            bindings, fingerprints = self._bindings(db, value["service_ids"])
            if _origin(value["base_url"]) not in _allowed_origins():
                raise HTTPException(403, "该网关来源尚未加入部署允许清单 MOTOR_GATEWAY_ALLOWED_ORIGINS")
        check = self._discover(value)  # No mutation lock or SQL transaction across network I/O.
        if check["status"] == "discovered":
            available = set(check["model_ids"])
            for binding in bindings:
                expected = binding["expected_model_ids"]
                missing = [name for name in expected if name not in available]
                binding.update(missing_model_ids=missing, status="matched" if expected and not missing else "missing" if expected else "unconfigured")
            check["bindings"] = bindings
            if any(binding["status"] != "matched" for binding in bindings):
                check.update(status="missing", reason="model_binding_unmatched", message="部分绑定服务的模型未发现或尚未配置模型名称")
            elif bindings:
                check.update(status="matched", reason="model_bindings_matched", message="已发现绑定服务配置中的模型；不代表推理或网关进程完整健康")
        with self.mutation_lock, self.store.unit_of_work(write=True) as db:
            current = self._gateway(db, identifier)
            if current["revision"] != revision:
                raise HTTPException(409, "检查期间网关连接已变化，旧检查结果未保存")
            try:
                _, current_fingerprints = self._bindings(db, value["service_ids"])
            except HTTPException:
                raise HTTPException(409, "检查期间绑定服务已变化，旧检查结果未保存") from None
            if current_fingerprints != fingerprints:
                raise HTTPException(409, "检查期间绑定服务配置已变化，旧检查结果未保存")
            value.update(revision=revision + 1, updated_at=now(), last_check=check, _service_fingerprints=fingerprints)
            self.store._put(db, "gateways", value)
            self.store.audit("检查网关连接", identifier, check["status"], "已执行只读模型发现，未发送推理请求",
                             metadata={"model_count": check["model_count"], "binding_count": len(bindings), "reason": check["reason"]}, connection=db)
        return self.view(value)


def create_gateway_router(store, secrets, mutation_lock, role_for_request):
    """The application additionally attaches its authentication/CSRF dependency."""
    registry = GatewayRegistry(store, secrets, mutation_lock)
    router = APIRouter(prefix="/api/gateways", tags=["gateway-connections"], route_class=_GatewayRoute)

    def role(request: Request, allowed):
        if role_for_request(request) not in allowed:
            raise HTTPException(403, "当前账号无权执行此网关操作")

    readers = {"admin", "operator", "viewer", "demo"}

    @router.get("")
    def list_gateways(request: Request):
        role(request, readers)
        return {"items": [registry.view(item) for item in store.list("gateways")]}

    @router.post("", status_code=201)
    def create_gateway(data: GatewayInput, request: Request):
        role(request, {"admin"})
        return registry.create(data)

    @router.get("/{identifier}")
    def get_gateway(identifier: str, request: Request):
        role(request, readers)
        return registry.get(identifier)

    @router.put("/{identifier}")
    def update_gateway(identifier: str, data: GatewayUpdate, request: Request):
        role(request, {"admin"})
        return registry.update(identifier, data)

    @router.delete("/{identifier}")
    def delete_gateway(identifier: str, request: Request,
                       revision: str = Query(..., min_length=1, max_length=20, pattern=r"^[1-9][0-9]*$")):
        role(request, {"admin"})
        return registry.delete(identifier, int(revision))

    @router.post("/{identifier}/probe")
    def probe_gateway(identifier: str, data: GatewayProbe, request: Request):
        role(request, {"admin", "operator"})
        return registry.probe(identifier, data.revision)

    @router.get("/{identifier}/export")
    def export_gateway(identifier: str, request: Request):
        role(request, readers)
        value = registry.get(identifier)
        return {"format": "motor-console-gateway-connection-v1", "kind": "connection_descriptor", "deployable": False,
                "credentials_included": False, "gateway": {key: value[key] for key in
                    ("id", "name", "base_url", "service_ids", "revision", "has_api_key", "deployment")},
                "note": "仅描述已有网关连接；不含凭据，不是网关部署产物"}

    @router.post("/{identifier}/deploy")
    @router.post("/{identifier}/stop")
    @router.post("/{identifier}/upgrade")
    def unavailable_gateway_lifecycle(identifier: str, request: Request):
        role(request, {"admin"})
        registry.get(identifier)
        raise HTTPException(409, {"code": "artifact_not_configured", "message": DEPLOYMENT["message"]})

    return router
