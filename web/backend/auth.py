"""Accounts and revocable opaque sessions through the storage repository port.

Security references (reviewed 2026-11-02): OWASP Password Storage,
Authentication, Session Management and CSRF Prevention Cheat Sheets.
No default password, JWT signing implementation, local MFA or implied SSO.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from .observability import actor_identity, context_fields, redact, register_secret
from .ports.storage import DuplicateIdentity


ROLES = frozenset({"admin", "operator", "viewer"})
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{2,63}\Z")
_KDF_SLOTS = threading.BoundedSemaphore(2)
_COMMON = {"passwordpassword", "123456789012345", "1234567890123456", "adminadminadmin", "letmeinletmeinletmein"}


def permissions_for(role: str) -> list[str]:
    base = ["overview:read", "services:read", "metrics:read", "logs:read", "jobs:read"]
    if role in {"operator", "admin"}:
        base += ["services:operate", "configuration:read"]
    if role == "admin":
        base += ["users:manage", "nodes:manage", "system:manage"]
    return base if role in ROLES else []


def _boolean(name: str, default: str) -> bool:
    value = os.getenv(name, default).lower()
    if value not in {"true", "false"}:
        raise ValueError(name + " must be true or false")
    return value == "true"


def _error(code: int, message: str, **kwargs):
    raise HTTPException(code, message, **kwargs)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _stamp(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _username(value: str) -> str:
    result = value.strip().lower()
    if not _USERNAME.fullmatch(result):
        _error(422, "用户名需为 3..64 个英文小写字母、数字、点、下划线或短横线")
    return result


def _password(value: str) -> None:
    if not isinstance(value, str) or not 15 <= len(value) <= 128 or len(value.encode()) > 1024 or "\x00" in value:
        _error(422, "密码需为 15..128 个字符，支持空格及 Unicode；不得包含空字符")
    if value.casefold() in _COMMON or len(set(value)) < 4:
        _error(422, "请使用较长且难以猜测的密码或口令短语")


class PasswordHasher:
    """OWASP scrypt profile: N=2^17, r=8, p=1; random 128-bit salt."""
    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = self._derive(password, salt)
        return "scrypt$131072$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        if not _KDF_SLOTS.acquire(blocking=False):
            _error(429, "密码验证繁忙，请稍后重试", headers={"Retry-After": "2"})
        try:
            return hashlib.scrypt(password.encode(), salt=salt, n=2**17, r=8, p=1,
                                  maxmem=256 * 1024 * 1024, dklen=64)
        finally:
            _KDF_SLOTS.release()

    def verify(self, encoded: str, password: str) -> bool:
        try:
            algorithm, n, r, p, salt, expected = encoded.split("$")
            if (algorithm, n, r, p) != ("scrypt", "131072", "8", "1"):
                return False
            raw_salt, raw_expected = base64.b64decode(salt, validate=True), base64.b64decode(expected, validate=True)
            if len(raw_salt) != 16 or len(raw_expected) != 64:
                return False
            return hmac.compare_digest(self._derive(password, raw_salt), raw_expected)
        except (ValueError, TypeError):
            return False


@dataclass(frozen=True)
class AuthSettings:
    secure_cookie: bool = True
    session_ttl: int = 8 * 3600
    idle_timeout: int = 30 * 60
    cookie_name: str = ""
    trusted_origins: tuple[str, ...] = ()
    bootstrap_token: str = field(default="", repr=False)
    allow_legacy_token: bool = False
    legacy_role: str = "operator"
    throttle_window: int = 900
    account_attempts: int = 8
    ip_attempts: int = 40

    def __post_init__(self):
        if not self.cookie_name:
            object.__setattr__(self, "cookie_name", "__Host-motor_console_session" if self.secure_cookie else "motor_console_session")
        if self.cookie_name.startswith("__Host-") and not self.secure_cookie:
            raise ValueError("__Host- cookies require Secure")
        if self.bootstrap_token and len(self.bootstrap_token) < 32:
            raise ValueError("MOTOR_BOOTSTRAP_TOKEN must contain at least 32 characters")
        if self.legacy_role not in ROLES:
            raise ValueError("Invalid legacy-token role")
        if not 60 <= self.idle_timeout <= self.session_ttl <= 7 * 86400:
            raise ValueError("Session limits must satisfy 60 <= idle <= absolute <= 7 days")
        if not 1 <= self.account_attempts <= self.ip_attempts or self.throttle_window < 1:
            raise ValueError("Invalid login throttling limits")
        for origin in self.trusted_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment):
                raise ValueError("Trusted origins must be exact HTTP(S) origins without paths")

    @classmethod
    def from_env(cls):
        return cls(secure_cookie=_boolean("MOTOR_COOKIE_SECURE", "true"),
                   session_ttl=int(os.getenv("MOTOR_SESSION_TTL", "28800")),
                   idle_timeout=int(os.getenv("MOTOR_SESSION_IDLE_TIMEOUT", "1800")),
                   trusted_origins=tuple(v.strip().rstrip("/") for v in os.getenv("MOTOR_TRUSTED_ORIGINS", "").split(",") if v.strip()),
                   bootstrap_token=os.getenv("MOTOR_BOOTSTRAP_TOKEN", ""),
                   allow_legacy_token=_boolean("MOTOR_ALLOW_LEGACY_TOKEN", "false"),
                   legacy_role=os.getenv("MOTOR_LEGACY_TOKEN_ROLE", "operator"))


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    display_name: str
    role: str
    kind: str = "session"
    session_id: str | None = field(default=None, repr=False)
    expires_at: float | None = None
    auth_source: str = "local"

    def as_dict(self):
        return {"id": self.user_id, "username": self.username, "display_name": self.display_name,
                "role": self.role, "kind": self.kind, "auth_source": self.auth_source}


class AuthManager:
    def __init__(self, store, *, settings: AuthSettings | None = None, legacy_token: str = "", clock=None, hasher=None):
        self.store, self.settings = store, settings or AuthSettings.from_env()
        self.legacy_token, self.clock = legacy_token, clock or time.time
        register_secret(legacy_token)
        register_secret(self.settings.bootstrap_token)
        self.hasher = hasher or PasswordHasher()
        self.sso_status = {"enabled": False}
        if self.settings.allow_legacy_token and len(legacy_token) < 24:
            raise ValueError("Explicit compatibility credentials require at least 24 characters")
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(40))

    @contextmanager
    def transaction(self, *, write=False):
        with self.store.unit_of_work(write=write) as db:
            yield db

    def _event(self, db, actor: str, action: str, target: str, status="success", *, target_snapshot=None):
        event = redact({"id": "auth_" + secrets.token_hex(12), "time": _stamp(self.clock()),
                        "actor": actor_identity(actor), "request_id": context_fields().get("request_id", ""),
                        "action": "auth." + action, "target": target, "status": status, "detail": ""})
        db.audit.append(event, target_snapshot=target_snapshot)

    @staticmethod
    def _public(row):
        result = {key: row[key] for key in ("id", "username", "display_name", "role", "disabled", "created_at", "updated_at", "auth_source")}
        result["disabled"] = bool(result["disabled"])
        for key in ("created_at", "updated_at"):
            result[key] = _stamp(result[key])
        return result

    def setup_required(self) -> bool:
        with self.transaction() as db:
            return not db.accounts.exists_any().fetchone()

    def check_origin(self, request: Request):
        origin = request.headers.get("origin")
        if origin is None:
            referer = request.headers.get("referer", "")
            parsed = urlsplit(referer)
            origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
        accepted = self.settings.trusted_origins or (str(request.base_url).rstrip("/"),)
        if not origin or origin == "null" or origin not in accepted or request.headers.get("sec-fetch-site") == "cross-site":
            _error(403, "请求来源校验失败")

    def _throttle(self, request: Request, account: str):
        # Ignore untrusted forwarded headers. The server/proxy must establish
        # request.client correctly; operators must configure trusted proxies.
        address = request.client.host if request.client else "unknown"
        now = self.clock()
        keys = (("ip:" + _digest(address), self.settings.ip_attempts),
                ("account:" + _digest(account), self.settings.account_attempts))
        blocked = False
        with self.transaction(write=True) as db:
            db.accounts.remove_expired_throttles(now - self.settings.throttle_window * 2)
            for key, maximum in keys:
                row = db.accounts.get_throttle(key).fetchone()
                if row and now - row["window_start"] < self.settings.throttle_window:
                    blocked |= row["attempts"] >= maximum
                    db.accounts.increment_throttle(key)
                else:
                    db.accounts.reset_throttle(key, now)
            if blocked:
                self._event(db, "anonymous", "login_limited", "account", "denied")
        if blocked:
            _error(429, "登录尝试过多，请稍后重试", headers={"Retry-After": str(self.settings.throttle_window)})

    def _new_session(self, db, user) -> tuple[str, float]:
        raw, now = secrets.token_urlsafe(32), self.clock()
        expires = now + self.settings.session_ttl
        db.accounts.prune_expired_sessions(now, now - self.settings.idle_timeout)
        db.accounts.create_session(_digest(raw), user['id'], user['auth_version'], now, now, expires)
        db.accounts.trim_user_sessions(user['id'])
        return raw, expires

    @staticmethod
    def csrf(raw: str) -> str:
        return hmac.new(raw.encode(), b"motor-console-csrf-v1", hashlib.sha256).hexdigest()

    def set_cookie(self, response: Response, raw: str):
        response.set_cookie(self.settings.cookie_name, raw, max_age=self.settings.session_ttl,
                            secure=self.settings.secure_cookie, httponly=True, samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"

    def clear_cookie(self, response: Response):
        response.delete_cookie(self.settings.cookie_name, path="/", secure=self.settings.secure_cookie, httponly=True, samesite="lax")

    def authenticate(self, request: Request, *, required=True) -> Principal | None:
        # A present browser cookie has priority: a stale/forged cookie cannot
        # silently downgrade to an unrelated shared machine credential.
        raw = request.cookies.get(self.settings.cookie_name)
        if raw and len(raw) <= 256:
            now = self.clock()
            with self.transaction(write=True) as db:
                row = db.accounts.get_session_account(_digest(raw)).fetchone()
                if row and not row["disabled"] and row["auth_version"] == row["session_version"] and now < row["expires_at"] and now - row["last_seen"] < self.settings.idle_timeout:
                    db.accounts.touch_session(now, row['token_hash'])
                    principal = Principal(row["id"], row["username"], row["display_name"], row["role"], session_id=row["token_hash"], expires_at=row["expires_at"], auth_source=row["auth_source"])
                    request.state.principal = principal
                    return principal
                db.accounts.revoke_session(_digest(raw))
        elif not raw and self.settings.allow_legacy_token:
            supplied = request.headers.get("authorization", "")
            if hmac.compare_digest(supplied.encode(), ("Bearer " + self.legacy_token).encode()):
                principal = Principal("machine:legacy", "legacy-machine", "兼容机器凭据", self.settings.legacy_role, kind="machine", auth_source="legacy")
                request.state.principal = principal
                return principal
        if required:
            _error(401, "请先登录控制台")
        return None

    def authorize(self, request: Request, allowed_roles=ROLES, *, csrf=True, account_only=False) -> Principal:
        principal = self.authenticate(request)
        if principal.role not in allowed_roles or account_only and principal.kind != "session":
            _error(403, "当前账号无权执行此操作")
        if csrf and request.method not in SAFE_METHODS and principal.kind == "session":
            self.check_origin(request)
            expected = self.csrf(request.cookies[self.settings.cookie_name])
            if not hmac.compare_digest(request.headers.get("x-csrf-token", "").encode(), expected.encode()):
                _error(403, "CSRF 校验失败，请刷新页面后重试")
        return principal

    def _login_result(self, user, raw: str, expires: float, response: Response):
        self.set_cookie(response, raw)
        return {"user": self._public(user), "csrf_token": self.csrf(raw), "expires_at": _stamp(expires)}

    def bootstrap(self, request: Request, response: Response, *, username: str, password: str, display_name=""):
        self.check_origin(request)
        self._throttle(request, "bootstrap")
        supplied = request.headers.get("x-bootstrap-token", "")
        if not self.settings.bootstrap_token or not hmac.compare_digest(supplied.encode(), self.settings.bootstrap_token.encode()):
            _error(403, "管理员初始化凭据无效或未配置")
        if not self.setup_required():
            _error(409, "管理员初始化已完成")
        username = _username(username)
        _password(password)
        encoded = self.hasher.hash(password)
        with self.transaction(write=True) as db:
            if db.accounts.exists_any().fetchone():
                _error(409, "管理员初始化已完成")
            now, user_id = self.clock(), "usr_" + secrets.token_hex(16)
            db.accounts.create_user(user_id, username, display_name.strip() or username, encoded, 'admin', now, now, 'local')
            user = db.accounts.get_user(user_id).fetchone()
            raw, expires = self._new_session(db, user)
            self._event(db, user_id, "bootstrap", user_id)
        return self._login_result(user, raw, expires, response)

    def login(self, request: Request, response: Response, username: str, password: str):
        self.check_origin(request)
        normalized = username.strip().lower()
        self._throttle(request, normalized)
        with self.transaction() as db:
            user = db.accounts.get_username(normalized).fetchone()
        valid = self.hasher.verify(user["password_hash"] if user and user["password_hash"] else self.dummy_hash, password)
        failed = not valid or not user or user["disabled"] or user["auth_source"] != "local"
        with self.transaction(write=True) as db:
            current = db.accounts.get_user(user['id']).fetchone() if user else None
            if failed or not current or current["disabled"] or current["auth_version"] != user["auth_version"] or current["password_hash"] != user["password_hash"]:
                self._event(db, "anonymous", "login", "account", "denied")
                result = None
            else:
                old = request.cookies.get(self.settings.cookie_name, "")
                if old:
                    db.accounts.revoke_session(_digest(old))
                raw, expires = self._new_session(db, current)
                self._event(db, current["id"], "login", current["id"])
                result = (current, raw, expires)
        if result is None:
            _error(401, "用户名或密码不正确")
        return self._login_result(*result, response)

    def users(self, request):
        self.authorize(request, {"admin"}, account_only=True)
        with self.transaction() as db:
            return {"items": [self._public(r) for r in db.accounts.list_users()]}

    def create_user(self, request, *, username, password, role="viewer", display_name=""):
        actor = self.authorize(request, {"admin"}, account_only=True)
        username = _username(username)
        _password(password)
        if role not in ROLES:
            _error(422, "角色无效")
        encoded = self.hasher.hash(password)
        with self.transaction(write=True) as db:
            self._require_live_admin(db, actor)
            now, user_id = self.clock(), "usr_" + secrets.token_hex(16)
            try:
                db.accounts.create_user(user_id, username, display_name.strip() or username, encoded, role, now, now, 'local')
            except DuplicateIdentity:
                _error(409, "用户名已存在")
            self._event(db, actor.user_id, "user_create", user_id)
            return self._public(db.accounts.get_user(user_id).fetchone())

    def _require_live_admin(self, db, principal):
        row = db.accounts.get_admin_status(principal.user_id).fetchone()
        session = db.accounts.get_session_window(principal.session_id).fetchone()
        if (not row or row["role"] != "admin" or row["disabled"] or not session
                or self.clock() >= session["expires_at"] or self.clock() - session["last_seen"] >= self.settings.idle_timeout):
            _error(403, "管理员会话已失效")

    def update_user(self, request, user_id, *, role=None, disabled=None, display_name=None, delete=False, new_password=None):
        actor = self.authorize(request, {"admin"}, account_only=True)
        if role is not None and role not in ROLES:
            _error(422, "角色无效")
        encoded = None
        if new_password is not None:
            _password(new_password)
            encoded = self.hasher.hash(new_password)
        with self.transaction(write=True) as db:
            self._require_live_admin(db, actor)
            user = db.accounts.get_user(user_id).fetchone()
            if not user:
                _error(404, "账号不存在")
            target_role, target_disabled = role or user["role"], bool(user["disabled"] if disabled is None else disabled)
            if user["role"] == "admin" and not user["disabled"] and (delete or target_disabled or target_role != "admin"):
                count = db.accounts.count_active_admins().fetchone()[0]
                local_count = db.accounts.count_active_local_admins().fetchone()[0]
                if count <= 1 or user["auth_source"] == "local" and local_count <= 1:
                    _error(409, "不能删除、禁用或降级最后一个有效本地管理员")
            if new_password is not None and user["auth_source"] != "local":
                _error(409, "外部身份账号的密码由身份提供方管理")
            db.accounts.revoke_user_sessions(user_id)
            if delete:
                db.accounts.delete_identities(user_id)
                db.accounts.delete_user(user_id)
                result = {"ok": True}
            else:
                db.accounts.update_user(target_role, int(target_disabled), display_name.strip() if display_name is not None else user['display_name'], encoded or user['password_hash'], self.clock(), user_id)
                result = self._public(db.accounts.get_user(user_id).fetchone())
            self._event(db, actor.user_id, "user_delete" if delete else "password_reset" if encoded else "user_update", user_id,
                        target_snapshot={key: user[key] for key in ("id", "username", "display_name")} if delete else None)
            return result

    def change_password(self, request, response, current_password, new_password):
        actor = self.authorize(request, account_only=True)
        self._throttle(request, "change:" + actor.user_id)
        _password(new_password)
        with self.transaction() as db:
            user = db.accounts.get_user(actor.user_id).fetchone()
        if user["auth_source"] != "local":
            _error(409, "外部账号请在身份提供方修改密码")
        if not self.hasher.verify(user["password_hash"], current_password):
            _error(401, "当前密码不正确")
        encoded = self.hasher.hash(new_password)
        with self.transaction(write=True) as db:
            current = db.accounts.get_user(actor.user_id).fetchone()
            session = db.accounts.has_session(actor.session_id).fetchone()
            if not current or current["disabled"] or current["auth_version"] != user["auth_version"] or not session:
                _error(409, "账号状态已变化，请重新登录")
            db.accounts.update_password(encoded, self.clock(), actor.user_id)
            db.accounts.revoke_user_sessions(actor.user_id)
            self._event(db, actor.user_id, "password_change", actor.user_id)
        self.clear_cookie(response)
        return {"ok": True, "reauthenticate": True}

    def federated_login(self, request, response, *, issuer: str, subject: str, display_name: str, role="viewer"):
        """Internal bridge: only call after the OIDC adapter verifies the token.

        Identity is (issuer, subject), never email or a local username. Explicit
        server role mappings are reapplied at login; disabled local records stay
        disabled. A local administrator must exist before external provisioning.
        """
        if role not in ROLES or not issuer or not isinstance(subject, str) or not 1 <= len(subject) <= 255:
            _error(403, "外部身份或角色无效")
        with self.transaction(write=True) as db:
            if not db.accounts.has_local_admin().fetchone():
                _error(409, "请先初始化并保留有效的本地管理员")
            user = db.accounts.get_federated_user(issuer, subject).fetchone()
            now = self.clock()
            if user and user["auth_source"] != "oidc":
                _error(403, "外部身份关联类型无效，拒绝关联本地账号")
            if user and user["disabled"]:
                _error(403, "该外部账号已在控制台禁用")
            if not user:
                user_id = "usr_" + secrets.token_hex(16)
                username = "oidc_" + _digest(issuer + "\x00" + subject)[:32]
                db.accounts.create_user(user_id, username, display_name[:100] or username, None, role, now, now, 'oidc')
                db.accounts.bind_identity(issuer, subject, user_id)
                self._event(db, user_id, "oidc_provision", user_id)
            else:
                user_id = user["id"]
                if user["role"] != role:
                    db.accounts.set_role(role, now, user_id)
                    db.accounts.revoke_user_sessions(user_id)
                db.accounts.set_display(display_name[:100] or user['display_name'], now, user_id)
            user = db.accounts.get_user(user_id).fetchone()
            previous = request.cookies.get(self.settings.cookie_name)
            if previous:
                db.accounts.revoke_session(_digest(previous))
            raw, expires = self._new_session(db, user)
            self._event(db, user_id, "oidc_login", user_id)
        return self._login_result(user, raw, expires, response)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginInput(_Body):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128, repr=False)


class BootstrapInput(LoginInput):
    display_name: str = Field(default="", max_length=100)


class UserInput(BootstrapInput):
    role: Literal["admin", "operator", "viewer"] = "viewer"


class UserUpdate(_Body):
    role: Literal["admin", "operator", "viewer"] | None = None
    disabled: bool | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=100)


class PasswordInput(_Body):
    current_password: str = Field(min_length=1, max_length=128, repr=False)
    new_password: str = Field(min_length=15, max_length=128, repr=False)


class ResetInput(_Body):
    new_password: str = Field(min_length=15, max_length=128, repr=False)


class _AuthRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()
        async def handle(request):
            try:
                return await original(request)
            except RequestValidationError:
                # FastAPI's default error includes input values, including a
                # rejected password. Never reflect account secrets in errors.
                _error(422, "账号请求字段格式无效", headers={"Cache-Control": "no-store"})
        return handle


def create_auth_router(manager: AuthManager) -> APIRouter:
    def uncache(response: Response):
        response.headers.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
    router = APIRouter(prefix="/api/auth", tags=["accounts"], route_class=_AuthRoute, dependencies=[Depends(uncache)])

    @router.get("/status")
    def status(request: Request, response: Response):
        principal = manager.authenticate(request, required=False)
        response.headers["Cache-Control"] = "no-store"
        return {"setup_required": manager.setup_required(), "authenticated": bool(principal),
                "user": principal.as_dict() if principal else None,
                "csrf_token": manager.csrf(request.cookies[manager.settings.cookie_name]) if principal and principal.kind == "session" else None,
                "expires_at": _stamp(principal.expires_at) if principal and principal.expires_at else None,
                "permissions": permissions_for(principal.role) if principal else [], "sso": manager.sso_status}

    @router.post("/bootstrap", status_code=201)
    def bootstrap(data: BootstrapInput, request: Request, response: Response):
        return manager.bootstrap(request, response, **data.model_dump())

    @router.post("/login")
    def login(data: LoginInput, request: Request, response: Response):
        return manager.login(request, response, **data.model_dump())

    @router.post("/logout")
    def logout(request: Request, response: Response):
        actor = manager.authorize(request, account_only=True)
        with manager.transaction(write=True) as db:
            db.accounts.revoke_session(actor.session_id)
            manager._event(db, actor.user_id, "logout", actor.user_id)
        manager.clear_cookie(response)
        return {"ok": True}

    @router.post("/password")
    def password(data: PasswordInput, request: Request, response: Response):
        return manager.change_password(request, response, **data.model_dump())

    @router.get("/users")
    def users(request: Request):
        return manager.users(request)

    @router.post("/users", status_code=201)
    def create_user(data: UserInput, request: Request):
        return manager.create_user(request, **data.model_dump())

    @router.patch("/users/{user_id}")
    def update_user(user_id: str, data: UserUpdate, request: Request):
        return manager.update_user(request, user_id, **data.model_dump(exclude_unset=True))

    @router.post("/users/{user_id}/reset-password")
    def reset_password(user_id: str, data: ResetInput, request: Request):
        return manager.update_user(request, user_id, **data.model_dump())

    @router.delete("/users/{user_id}")
    def delete_user(user_id: str, request: Request):
        return manager.update_user(request, user_id, delete=True)

    return router
